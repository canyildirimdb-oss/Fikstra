import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Coupon, Match, Prediction
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


def settle_finished_matches(db: Session, espn_events: Optional[list[dict]] = None) -> int:
    """
    Settle matches whose kickoff was 130+ minutes ago using real 90-minute regular-time scores.
    Postponed/canceled/abandoned/36h+ stale matches and unseparable AET/PEN matches are marked VOID.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=130)
    stale_void_cutoff = now - timedelta(hours=36)
    pending_matches = (
        db.query(Match)
        .filter(Match.start_time_utc <= cutoff, Match.status.notin_(["FINISHED", "VOID"]))
        .all()
    )
    if not pending_matches:
        return 0

    if espn_events is None:
        dates = sorted({m.start_time_utc.strftime("%Y%m%d") for m in pending_matches})
        espn_events = fetch_espn_events_for_dates(dates)

    settled_count = 0

    for match in pending_matches:
        m_start = (
            match.start_time_utc.replace(tzinfo=timezone.utc)
            if match.start_time_utc.tzinfo is None
            else match.start_time_utc
        )
        ext = find_best_external_match(
            match.home_team,
            match.away_team,
            match.start_time_utc,
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
        for p in preds:
            p.status = evaluate_prediction_outcome(p.market_key, p.selection_key, hs, as_)
            p.settled_at = now

    if settled_count:
        db.flush()
        # Update pending coupons whose items are all settled
        pending_coupons = db.query(Coupon).filter(Coupon.status == "PENDING").all()
        for coupon in pending_coupons:
            statuses = [item.prediction.status for item in coupon.items]
            if not statuses:
                continue
            if "LOST" in statuses:
                coupon.status = "LOST"
            elif "PENDING" in statuses:
                continue
            elif all(s == "VOID" for s in statuses):
                coupon.status = "VOID"
                coupon.total_odd = 1.0
            elif all(s in ("WON", "VOID") for s in statuses):
                coupon.status = "WON"
                effective_odds = [
                    item.prediction.iddaa_odd
                    for item in coupon.items
                    if item.prediction.status != "VOID"
                ]
                coupon.total_odd = round(math.prod(effective_odds), 2) if effective_odds else 1.0

    db.commit()
    return settled_count
