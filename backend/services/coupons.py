import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Coupon, CouponItem, Match, Prediction
from scrapers.iddaa import is_youth_or_women_match
from services.analyzer import MIN_ODD_RULE
from utils.time_utils import is_tr_today_or_next_24h, tr_today_str


def _is_eligible_coupon_match(match: Match, kickoff_lock: Optional[datetime] = None) -> bool:
    if is_youth_or_women_match(match.league, match.home_team, match.away_team):
        return False
    if kickoff_lock is not None:
        m_start = (
            match.start_time_utc.replace(tzinfo=timezone.utc)
            if match.start_time_utc.tzinfo is None
            else match.start_time_utc
        )
        if m_start <= kickoff_lock:
            return False
    return True


def _is_contradicting_pick(
    p: Prediction,
    top_pick: Optional[Prediction],
    match_pick_conf: dict[tuple[int, str, str], float],
) -> bool:
    """
    Opposite-Pick & #1-Pick Conflict Lock:
    (a) Reject a selection whose opposite side in the same market has higher confidence.
    (b) Reject any selection that directly contradicts the match's #1 highest-confidence pick.
    """
    mid = p.match_id
    mk = p.market_key
    sk = p.selection_key

    # (a) Same-market opposite confidence check
    opp_map = {
        ("AU_15", "ALT"): ("AU_15", "UST"),
        ("AU_15", "UST"): ("AU_15", "ALT"),
        ("AU_25", "ALT"): ("AU_25", "UST"),
        ("AU_25", "UST"): ("AU_25", "ALT"),
        ("AU_35", "ALT"): ("AU_35", "UST"),
        ("AU_35", "UST"): ("AU_35", "ALT"),
        ("KG", "VAR"): ("KG", "YOK"),
        ("KG", "YOK"): ("KG", "VAR"),
        ("MS", "1"): ("MS", "2"),
        ("MS", "2"): ("MS", "1"),
    }
    opp = opp_map.get((mk, sk))
    if opp:
        opp_conf = match_pick_conf.get((mid, opp[0], opp[1]))
        if opp_conf is not None and opp_conf > p.confidence_score:
            return True

    # (b) Direct contradiction with match's #1 top pick
    if top_pick is None:
        return False
    t_mk = top_pick.market_key
    t_sk = top_pick.selection_key

    if t_mk.startswith("AU_"):
        t_line = float(t_mk.split("_")[1]) / 10.0
        if t_sk == "UST":
            if mk.startswith("AU_") and sk == "ALT":
                return True
            if mk == "KG" and sk == "YOK":
                return True
        elif t_sk == "ALT":
            if mk.startswith("AU_") and sk == "UST":
                p_line = float(mk.split("_")[1]) / 10.0
                if p_line >= t_line:
                    return True
            if t_line <= 1.5 and mk == "KG" and sk == "VAR":
                return True
    elif t_mk == "KG":
        if t_sk == "VAR" and ((mk == "KG" and sk == "YOK") or (mk == "AU_15" and sk == "ALT")):
            return True
        if t_sk == "YOK" and ((mk == "KG" and sk == "VAR") or (mk in ("AU_25", "AU_35") and sk == "UST")):
            return True
    elif t_mk == "MS":
        if t_sk == "1" and ((mk == "MS" and sk == "2") or (mk == "CS" and sk == "X2")):
            return True
        if t_sk == "2" and ((mk == "MS" and sk == "1") or (mk == "CS" and sk == "1X")):
            return True
    elif t_mk == "CS":
        if t_sk == "1X" and mk == "MS" and sk == "2":
            return True
        if t_sk == "X2" and mk == "MS" and sk == "1":
            return True

    return False


def _pick_distinct_match_preds(
    candidates: list[Prediction],
    target_count: int,
    min_odd: float,
    max_odd: Optional[float] = None,
    prefer_value_drop: bool = False,
    global_used_matches: Optional[set[int]] = None,
    top_pick_ids: Optional[set[int]] = None,
) -> list[Prediction]:
    filtered = [
        p
        for p in candidates
        if p.iddaa_odd >= max(MIN_ODD_RULE, min_odd)
        and (max_odd is None or p.iddaa_odd <= max_odd)
    ]

    def _sort_key(p: Prediction):
        is_top = 1 if (top_pick_ids and p.id in top_pick_ids) else 0
        is_major = 1 if p.match.is_major_league else 0
        if prefer_value_drop:
            score = (
                (p.value_edge_pct or -10.0) * 1.5
                + (p.odds_drop_pct or 0.0) * 2.0
                + p.confidence_score * 0.6
            )
        else:
            score = p.confidence_score
        return (is_top, is_major, score)

    filtered.sort(key=_sort_key, reverse=True)

    chosen: list[Prediction] = []
    used_in_coupon: set[int] = set()
    market_counts: dict[str, int] = {}

    # Pass 1: strictly prioritize #1 top picks of unused matches
    # Pass 2: only if not enough #1 picks exist in this band, allow compatible 2nd picks of unused matches
    for require_top in (True, False):
        for p in filtered:
            if require_top and top_pick_ids and p.id not in top_pick_ids:
                continue
            if p.match_id in used_in_coupon:
                continue
            if global_used_matches and p.match_id in global_used_matches:
                continue
            if market_counts.get(p.market_key, 0) >= 2:
                continue
            chosen.append(p)
            used_in_coupon.add(p.match_id)
            market_counts[p.market_key] = market_counts.get(p.market_key, 0) + 1
            if len(chosen) >= target_count:
                return chosen

    return chosen


