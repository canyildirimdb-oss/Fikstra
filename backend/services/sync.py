import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Match, MatchContext, NotificationLog, OddsCurrent, OddsHistory, Prediction, ScraperCache, SyncLog
from scrapers.clubelo import load_clubelo_ratings, lookup_clubelo
from scrapers.external_data import (
    fetch_espn_events_for_dates,
    fetch_espn_match_details,
    fetch_espn_team_season_stats,
    fetch_espn_team_shot_xg,
    fetch_global_team_recent_matches,
    fetch_transfermarkt_injuries,
    get_target_espn_dates,
)
from scrapers.iddaa import fetch_iddaa_bulletin
from scrapers.odds_api import sync_the_odds_api
from scrapers.understat import UNDERSTAT_LEAGUES, fetch_understat_league_xg, lookup_understat_xg
from services.ai_summary import generate_match_summary
from services.analyzer import analyze_upcoming_matches
from services.coupons import generate_daily_coupons
from services.push import dispatch_sync_alerts
from services.settlement import settle_finished_matches
from utils.team_matcher import find_best_external_match

_SYNC_LOCK = threading.Lock()
CONTEXT_CACHE_TTL = timedelta(hours=6)
SYNC_LOCK_KEY = "system:sync_lock"
SYNC_LOCK_STALE_MINUTES = 15
_PG_SYNC_LOCK_KEY = 84729101


