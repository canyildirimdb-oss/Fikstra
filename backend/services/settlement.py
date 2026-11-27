import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session, joinedload

from app.models import Coupon, CouponItem, Match, Prediction
from scrapers.external_data import (
    fetch_espn_event_by_id,
    fetch_espn_events_for_dates,
    fetch_fotmob_matches_for_dates,
)
from utils.team_matcher import find_best_external_match
from utils.time_utils import to_tr_datetime

_TR_UPPER_MAP = str.maketrans({
    "ç": "C", "Ç": "C",
    "ğ": "G", "Ğ": "G",
    "ı": "I", "İ": "I",
    "ö": "O", "Ö": "O",
    "ş": "S", "Ş": "S",
    "ü": "U", "Ü": "U",
})


def _tr_upper(val: Optional[str]) -> str:
    return (val or "").strip().translate(_TR_UPPER_MAP).upper()


def evaluate_prediction_outcome(
    market_key: str,
    selection_key: str,
    home_score: int,
    away_score: int,
) -> str:
    m_key = _tr_upper(market_key)
    s_key = _tr_upper(selection_key)
    total_goals = home_score + away_score

    if m_key == "MS":
        if s_key == "1":
            return "WON" if home_score > away_score else "LOST"
        if s_key in ("X", "0"):
            return "WON" if home_score == away_score else "LOST"
        if s_key == "2":
            return "WON" if away_score > home_score else "LOST"

    elif m_key == "CS":
        if s_key in ("1X", "10", "1 VE 0"):
            return "WON" if home_score >= away_score else "LOST"
        if s_key in ("12", "1 VE 2"):
            return "WON" if home_score != away_score else "LOST"
        if s_key in ("X2", "02", "0 VE 2"):
            return "WON" if away_score >= home_score else "LOST"

    elif m_key.startswith("AU_"):
        try:
            line = float(m_key.split("_")[1]) / 10.0  # 15 -> 1.5, 25 -> 2.5, 35 -> 3.5
        except (IndexError, ValueError):
            return "VOID"
        if s_key == "ALT":
            return "WON" if total_goals < line else "LOST"
        if s_key == "UST":
            return "WON" if total_goals > line else "LOST"

    elif m_key == "KG":
        both_scored = home_score > 0 and away_score > 0
        if s_key == "VAR":
            return "WON" if both_scored else "LOST"
        if s_key == "YOK":
            return "WON" if not both_scored else "LOST"

    return "VOID"


def _mark_match_void(
    db: Session,
    match: Match,
    now: datetime,
    reason: str = "score_unavailable_48h",
) -> int:
    match.status = "VOID"
    match.void_reason = (reason or "score_unavailable_48h")[:128]
    preds = (
        db.query(Prediction)
        .filter(Prediction.match_id == match.id, Prediction.status == "PENDING")
        .all()
    )
    for p in preds:
        p.status = "VOID"
        p.settled_at = now
    return 1


