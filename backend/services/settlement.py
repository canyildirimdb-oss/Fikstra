import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Coupon, CouponItem, Match, Prediction
from scrapers.external_data import fetch_espn_events_for_dates
from utils.team_matcher import find_best_external_match


def evaluate_prediction_outcome(
    market_key: str,
    selection_key: str,
    home_score: int,
    away_score: int,
) -> str:
    total_goals = home_score + away_score

    if market_key == "MS":
        if selection_key == "1":
            return "WON" if home_score > away_score else "LOST"
        if selection_key == "X":
            return "WON" if home_score == away_score else "LOST"
        if selection_key == "2":
            return "WON" if away_score > home_score else "LOST"

    elif market_key == "CS":
        if selection_key == "1X":
            return "WON" if home_score >= away_score else "LOST"
        if selection_key == "12":
            return "WON" if home_score != away_score else "LOST"
        if selection_key == "X2":
            return "WON" if away_score >= home_score else "LOST"

    elif market_key.startswith("AU_"):
        line = float(market_key.split("_")[1]) / 10.0  # 15 -> 1.5, 25 -> 2.5, 35 -> 3.5
        if selection_key == "ALT":
            return "WON" if total_goals < line else "LOST"
        if selection_key == "UST":
            return "WON" if total_goals > line else "LOST"

    elif market_key == "KG":
        both_scored = home_score > 0 and away_score > 0
        if selection_key == "VAR":
            return "WON" if both_scored else "LOST"
        if selection_key == "YOK":
            return "WON" if not both_scored else "LOST"

    return "VOID"


def _mark_match_void(db: Session, match: Match, now: datetime) -> int:
    match.status = "VOID"
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
    counted in performance/archive. Legacy multi-market WON/LOST rows for the same match
    are demoted (is_top_pick=False, and marked VOID if not referenced by a CouponItem).
    """
    cutoff_3d = datetime.now(timezone.utc) - timedelta(days=3)
    settled_rows = (
        db.query(Prediction)
        .filter(
            Prediction.status.in_(["WON", "LOST"]),
            Prediction.settled_at >= cutoff_3d,
        )
        .all()
    )
    if not settled_rows:
        return 0

    by_match: dict[int, list[Prediction]] = {}
    for p in settled_rows:
        by_match.setdefault(p.match_id, []).append(p)

    coupon_pred_ids = {
        row[0] for row in db.query(CouponItem.prediction_id).distinct().all()
    }

    cleaned = 0
    for mid, preds in by_match.items():
        flagged = [p for p in preds if p.is_top_pick]
        if len(flagged) == 1 and len(preds) == 1:
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
            published_only = [p for p in preds if p.published_at is not None]
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
            if official is not None and p.id == official.id:
                if not p.is_top_pick:
                    p.is_top_pick = True
                    cleaned += 1
            else:
                if p.is_top_pick:
                    p.is_top_pick = False
                    cleaned += 1
                if p.id not in coupon_pred_ids and p.status != "VOID":
                    p.status = "VOID"
                    cleaned += 1

    if cleaned:
        db.commit()
    return cleaned


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def settle_coupons(db: Session) -> int:
    """
    Settle ALL PENDING coupons in the database (regardless of coupon_date)
    whose legs have finished. Returns number of newly settled coupons.
    """
    pending_coupons = db.query(Coupon).filter(Coupon.status == "PENDING").all()
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


def settle_finished_matches(db: Session, espn_events: Optional[list[dict]] = None) -> int:
    """
    Settle matches whose kickoff was between 95 minutes and 48 hours ago (UTC) using real
    90-minute regular-time scores, and void matches older than 48h/36h that never completed.
    Only the single official #1 pick (is_top_pick=True) per match is settled as WON/LOST for
    archive & performance stats (coupon-referenced secondary picks settle only for their coupon).
    """
    cleanup_multi_market_settled_predictions(db)
    now = datetime.now(timezone.utc)
    window_end = now - timedelta(minutes=95)
    window_start = now - timedelta(hours=48)
    stale_void_cutoff = now - timedelta(hours=36)

    pending_matches = (
        db.query(Match)
        .filter(Match.start_time_utc <= window_end, Match.status.notin_(["FINISHED", "VOID"]))
        .all()
    )

    settled_count = 0
    recent_matches: list[Match] = []
    for m in pending_matches:
        m_start = _as_utc(m.start_time_utc)
        if m_start < window_start:
            settled_count += _mark_match_void(db, m, now)
        elif window_start <= m_start <= window_end:
            recent_matches.append(m)

    if recent_matches:
        if espn_events is None:
            date_set: set[str] = set()
            for m in recent_matches:
                ms = _as_utc(m.start_time_utc)
                date_set.add(ms.strftime("%Y%m%d"))
                date_set.add((ms - timedelta(hours=6)).strftime("%Y%m%d"))
            espn_events = fetch_espn_events_for_dates(sorted(date_set))

        for match in recent_matches:
            m_start = _as_utc(match.start_time_utc)
            ext = find_best_external_match(
                match.home_team,
                match.away_team,
                m_start,
                espn_events,
            )

            if ext and ext.get("is_void_status"):
                settled_count += _mark_match_void(db, match, now)
                continue

            if not ext or not ext.get("completed"):
                if m_start <= stale_void_cutoff:
                    settled_count += _mark_match_void(db, match, now)
                continue

            if ext.get("is_aet_or_pen"):
                hs = ext.get("reg_home_score")
                as_ = ext.get("reg_away_score")
                if hs is None or as_ is None:
                    settled_count += _mark_match_void(db, match, now)
                    continue
            else:
                hs = ext.get("home_score")
                as_ = ext.get("away_score")
                if hs is None or as_ is None:
                    if m_start <= stale_void_cutoff:
                        settled_count += _mark_match_void(db, match, now)
                    continue

            match.status = "FINISHED"
            match.home_score = hs
            match.away_score = as_
            settled_count += 1

            preds = (
                db.query(Prediction)
                .filter(Prediction.match_id == match.id, Prediction.status == "PENDING")
                .all()
            )
            if preds:
                flagged = [p for p in preds if p.is_top_pick]
                if flagged:
                    official = flagged[0]
                else:
                    published_only = [p for p in preds if p.published_at is not None]
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
                pred_ids = [p.id for p in preds]
                coupon_pred_ids = {
                    row[0]
                    for row in db.query(CouponItem.prediction_id)
                    .filter(CouponItem.prediction_id.in_(pred_ids))
                    .all()
                }
                for p in preds:
                    if official is not None and p.id == official.id:
                        p.is_top_pick = True
                        p.status = evaluate_prediction_outcome(p.market_key, p.selection_key, hs, as_)
                        p.settled_at = now
                    elif p.id in coupon_pred_ids:
                        p.is_top_pick = False
                        p.status = evaluate_prediction_outcome(p.market_key, p.selection_key, hs, as_)
                        p.settled_at = now
                    else:
                        p.is_top_pick = False
                        p.status = "VOID"
                        p.settled_at = now

    if settled_count:
        db.flush()

    settle_coupons(db)
    db.commit()
    return settled_count