def acquire_db_sync_lock(db: Session, now: Optional[datetime] = None) -> bool:
    """Acquire DB-backed sync_lock with 15-minute stale-timeout protection."""
    now = now or datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=SYNC_LOCK_STALE_MINUTES)
    row = db.query(ScraperCache).filter(ScraperCache.cache_key == SYNC_LOCK_KEY).first()
    if row:
        try:
            payload = json.loads(row.payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if payload.get("locked"):
            upd = (
                row.updated_at.replace(tzinfo=timezone.utc)
                if row.updated_at and row.updated_at.tzinfo is None
                else row.updated_at
            )
            if upd and upd >= stale_cutoff:
                return False
        row.payload_json = json.dumps({"locked": True, "pid": os.getpid(), "acquired_at": now.isoformat()})
        row.updated_at = now
    else:
        db.add(
            ScraperCache(
                cache_key=SYNC_LOCK_KEY,
                payload_json=json.dumps({"locked": True, "pid": os.getpid(), "acquired_at": now.isoformat()}),
                updated_at=now,
            )
        )
    db.commit()
    return True


def release_db_sync_lock(db: Session) -> None:
    row = db.query(ScraperCache).filter(ScraperCache.cache_key == SYNC_LOCK_KEY).first()
    if row:
        row.payload_json = json.dumps({"locked": False, "released_at": datetime.now(timezone.utc).isoformat()})
        row.updated_at = datetime.now(timezone.utc)
        db.commit()


def is_sync_running(db: Optional[Session] = None) -> bool:
    if _SYNC_LOCK.locked():
        return True
    if db is not None:
        row = db.query(ScraperCache).filter(ScraperCache.cache_key == SYNC_LOCK_KEY).first()
        if row:
            try:
                payload = json.loads(row.payload_json or "{}")
            except json.JSONDecodeError:
                payload = {}
            if payload.get("locked"):
                upd = (
                    row.updated_at.replace(tzinfo=timezone.utc)
                    if row.updated_at and row.updated_at.tzinfo is None
                    else row.updated_at
                )
                if upd and upd >= datetime.now(timezone.utc) - timedelta(minutes=SYNC_LOCK_STALE_MINUTES):
                    return True
    return False


def _is_ctx_fresh(ctx: MatchContext | None, now: datetime) -> bool:
    if not ctx or ctx.context_updated_at is None:
        return False
    ts = (
        ctx.context_updated_at.replace(tzinfo=timezone.utc)
        if ctx.context_updated_at.tzinfo is None
        else ctx.context_updated_at
    )
    return ts >= (now - CONTEXT_CACHE_TTL)


def run_sync_pipeline(
    max_external_enrich: int = 40,
    max_tm_fresh: int = 16,
    include_injuries: bool = True,
    skip_odds_api: bool = False,
) -> dict[str, Any]:
    """Run Engine v5.1 sync: double-lock (in-process + 15m DB lock), iddaa, ESPN, ClubElo, TM, Poisson."""
    if not _SYNC_LOCK.acquire(blocking=False):
        return {"status": "already_running"}

    db: Session = SessionLocal()
    is_pg = db.bind is not None and db.bind.dialect.name == "postgresql"
    pg_locked = False
    db_locked = False
    try:
        if is_pg:
            pg_locked = bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _PG_SYNC_LOCK_KEY}).scalar())
            if not pg_locked:
                db.close()
                _SYNC_LOCK.release()
                return {"status": "already_running"}
        db_locked = acquire_db_sync_lock(db)
        if not db_locked:
            if is_pg and pg_locked:
                try:
                    db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _PG_SYNC_LOCK_KEY})
                except Exception:
                    pass
            db.close()
            _SYNC_LOCK.release()
            return {"status": "already_running"}
    except Exception:
        if is_pg and pg_locked:
            try:
                db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _PG_SYNC_LOCK_KEY})
            except Exception:
                pass
        db.close()
        _SYNC_LOCK.release()
        raise

    log = SyncLog(status="RUNNING", message="Started Engine v5.1 sync pipeline")
    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        now = datetime.now(timezone.utc)
        horizon_48h = now + timedelta(hours=48)
        bulletin = fetch_iddaa_bulletin()
        espn_events = fetch_espn_events_for_dates(get_target_espn_dates())

        matched_pairs: list[tuple[Match, dict[str, Any], bool]] = []

        for item in bulletin:
            match = db.query(Match).filter(Match.iddaa_id == item["iddaa_id"]).first()
            if not match:
                match = Match(
                    iddaa_id=item["iddaa_id"],
                    league=item["league"],
                    country=item["country"],
                    is_major_league=item["is_major_league"],
                    home_team=item["home_team"],
                    away_team=item["away_team"],
                    start_time_utc=item["start_time_utc"],
                    status=item["status"],
                    home_score=item["home_score"],
                    away_score=item["away_score"],
                    data_status="iddaa_only",
                )
                db.add(match)
                db.flush()
            else:
                match.league = item["league"]
                match.is_major_league = item["is_major_league"]
                match.start_time_utc = item["start_time_utc"]
                if match.status != "FINISHED":
                    match.status = item["status"]
                    if item["home_score"] is not None:
                        match.home_score = item["home_score"]
                    if item["away_score"] is not None:
                        match.away_score = item["away_score"]

            existing_odds = {
                (o.market_key, o.selection_key): o
                for o in db.query(OddsCurrent).filter(OddsCurrent.match_id == match.id).all()
            }
            for o_item in item["odds"]:
                key = (o_item["market_key"], o_item["selection_key"])
                curr = existing_odds.get(key)
                if not curr:
                    curr = OddsCurrent(
                        match_id=match.id,
                        market_key=o_item["market_key"],
                        selection_key=o_item["selection_key"],
                        iddaa_odd=o_item["odd"],
                        opening_odd=o_item["odd"],
                    )
                    db.add(curr)
                    existing_odds[key] = curr
                    db.add(
                        OddsHistory(
                            match_id=match.id,
                            market_key=o_item["market_key"],
                            selection_key=o_item["selection_key"],
                            iddaa_odd=o_item["odd"],
                            recorded_at=now,
                        )
                    )
                else:
                    if abs(curr.iddaa_odd - o_item["odd"]) >= 0.01:
                        curr.iddaa_odd = o_item["odd"]
                        db.add(
                            OddsHistory(
                                match_id=match.id,
                                market_key=o_item["market_key"],
                                selection_key=o_item["selection_key"],
                                iddaa_odd=o_item["odd"],
                                recorded_at=now,
                            )
                        )

            ext = find_best_external_match(
                match.home_team,
                match.away_team,
                match.start_time_utc,
                espn_events,
                external_match_id=match.external_match_id if match.external_source == "espn" else None,
                league_name=match.league,
            )
            if ext:
                match.external_source = "espn"
                match.external_match_id = ext["espn_id"]
                if match.status != "FINISHED" and ext.get("state") == "in":
                    if ext.get("home_score") is not None and ext.get("away_score") is not None:
                        match.status = "LIVE"
                        match.home_score = ext["home_score"]
                        match.away_score = ext["away_score"]
                if ext.get("home_logo_url"):
                    match.home_logo_url = ext["home_logo_url"]
                if ext.get("away_logo_url"):
                    match.away_logo_url = ext["away_logo_url"]
                if ext.get("league_logo_url"):
                    match.league_logo_url = ext["league_logo_url"]
                ctx = db.query(MatchContext).filter(MatchContext.match_id == match.id).first()
                fresh = _is_ctx_fresh(ctx, now)
                matched_pairs.append((match, ext, fresh))
            else:
                if match.data_status == "pending":
                    match.data_status = "no_external_match"

        # Prioritize enrichment queue: major + <=48h + missing/stale context first
        def _priority_key(pair: tuple[Match, dict[str, Any], bool]):
            m, _, fresh = pair
            m_time = (
                m.start_time_utc.replace(tzinfo=timezone.utc)
                if m.start_time_utc.tzinfo is None
                else m.start_time_utc
            )
            in_48h = m_time <= horizon_48h
            return (
                0 if m.is_major_league else 1,
                0 if in_48h else 1,
                1 if fresh else 0,
                m_time,
            )

        matched_pairs.sort(key=_priority_key)
        to_enrich = matched_pairs[:max_external_enrich]

        # Concurrent ESPN summary fetch (lightweight JSON)
        def _fetch_one(pair: tuple[Match, dict[str, Any], bool]):
            m, ext, _ = pair
            try:
                return fetch_espn_match_details(
                    ext["espn_id"],
                    ext["home_espn_id"],
                    ext["away_espn_id"],
                    match_start_utc=m.start_time_utc,
                )
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=6) as pool:
            espn_details_list = list(pool.map(_fetch_one, to_enrich))

        clubelo_ratings = load_clubelo_ratings(db)
        understat_cache: dict[str, dict[str, dict[str, float]]] = {}

        enriched_count = 0
        cache_hits = 0
        tm_fresh_count = 0
        tm_squad_fetches = 0

        for (match, ext, ctx_fresh), details in zip(to_enrich, espn_details_list):
            if not details:
                continue
            enriched_count += 1

            global_odds = details.get("global_odds") or {}
            if global_odds:
                db.flush()
                for oc in db.query(OddsCurrent).filter(OddsCurrent.match_id == match.id).all():
                    g_val = global_odds.get((oc.market_key, oc.selection_key))
                    if g_val:
                        oc.global_avg_odd = g_val
                        if not oc.global_source:
                            oc.global_source = "espn"

            ctx = db.query(MatchContext).filter(MatchContext.match_id == match.id).first()
            if not ctx:
                ctx = MatchContext(match_id=match.id)
                db.add(ctx)

            # ClubElo (24h cached map with European geographic & country guard)
            ctx.home_elo = (
                lookup_clubelo(ext["home_team"], clubelo_ratings, league=match.league, country=match.country)
                or lookup_clubelo(match.home_team, clubelo_ratings, league=match.league, country=match.country)
            )
            ctx.away_elo = (
                lookup_clubelo(ext["away_team"], clubelo_ratings, league=match.league, country=match.country)
                or lookup_clubelo(match.away_team, clubelo_ratings, league=match.league, country=match.country)
            )

            league_slug = details.get("league_slug")
            if details.get("home_goal_std") is not None:
                ctx.home_goal_std = details.get("home_goal_std")
            if details.get("away_goal_std") is not None:
                ctx.away_goal_std = details.get("away_goal_std")

            if ctx_fresh:
                cache_hits += 1
            else:
                injuries_payload = {"home": [], "away": []}
                if include_injuries and (match.is_major_league or tm_fresh_count < max_tm_fresh):
                    if tm_fresh_count < max_tm_fresh:
                        h_inj, h_sq_live = fetch_transfermarkt_injuries(
                            ext["home_team"],
                            db=db,
                            allow_squad_fetch=(tm_squad_fetches < 12),
                            match_league=match.league,
                        )
                        if h_sq_live:
                            tm_squad_fetches += 1
                        a_inj, a_sq_live = fetch_transfermarkt_injuries(
                            ext["away_team"],
                            db=db,
                            allow_squad_fetch=(tm_squad_fetches < 12),
                            match_league=match.league,
                        )
                        if a_sq_live:
                            tm_squad_fetches += 1
                        injuries_payload["home"] = h_inj
                        injuries_payload["away"] = a_inj
                        tm_fresh_count += 1

                # Global Multi-Source Form & Recent Matches (v5: country-isolated & team-id verified)
                h_global = fetch_global_team_recent_matches(
                    match.home_team,
                    match_start_utc=match.start_time_utc,
                    prefer_home=True,
                    team_espn_id=ext.get("home_espn_id"),
                    exclude_event_id=ext.get("espn_id"),
                    db=db,
                    league=match.league,
                    country=match.country,
                )
                a_global = fetch_global_team_recent_matches(
                    match.away_team,
                    match_start_utc=match.start_time_utc,
                    prefer_home=False,
                    team_espn_id=ext.get("away_espn_id"),
                    exclude_event_id=ext.get("espn_id"),
                    db=db,
                    league=match.league,
                    country=match.country,
                )

                h_form_val = h_global["form"] if h_global["form"] else details.get("home_form")
                a_form_val = a_global["form"] if a_global["form"] else details.get("away_form")
                h_rm_val = h_global["recent_matches"] if h_global["recent_matches"] else (details.get("home_recent_matches") or [])
                a_rm_val = a_global["recent_matches"] if a_global["recent_matches"] else (details.get("away_recent_matches") or [])

                ctx.home_form = json.dumps(h_form_val) if h_form_val is not None else None
                ctx.away_form = json.dumps(a_form_val) if a_form_val is not None else None
                ctx.home_goals_avg = h_global["goals_avg"] if h_global["goals_avg"] is not None else details["home_goals_avg"]
                ctx.away_goals_avg = a_global["goals_avg"] if a_global["goals_avg"] is not None else details["away_goals_avg"]
                ctx.home_conceded_avg = h_global["conceded_avg"] if h_global["conceded_avg"] is not None else details["home_conceded_avg"]
                ctx.away_conceded_avg = a_global["conceded_avg"] if a_global["conceded_avg"] is not None else details["away_conceded_avg"]
                ctx.home_rest_days = h_global["rest_days"] if h_global["rest_days"] is not None else details.get("home_rest_days")
                ctx.away_rest_days = a_global["rest_days"] if a_global["rest_days"] is not None else details.get("away_rest_days")
                ctx.injuries_json = json.dumps(injuries_payload, ensure_ascii=False)
                ctx.context_updated_at = now
                if h_rm_val or a_rm_val:
                    from utils.cache import set_cached_json
                    set_cached_json(
                        db,
                        f"recent_v4:{match.id}",
                        {
                            "home": h_rm_val,
                            "away": a_rm_val,
                            "home_source": h_global.get("source"),
                            "away_source": a_global.get("source"),
                        },
                    )

                # ESPN Standings & Seasonal Home/Away Stats (6h DB cached)
                if league_slug:
                    h_season = fetch_espn_team_season_stats(db, league_slug, ext["home_espn_id"])
                    a_season = fetch_espn_team_season_stats(db, league_slug, ext["away_espn_id"])
                    ctx.home_rank = h_season.get("rank")
                    ctx.away_rank = a_season.get("rank")
                    ctx.season_home_gf = h_season.get("home_gf")
                    ctx.season_home_ga = h_season.get("home_ga")
                    ctx.season_away_gf = a_season.get("away_gf")
                    ctx.season_away_ga = a_season.get("away_ga")
                    ctx.season_home_played = h_season.get("home_played")
                    ctx.season_away_played = a_season.get("away_played")

                # If UEFA/cup table has <= 2 venue matches, supplement from 10-match all-comp schedule
                h_sv = details.get("home_sched_venue") or {}
                a_sv = details.get("away_sched_venue") or {}
                if (ctx.season_home_played or 0) <= 2 and (h_sv.get("home_played") or 0) > (ctx.season_home_played or 0):
                    ctx.season_home_played = h_sv.get("home_played")
                    ctx.season_home_gf = h_sv.get("home_gf")
                    ctx.season_home_ga = h_sv.get("home_ga")
                if (ctx.season_away_played or 0) <= 2 and (a_sv.get("away_played") or 0) > (ctx.season_away_played or 0):
                    ctx.season_away_played = a_sv.get("away_played")
                    ctx.season_away_gf = a_sv.get("away_gf")
                    ctx.season_away_ga = a_sv.get("away_ga")

                # 1) Understat True xG/xGA for Top 5 leagues (12h DB cached)
                got_understat = False
                if league_slug in UNDERSTAT_LEAGUES:
                    if league_slug not in understat_cache:
                        u_map, _u_status = fetch_understat_league_xg(db, league_slug)
                        understat_cache[league_slug] = u_map
                    u_map = understat_cache[league_slug]
                    h_xg = lookup_understat_xg(ext["home_team"], u_map) or lookup_understat_xg(match.home_team, u_map)
                    a_xg = lookup_understat_xg(ext["away_team"], u_map) or lookup_understat_xg(match.away_team, u_map)
                    if h_xg and a_xg:
                        ctx.home_true_xg = h_xg["xg_per_game"]
                        ctx.home_true_xga = h_xg["xga_per_game"]
                        ctx.away_true_xg = a_xg["xg_per_game"]
                        ctx.away_true_xga = a_xg["xga_per_game"]
                        ctx.xg_source = "understat"
                        got_understat = True

                # 2) Fallback: ESPN Shot-xG from recent completed matches (12h DB cached)
                if not got_understat:
                    h_sxg = fetch_espn_team_shot_xg(db, ext["home_espn_id"], details.get("home_recent_refs") or [])
                    a_sxg = fetch_espn_team_shot_xg(db, ext["away_espn_id"], details.get("away_recent_refs") or [])
                    if h_sxg.get("shot_xg") is not None and a_sxg.get("shot_xg") is not None:
                        ctx.home_true_xg = h_sxg["shot_xg"]
                        ctx.home_true_xga = h_sxg["shot_xga"]
                        ctx.home_shots_pg = h_sxg.get("shots_pg")
                        ctx.home_sot_pg = h_sxg.get("sot_pg")
                        ctx.away_true_xg = a_sxg["shot_xg"]
                        ctx.away_true_xga = a_sxg["shot_xga"]
                        ctx.away_shots_pg = a_sxg.get("shots_pg")
                        ctx.away_sot_pg = a_sxg.get("sot_pg")
                        ctx.xg_source = "espn_shots"

            # Backfill Shot-xG if existing fresh context predated Motor v5.0
            if ctx.xg_source is None:
                h_sxg = fetch_espn_team_shot_xg(db, ext["home_espn_id"], details.get("home_recent_refs") or [])
                a_sxg = fetch_espn_team_shot_xg(db, ext["away_espn_id"], details.get("away_recent_refs") or [])
                if h_sxg.get("shot_xg") is not None and a_sxg.get("shot_xg") is not None:
                    ctx.home_true_xg = h_sxg["shot_xg"]
                    ctx.home_true_xga = h_sxg["shot_xga"]
                    ctx.home_shots_pg = h_sxg.get("shots_pg")
                    ctx.home_sot_pg = h_sxg.get("sot_pg")
                    ctx.away_true_xg = a_sxg["shot_xg"]
                    ctx.away_true_xga = a_sxg["shot_xga"]
                    ctx.away_shots_pg = a_sxg.get("shots_pg")
                    ctx.away_sot_pg = a_sxg.get("sot_pg")
                    ctx.xg_source = "espn_shots"

            # Backfill rank/season/played if existing fresh context predated Motor v4.1
            if league_slug and (ctx.season_home_played is None or (ctx.home_rank is None and ctx.season_home_gf is None)):
                h_season = fetch_espn_team_season_stats(db, league_slug, ext["home_espn_id"])
                a_season = fetch_espn_team_season_stats(db, league_slug, ext["away_espn_id"])
                ctx.home_rank = h_season.get("rank")
                ctx.away_rank = a_season.get("rank")
                ctx.season_home_gf = h_season.get("home_gf")
                ctx.season_home_ga = h_season.get("home_ga")
                ctx.season_away_gf = a_season.get("away_gf")
                ctx.season_away_ga = a_season.get("away_ga")
                ctx.season_home_played = h_season.get("home_played")
                ctx.season_away_played = a_season.get("away_played")

            inj_parsed = json.loads(ctx.injuries_json) if ctx.injuries_json else {}
            parts = []
            if global_odds:
                parts.append(f"global_odds({details.get('provider') or 'espn'})")
            if ctx.home_form or ctx.away_form:
                parts.append("form")
            if ctx.home_elo and ctx.away_elo:
                parts.append("elo")
            if ctx.season_home_gf is not None or ctx.season_away_gf is not None:
                parts.append("season")
            if ctx.home_true_xg is not None and ctx.away_true_xg is not None:
                parts.append("xg")
            if inj_parsed.get("home") or inj_parsed.get("away"):
                parts.append("tm_injuries")
            match.data_status = "+".join(parts) if parts else "espn_matched_no_details"

        # Also populate ClubElo for any remaining European scheduled matches without overwriting valid ELO
        for m in db.query(Match).filter(Match.status == "SCHEDULED").all():
            ctx = db.query(MatchContext).filter(MatchContext.match_id == m.id).first()
            need_home = ctx is None or ctx.home_elo is None
            need_away = ctx is None or ctx.away_elo is None
            if not need_home and not need_away:
                continue
            h_elo = (
                lookup_clubelo(m.home_team, clubelo_ratings, league=m.league, country=m.country)
                if need_home
                else None
            )
            a_elo = (
                lookup_clubelo(m.away_team, clubelo_ratings, league=m.league, country=m.country)
                if need_away
                else None
            )
            if (h_elo is not None) or (a_elo is not None):
                if not ctx:
                    ctx = MatchContext(match_id=m.id)
                    db.add(ctx)
                if need_home and h_elo is not None:
                    ctx.home_elo = h_elo
                if need_away and a_elo is not None:
                    ctx.away_elo = a_elo

        db.commit()
        # Ensure all remaining SCHEDULED matches (including non-ESPN leagues) get global form & recent_v4
        refresh_all_active_forms(db, force_refresh=False, only_missing=True)
        odds_api_res = (
            {"status": "skipped", "api_calls": 0, "cache_hits": 0, "matched": 0}
            if skip_odds_api
            else sync_the_odds_api(db)
        )
        db.commit()
        # Lock closing odds & CLV for matches starting within <= 25 minutes
        closing_cutoff = now + timedelta(minutes=25)
        near_preds = (
            db.query(Prediction)
            .join(Match, Prediction.match_id == Match.id)
            .filter(
                Match.status == "SCHEDULED",
                Match.start_time_utc <= closing_cutoff,
                Prediction.status == "PENDING",
                Prediction.closing_odd.is_(None),
            )
            .all()
        )
        for np in near_preds:
            curr_o = (
                db.query(OddsCurrent)
                .filter(
                    OddsCurrent.match_id == np.match_id,
                    OddsCurrent.market_key == np.market_key,
                    OddsCurrent.selection_key == np.selection_key,
                )
                .first()
            )
            if curr_o and curr_o.iddaa_odd > 1.0:
                ref_odd = np.published_odd if np.published_odd is not None else np.iddaa_odd
                np.closing_odd = curr_o.iddaa_odd
                np.clv_pct = round(((ref_odd / curr_o.iddaa_odd) - 1.0) * 100.0, 2)
        db.commit()

        settled = settle_finished_matches(db, espn_events=espn_events)
        analyzed = analyze_upcoming_matches(db)
        coupons = generate_daily_coupons(db)

        top_preds = (
            db.query(Prediction)
            .join(Match, Prediction.match_id == Match.id)
            .filter(Match.status == "SCHEDULED", Prediction.status == "PENDING")
            .order_by(Prediction.confidence_score.desc())
            .limit(10)
            .all()
        )
        seen_m: set[int] = set()
        for tp in top_preds:
            if tp.match_id in seen_m:
                continue
            seen_m.add(tp.match_id)
            generate_match_summary(db, tp.match)
            if len(seen_m) >= 3:
                break

        alerts = dispatch_sync_alerts(db, coupons)

        # Free-tier DB disk hygiene: prune >30d odds_history/sync_logs/notification_logs & >14d scraper_cache
        prune_30d_cutoff = now - timedelta(days=30)
        db.query(OddsHistory).filter(OddsHistory.recorded_at < prune_30d_cutoff).delete(synchronize_session=False)
        db.query(SyncLog).filter(SyncLog.started_at < prune_30d_cutoff).delete(synchronize_session=False)
        db.query(NotificationLog).filter(NotificationLog.sent_at < prune_30d_cutoff).delete(synchronize_session=False)
        prune_cache_cutoff = now - timedelta(days=14)
        db.query(ScraperCache).filter(
            ScraperCache.updated_at < prune_cache_cutoff,
            ~ScraperCache.cache_key.like("system:%"),
            ~ScraperCache.cache_key.like("odds_api:%"),
        ).delete(synchronize_session=False)

        log.status = "SUCCESS"
        log.finished_at = datetime.now(timezone.utc)
        log.matches_synced = len(bulletin)
        log.message = (
            f"bulletin={len(bulletin)}, matched={len(matched_pairs)}, enriched={enriched_count}, "
            f"ctx_cache_hits={cache_hits}, tm_fresh={tm_fresh_count}, settled={settled}, "
            f"analyzed={analyzed}, coupons={len(coupons)}, alerts={len(alerts)}"
        )
        db.commit()

        try:
            from app.telegram_bot import check_and_send_health_alerts, dispatch_telegram_settlements, dispatch_telegram_updates
            dispatch_telegram_updates(db)
            dispatch_telegram_settlements(db)
            check_and_send_health_alerts(db)
        except Exception:
            pass

        try:
            from app.sportoto_sync import maybe_run_sportoto_auto_sync
            maybe_run_sportoto_auto_sync(db, min_interval_minutes=60)
        except Exception:
            pass

        try:
            from app.routers.api import clear_api_cache
            clear_api_cache()
        except Exception:
            pass

        return {
            "status": "success",
            "bulletin_count": len(bulletin),
            "matched_count": len(matched_pairs),
            "enriched_count": enriched_count,
            "context_cache_hits": cache_hits,
            "tm_fresh": tm_fresh_count,
            "settled_count": settled,
            "analyzed_count": analyzed,
            "coupons_count": len(coupons),
            "alerts_count": len(alerts),
            "odds_api": odds_api_res,
        }
    except Exception as exc:
        db.rollback()
        log.status = "ERROR"
        log.finished_at = datetime.now(timezone.utc)
        log.message = str(exc)[:500]
        db.commit()
        try:
            from app.telegram_bot import check_and_send_health_alerts
            check_and_send_health_alerts(db)
        except Exception:
            pass
        raise
    finally:
        if db_locked:
            try:
                release_db_sync_lock(db)
            except Exception:
                pass
        if is_pg and pg_locked:
            try:
                db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _PG_SYNC_LOCK_KEY})
            except Exception:
                pass
        db.close()
        _SYNC_LOCK.release()