def cleanup_multi_market_settled_predictions(db: Session) -> int:
    """
    Ensure every settled match has at most ONE official #1 prediction (is_top_pick=True)
    counted in performance/archive. Secondary markets on FINISHED matches are NEVER forced
    to VOID — if a valid secondary market was previously forced to VOID on a FINISHED match
    with scores, it is re-evaluated to WON/LOST.
    """
    cutoff_3d = datetime.now(timezone.utc) - timedelta(days=3)
    settled_rows = (
        db.query(Prediction)
        .options(joinedload(Prediction.match))
        .filter(
            Prediction.status.in_(["WON", "LOST", "VOID"]),
            Prediction.settled_at >= cutoff_3d,
        )
        .all()
    )
    if not settled_rows:
        return 0

    by_match: dict[int, list[Prediction]] = {}
    for p in settled_rows:
        by_match.setdefault(p.match_id, []).append(p)

    now = datetime.now(timezone.utc)
    cleaned = 0
    for mid, preds in by_match.items():
        match = preds[0].match
        # Re-evaluate any wrongly-VOIDed predictions on FINISHED matches with real 90m scores
        if match and match.status == "FINISHED" and match.home_score is not None and match.away_score is not None:
            for p in preds:
                if p.status == "VOID":
                    outcome = evaluate_prediction_outcome(
                        p.market_key, p.selection_key, match.home_score, match.away_score
                    )
                    if outcome in ("WON", "LOST"):
                        p.status = outcome
                        p.settled_at = p.settled_at or now
                        cleaned += 1

        won_lost = [p for p in preds if p.status in ("WON", "LOST")]
        if not won_lost:
            continue

        flagged = [p for p in won_lost if p.is_top_pick]
        if len(flagged) == 1:
            continue

        if flagged:
            official = max(
                flagged,
                key=lambda p: (
                    p.confidence_score,
                    p.value_edge_pct if p.value_edge_pct is not None else -99.0,
                    p.iddaa_odd,
                ),
            )
        else:
            published_only = [p for p in won_lost if p.published_at is not None]
            official = (
                max(
                    published_only,
                    key=lambda p: (
                        p.confidence_score,
                        p.value_edge_pct if p.value_edge_pct is not None else -99.0,
                        p.iddaa_odd,
                    ),
                )
                if published_only
                else None
            )

        for p in won_lost:
            if official is not None and p.id == official.id:
                if not p.is_top_pick:
                    p.is_top_pick = True
                    cleaned += 1
            else:
                if p.is_top_pick:
                    p.is_top_pick = False
                    cleaned += 1

    if cleaned:
        db.commit()
    return cleaned


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _date_window_for_matches(matches: list[Match]) -> list[str]:
    """Build ±1 day UTC and Europe/Istanbul YYYYMMDD date strings to prevent midnight shift misses."""
    date_set: set[str] = set()
    for m in matches:
        ms = _as_utc(m.start_time_utc)
        tr_ms = to_tr_datetime(ms)
        for base_dt in (ms, tr_ms):
            for offset_days in (-1, 0, 1):
                date_set.add((base_dt + timedelta(days=offset_days)).strftime("%Y%m%d"))
    return sorted(date_set)


def settle_coupons(db: Session) -> int:
    """
    Idempotently settle ALL PENDING coupons in the database (regardless of coupon_date)
    whose legs have finished. Returns number of newly settled coupons.
    """
    pending_coupons = (
        db.query(Coupon)
        .options(joinedload(Coupon.items).joinedload(CouponItem.prediction))
        .filter(Coupon.status == "PENDING")
        .all()
    )
    settled_coupons = 0
    for coupon in pending_coupons:
        statuses = [item.prediction.status for item in coupon.items if item.prediction]
        if not statuses:
            coupon.status = "VOID"
            coupon.total_odd = 1.0
            settled_coupons += 1
            continue
        if "LOST" in statuses:
            coupon.status = "LOST"
            settled_coupons += 1
        elif "PENDING" in statuses:
            continue
        elif all(s == "VOID" for s in statuses):
            coupon.status = "VOID"
            coupon.total_odd = 1.0
            settled_coupons += 1
        elif all(s in ("WON", "VOID") for s in statuses):
            coupon.status = "WON"
            effective_odds = [
                (item.odd_at_pick if item.odd_at_pick is not None else item.prediction.iddaa_odd)
                for item in coupon.items
                if item.prediction and item.prediction.status != "VOID"
            ]
            coupon.total_odd = round(math.prod(effective_odds), 2) if effective_odds else 1.0
            settled_coupons += 1
    return settled_coupons


def _settle_match_predictions(
    db: Session,
    match: Match,
    hs: int,
    as_: int,
    now: datetime,
) -> None:
    """Evaluate ALL PENDING (and any wrongly-VOIDed) predictions on a finished match."""
    match.status = "FINISHED"
    match.home_score = hs
    match.away_score = as_
    match.void_reason = None

    preds = (
        db.query(Prediction)
        .filter(Prediction.match_id == match.id, Prediction.status.in_(["PENDING", "VOID"]))
        .all()
    )
    if not preds:
        return

    all_match_preds = (
        db.query(Prediction)
        .filter(Prediction.match_id == match.id)
        .all()
    )
    flagged = [p for p in all_match_preds if p.is_top_pick]
    if flagged:
        official = flagged[0]
    else:
        published_only = [p for p in all_match_preds if p.published_at is not None]
        official = (
            max(
                published_only,
                key=lambda p: (
                    p.confidence_score,
                    p.value_edge_pct if p.value_edge_pct is not None else -99.0,
                    p.iddaa_odd,
                ),
            )
            if published_only
            else None
        )

    for p in preds:
        p.status = evaluate_prediction_outcome(p.market_key, p.selection_key, hs, as_)
        p.settled_at = now
        if official is not None:
            p.is_top_pick = p.id == official.id


