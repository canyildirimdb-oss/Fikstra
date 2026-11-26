import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Match, MatchContext, OddsCurrent, OddsHistory, Prediction, ScraperCache, SyncLog
from scrapers.clubelo import load_clubelo_ratings, lookup_clubelo
from scrapers.external_data import (
    fetch_espn_events_for_dates,
    fetch_espn_match_details,
    fetch_espn_team_season_stats,
    fetch_espn_team_shot_xg,
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
            )
            if ext:
                match.external_source = "espn"
                match.external_match_id = ext["espn_id"]
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

                ctx.home_form = json.dumps(details["home_form"]) if details["home_form"] is not None else None
                ctx.away_form = json.dumps(details["away_form"]) if details["away_form"] is not None else None
                ctx.home_goals_avg = details["home_goals_avg"]
                ctx.away_goals_avg = details["away_goals_avg"]
                ctx.home_conceded_avg = details["home_conceded_avg"]
                ctx.away_conceded_avg = details["away_conceded_avg"]
                ctx.home_rest_days = details.get("home_rest_days")
                ctx.away_rest_days = details.get("away_rest_days")
                ctx.injuries_json = json.dumps(injuries_payload, ensure_ascii=False)
                ctx.context_updated_at = now

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

        # Also populate ClubElo for any remaining European scheduled matches without ESPN match ID
        for m in db.query(Match).filter(Match.status == "SCHEDULED").all():
            h_elo = lookup_clubelo(m.home_team, clubelo_ratings, league=m.league, country=m.country)
            a_elo = lookup_clubelo(m.away_team, clubelo_ratings, league=m.league, country=m.country)
            ctx = db.query(MatchContext).filter(MatchContext.match_id == m.id).first()
            if h_elo or a_elo:
                if not ctx:
                    ctx = MatchContext(match_id=m.id)
                    db.add(ctx)
                ctx.home_elo = h_elo
                ctx.away_elo = a_elo
            elif ctx and (ctx.home_elo is not None or ctx.away_elo is not None):
                ctx.home_elo = None
                ctx.away_elo = None

        db.commit()
        odds_api_res = sync_the_odds_api(db)
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

        # Neon 500MB DB quota protection: prune >30d odds_history & >14d expired scraper_cache
        prune_odds_cutoff = now - timedelta(days=30)
        db.query(OddsHistory).filter(OddsHistory.recorded_at < prune_odds_cutoff).delete(synchronize_session=False)
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