def refresh_all_active_forms(
    db: Session,
    force_refresh: bool = True,
    only_missing: bool = False,
    only_with_predictions: bool = False,
) -> dict[str, Any]:
    """
    Refresh global recent matches & form for SCHEDULED matches using 0 Odds API credits.
    Bounded concurrency (max_workers=8) and per-team timeout (5.5s).
    """
    import re as _re
    import time as _time
    from utils.cache import get_cached_json, set_cached_json
    from utils.team_matcher import extract_country_context, normalize_team_name

    def _extract_fotmob_id_from_logo(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        m_id = _re.search(r"/teamlogo/(\d+)\.png", url)
        return m_id.group(1) if m_id else None

    t0 = _time.monotonic()
    q = db.query(Match).filter(Match.status == "SCHEDULED")
    if only_with_predictions:
        pred_match_ids = db.query(Prediction.match_id).distinct()
        q = q.filter(Match.id.in_(pred_match_ids))
    scheduled = q.order_by(Match.is_major_league.desc(), Match.start_time_utc.asc()).all()
    if not scheduled:
        return {
            "ok": True,
            "total_matches": 0,
            "total_teams": 0,
            "updated_teams": 0,
            "coverage_pct": 0.0,
            "source_breakdown": {},
            "elapsed_sec": 0.0,
        }

    # Bulk-load 2h global_team_recent:v5 cache in 1 SQL query (country/ID isolated)
    now_utc = datetime.now(timezone.utc)
    cutoff_2h = now_utc - timedelta(hours=2)
    cached_results: dict[str, dict[str, Any]] = {}
    if not force_refresh:
        for row in db.query(ScraperCache).filter(ScraperCache.cache_key.like("global_team_recent:v5:%")).all():
            if not row.updated_at:
                continue
            upd = row.updated_at.replace(tzinfo=timezone.utc) if row.updated_at.tzinfo is None else row.updated_at
            if upd < cutoff_2h:
                continue
            ck = row.cache_key.removeprefix("global_team_recent:v5:")
            try:
                payload = json.loads(row.payload_json)
                if isinstance(payload, dict) and payload.get("recent_matches"):
                    cached_results[ck] = payload
            except Exception:
                pass

    existing_recent_mids: set[int] = set()
    if only_missing:
        cutoff_6h = now_utc - timedelta(hours=6)
        for row in db.query(ScraperCache).filter(ScraperCache.cache_key.like("recent_v4:%")).all():
            if not row.updated_at:
                continue
            upd = row.updated_at.replace(tzinfo=timezone.utc) if row.updated_at.tzinfo is None else row.updated_at
            if upd < cutoff_6h:
                continue
            try:
                mid_int = int(row.cache_key.removeprefix("recent_v4:"))
                payload = json.loads(row.payload_json)
                if isinstance(payload, dict) and (payload.get("home") or payload.get("away")):
                    existing_recent_mids.add(mid_int)
            except Exception:
                pass

    # Bulk-load MatchContext rows + visible/coupon priority sets so visible 72h matches come first
    ctx_by_mid = {
        c.match_id: c
        for c in db.query(MatchContext).filter(MatchContext.match_id.in_([m.id for m in scheduled])).all()
    }
    horizon_72h = now_utc + timedelta(hours=72)
    visible_pred_mids: set[int] = set()
    any_pred_mids: set[int] = set()
    for p_mid, p_top, p_conf, p_pub_conf in (
        db.query(
            Prediction.match_id,
            Prediction.is_top_pick,
            Prediction.confidence_score,
            Prediction.published_confidence,
        )
        .filter(Prediction.status == "PENDING")
        .all()
    ):
        any_pred_mids.add(p_mid)
        eff_c = p_pub_conf if p_pub_conf is not None else (p_conf or 0.0)
        if p_top and eff_c >= 60.0:
            visible_pred_mids.add(p_mid)

    active_coupon_mids: set[int] = set()
    try:
        from app.models import Coupon
        for c in db.query(Coupon).filter(Coupon.status == "ACTIVE").all():
            for it in (c.items or []):
                if getattr(it, "match_id", None):
                    active_coupon_mids.add(int(it.match_id))
    except Exception:
        pass

    def _match_priority(m: Match) -> tuple[int, int, datetime]:
        dt_utc = m.start_time_utc.replace(tzinfo=timezone.utc) if m.start_time_utc.tzinfo is None else m.start_time_utc
        in_72h = dt_utc <= horizon_72h
        if m.id in active_coupon_mids or (in_72h and m.id in visible_pred_mids):
            tier = 0
        elif in_72h and m.id in any_pred_mids:
            tier = 1
        elif in_72h:
            tier = 2
        else:
            tier = 3
        return (tier, 0 if m.is_major_league else 1, dt_utc)

    scheduled.sort(key=_match_priority)

    missing_tasks: dict[str, tuple[str, datetime, Optional[bool], str, str, Optional[str]]] = {}
    other_tasks: dict[str, tuple[str, datetime, Optional[bool], str, str, Optional[str]]] = {}
    for m in scheduled:
        ctx = ctx_by_mid.get(m.id)
        h_empty = not ctx or not ctx.home_form or ctx.home_form in ("[]", "null")
        a_empty = not ctx or not ctx.away_form or ctx.away_form in ("[]", "null")
        if only_missing and m.id in existing_recent_mids and not h_empty and not a_empty:
            continue
        c_ctx = extract_country_context(m.league, m.country)
        for tname, pref_home, is_empty, logo_url in (
            (m.home_team, True, h_empty, m.home_logo_url),
            (m.away_team, False, a_empty, m.away_logo_url),
        ):
            nk = normalize_team_name(tname)
            if not nk:
                continue
            fm_tid = _extract_fotmob_id_from_logo(logo_url)
            scope = f"fm:{fm_tid}" if fm_tid else c_ctx["cache_scope"]
            iso_key = f"{nk}:{scope}"
            if iso_key in cached_results or iso_key in missing_tasks or iso_key in other_tasks:
                continue
            if is_empty:
                missing_tasks[iso_key] = (tname, m.start_time_utc, pref_home, m.league, m.country, fm_tid)
            elif not only_missing:
                other_tasks[iso_key] = (tname, m.start_time_utc, pref_home, m.league, m.country, fm_tid)

    team_tasks = {**missing_tasks, **other_tasks}

    if not team_tasks and only_missing:
        return {
            "ok": True,
            "total_matches": len(scheduled),
            "total_teams": len({normalize_team_name(t) for m in scheduled for t in (m.home_team, m.away_team)}),
            "updated_teams": 0,
            "coverage_pct": 100.0,
            "source_breakdown": {},
            "elapsed_sec": round(_time.monotonic() - t0, 2),
        }

    def _fetch_team(item: tuple[str, tuple[str, datetime, Optional[bool], str, str, Optional[str]]]):
        iso_key, (tname, dt_utc, pref_home, lg, ctry, fm_tid) = item
        try:
            res = fetch_global_team_recent_matches(
                tname,
                match_start_utc=dt_utc,
                prefer_home=pref_home,
                db=None,  # thread-safe: avoid sharing SQLAlchemy session across threads
                timeout=4.0,
                league=lg,
                country=ctry,
                team_fotmob_id=fm_tid,
            )
            return iso_key, res
        except Exception:
            return iso_key, {"form": [], "recent_matches": [], "goals_avg": None, "conceded_avg": None, "rest_days": None, "source": None}

    results_by_iso: dict[str, dict[str, Any]] = dict(cached_results)
    batch_tasks = list(team_tasks.items())[:160]
    if batch_tasks:
        from concurrent.futures import TimeoutError as FuturesTimeoutError, as_completed

        pool = ThreadPoolExecutor(max_workers=8)
        future_map = {pool.submit(_fetch_team, item): item[0] for item in batch_tasks}
        try:
            for fut in as_completed(future_map, timeout=25.0):
                try:
                    iso_key, res = fut.result()
                except Exception:
                    continue
                results_by_iso[iso_key] = res
                if res.get("recent_matches"):
                    set_cached_json(db, f"global_team_recent:v5:{iso_key}", res)
        except FuturesTimeoutError:
            pass
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    source_breakdown: dict[str, int] = {}
    covered_teams: set[str] = set()
    all_teams: set[str] = set()

    for m in scheduled:
        c_ctx = extract_country_context(m.league, m.country)
        hk_raw = normalize_team_name(m.home_team)
        ak_raw = normalize_team_name(m.away_team)
        h_fm = _extract_fotmob_id_from_logo(m.home_logo_url)
        a_fm = _extract_fotmob_id_from_logo(m.away_logo_url)
        hk = f"{hk_raw}:{'fm:' + h_fm if h_fm else c_ctx['cache_scope']}"
        ak = f"{ak_raw}:{'fm:' + a_fm if a_fm else c_ctx['cache_scope']}"
        all_teams.add(hk)
        all_teams.add(ak)

        h_res = results_by_iso.get(hk)
        a_res = results_by_iso.get(ak)
        if not force_refresh and h_res is None and a_res is None:
            continue

        ctx = ctx_by_mid.get(m.id)
        if not ctx:
            ctx = MatchContext(match_id=m.id)
            db.add(ctx)
            ctx_by_mid[m.id] = ctx

        if h_res and h_res.get("recent_matches"):
            ctx.home_form = json.dumps(h_res["form"])
            if ctx.home_goals_avg is None or force_refresh:
                ctx.home_goals_avg = h_res.get("goals_avg")
                ctx.home_conceded_avg = h_res.get("conceded_avg")
            if ctx.home_rest_days is None and h_res.get("rest_days") is not None:
                ctx.home_rest_days = h_res.get("rest_days")
            covered_teams.add(hk)
            src = h_res.get("source") or "unknown"
            source_breakdown[src] = source_breakdown.get(src, 0) + 1

        if a_res and a_res.get("recent_matches"):
            ctx.away_form = json.dumps(a_res["form"])
            if ctx.away_goals_avg is None or force_refresh:
                ctx.away_goals_avg = a_res.get("goals_avg")
                ctx.away_conceded_avg = a_res.get("conceded_avg")
            if ctx.away_rest_days is None and a_res.get("rest_days") is not None:
                ctx.away_rest_days = a_res.get("rest_days")
            covered_teams.add(ak)
            src = a_res.get("source") or "unknown"
            source_breakdown[src] = source_breakdown.get(src, 0) + 1

        if (h_res and h_res.get("recent_matches")) or (a_res and a_res.get("recent_matches")):
            set_cached_json(
                db,
                f"recent_v4:{m.id}",
                {
                    "home": (h_res or {}).get("recent_matches") or [],
                    "away": (a_res or {}).get("recent_matches") or [],
                    "home_source": (h_res or {}).get("source"),
                    "away_source": (a_res or {}).get("source"),
                },
            )

    db.commit()

    # Backfill missing team logos from FotMob date-based matches (0 Odds API credits)
    logos_updated = _backfill_missing_logos_from_fotmob(db, scheduled)

    total_teams = len(all_teams)
    updated_teams = len(covered_teams)
    coverage_pct = round((updated_teams / total_teams) * 100.0, 1) if total_teams else 0.0
    return {
        "ok": True,
        "total_matches": len(scheduled),
        "total_teams": total_teams,
        "updated_teams": updated_teams,
        "coverage_pct": coverage_pct,
        "logos_updated": logos_updated,
        "source_breakdown": source_breakdown,
        "elapsed_sec": round(_time.monotonic() - t0, 2),
    }


def _backfill_missing_logos_from_fotmob(db: Session, matches: list[Match]) -> int:
    """Populate missing home/away logos from FotMob public match schedule (0 Odds API credits)."""
    from scrapers.external_data import fetch_fotmob_matches_for_dates
    from utils.team_matcher import find_best_external_match

    missing = [m for m in matches if not m.home_logo_url or not m.away_logo_url]
    if not missing:
        return 0

    date_strs: list[str] = []
    seen_dates: set[str] = set()
    for m in missing:
        if not m.start_time_utc:
            continue
        st = m.start_time_utc.replace(tzinfo=timezone.utc) if m.start_time_utc.tzinfo is None else m.start_time_utc
        for delta in (-1, 0, 1):
            ds = (st + timedelta(days=delta)).strftime("%Y%m%d")
            if ds not in seen_dates:
                seen_dates.add(ds)
                date_strs.append(ds)
        if len(date_strs) >= 6:
            break

    if not date_strs:
        return 0

    try:
        fm_events = fetch_fotmob_matches_for_dates(date_strs, timeout=8.0)
    except Exception:
        return 0
    if not fm_events:
        return 0

    updated = 0
    for m in missing:
        fm = find_best_external_match(
            m.home_team,
            m.away_team,
            m.start_time_utc,
            fm_events,
            external_match_id=m.external_match_id if m.external_source == "fotmob" else None,
            league_name=m.league,
        )
        if not fm:
            continue
        changed = False
        if not m.home_logo_url and fm.get("home_logo_url"):
            m.home_logo_url = fm["home_logo_url"]
            changed = True
        if not m.away_logo_url and fm.get("away_logo_url"):
            m.away_logo_url = fm["away_logo_url"]
            changed = True
        if not m.external_match_id and fm.get("fotmob_id"):
            m.external_source = "fotmob"
            m.external_match_id = str(fm["fotmob_id"])
            changed = True
        if changed:
            updated += 1

    if updated:
        db.commit()
    return updated
