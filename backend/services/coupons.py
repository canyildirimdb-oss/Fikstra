import json
import math
import re
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Coupon, CouponItem, Match, Prediction
from scrapers.iddaa import is_youth_or_women_match
from services.analyzer import MIN_ODD_RULE
from utils.time_utils import to_tr_datetime, tr_today_str

# --- Kupon V2 Constants ---
KICKOFF_BUFFER_MINUTES = 60
MAX_HORIZON_HOURS = 48
TIER1_WINDOW_HOURS = 24
COUPON_SPAN_MAX_HOURS = 24

# Banko Quality Armor
BANKO_MIN_CONF = 65.0
BANKO_MIN_PROB = 0.58
BANKO_MIN_ODD = 1.20
BANKO_MAX_ODD = 1.58
BANKO_CORE_MIN_COMBINED_PROB = 0.35
BANKO_3LEG_MIN_PROB = 0.63
BANKO_3LEG_MIN_CONF = 66.0
BANKO_3LEG_MAX_ODD = 1.48
BANKO_3LEG_MIN_COMBINED_PROB = 0.30
BANKO_TOTAL_ODD_MIN = 1.75
BANKO_TOTAL_ODD_MAX = 2.65
BANKO_DEAD_ODD_EDGE_FLOOR = -10.5

# Dengeli
DENGELI_MIN_CONF = 63.0
DENGELI_MIN_PROB = 0.50
DENGELI_MIN_ODD = 1.42
DENGELI_MAX_ODD = 2.10
DENGELI_MIN_COMBINED_PROB = 0.20
DENGELI_TOTAL_ODD_MIN = 2.80
DENGELI_TOTAL_ODD_MAX = 4.80

# Surpriz
SURPRIZ_MIN_CONF = 60.0
SURPRIZ_MIN_ODD = 1.85
SURPRIZ_MAX_ODD = 3.50
SURPRIZ_TOTAL_ODD_MIN = 4.80
SURPRIZ_TOTAL_ODD_MAX = 10.00

_RESERVE_YOUTH_WOMEN_RE = re.compile(
    r"(\bjong\b|\bu1[789]\b|\bu2[013]\b|\breserves?\b|\bb\s+tak[ıi]m[ıi]\b|\b(ii|iii)\b|\(w\)|\(k\)|\bwomen\b|\blad(y|ies)\b|\bfem[ei]nin[oa]?\b|\bkad[ıi]n\b|\bbayan\b|\byouth\b|\bnext\s+gen\b)",
    re.IGNORECASE,
)
_TRAILING_B_TEAM_RE = re.compile(r"\s+b$", re.IGNORECASE)
_BAD_DATA_STATUSES = {"pending", "no_global", "insufficient", "missing"}


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_reserve_or_youth_or_women(league_name: str, home_team: str = "", away_team: str = "") -> bool:
    if is_youth_or_women_match(league_name, home_team, away_team):
        return True
    for text in (league_name, home_team, away_team):
        s = (text or "").strip()
        if not s:
            continue
        if _RESERVE_YOUTH_WOMEN_RE.search(s):
            return True
        if _TRAILING_B_TEAM_RE.search(s):
            return True
    return False


def _is_eligible_coupon_match(match: Match, kickoff_lock: Optional[datetime] = None) -> bool:
    if is_youth_or_women_match(match.league, match.home_team, match.away_team):
        return False
    if kickoff_lock is not None:
        m_start = _ensure_utc(match.start_time_utc)
        if m_start <= kickoff_lock:
            return False
    return True


def extract_leg_probability(p: Prediction) -> float:
    """
    Extract calibrated leg win probability in (0, 1).
    Uses published_prob -> score_breakdown_json (blended/true/poisson) -> conservative odds fallback.
    """
    if p.published_prob is not None and 0.01 <= float(p.published_prob) <= 0.99:
        return float(p.published_prob)

    if p.score_breakdown_json:
        try:
            bd = json.loads(p.score_breakdown_json)
            for key in ("blended_prob_pct", "true_prob_pct", "poisson_prob_pct"):
                val = bd.get(key)
                if val is not None:
                    prob = float(val) / 100.0
                    return max(0.02, min(0.96, prob))
        except Exception:
            pass

    eff_odd = p.global_odd if (p.global_odd and p.global_odd > 1.01) else p.iddaa_odd
    if eff_odd and eff_odd > 1.01:
        return max(0.05, min(0.92, (1.0 / float(eff_odd)) * 0.92))
    return 0.50