def _resolve_match_from_sources(
    match: Match,
    espn_events: list[dict[str, Any]],
    fotmob_events_holder: dict[str, Optional[list[dict[str, Any]]]],
    date_list: list[str],
) -> Optional[dict[str, Any]]:
    """
    ID-first + lightweight multi-source fallback chain:
    1) Direct external_match_id in espn_events
    2) Shared matcher on espn_events (persist external_match_id if found)
    3) Direct ESPN summary by external_match_id (when batch didn't complete or missed league)
    4) Shared matcher on FotMob date-based matches JSON
    """
    m_start = _as_utc(match.start_time_utc)

    # 1 & 2: ESPN batch (ID-first + paired matcher)
    ext = find_best_external_match(
        match.home_team,
        match.away_team,
        m_start,
        espn_events,
        external_match_id=match.external_match_id if match.external_source == "espn" else None,
        league_name=match.league,
    )
    if ext and not match.external_match_id and ext.get("espn_id"):
        match.external_source = "espn"
        match.external_match_id = str(ext["espn_id"])

    if ext and (ext.get("completed") or ext.get("is_void_status")):
        if not ext.get("is_aet_or_pen") or ext.get("reg_home_score") is not None:
            return ext

    # 3: Direct ESPN summary by ID if available and batch didn't finish it
    if match.external_source == "espn" and match.external_match_id:
        direct = fetch_espn_event_by_id(match.external_match_id)
        if direct and (direct.get("completed") or direct.get("is_void_status")):
            if not direct.get("is_aet_or_pen") or direct.get("reg_home_score") is not None:
                return direct
        if direct and not ext:
            ext = direct

    # 4: FotMob date-based fallback (lazy fetch once per batch)
    if fotmob_events_holder["data"] is None:
        fotmob_events_holder["data"] = fetch_fotmob_matches_for_dates(date_list)
    fm_events = fotmob_events_holder["data"] or []
    fm = find_best_external_match(
        match.home_team,
        match.away_team,
        m_start,
        fm_events,
        external_match_id=match.external_match_id if match.external_source == "fotmob" else None,
        league_name=match.league,
    )
    if fm:
        if not match.external_match_id and fm.get("fotmob_id"):
            match.external_source = "fotmob"
            match.external_match_id = str(fm["fotmob_id"])
        if fm.get("completed") or fm.get("is_void_status"):
            return fm
        if not ext:
            ext = fm

    return ext


