import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CouponItem, Match, MatchContext, OddsCurrent, OddsHistory, Prediction

MIN_ODD_RULE = 1.30
MAX_HIGHLIGHT_ODD = 4.00

BASE_HOME_GF = 1.45
BASE_HOME_GA = 1.15
BASE_AWAY_GF = 1.15
BASE_AWAY_GA = 1.45
DIXON_COLES_RHO = -0.10


def _form_points(form_list: list[str]) -> float:
    if not form_list:
        return 7.5
    pts = sum(3 if x == "W" else (1 if x == "D" else 0) for x in form_list[:5])
    return pts * (5.0 / len(form_list[:5]))


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _fresh_injury_weight(item: dict[str, Any]) -> float:
    """
    Motor v4.0 Fresh Injury Tiering:
    - missed_games <= 3 (or None): 100% weight (fresh impact not yet baked into last-5 form)
    - 4 <= missed_games <= 8: 50% weight
    - missed_games >= 9: 15% minimal weight (already reflected in recent team form)
    """
    mg = item.get("missed_games")
    if mg is None:
        return 1.0
    try:
        mg_int = int(mg)
    except (TypeError, ValueError):
        return 1.0
    if mg_int <= 3:
        return 1.0
    if mg_int <= 8:
        return 0.50
    return 0.15


def _injury_xg_factors(injuries: list[dict[str, Any]]) -> tuple[float, float]:
    """
    Compute (attack_reduction_factor, defense_leak_increase_factor) from position,
    market value, and Motor v4.0 fresh-injury tiering. Both are capped at <= 14% (0.14).
    """
    atk_burden = 0.0
    def_burden = 0.0

    for item in injuries:
        fresh_w = _fresh_injury_weight(item)
        pos = (item.get("position") or "").lower()
        val_m = item.get("value_m")
        share_pct = item.get("squad_value_share_pct")
        if share_pct is not None:
            if share_pct >= 5.0 and fresh_w >= 0.99:
                val_w = min(1.85, 1.35 + (share_pct - 5.0) * 0.06)
            elif share_pct >= 5.0:
                val_w = 1.30
            elif share_pct < 1.0:
                val_w = 0.35
            else:
                val_w = 0.85 + share_pct * 0.08
        else:
            val_w = 1.45 if (val_m and val_m >= 15.0) else (1.15 if (val_m and val_m >= 5.0) else 0.85)
        weight = val_w * fresh_w

        if any(k in pos for k in ("forward", "striker", "winger", "attack")):
            atk_burden += weight * 0.032
        elif "midfield" in pos:
            atk_burden += weight * 0.020
            def_burden += weight * 0.012
        elif any(k in pos for k in ("back", "defend", "goalkeeper", "keeper")):
            def_burden += weight * 0.032
        else:
            # Fallback when position info isn't present
            atk_burden += weight * 0.015
            def_burden += weight * 0.015

    return min(0.14, atk_burden), min(0.14, def_burden)