def _effective_edge_pct(p: Prediction) -> Optional[float]:
    if p.value_edge_pct is not None:
        return float(p.value_edge_pct)
    if p.global_odd is not None and p.global_odd > 1.01 and p.iddaa_odd > 1.0:
        return ((float(p.iddaa_odd) / float(p.global_odd)) - 1.0) * 100.0
    return None


def _passes_banko_quality_armor(
    p: Prediction,
    matches_with_global: Optional[set[int]] = None,
) -> bool:
    """Strict Banko quality armor: match-level global data present, no reserve/youth/women, no dead odds."""
    m = p.match
    if _is_reserve_or_youth_or_women(m.league, m.home_team, m.away_team):
        return False
    has_own_global = p.global_odd is not None and p.global_odd > 1.01
    has_match_global = bool(matches_with_global and p.match_id in matches_with_global)
    if not (has_own_global or has_match_global):
        return False
    if (m.data_status or "").strip().lower() in _BAD_DATA_STATUSES:
        return False
    edge = _effective_edge_pct(p)
    if edge is not None and edge < BANKO_DEAD_ODD_EDGE_FLOOR:
        return False
    return True


def _is_primetime_tr(start_time_utc: datetime) -> bool:
    tr_dt = to_tr_datetime(_ensure_utc(start_time_utc))
    return 13 <= tr_dt.hour <= 23


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


def _leg_quality_score(
    p: Prediction,
    tier: str,
    now_utc: datetime,
    top_pick_ids: set[int],
) -> float:
    prob = extract_leg_probability(p)
    conf = float(p.confidence_score)
    edge = _effective_edge_pct(p)
    drop = float(p.odds_drop_pct or 0.0)
    is_major = 1.0 if p.match.is_major_league else 0.0
    is_top = 1.0 if p.id in top_pick_ids else 0.0
    hours_away = max(0.0, (_ensure_utc(p.match.start_time_utc) - now_utc).total_seconds() / 3600.0)
    early_bonus = max(0.0, (48.0 - hours_away) / 48.0) * 1.5
    primetime_bonus = 2.0 if _is_primetime_tr(p.match.start_time_utc) else 0.0

    if tier == "BANKO":
        low_var_bonus = 0.0
        if (p.market_key == "AU_15" and p.selection_key == "UST") or p.market_key == "CS":
            low_var_bonus = 2.5
        elif p.market_key == "AU_25":
            low_var_bonus = 1.5
        return (
            prob * 100.0 * 1.25
            + conf * 0.45
            + is_major * 2.5
            + is_top * 2.0
            + low_var_bonus
            + primetime_bonus
            + early_bonus
        )

    if tier == "DENGELI":
        edge_bonus = max(-3.0, min(6.0, (edge or 0.0) * 0.4))
        return (
            prob * 100.0 * 0.85
            + conf * 0.70
            + edge_bonus
            + is_major * 1.5
            + is_top * 2.0
            + primetime_bonus
            + early_bonus
        )

    # SURPRIZ: value edge + EV + model support
    ev_component = (prob * float(p.iddaa_odd) - 1.0) * 100.0
    return (
        (edge if edge is not None else -5.0) * 1.6
        + ev_component * 0.8
        + drop * 1.5
        + conf * 0.55
        + is_major * 1.0
        + is_top * 1.5
        + primetime_bonus
        + early_bonus
    )