def refresh_live_coupon_matches(
    db: Session,
    espn_events: Optional[list[dict[str, Any]]] = None,
) -> int:
    """
    Lightweight live & finished score refresh for matches on active PENDING coupons
    that have kicked off (so 'Canli' / 'SCHEDULED' never gets stuck).
    """
    now = datetime.now(timezone.utc)
    pending_coupons = (
        db.query(Coupon)
        .options(joinedload(Coupon.items).joinedload(CouponItem.prediction).joinedload(Prediction.match))
        .filter(Coupon.status == "PENDING")
        .all()
    )
    if not pending_coupons:
        return 0

    target_matches: dict[int, Match] = {}
    for c in pending_coupons:
        for item in c.items:
            if not item.prediction or not item.prediction.match:
                continue
            m = item.prediction.match
            m_start = _as_utc(m.start_time_utc)
            if m.status not in ("FINISHED", "VOID") and m_start <= now:
                target_matches[m.id] = m

    if not target_matches:
        return 0

    matches_list = list(target_matches.values())
    date_list = _date_window_for_matches(matches_list)
    if espn_events is None:
        espn_events = fetch_espn_events_for_dates(date_list)
    fotmob_holder: dict[str, Optional[list[dict[str, Any]]]] = {"data": None}

    updated = 0
    for match in matches_list:
        m_start = _as_utc(match.start_time_utc)
        ext = _resolve_match_from_sources(match, espn_events, fotmob_holder, date_list)
        if not ext:
            continue

        if ext.get("is_void_status"):
            updated += _mark_match_void(
                db, match, now, reason=f"source_status:{ext.get('status_name') or 'VOID'}"
            )
            continue

        if ext.get("completed"):
            if ext.get("is_aet_or_pen"):
                hs = ext.get("reg_home_score")
                as_ = ext.get("reg_away_score")
            else:
                hs = ext.get("home_score")
                as_ = ext.get("away_score")
            if hs is not None and as_ is not None:
                _settle_match_predictions(db, match, hs, as_, now)
                updated += 1
                continue

        # In-progress live score update
        if ext.get("state") == "in" or (m_start <= now and ext.get("home_score") is not None):
            hs = ext.get("home_score")
            as_ = ext.get("away_score")
            if match.status != "LIVE" or match.home_score != hs or match.away_score != as_:
                match.status = "LIVE"
                if hs is not None:
                    match.home_score = hs
                if as_ is not None:
                    match.away_score = as_
                updated += 1

    if updated:
        db.flush()
        settle_coupons(db)
        db.commit()
    return updated


def settle_finished_matches(
    db: Session,
    espn_events: Optional[list[dict[str, Any]]] = None,
    fotmob_events: Optional[list[dict[str, Any]]] = None,
) -> int:
    """
    Settle matches whose kickoff was >= 95 minutes ago using real 90-minute regular-time scores.
    - Evaluates ALL active markets on the match (never forcing secondary markets to VOID).
    - Bans premature VOID: waits at least 48 hours before voiding an unfound score unless
      a trusted source explicitly reports POSTPONED/CANCELLED/ABANDONED/AWARDED/SUSPENDED.
    """
    cleanup_multi_market_settled_predictions(db)
    now = datetime.now(timezone.utc)
    window_end = now - timedelta(minutes=95)
    window_start = now - timedelta(hours=48)

    pending_matches = (
        db.query(Match)
        .filter(Match.start_time_utc <= window_end, Match.status.notin_(["FINISHED", "VOID"]))
        .all()
    )

    settled_count = 0
    if pending_matches:
        date_list = _date_window_for_matches(pending_matches)
        if espn_events is None:
            espn_events = fetch_espn_events_for_dates(date_list)
        fotmob_holder: dict[str, Optional[list[dict[str, Any]]]] = {"data": fotmob_events}

        for match in pending_matches:
            m_start = _as_utc(match.start_time_utc)
            ext = _resolve_match_from_sources(match, espn_events, fotmob_holder, date_list)

            if ext and ext.get("is_void_status"):
                settled_count += _mark_match_void(
                    db, match, now, reason=f"source_status:{ext.get('status_name') or 'VOID'}"
                )
                continue

            if not ext or not ext.get("completed"):
                if m_start <= window_start:
                    settled_count += _mark_match_void(db, match, now, reason="score_unavailable_48h")
                continue

            if ext.get("is_aet_or_pen"):
                hs = ext.get("reg_home_score")
                as_ = ext.get("reg_away_score")
                if hs is None or as_ is None:
                    if m_start <= window_start:
                        settled_count += _mark_match_void(
                            db, match, now, reason="aet_regular_time_unavailable_48h"
                        )
                    continue
            else:
                hs = ext.get("home_score")
                as_ = ext.get("away_score")
                if hs is None or as_ is None:
                    if m_start <= window_start:
                        settled_count += _mark_match_void(db, match, now, reason="score_unavailable_48h")
                    continue

            _settle_match_predictions(db, match, hs, as_, now)
            settled_count += 1

    # Also refresh any live/in-progress coupon matches (<95m since kickoff)
    settled_count += refresh_live_coupon_matches(db, espn_events=espn_events)

    if settled_count:
        db.flush()

    settle_coupons(db)
    db.commit()
    return settled_count