def _compute_dixon_coles_probs(
    ctx: Optional[MatchContext],
    data_status: str = "",
) -> dict[str, Any]:
    """
    Motor v4.0 Dixon-Coles (rho=-0.10) Poisson model with:
    - 3-source goal expectation: 45% full-season home/away + 40% time-weighted last 5 + 15% base
    - Understat True xG/xGA blend (60% True xG/xGA + 40% actual goals) when available
    - ClubElo strength adjustment: elo_diff = (home_elo + 55) - away_elo
    - Fixture fatigue adjustment (rest_days <= 3 vs >= 5)
    - Freshness-tiered injury xG adjustments
    """
    if not ctx or ctx.home_goals_avg is None or ctx.away_goals_avg is None:
        return {"probs": None, "raw_xg_h": None, "raw_xg_a": None, "adj_xg_h": None, "adj_xg_a": None}

    last5_h_gf = ctx.home_goals_avg
    last5_a_gf = ctx.away_goals_avg
    last5_h_ga = ctx.home_conceded_avg if ctx.home_conceded_avg is not None else BASE_HOME_GA
    last5_a_ga = ctx.away_conceded_avg if ctx.away_conceded_avg is not None else BASE_AWAY_GA

    # Dynamic sample-size weighted 3-source blend:
    # season_weight = min(0.45, played * 0.05), remaining split 40/55 last5 & 15/55 base
    if ctx.season_home_gf is not None and ctx.season_home_ga is not None:
        h_played = ctx.season_home_played if ctx.season_home_played is not None else 9
        w_s_h = min(0.45, max(0, h_played) * 0.05)
        rem_h = 1.0 - w_s_h
        w_l5_h = rem_h * (40.0 / 55.0)
        w_b_h = rem_h * (15.0 / 55.0)
        h_gf = w_s_h * ctx.season_home_gf + w_l5_h * last5_h_gf + w_b_h * BASE_HOME_GF
        h_ga = w_s_h * ctx.season_home_ga + w_l5_h * last5_h_ga + w_b_h * BASE_HOME_GA
    else:
        h_gf = 0.65 * last5_h_gf + 0.35 * BASE_HOME_GF
        h_ga = 0.65 * last5_h_ga + 0.35 * BASE_HOME_GA

    if ctx.season_away_gf is not None and ctx.season_away_ga is not None:
        a_played = ctx.season_away_played if ctx.season_away_played is not None else 9
        w_s_a = min(0.45, max(0, a_played) * 0.05)
        rem_a = 1.0 - w_s_a
        w_l5_a = rem_a * (40.0 / 55.0)
        w_b_a = rem_a * (15.0 / 55.0)
        a_gf = w_s_a * ctx.season_away_gf + w_l5_a * last5_a_gf + w_b_a * BASE_AWAY_GF
        a_ga = w_s_a * ctx.season_away_ga + w_l5_a * last5_a_ga + w_b_a * BASE_AWAY_GA
    else:
        a_gf = 0.65 * last5_a_gf + 0.35 * BASE_AWAY_GF
        a_ga = 0.65 * last5_a_ga + 0.35 * BASE_AWAY_GA

    # True xG / xGA blend:
    # (1) Understat True xG: 60% Understat + 40% actual goals
    # (2) ESPN Shot-xG fallback: 55% Shot-xG + 45% actual goals
    xg_w = 0.55 if (ctx.xg_source == "espn_shots") else 0.60
    if ctx.home_true_xg is not None and ctx.home_true_xga is not None:
        h_gf = xg_w * ctx.home_true_xg + (1.0 - xg_w) * h_gf
        h_ga = xg_w * ctx.home_true_xga + (1.0 - xg_w) * h_ga
    if ctx.away_true_xg is not None and ctx.away_true_xga is not None:
        a_gf = xg_w * ctx.away_true_xg + (1.0 - xg_w) * a_gf
        a_ga = xg_w * ctx.away_true_xga + (1.0 - xg_w) * a_ga

    raw_lam_h = max(0.35, min(3.6, (h_gf + a_ga) / 2.0))
    raw_lam_a = max(0.30, min(3.2, (a_gf + h_ga) / 2.0))

    lam_h = raw_lam_h
    lam_a = raw_lam_a

    # ClubElo strength adjustment (elo_diff includes +55 home advantage)
    if ctx.home_elo is not None and ctx.away_elo is not None:
        elo_diff = (ctx.home_elo + 55.0) - ctx.away_elo
        elo_net = max(-220.0, min(220.0, elo_diff - 55.0))
        elo_scale = elo_net / 1000.0
        lam_h *= (1.0 + elo_scale)
        lam_a *= (1.0 - elo_scale * 0.85)

    # Fatigue / rest-days adjustment
    h_rest = ctx.home_rest_days
    a_rest = ctx.away_rest_days
    if h_rest is not None and a_rest is not None:
        if h_rest <= 3 and a_rest >= 5:
            lam_h *= 0.95
            lam_a *= 1.04
        elif a_rest <= 3 and h_rest >= 5:
            lam_a *= 0.95
            lam_h *= 1.04

    # Position & market-value weighted injury xG adjustment
    if ctx.injuries_json and "tm_injuries" in (data_status or ""):
        inj = json.loads(ctx.injuries_json)
        h_atk_red, h_def_inc = _injury_xg_factors(inj.get("home") or [])
        a_atk_red, a_def_inc = _injury_xg_factors(inj.get("away") or [])
        lam_h = lam_h * (1.0 - h_atk_red) * (1.0 + a_def_inc)
        lam_a = lam_a * (1.0 - a_atk_red) * (1.0 + h_def_inc)

    lam_h = round(max(0.30, min(3.6, lam_h)), 2)
    lam_a = round(max(0.25, min(3.2, lam_a)), 2)

    p_home = [_poisson_pmf(i, lam_h) for i in range(10)]
    p_away = [_poisson_pmf(j, lam_a) for j in range(10)]

    matrix = [[0.0] * 10 for _ in range(10)]
    total_mass = 0.0
    for i in range(10):
        for j in range(10):
            tau = 1.0
            if i == 0 and j == 0:
                tau = max(0.1, 1.0 - lam_h * lam_a * DIXON_COLES_RHO)
            elif i == 1 and j == 0:
                tau = max(0.1, 1.0 + lam_a * DIXON_COLES_RHO)
            elif i == 0 and j == 1:
                tau = max(0.1, 1.0 + lam_h * DIXON_COLES_RHO)
            elif i == 1 and j == 1:
                tau = max(0.1, 1.0 - DIXON_COLES_RHO)

            val = p_home[i] * p_away[j] * tau
            matrix[i][j] = val
            total_mass += val

    if total_mass <= 0:
        return {"probs": None, "raw_xg_h": round(raw_lam_h, 2), "raw_xg_a": round(raw_lam_a, 2), "adj_xg_h": lam_h, "adj_xg_a": lam_a}

    p1 = px = p2 = p_u15 = p_u25 = p_u35 = p_kg = 0.0
    for i in range(10):
        for j in range(10):
            p = matrix[i][j] / total_mass
            if i > j:
                p1 += p
            elif i == j:
                px += p
            else:
                p2 += p
            tot = i + j
            if tot > 1.5:
                p_u15 += p
            if tot > 2.5:
                p_u25 += p
            if tot > 3.5:
                p_u35 += p
            if i > 0 and j > 0:
                p_kg += p

    probs = {
        ("MS", "1"): p1,
        ("MS", "X"): px,
        ("MS", "2"): p2,
        ("CS", "1X"): p1 + px,
        ("CS", "12"): p1 + p2,
        ("CS", "X2"): px + p2,
        ("AU_15", "UST"): p_u15,
        ("AU_15", "ALT"): 1.0 - p_u15,
        ("AU_25", "UST"): p_u25,
        ("AU_25", "ALT"): 1.0 - p_u25,
        ("AU_35", "UST"): p_u35,
        ("AU_35", "ALT"): 1.0 - p_u35,
        ("KG", "VAR"): p_kg,
        ("KG", "YOK"): 1.0 - p_kg,
    }
    return {
        "probs": probs,
        "raw_xg_h": round(raw_lam_h, 2),
        "raw_xg_a": round(raw_lam_a, 2),
        "adj_xg_h": lam_h,
        "adj_xg_a": lam_a,
    }