def _best_combo_for_pool(
    pool: list[Prediction],
    tier: str,
    now_utc: datetime,
    top_pick_ids: set[int],
    global_used_matches: set[int],
    match_usage_counts: dict[int, int],
    max_overlap: int = 0,
) -> list[Prediction]:
    """
    Select the best 2-leg or 3-leg combination for a tier from `pool`.
    Enforces: distinct match_id, max 2 same-market legs, <=24h span preference,
    league-hour clustering penalty, and exact tier probability/odds gates.
    """
    if len(pool) < 2:
        return []

    # Sort & cap candidate pool to top 18 (deduped to best per match_id + up to 2nd pick if needed)
    scored = sorted(
        pool,
        key=lambda p: _leg_quality_score(p, tier, now_utc, top_pick_ids),
        reverse=True,
    )
    # Keep at most top 18 candidates (with max 1 per match_id first, then 2nd pick if pool is tiny)
    by_match: dict[int, list[Prediction]] = {}
    for p in scored:
        by_match.setdefault(p.match_id, []).append(p)
    trimmed: list[Prediction] = []
    for preds in by_match.values():
        trimmed.append(preds[0])
    trimmed.sort(key=lambda p: _leg_quality_score(p, tier, now_utc, top_pick_ids), reverse=True)
    trimmed = trimmed[:18]

    if len(trimmed) < 2:
        return []

    def _combo_valid_and_score(legs: tuple[Prediction, ...]) -> Optional[tuple[float, list[Prediction]]]:
        mids = [p.match_id for p in legs]
        if len(set(mids)) != len(legs):
            return None

        # Cross-coupon overlap check
        overlap = sum(1 for mid in mids if mid in global_used_matches)
        if overlap > max_overlap:
            return None
        if any(match_usage_counts.get(mid, 0) >= 2 for mid in mids):
            return None

        # Soft market diversity penalty instead of hard veto when all legs share one market_key
        mkt_counts: dict[str, int] = {}
        for p in legs:
            mkt_counts[p.market_key] = mkt_counts.get(p.market_key, 0) + 1
        market_penalty = sum(max(0, cnt - 2) * 2.5 for cnt in mkt_counts.values())

        probs = [extract_leg_probability(p) for p in legs]
        comb_prob = math.prod(probs)
        total_odd = math.prod(float(p.published_odd or p.iddaa_odd) for p in legs)

        starts = [_ensure_utc(p.match.start_time_utc) for p in legs]
        span_hours = (max(starts) - min(starts)).total_seconds() / 3600.0

        # Tier-specific hard gates
        if tier == "BANKO":
            if len(legs) == 2:
                if comb_prob < BANKO_CORE_MIN_COMBINED_PROB:
                    return None
                if total_odd > BANKO_TOTAL_ODD_MAX:
                    return None
            elif len(legs) == 3:
                if comb_prob < BANKO_3LEG_MIN_COMBINED_PROB:
                    return None
                if not (BANKO_TOTAL_ODD_MIN <= total_odd <= BANKO_TOTAL_ODD_MAX):
                    return None
                # The 3rd (weakest-quality) leg must meet the ultra-safe 3rd-leg rule (>=63%, conf>=66, <=1.48)
                sorted_by_q = sorted(
                    legs,
                    key=lambda x: _leg_quality_score(x, tier, now_utc, top_pick_ids),
                    reverse=True,
                )
                third_leg = sorted_by_q[2]
                third_odd = float(third_leg.published_odd or third_leg.iddaa_odd)
                if (
                    extract_leg_probability(third_leg) < BANKO_3LEG_MIN_PROB
                    or float(third_leg.confidence_score) < BANKO_3LEG_MIN_CONF
                    or third_odd > BANKO_3LEG_MAX_ODD
                ):
                    return None
            else:
                return None
        elif tier == "DENGELI":
            if comb_prob < DENGELI_MIN_COMBINED_PROB:
                return None
            if not (DENGELI_TOTAL_ODD_MIN <= total_odd <= DENGELI_TOTAL_ODD_MAX):
                return None
        elif tier == "SURPRIZ":
            if not (SURPRIZ_TOTAL_ODD_MIN <= total_odd <= SURPRIZ_TOTAL_ODD_MAX):
                return None
        else:
            return None

        # League / kickoff-hour clustering penalty
        cluster_seen: set[tuple[str, int]] = set()
        league_seen: set[str] = set()
        cluster_penalty = 0.0
        for p in legs:
            tr_h = to_tr_datetime(_ensure_utc(p.match.start_time_utc)).hour
            lg = (p.match.league or "").lower()
            if (lg, tr_h) in cluster_seen:
                cluster_penalty += 4.0
            elif lg in league_seen:
                cluster_penalty += 1.5
            cluster_seen.add((lg, tr_h))
            league_seen.add(lg)

        span_penalty = 12.0 if span_hours > COUPON_SPAN_MAX_HOURS else (span_hours / COUPON_SPAN_MAX_HOURS) * 1.5
        overlap_penalty = overlap * 15.0

        avg_leg_score = sum(_leg_quality_score(p, tier, now_utc, top_pick_ids) for p in legs) / len(legs)
        if tier == "BANKO":
            band_bonus = 6.0 if (BANKO_TOTAL_ODD_MIN <= total_odd <= BANKO_TOTAL_ODD_MAX) else 0.0
            score = comb_prob * 100.0 * 1.1 + avg_leg_score * 0.5 + band_bonus - span_penalty - cluster_penalty - market_penalty - overlap_penalty
        elif tier == "DENGELI":
            score = comb_prob * 100.0 * 0.9 + avg_leg_score * 0.6 - span_penalty - cluster_penalty - market_penalty - overlap_penalty
        else:
            score = avg_leg_score + comb_prob * 40.0 - span_penalty - cluster_penalty - market_penalty - overlap_penalty

        return score, list(legs)

    best_2: Optional[tuple[float, list[Prediction]]] = None
    best_3: Optional[tuple[float, list[Prediction]]] = None

    for comb in combinations(trimmed, 2):
        res = _combo_valid_and_score(comb)
        if res and (best_2 is None or res[0] > best_2[0]):
            best_2 = res

    for comb in combinations(trimmed[:14], 3):
        res = _combo_valid_and_score(comb)
        if res and (best_3 is None or res[0] > best_3[0]):
            best_3 = res

    if tier == "BANKO":
        if best_2 and best_3:
            odd_2 = math.prod(float(p.published_odd or p.iddaa_odd) for p in best_2[1])
            comb_3 = math.prod(extract_leg_probability(p) for p in best_3[1])
            # Add 3rd leg when it keeps Banko in 1.75-2.65 AND either 2-leg odd <1.90 or 3-leg prob >=0.33
            if odd_2 < 1.90 or comb_3 >= 0.33:
                return best_3[1]
            return best_2[1]
        return (best_3 or best_2 or (0.0, []))[1]

    if best_2 and best_3:
        return best_3[1] if best_3[0] >= best_2[0] else best_2[1]
    return (best_3 or best_2 or (0.0, []))[1]