def _should_freeze_coupon(coupon: Coupon, now_utc: datetime) -> bool:
    if coupon.is_locked or coupon.status != "PENDING":
        return True
    freeze_cutoff = now_utc + timedelta(minutes=15)
    for item in coupon.items:
        pred = item.prediction
        if not pred or not pred.match:
            continue
        m = pred.match
        if m.status != "SCHEDULED":
            return True
        st = m.start_time_utc
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        if st <= freeze_cutoff:
            return True
    return False


def generate_daily_coupons(db: Session) -> list[Coupon]:
    """
    Generate daily BANKO, DENGELI, and SURPRIZ coupons using only next-24h / today TR matches,
    excluding youth/reserve/women leagues, keeping SURPRIZ odds in 1.90-3.60,
    enforcing 1-coupon-per-match, #1-pick priority, opposite-pick lock, and 15m coupon freeze lock.
    """
    now_utc = datetime.now(timezone.utc)
    today_str = tr_today_str()
    kickoff_lock = now_utc + timedelta(minutes=5)

    existing_today = (
        db.query(Coupon)
        .filter(Coupon.coupon_date == today_str)
        .all()
    )
    existing_by_tier: dict[str, Coupon] = {c.tier: c for c in existing_today}
    frozen_tiers: set[str] = set()
    global_used_matches: set[int] = set()

    for tier, c in existing_by_tier.items():
        if _should_freeze_coupon(c, now_utc):
            c.is_locked = True
            frozen_tiers.add(tier)
            for item in c.items:
                if item.prediction:
                    global_used_matches.add(item.prediction.match_id)

    all_pending = (
        db.query(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .filter(
            Match.status == "SCHEDULED",
            Match.start_time_utc > kickoff_lock,
            Prediction.status == "PENDING",
            Prediction.iddaa_odd >= MIN_ODD_RULE,
        )
        .all()
    )
    # Build #1 pick per match & market confidence map for opposite-pick conflict lock
    all_match_preds = (
        db.query(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .filter(
            Match.status == "SCHEDULED",
            Match.start_time_utc > kickoff_lock,
            Prediction.status == "PENDING",
        )
        .all()
    )
    top_pick_by_match: dict[int, Prediction] = {}
    match_pick_conf: dict[tuple[int, str, str], float] = {}
    for mp in all_match_preds:
        match_pick_conf[(mp.match_id, mp.market_key, mp.selection_key)] = mp.confidence_score
        prev = top_pick_by_match.get(mp.match_id)
        if prev is None or mp.confidence_score > prev.confidence_score:
            top_pick_by_match[mp.match_id] = mp

    top_pick_ids: set[int] = {p.id for p in top_pick_by_match.values()}

    eligible = [
        p
        for p in all_pending
        if _is_eligible_coupon_match(p.match, kickoff_lock=kickoff_lock)
        and not _is_contradicting_pick(p, top_pick_by_match.get(p.match_id), match_pick_conf)
    ]
    today_preds = [p for p in eligible if is_tr_today_or_next_24h(p.match.start_time_utc)]
    if not today_preds:
        today_preds = eligible

    specs = [
        ("BANKO", 3, 1.30, 1.55, False, True, 68.0),
        ("DENGELI", 3, 1.45, 2.00, False, True, 62.0),
        ("SURPRIZ", 2, 1.90, 3.60, True, False, 58.0),
    ]

    created_or_updated: list[Coupon] = []

    for tier, count, min_o, max_o, prefer_vd, block_traps, min_conf in specs:
        if tier in frozen_tiers:
            created_or_updated.append(existing_by_tier[tier])
            continue

        tier_pool = [
            p
            for p in today_preds
            if p.confidence_score >= min_conf
            and not (block_traps and p.trap_warning)
        ]
        picked = _pick_distinct_match_preds(
            tier_pool,
            target_count=count,
            min_odd=min_o,
            max_odd=max_o,
            prefer_value_drop=prefer_vd,
            global_used_matches=global_used_matches,
            top_pick_ids=top_pick_ids,
        )
        if len(picked) < 2 and tier != "SURPRIZ" and max_o is not None:
            picked = _pick_distinct_match_preds(
                tier_pool,
                target_count=count,
                min_odd=min_o,
                max_odd=max_o + 0.30,
                prefer_value_drop=prefer_vd,
                global_used_matches=global_used_matches,
                top_pick_ids=top_pick_ids,
            )

        if len(picked) < 2:
            # Quality threshold not met: do not force junk/single-leg coupons; remove unlocked stale coupon if any
            if tier in existing_by_tier:
                db.delete(existing_by_tier[tier])
            continue

        for p in picked:
            global_used_matches.add(p.match_id)

        total_odd = round(math.prod(p.iddaa_odd for p in picked), 2)
        avg_conf = round(sum(p.confidence_score for p in picked) / len(picked), 1)

        existing = existing_by_tier.get(tier)
        if not existing:
            coupon = Coupon(
                coupon_date=today_str,
                tier=tier,
                total_odd=total_odd,
                avg_confidence=avg_conf,
                status="PENDING",
                is_locked=False,
            )
            db.add(coupon)
            db.flush()
        else:
            coupon = existing
            coupon.total_odd = total_odd
            coupon.avg_confidence = avg_conf
            db.query(CouponItem).filter(CouponItem.coupon_id == coupon.id).delete()

        for p in picked:
            db.add(CouponItem(coupon_id=coupon.id, prediction_id=p.id))

        created_or_updated.append(coupon)

    db.commit()
    return created_or_updated