def _is_sparse_data(ctx: Optional[MatchContext], odd: OddsCurrent) -> bool:
    """True when match lacks rich stats (no form or no season/xG or no global odds)."""
    has_form = bool(ctx and ctx.home_form and ctx.away_form)
    has_season_or_xg = bool(
        ctx and (
            (ctx.season_home_gf is not None and ctx.season_away_gf is not None)
            or (ctx.home_true_xg is not None and ctx.away_true_xg is not None)
        )
    )
    has_global = bool(odd.global_avg_odd and odd.global_avg_odd > 1.0)
    return (not has_form) or (not has_season_or_xg and not has_global)


def _calibrate_market_prob(
    market_key: str,
    true_prob: float,
    poisson_prob: Optional[float],
    sparse_data: bool,
) -> float:
    """
    Bayesian Market Calibration: anchor Dixon-Coles probabilities strongly to
    de-vigged market probability so low-data matches cannot hallucinate huge edges.
    - Sparse data: 75% Market + 25% Model
    - Rich data (AU/KG/MS/CS): 60% Market + 40% Model
    """
    if poisson_prob is None:
        return true_prob
    if sparse_data:
        return 0.75 * true_prob + 0.25 * poisson_prob
    if market_key in ("AU_15", "AU_25", "AU_35", "KG", "MS", "CS"):
        return 0.60 * true_prob + 0.40 * poisson_prob
    return 0.65 * true_prob + 0.35 * poisson_prob


def _is_high_risk_pick(market_key: str, selection_key: str, true_prob: float, iddaa_odd: float) -> bool:
    """High-risk markets (MS X, 1.5 Alt, or market prob < 42% / odd > 2.35)."""
    if market_key == "MS" and selection_key == "X":
        return True
    if market_key == "AU_15" and selection_key == "ALT":
        return True
    if true_prob < 0.42 or iddaa_odd > 2.35:
        return True
    return False


_EXPECTED_MARKET_OUTCOMES: dict[str, tuple[str, ...]] = {
    "MS": ("1", "X", "2"),
    "AU_15": ("UST", "ALT"),
    "AU_25": ("UST", "ALT"),
    "AU_35": ("UST", "ALT"),
    "KG": ("VAR", "YOK"),
}


def _devig(market_key: str, odds_map: dict[tuple[str, str], OddsCurrent]) -> dict[str, float]:
    """
    Compute de-vigged implied probabilities for a market:
    - MS requires all 3 outcomes (1, X, 2); AU_*/KG require 2 outcomes. Incomplete groups are NOT de-vigged.
    - CS (1X, 12, X2) is NEVER de-vigged from its 3-way sum; it is derived from de-vigged MS probabilities.
    """
    if market_key == "CS":
        ms_probs = _devig("MS", odds_map)
        if "1" in ms_probs and "X" in ms_probs and "2" in ms_probs:
            return {
                "1X": min(0.99, ms_probs["1"] + ms_probs["X"]),
                "12": min(0.99, ms_probs["1"] + ms_probs["2"]),
                "X2": min(0.99, ms_probs["X"] + ms_probs["2"]),
            }
        # Fallback if MS is incomplete: raw implied probability without 3-way CS overround
        out_cs: dict[str, float] = {}
        for sk in ("1X", "12", "X2"):
            o = odds_map.get(("CS", sk))
            if o:
                ref = o.global_avg_odd if (o.global_avg_odd and o.global_avg_odd > 1.0) else o.iddaa_odd
                if ref and ref > 1.0:
                    out_cs[sk] = min(0.95, 1.0 / ref)
        return out_cs

    expected = _EXPECTED_MARKET_OUTCOMES.get(market_key, ())
    if expected:
        objs = [odds_map.get((market_key, k)) for k in expected]
        if all(o is not None and o.global_avg_odd and o.global_avg_odd > 1.0 for o in objs):
            overround = sum(1.0 / o.global_avg_odd for o in objs if o is not None and o.global_avg_odd)
            if overround > 0:
                return {
                    k: (1.0 / odds_map[(market_key, k)].global_avg_odd) / overround  # type: ignore[operator]
                    for k in expected
                }
        if all(o is not None and o.iddaa_odd and o.iddaa_odd > 1.0 for o in objs):
            overround = sum(1.0 / o.iddaa_odd for o in objs if o is not None)
            if overround > 0:
                return {k: (1.0 / odds_map[(market_key, k)].iddaa_odd) / overround for k in expected}

    # Incomplete group: do not de-vig
    out: dict[str, float] = {}
    for (mk, sk), o in odds_map.items():
        if mk != market_key:
            continue
        ref = o.global_avg_odd if (o.global_avg_odd and o.global_avg_odd > 1.0) else o.iddaa_odd
        if ref and ref > 1.0:
            out[sk] = min(0.95, 1.0 / ref)
    return out