def _pick_tier_with_windows(
    tier_pool: list[Prediction],
    tier: str,
    now_utc: datetime,
    top_pick_ids: set[int],
    global_used_matches: set[int],
    match_usage_counts: dict[int, int],
) -> list[Prediction]:
    """
    Windowed selection:
    1) 0-24h prime-time (13:00-23:59 TR) first, then 0-24h all hours.
    2) 24-48h fallback ONLY if 0-24h has no valid combo OR 24-48h has a clearly stronger candidate
       (+6 confidence or +5% probability over 0-24h weakest leg).
    3) Cross-coupon overlap: 0 overlap first, max 1 overlap fallback only if needed.
    """
    t1_cutoff = now_utc + timedelta(hours=TIER1_WINDOW_HOURS)
    pool_24h = [p for p in tier_pool if _ensure_utc(p.match.start_time_utc) <= t1_cutoff]
    pool_24h_prime = [p for p in pool_24h if _is_primetime_tr(p.match.start_time_utc)]
    pool_48h_prime = [p for p in tier_pool if _is_primetime_tr(p.match.start_time_utc)]

    def _try_pool(sub_pool: list[Prediction]) -> list[Prediction]:
        res = _best_combo_for_pool(
            sub_pool, tier, now_utc, top_pick_ids, global_used_matches, match_usage_counts, max_overlap=0
        )
        if res:
            return res
        return _best_combo_for_pool(
            sub_pool, tier, now_utc, top_pick_ids, global_used_matches, match_usage_counts, max_overlap=1
        )

    # Step 1: Try 0-24h prime-time, then full 0-24h
    picked_24h = _try_pool(pool_24h_prime)
    if not picked_24h and len(pool_24h) > len(pool_24h_prime):
        picked_24h = _try_pool(pool_24h)

    if picked_24h:
        # Check if 24-48h has a clearly stronger candidate (+6 conf or +5% prob over weakest 24h leg)
        pool_24_48 = [p for p in tier_pool if _ensure_utc(p.match.start_time_utc) > t1_cutoff]
        if pool_24_48:
            weakest_conf = min(float(p.confidence_score) for p in picked_24h)
            weakest_prob = min(extract_leg_probability(p) for p in picked_24h)
            stronger_24_48 = [
                p
                for p in pool_24_48
                if float(p.confidence_score) >= weakest_conf + 6.0
                or extract_leg_probability(p) >= weakest_prob + 0.05
            ]
            if stronger_24_48:
                combined_pool = pool_24h + stronger_24_48
                candidate_48 = _try_pool(
                    [p for p in combined_pool if _is_primetime_tr(p.match.start_time_utc)]
                ) or _try_pool(combined_pool)
                if candidate_48:
                    return candidate_48
        return picked_24h

    # Step 2: 0-24h had no valid combo -> allow 0-48h (prime-time first, then all hours)
    picked_48h = _try_pool(pool_48h_prime)
    if not picked_48h and len(tier_pool) > len(pool_48h_prime):
        picked_48h = _try_pool(tier_pool)
    return picked_48h


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
        st = _ensure_utc(m.start_time_utc)
        if st <= freeze_cutoff:
            return True
    return False