def _compute_value_component(
    odd: OddsCurrent,
    odds_map: dict[tuple[str, str], OddsCurrent],
    calibrated_prob: Optional[float],
    true_prob: Optional[float] = None,
) -> tuple[float, float, Optional[float], Optional[float], bool]:
    if true_prob is None:
        devigged = _devig(odd.market_key, odds_map)
        true_prob = devigged.get(odd.selection_key)
        if true_prob is None:
            ref = odd.global_avg_odd if (odd.global_avg_odd and odd.global_avg_odd > 1.0) else odd.iddaa_odd
            true_prob = min(0.95, 1.0 / ref) if ref and ref > 1.0 else 0.5

    prob_pts = min(16.5, true_prob * 23.0)
    value_edge_pct: Optional[float] = None
    poisson_edge_pct: Optional[float] = None
    conflict_flag = False

    if odd.global_avg_odd and odd.global_avg_odd > 1.0:
        ratio = odd.iddaa_odd / odd.global_avg_odd
        value_edge_pct = round((ratio - 1.0) * 100.0, 2)
        if ratio >= 1.0:
            edge_pts = 15.0
        elif ratio >= 0.93:
            edge_pts = 10.0 + ((ratio - 0.93) / 0.07) * 4.5
        elif ratio >= 0.86:
            edge_pts = 5.0 + ((ratio - 0.86) / 0.07) * 5.0
        else:
            edge_pts = 1.5
    else:
        # Neutral baseline when global odds are unavailable (avoid rewarding low odds twice)
        edge_pts = min(8.5, 4.5 + true_prob * 5.0)

    # Edge reward cap: (Calibrated Model Prob - Market Prob) capped at max +8% (+0.08)
    model_market_edge = 0.0
    if calibrated_prob is not None:
        raw_mme = calibrated_prob - true_prob
        model_market_edge = max(-0.08, min(0.08, raw_mme))
        raw_edge = (calibrated_prob * odd.iddaa_odd - 1.0) * 100.0
        if abs(raw_mme) > 0.14:
            conflict_flag = True
            poisson_edge_pct = 0.0
        else:
            capped_edge = min(8.0, raw_edge)
            poisson_edge_pct = round(capped_edge, 2)
            if model_market_edge > 0.01:
                edge_pts = min(16.0, edge_pts + min(3.2, model_market_edge * 40.0))
            elif capped_edge > 0:
                edge_pts = min(15.0, edge_pts + min(1.5, capped_edge * 0.18))

    # Brake raw-probability inflation on very low-odd markets (< 1.38, e.g. AU_35:ALT, AU_15:UST)
    # unless backed by a genuine strong positive edge (Model Prob - Market Prob >= 4% or global >= +3%)
    low_odd_penalty = 0.0
    is_low_odd_trap_market = odd.iddaa_odd < 1.38 or (
        ((odd.market_key == "AU_35" and odd.selection_key == "ALT")
        or (odd.market_key == "AU_15" and odd.selection_key == "UST"))
        and odd.iddaa_odd < 1.42
    )
    if is_low_odd_trap_market:
        has_strong_edge = (model_market_edge >= 0.04) or (value_edge_pct is not None and value_edge_pct >= 3.0)
        if not has_strong_edge:
            low_odd_penalty = 5.5 if model_market_edge < 0.02 else 3.0

    # Extra brake for anti-market AU_35:ALT / AU_25:ALT picks when global odds say iddaa is below market
    if (
        odd.market_key in ("AU_35", "AU_25")
        and odd.selection_key == "ALT"
        and value_edge_pct is not None
        and value_edge_pct < -4.0
    ):
        low_odd_penalty += 3.5

    # Value weight boost for balanced core markets (MS, KG, AU_25, CS) with genuine positive edge
    if (
        not conflict_flag
        and odd.market_key in ("MS", "KG", "AU_25", "CS")
        and odd.iddaa_odd >= 1.40
        and (model_market_edge >= 0.02 or (value_edge_pct is not None and value_edge_pct >= 0.5))
    ):
        edge_pts = min(16.5, edge_pts + 2.0)

    total_val = max(4.0, min(35.0, prob_pts + edge_pts - low_odd_penalty))
    return round(total_val, 1), round(true_prob, 4), value_edge_pct, poisson_edge_pct, conflict_flag


def _compute_smart_money_and_trap(
    odd: OddsCurrent,
    all_odds_map: dict[tuple[str, str], OddsCurrent],
    hist_by_key: dict[tuple[str, str], list[OddsHistory]],
    now: datetime,
) -> dict[str, Any]:
    """
    Measure opening drop, 3h velocity, cross-market confirmation, and trap movement (>=5% odd rise).
    """
    drop_pct = ((odd.opening_odd - odd.iddaa_odd) / odd.opening_odd) * 100.0 if odd.opening_odd > 1.0 else 0.0

    # 3-hour velocity (only when >= 2 history snapshots exist)
    vel_3h_pct = 0.0
    h_list = hist_by_key.get((odd.market_key, odd.selection_key)) or []
    if len(h_list) >= 2:
        cutoff_3h = now - timedelta(hours=3)
        ref_odd = None
        for h in h_list:
            rec = h.recorded_at.replace(tzinfo=timezone.utc) if h.recorded_at.tzinfo is None else h.recorded_at
            if rec <= cutoff_3h:
                ref_odd = h.iddaa_odd
            elif ref_odd is None:
                ref_odd = h.iddaa_odd
                break
        if ref_odd and ref_odd > 1.0:
            vel_3h_pct = ((ref_odd - odd.iddaa_odd) / ref_odd) * 100.0

    # Cross-market confirmation
    confirm_pairs = {
        ("MS", "1"): [("CS", "1X"), ("AU_15", "UST")],
        ("MS", "2"): [("CS", "X2"), ("AU_15", "UST")],
        ("AU_25", "UST"): [("KG", "VAR"), ("AU_15", "UST")],
        ("KG", "VAR"): [("AU_25", "UST"), ("AU_15", "UST")],
    }
    smart_money = False
    if drop_pct >= 1.5 or vel_3h_pct >= 1.5:
        for ck in confirm_pairs.get((odd.market_key, odd.selection_key), []):
            co = all_odds_map.get(ck)
            if co and co.opening_odd > 1.0:
                c_drop = ((co.opening_odd - co.iddaa_odd) / co.opening_odd) * 100.0
                if c_drop >= 1.0:
                    smart_money = True
                    break

    trap_warning = False
    trap_reason = ""
    if drop_pct <= -5.0 or vel_3h_pct <= -5.0:
        trap_warning = True
        trap_reason = "Oranda %5+ tersine yükseliş (Tuzak Şüphesi)"

    # Neutral baseline when odds_history is empty (day-1 on fresh DB) and no real drop/rise yet
    if len(h_list) == 0 and abs(drop_pct) < 2.0:
        score = 14.5
    elif drop_pct >= 7.0:
        score = 25.0
    elif drop_pct >= 4.0:
        score = 21.0 + min(4.0, (drop_pct - 4.0) * 1.2)
    elif drop_pct > 0.2:
        score = 14.0 + drop_pct * 1.8
    elif drop_pct <= -6.0:
        score = 4.0
    elif drop_pct < -0.2:
        score = max(5.0, 13.5 + drop_pct * 1.5)
    else:
        score = 14.5

    if smart_money:
        score = min(25.0, score + 4.0)

    return {
        "score": round(score, 1),
        "drop_pct": round(drop_pct, 2),
        "vel_3h_pct": round(vel_3h_pct, 2),
        "smart_money": smart_money,
        "trap_warning": trap_warning,
        "trap_reason": trap_reason,
    }


def _compute_form_goals_component(
    market_key: str,
    selection_key: str,
    ctx: Optional[MatchContext],
    blended_prob: float,
    conflict_flag: bool,
) -> float:
    if not ctx or (not ctx.home_form and not ctx.away_form and ctx.home_goals_avg is None):
        return 10.0

    hf = json.loads(ctx.home_form) if ctx.home_form else []
    af = json.loads(ctx.away_form) if ctx.away_form else []
    hp = _form_points(hf)
    ap = _form_points(af)

    if (market_key == "MS" and selection_key == "1") or (market_key == "CS" and selection_key == "1X"):
        form_pts = max(2.0, min(12.0, 6.5 + (hp - ap) * 0.6))
    elif (market_key == "MS" and selection_key == "2") or (market_key == "CS" and selection_key == "X2"):
        form_pts = max(2.0, min(12.0, 6.5 + (ap - hp) * 0.6))
    else:
        form_pts = 7.5

    model_pts = min(13.0, 4.0 + blended_prob * 11.5)
    total = form_pts + model_pts
    if conflict_flag:
        total = max(6.0, total - 2.0)

    return round(min(25.0, total), 1)