def generate_daily_coupons(db: Session) -> list[Coupon]:
    """
    Kupon V2:
    - Evaluates all upcoming Predictions (>=60m kickoff buffer, <=48h horizon).
    - Tiered probability/odds/quality gates for BANKO, DENGELI, SURPRIZ (dynamic 2-3 legs).
    - 0-24h priority with 24-48h quality fallback, TR prime-time (13:00-23:59) priority.
    - Cross-coupon match_id protection and full preservation of locked/settled coupons.
    """
    now_utc = datetime.now(timezone.utc)
    today_str = tr_today_str()
    kickoff_lock = now_utc + timedelta(minutes=KICKOFF_BUFFER_MINUTES)
    max_horizon = now_utc + timedelta(hours=MAX_HORIZON_HOURS)

    existing_today = (
        db.query(Coupon)
        .filter(Coupon.coupon_date == today_str)
        .all()
    )
    existing_by_tier: dict[str, Coupon] = {c.tier: c for c in existing_today}
    frozen_tiers: set[str] = set()
    global_used_matches: set[int] = set()
    match_usage_counts: dict[int, int] = {}

    # Preserve ALL locked, started, or previous-day PENDING coupons untouched
    all_pending_coupons = (
        db.query(Coupon)
        .filter(Coupon.status == "PENDING")
        .order_by(Coupon.coupon_date.desc(), Coupon.id.desc())
        .all()
    )
    for pending_c in all_pending_coupons:
        if (
            pending_c.is_locked
            or pending_c.coupon_date != today_str
            or _should_freeze_coupon(pending_c, now_utc)
        ):
            pending_c.is_locked = True
            frozen_tiers.add(pending_c.tier)
            if pending_c.tier not in existing_by_tier or pending_c.coupon_date == today_str:
                existing_by_tier[pending_c.tier] = pending_c
            for item in pending_c.items:
                if item.prediction:
                    mid = item.prediction.match_id
                    global_used_matches.add(mid)
                    match_usage_counts[mid] = match_usage_counts.get(mid, 0) + 1

    # Also do not overwrite already-settled coupons for today
    for tier, c in list(existing_by_tier.items()):
        if c.status != "PENDING":
            frozen_tiers.add(tier)

    all_pending = (
        db.query(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .filter(
            Match.status == "SCHEDULED",
            Match.start_time_utc > kickoff_lock,
            Match.start_time_utc <= max_horizon,
            Prediction.status == "PENDING",
            Prediction.iddaa_odd >= MIN_ODD_RULE,
        )
        .all()
    )

    top_pick_by_match: dict[int, Prediction] = {}
    match_pick_conf: dict[tuple[int, str, str], float] = {}
    matches_with_global: set[int] = set()
    for mp in all_pending:
        match_pick_conf[(mp.match_id, mp.market_key, mp.selection_key)] = mp.confidence_score
        if mp.global_odd is not None and mp.global_odd > 1.01:
            matches_with_global.add(mp.match_id)
        if mp.is_top_pick:
            top_pick_by_match[mp.match_id] = mp
    for mp in all_pending:
        if mp.match_id not in top_pick_by_match:
            top_pick_by_match[mp.match_id] = mp
        elif not top_pick_by_match[mp.match_id].is_top_pick and mp.confidence_score > top_pick_by_match[mp.match_id].confidence_score:
            top_pick_by_match[mp.match_id] = mp

    top_pick_ids: set[int] = {p.id for p in top_pick_by_match.values()}

    eligible = [
        p
        for p in all_pending
        if _is_eligible_coupon_match(p.match, kickoff_lock=kickoff_lock)
        and _ensure_utc(p.match.start_time_utc) <= max_horizon
        and not _is_contradicting_pick(p, top_pick_by_match.get(p.match_id), match_pick_conf)
    ]

    tier_filters = {
        "BANKO": lambda p: (
            not p.trap_warning
            and _passes_banko_quality_armor(p, matches_with_global=matches_with_global)
            and float(p.confidence_score) >= BANKO_MIN_CONF
            and extract_leg_probability(p) >= BANKO_MIN_PROB
            and BANKO_MIN_ODD <= float(p.iddaa_odd) <= BANKO_MAX_ODD
        ),
        "DENGELI": lambda p: (
            not p.trap_warning
            and float(p.confidence_score) >= DENGELI_MIN_CONF
            and extract_leg_probability(p) >= DENGELI_MIN_PROB
            and DENGELI_MIN_ODD <= float(p.iddaa_odd) <= DENGELI_MAX_ODD
        ),
        "SURPRIZ": lambda p: (
            float(p.confidence_score) >= SURPRIZ_MIN_CONF
            and SURPRIZ_MIN_ODD <= float(p.iddaa_odd) <= SURPRIZ_MAX_ODD
        ),
    }

    created_or_updated: list[Coupon] = []

    for tier in ("BANKO", "DENGELI", "SURPRIZ"):
        if tier in frozen_tiers:
            created_or_updated.append(existing_by_tier[tier])
            continue

        tier_pool = [p for p in eligible if tier_filters[tier](p)]
        picked = _pick_tier_with_windows(
            tier_pool=tier_pool,
            tier=tier,
            now_utc=now_utc,
            top_pick_ids=top_pick_ids,
            global_used_matches=global_used_matches,
            match_usage_counts=match_usage_counts,
        )

        if len(picked) < 2:
            # Quality threshold not met: remove unlocked stale coupon if any, never force junk
            if tier in existing_by_tier and not existing_by_tier[tier].is_locked and existing_by_tier[tier].status == "PENDING":
                db.delete(existing_by_tier[tier])
            continue

        for p in picked:
            global_used_matches.add(p.match_id)
            match_usage_counts[p.match_id] = match_usage_counts.get(p.match_id, 0) + 1
            if p.published_odd is None:
                bd = json.loads(p.score_breakdown_json) if p.score_breakdown_json else {}
                pub_prob_pct = bd.get("blended_prob_pct") or bd.get("true_prob_pct")
                p.published_odd = p.iddaa_odd
                p.published_confidence = p.confidence_score
                p.published_prob = round(float(pub_prob_pct) / 100.0, 4) if pub_prob_pct is not None else None
                p.published_at = now_utc

        total_odd = round(math.prod((p.published_odd or p.iddaa_odd) for p in picked), 2)
        avg_conf = round(
            sum((p.published_confidence if p.published_confidence is not None else p.confidence_score) for p in picked)
            / len(picked),
            1,
        )

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
            db.add(
                CouponItem(
                    coupon_id=coupon.id,
                    prediction_id=p.id,
                    odd_at_pick=p.published_odd if p.published_odd is not None else p.iddaa_odd,
                )
            )

        created_or_updated.append(coupon)

    db.commit()
    return created_or_updated