def _compute_injury_component(
    market_key: str,
    selection_key: str,
    ctx: Optional[MatchContext],
    data_status: str = "",
) -> tuple[float, bool]:
    if not ctx or not ctx.injuries_json or "tm_injuries" not in (data_status or ""):
        return 12.0, False

    inj = json.loads(ctx.injuries_json)
    h_list = inj.get("home") or []
    a_list = inj.get("away") or []
    h_cnt = sum(_fresh_injury_weight(item) for item in h_list)
    a_cnt = sum(_fresh_injury_weight(item) for item in a_list)
    if h_cnt == 0 and a_cnt == 0:
        return 12.0, False

    warning = h_cnt >= 2.5 or a_cnt >= 2.5
    score = 12.0

    backing_home = (market_key == "MS" and selection_key == "1") or (market_key == "CS" and selection_key == "1X")
    backing_away = (market_key == "MS" and selection_key == "2") or (market_key == "CS" and selection_key == "X2")

    if backing_home:
        if h_cnt >= 4:
            score -= min(9.0, 4.0 + (h_cnt - 3) * 1.3)
        elif h_cnt > 0:
            score -= min(4.0, h_cnt * 1.1)
    elif backing_away:
        if a_cnt >= 4:
            score -= min(9.0, 4.0 + (a_cnt - 3) * 1.3)
        elif a_cnt > 0:
            score -= min(4.0, a_cnt * 1.1)
    elif market_key == "KG" and selection_key == "VAR":
        if h_cnt >= 4 or a_cnt >= 4:
            score -= min(6.5, max(h_cnt, a_cnt) * 1.0)
        else:
            score -= min(3.5, (h_cnt + a_cnt) * 0.4)
    else:
        score -= min(4.5, (h_cnt + a_cnt) * 0.45)

    return round(max(0.0, score), 1), warning


def _quarter_kelly_units(
    odd_val: float,
    blended_prob: float,
    confidence: float = 55.0,
    trap_warning: bool = False,
    global_odd: Optional[float] = None,
) -> float:
    """
    Motor v3.1 Margin-Adjusted Kelly + Confidence Score hybrid unit scale (1.0 - 5.0).
    Normalizes iddaa's ~14% overround to market fair scale so realistic unit
    distributions emerge instead of clamping to 1.0.
    """
    if trap_warning:
        return 1.0

    # Confidence base curve:
    # 60-65 -> ~1.5 - 2.1 | 66-72 -> ~2.3 - 3.3 | 73-80+ -> ~3.3 - 4.3
    if confidence < 55.0:
        base_units = 1.0
    elif confidence < 60.0:
        base_units = 1.0 + (confidence - 55.0) * 0.10
    elif confidence < 66.0:
        base_units = 1.5 + (confidence - 60.0) * (0.60 / 6.0)
    elif confidence < 73.0:
        base_units = 2.3 + (confidence - 66.0) * (1.00 / 7.0)
    else:
        base_units = 3.3 + min(1.0, (confidence - 73.0) * 0.10)

    # Real payout b = odd_val - 1.0 (no artificial 1.14 inflation)
    b = odd_val - 1.0
    kelly_boost = 0.0
    if b > 0:
        raw_k = (b * blended_prob - (1.0 - blended_prob)) / b
        q_kelly = 0.25 * max(0.0, raw_k)
        kelly_boost = min(0.90, q_kelly * 14.0)

    if global_odd and global_odd > 1.0:
        ratio = odd_val / global_odd
        if ratio >= 1.0:
            kelly_boost += 0.30
        elif ratio >= 0.96:
            kelly_boost += 0.15

    return round(max(1.0, min(5.0, base_units + kelly_boost)), 1)


def analyze_upcoming_matches(db: Session) -> int:
    """
    Motor v3.0: Dixon-Coles Poisson, fatigue, position/value-weighted injury xG,
    smart money velocity, trap movement lock, Quarter-Kelly units, and MIN_ODD_RULE (1.30).
    """
    now = datetime.now(timezone.utc)
    kickoff_lock = now + timedelta(minutes=5)
    matches = db.query(Match).filter(Match.status == "SCHEDULED").all()
    updated_count = 0

    for match in matches:
        m_start = (
            match.start_time_utc.replace(tzinfo=timezone.utc)
            if match.start_time_utc.tzinfo is None
            else match.start_time_utc
        )
        if m_start <= kickoff_lock:
            continue
        odds_rows = db.query(OddsCurrent).filter(OddsCurrent.match_id == match.id).all()
        if not odds_rows:
            continue

        by_market: dict[str, list[OddsCurrent]] = defaultdict(list)
        all_odds_map: dict[tuple[str, str], OddsCurrent] = {}
        for o in odds_rows:
            by_market[o.market_key].append(o)
            all_odds_map[(o.market_key, o.selection_key)] = o

        hist_rows = (
            db.query(OddsHistory)
            .filter(OddsHistory.match_id == match.id)
            .order_by(OddsHistory.recorded_at.asc())
            .all()
        )
        hist_by_key: dict[tuple[str, str], list[OddsHistory]] = defaultdict(list)
        for h in hist_rows:
            hist_by_key[(h.market_key, h.selection_key)].append(h)

        ctx = db.query(MatchContext).filter(MatchContext.match_id == match.id).first()
        dc = _compute_dixon_coles_probs(ctx, match.data_status or "")
        poisson_map = dc["probs"]
        has_form = bool(ctx and (ctx.home_form or ctx.away_form))

        for odd in odds_rows:
            if odd.iddaa_odd < MIN_ODD_RULE:
                db.query(Prediction).filter(
                    Prediction.match_id == match.id,
                    Prediction.market_key == odd.market_key,
                    Prediction.selection_key == odd.selection_key,
                    Prediction.status == "PENDING",
                    ~Prediction.id.in_(select(CouponItem.prediction_id)),
                ).delete(synchronize_session=False)
                continue

            p_prob = poisson_map.get((odd.market_key, odd.selection_key)) if poisson_map else None
            # Compute de-vigged market probability first for Bayesian calibration
            devigged_probs = _devig(odd.market_key, all_odds_map)
            mkt_true_prob = devigged_probs.get(odd.selection_key)
            if mkt_true_prob is None:
                ref = odd.global_avg_odd if (odd.global_avg_odd and odd.global_avg_odd > 1.0) else odd.iddaa_odd
                mkt_true_prob = min(0.95, 1.0 / ref) if ref and ref > 1.0 else 0.5

            sparse = _is_sparse_data(ctx, odd)
            calibrated_prob = _calibrate_market_prob(odd.market_key, mkt_true_prob, p_prob, sparse)

            val_s, true_prob, edge_pct, p_edge, conflict_flag = _compute_value_component(
                odd,
                all_odds_map,
                calibrated_prob if p_prob is not None else None,
                true_prob=mkt_true_prob,
            )
            blended_prob = calibrated_prob

            sm = _compute_smart_money_and_trap(odd, all_odds_map, hist_by_key, now)
            form_s = _compute_form_goals_component(
                odd.market_key, odd.selection_key, ctx, blended_prob, conflict_flag
            )
            inj_s, inj_warn = _compute_injury_component(
                odd.market_key, odd.selection_key, ctx, match.data_status or ""
            )

            total_score = val_s + sm["score"] + form_s + inj_s
            if sm["trap_warning"]:
                total_score -= 10.0

            # #1 Pick Realism Rule: high-risk picks (MS X, 1.5 Alt, prob < 42% / odd > 2.35)
            # without multi-signal confirmation (strong global edge >= 3% + odds drop >= 3%)
            high_risk = _is_high_risk_pick(odd.market_key, odd.selection_key, true_prob, odd.iddaa_odd)
            multi_signal = (
                edge_pct is not None and edge_pct >= 3.0
            ) and (sm["drop_pct"] >= 3.0 or sm["vel_3h_pct"] >= 3.0)
            unconfirmed_high_risk = high_risk and not multi_signal
            if unconfirmed_high_risk:
                total_score -= 8.0

            # Controlled ClubElo confidence nudge (max +/- 3.5 pts)
            elo_diff = None
            if ctx and ctx.home_elo is not None and ctx.away_elo is not None:
                elo_diff = round((ctx.home_elo + 55.0) - ctx.away_elo, 1)
                elo_nudge = max(-3.5, min(3.5, elo_diff / 80.0))
                if (odd.market_key == "MS" and odd.selection_key == "1") or (odd.market_key == "CS" and odd.selection_key == "1X"):
                    total_score += elo_nudge
                elif (odd.market_key == "MS" and odd.selection_key == "2") or (odd.market_key == "CS" and odd.selection_key == "X2"):
                    total_score -= elo_nudge

            # Motor v5.0: Goal stability / variance adjustment on AU_* and KG markets
            goal_std_adj = 0.0
            std_kelly_mult = 1.0
            avg_goal_std: Optional[float] = None
            if ctx and (ctx.home_goal_std is not None or ctx.away_goal_std is not None):
                stds = [s for s in (ctx.home_goal_std, ctx.away_goal_std) if s is not None]
                avg_goal_std = round(sum(stds) / len(stds), 3)
                if odd.market_key.startswith("AU_") or odd.market_key == "KG":
                    if avg_goal_std >= 1.35:
                        goal_std_adj = round(-min(6.0, 2.5 + (avg_goal_std - 1.35) * 7.0), 1)
                        std_kelly_mult = 0.85
                    elif avg_goal_std <= 0.85:
                        goal_std_adj = 1.5
                    total_score += goal_std_adj

            total_score = min(99.0, max(10.0, total_score))

            if true_prob < 0.35:
                total_score = min(total_score, 54.5)
            if unconfirmed_high_risk:
                total_score = min(total_score, 57.5)
            if odd.iddaa_odd >= MAX_HIGHLIGHT_ODD:
                total_score = min(total_score, 48.0)
            if not has_form and odd.global_avg_odd is None:
                total_score = min(total_score, 52.0)

            total_score = round(total_score, 1)
            kelly = _quarter_kelly_units(
                odd.iddaa_odd,
                blended_prob,
                confidence=total_score,
                trap_warning=bool(sm["trap_warning"]),
                global_odd=odd.global_avg_odd,
            )
            if std_kelly_mult < 1.0 and not sm["trap_warning"]:
                kelly = round(max(1.0, kelly * std_kelly_mult), 1)

            breakdown = {
                "value": val_s,
                "odds_move": sm["score"],
                "form_goals": form_s,
                "injury": inj_s,
                "true_prob_pct": round(true_prob * 100.0, 1),
                "poisson_prob_pct": round(p_prob * 100.0, 1) if p_prob is not None else None,
                "blended_prob_pct": round(blended_prob * 100.0, 1),
                "poisson_edge_pct": p_edge,
                "raw_xg_home": dc["raw_xg_h"],
                "raw_xg_away": dc["raw_xg_a"],
                "xg_home": dc["adj_xg_h"],
                "xg_away": dc["adj_xg_a"],
                "xg_source": ctx.xg_source if ctx else None,
                "home_goal_std": ctx.home_goal_std if ctx else None,
                "away_goal_std": ctx.away_goal_std if ctx else None,
                "goal_std_adj": goal_std_adj,
                "home_rest_days": ctx.home_rest_days if ctx else None,
                "away_rest_days": ctx.away_rest_days if ctx else None,
                "home_elo": ctx.home_elo if ctx else None,
                "away_elo": ctx.away_elo if ctx else None,
                "elo_diff": elo_diff,
                "home_rank": ctx.home_rank if ctx else None,
                "away_rank": ctx.away_rank if ctx else None,
                "global_source": odd.global_source,
                "vel_3h_pct": sm["vel_3h_pct"],
                "smart_money": sm["smart_money"],
                "trap_warning": sm["trap_warning"],
                "trap_reason": sm["trap_reason"],
                "kelly_units": kelly,
                "injury_warning": inj_warn,
            }

            pred = (
                db.query(Prediction)
                .filter(
                    Prediction.match_id == match.id,
                    Prediction.market_key == odd.market_key,
                    Prediction.selection_key == odd.selection_key,
                )
                .first()
            )
            if not pred:
                pred = Prediction(
                    match_id=match.id,
                    market_key=odd.market_key,
                    selection_key=odd.selection_key,
                    iddaa_odd=odd.iddaa_odd,
                    global_odd=odd.global_avg_odd,
                    confidence_score=total_score,
                    score_breakdown_json=json.dumps(breakdown),
                    value_edge_pct=edge_pct,
                    odds_drop_pct=sm["drop_pct"],
                    kelly_units=kelly,
                    trap_warning=sm["trap_warning"],
                    status="PENDING",
                )
                db.add(pred)
                updated_count += 1
            elif pred.status == "PENDING":
                pred.iddaa_odd = odd.iddaa_odd
                pred.global_odd = odd.global_avg_odd
                pred.confidence_score = total_score
                pred.score_breakdown_json = json.dumps(breakdown)
                pred.value_edge_pct = edge_pct
                pred.odds_drop_pct = sm["drop_pct"]
                pred.kelly_units = kelly
                pred.trap_warning = sm["trap_warning"]
                updated_count += 1

        # Mark exactly one official #1 top pick per match (frozen once published or match starts/settles)
        db.flush()
        pending_for_match = (
            db.query(Prediction)
            .filter(Prediction.match_id == match.id, Prediction.status == "PENDING")
            .all()
        )
        if pending_for_match:
            m_start = (
                match.start_time_utc.replace(tzinfo=timezone.utc)
                if match.start_time_utc and match.start_time_utc.tzinfo is None
                else match.start_time_utc
            )
            is_pre_kickoff = bool(m_start and m_start > now)
            locked_pred = next(
                (p for p in pending_for_match if p.is_top_pick and p.published_at is not None),
                None,
            )
            if locked_pred is not None:
                best_pred = locked_pred
            else:
                eligible_top = [p for p in pending_for_match if p.iddaa_odd < MAX_HIGHLIGHT_ODD] or pending_for_match
                realistic_top = []
                for p in eligible_top:
                    bd = json.loads(p.score_breakdown_json) if p.score_breakdown_json else {}
                    t_prob = float(bd.get("true_prob_pct", 50.0)) / 100.0
                    hr = _is_high_risk_pick(p.market_key, p.selection_key, t_prob, p.iddaa_odd)
                    msig = (p.value_edge_pct is not None and p.value_edge_pct >= 3.0) and (
                        (p.odds_drop_pct is not None and p.odds_drop_pct >= 3.0)
                        or float(bd.get("vel_3h_pct") or 0.0) >= 3.0
                    )
                    if not (hr and not msig):
                        realistic_top.append(p)
                candidate_pool = realistic_top or eligible_top
                best_pred = max(
                    candidate_pool,
                    key=lambda p: (
                        p.confidence_score,
                        p.value_edge_pct if p.value_edge_pct is not None else -99.0,
                        p.iddaa_odd,
                    ),
                )

            for p in pending_for_match:
                is_best = (p.id == best_pred.id) if (p.id is not None and best_pred.id is not None) else (p is best_pred)
                p.is_top_pick = is_best
                if (
                    is_best
                    and p.published_at is None
                    and p.confidence_score >= 60.0
                    and MIN_ODD_RULE <= p.iddaa_odd < MAX_HIGHLIGHT_ODD
                    and is_pre_kickoff
                ):
                    bd_best = json.loads(p.score_breakdown_json) if p.score_breakdown_json else {}
                    pub_prob_pct = bd_best.get("blended_prob_pct") or bd_best.get("true_prob_pct")
                    p.published_odd = p.iddaa_odd
                    p.published_confidence = p.confidence_score
                    p.published_prob = round(float(pub_prob_pct) / 100.0, 4) if pub_prob_pct is not None else None
                    p.published_at = now

    db.commit()
    return updated_count
