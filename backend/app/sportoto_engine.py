"""Fikstra Toto advanced mathematical engine: de-vig, Shannon chaos, trap signal, exact Poisson-Binomial, DP & shield13/contrarian optimization."""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlalchemy.orm import Session, joinedload

from app.models import Match, Prediction, SportotoMatch, SportotoWeek

VALID_RESULTS = {"1", "X", "2"}
VALID_PICK_CODES = {"1", "X", "2", "1X", "12", "X2", "1X2"}
SPORTOTO_BUDGETS = (16, 32, 64)

_TR_TRANS = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
_STOP_TOKENS = {"fc", "sk", "fk", "jk", "as", "cf", "sc", "ac", "afc", "cfk", "spor", "kulubu"}


def devig_1x2(odd_1: float, odd_x: float, odd_2: float) -> tuple[float, float, float]:
    """Remove bookmaker overround from 1-X-2 odds and return probabilities summing to 1.0."""
    o1, ox, o2 = float(odd_1), float(odd_x), float(odd_2)
    if o1 <= 1.0 or ox <= 1.0 or o2 <= 1.0:
        raise ValueError("All 1-X-2 odds must be strictly greater than 1.0")
    q1, qx, q2 = 1.0 / o1, 1.0 / ox, 1.0 / o2
    total = q1 + qx + q2
    return (q1 / total, qx / total, q2 / total)


def canonical_pick_code(code: str) -> str:
    c = (code or "").strip().upper()
    ordered = "".join(ch for ch in ("1", "X", "2") if ch in c)
    if ordered not in VALID_PICK_CODES:
        raise ValueError(f"Geçersiz Toto seçimi: '{code}'")
    return ordered


def pick_prob_for_code(p1: float, px: float, p2: float, code: str) -> float:
    c = canonical_pick_code(code)
    total = 0.0
    if "1" in c:
        total += p1
    if "X" in c:
        total += px
    if "2" in c:
        total += p2
    return float(min(1.0, max(0.0, total)))


def compute_chaos_score(
    p1: float,
    px: float,
    p2: float,
    public_pcts: Optional[tuple[Optional[float], Optional[float], Optional[float]]] = None,
) -> dict[str, Any]:
    """Spread chaos score across 0-100 using (1 - p_top1) and optional public/model divergence."""
    max_p = max(float(p1), float(px), float(p2))
    base_score = min(85.0, max(0.0, ((1.0 - max_p) / 0.67) * 85.0))
    div_bonus = 0.0
    if public_pcts and all(v is not None and float(v) > 0.0 for v in public_pcts):
        raw_pub = [float(public_pcts[0]), float(public_pcts[1]), float(public_pcts[2])]  # type: ignore[arg-type]
        s = sum(raw_pub)
        if 95.0 <= s <= 105.0:
            pub_norm = [v / s for v in raw_pub]
            true_probs = [float(p1), float(px), float(p2)]
            max_div_pct = max(abs(pub_norm[i] - true_probs[i]) * 100.0 for i in range(3))
            div_bonus = min(15.0, max_div_pct * 0.6)
    score = round(min(100.0, max(0.0, base_score + div_bonus)), 1)
    if max_p >= 0.65:
        label = "Banko"
    elif max_p >= 0.50:
        label = "Favori"
    elif max_p >= 0.42:
        label = "Ortada"
    else:
        label = "Sürprize Açık"
    return {"score": score, "label": label}


def compute_trap_signal(
    probs: tuple[float, float, float],
    public_pcts: tuple[Optional[float], Optional[float], Optional[float]],
) -> Optional[dict[str, Any]]:
    """
    Compute pool leverage & trap favorite ONLY when all 3 real public percentages are present (>0, ~100 sum).
    Requires fragile favorite (<62% true prob), heavy public load (>=68%), and >=14pt gap.
    Returns None otherwise (zero fake data).
    """
    if any(v is None or float(v) <= 0.0 for v in public_pcts):
        return None
    raw = [float(public_pcts[0]), float(public_pcts[1]), float(public_pcts[2])]  # type: ignore[arg-type]
    s = sum(raw)
    if s < 95.0 or s > 105.0:
        return None
    pub = [v / s * 100.0 for v in raw]
    p1, px, p2 = probs
    true_pcts = [p1 * 100.0, px * 100.0, p2 * 100.0]
    leverages = [round(true_pcts[i] / pub[i], 2) for i in range(3)]
    labels = ("1", "X", "2")
    fav_idx = max(range(3), key=lambda i: pub[i])
    over_loaded = pub[fav_idx] - true_pcts[fav_idx]
    is_trap = (
        true_pcts[fav_idx] < 62.0
        and pub[fav_idx] >= 68.0
        and over_loaded >= 14.0
    )
    explanation = (
        f"Halkın %{pub[fav_idx]:.0f}'i {labels[fav_idx]} bekliyor ama gerçek ihtimal %{true_pcts[fav_idx]:.0f}."
        if is_trap
        else None
    )
    return {
        "is_trap": is_trap,
        "fav_pick": labels[fav_idx],
        "fav_public_pct": round(pub[fav_idx], 1),
        "fav_true_pct": round(true_pcts[fav_idx], 1),
        "leverage_1": leverages[0],
        "leverage_x": leverages[1],
        "leverage_2": leverages[2],
        "max_leverage": max(leverages),
        "explanation": explanation,
    }


def exact_poisson_binomial(pick_probs: list[float]) -> dict[str, float]:
    """Exact 15-step DP distribution for hit counts P(S=k)."""
    n = len(pick_probs)
    dist = [0.0] * (n + 1)
    dist[0] = 1.0
    for raw_p in pick_probs:
        p = min(1.0, max(0.0, float(raw_p)))
        q = 1.0 - p
        for k in range(n, 0, -1):
            dist[k] = dist[k] * q + dist[k - 1] * p
        dist[0] *= q

    p15 = dist[15] if n >= 15 else dist[n]
    p14_plus = sum(dist[k] for k in range(min(14, n), n + 1))
    p13_plus = sum(dist[k] for k in range(min(13, n), n + 1))
    p12_plus = sum(dist[k] for k in range(min(12, n), n + 1))
    expected_hits = sum(float(p) for p in pick_probs)
    return {
        "p15": round(p15 * 100.0, 4),
        "p14_plus": round(p14_plus * 100.0, 4),
        "p13_plus": round(p13_plus * 100.0, 4),
        "p12_plus": round(p12_plus * 100.0, 4),
        "expected_hits": round(expected_hits, 2),
    }


def compute_live_survival(matches: list[dict[str, Any]], picks: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Calculate live survival probabilities based on settled matches."""
    pick_by_slot = {int(p["slot_no"]): p for p in picks}
    settled_hits = 0
    settled_count = 0
    remaining_probs: list[float] = []

    for m in sorted(matches, key=lambda x: int(x["slot_no"])):
        slot = int(m["slot_no"])
        pk = pick_by_slot.get(slot)
        if not pk:
            continue
        actual = (m.get("actual_result") or "").strip().upper()
        if actual in VALID_RESULTS:
            settled_count += 1
            if actual in pk["pick_code"]:
                settled_hits += 1
        else:
            remaining_probs.append(float(pk["pick_prob"]))

    if settled_count == 0:
        return None

    misses = settled_count - settled_hits
    rem_n = len(remaining_probs)
    dist = [0.0] * (rem_n + 1)
    dist[0] = 1.0
    for raw_p in remaining_probs:
        p = min(1.0, max(0.0, raw_p))
        q = 1.0 - p
        for k in range(rem_n, 0, -1):
            dist[k] = dist[k] * q + dist[k - 1] * p
        dist[0] *= q

    def prob_at_least_total(target: int) -> float:
        needed = target - settled_hits
        if needed <= 0:
            return 100.0
        if needed > rem_n:
            return 0.0
        return round(sum(dist[k] for k in range(needed, rem_n + 1)) * 100.0, 4)

    return {
        "settled_count": settled_count,
        "settled_hits": settled_hits,
        "settled_misses": misses,
        "remaining_count": rem_n,
        "can_hit_15": misses == 0,
        "can_hit_14": misses <= 1,
        "can_hit_13": misses <= 2,
        "can_hit_12": misses <= 3,
        "p15_live": prob_at_least_total(15),
        "p14_live": prob_at_least_total(14),
        "p13_live": prob_at_least_total(13),
        "p12_live": prob_at_least_total(12),
    }


def _best_options_for_match(p1: float, px: float, p2: float) -> dict[int, tuple[str, float, str]]:
    singles = [("1", p1), ("X", px), ("2", p2)]
    bs = max(singles, key=lambda x: x[1])
    p1x, px2, p12 = p1 + px, px + p2, p1 + p2
    best_draw_double = ("1X", p1x) if p1x >= px2 else ("X2", px2)
    if p12 - best_draw_double[1] <= 0.015:
        bd = best_draw_double
    else:
        bd = ("12", p12)
    return {
        1: (bs[0], float(bs[1]), "single"),
        2: (bd[0], float(min(1.0, bd[1])), "double"),
        3: ("1X2", 1.0, "triple"),
    }


def compute_marginal_advice(matches: list[dict[str, Any]], picks: list[dict[str, Any]]) -> dict[str, Any]:
    """Identify most efficient 1->2 or 2->3 upgrade and least harmful 3->2 or 2->1 downgrade."""
    by_slot = {int(m["slot_no"]): m for m in matches}
    best_upgrade: Optional[dict[str, Any]] = None
    best_up_gain = -1.0
    safest_downgrade: Optional[dict[str, Any]] = None
    min_down_loss = 1e9

    for pk in picks:
        slot = int(pk["slot_no"])
        m = by_slot.get(slot)
        if not m or m.get("prob_1") is None:
            continue
        p1, px, p2 = float(m["prob_1"]), float(m["prob_x"]), float(m["prob_2"])
        opts = _best_options_for_match(p1, px, p2)
        k = len(pk["pick_code"])
        cur_p = float(pk["pick_prob"])

        if k < 3:
            nxt_k = k + 1
            to_code, to_p, _ = opts[nxt_k]
            gain = math.log(max(to_p, 1e-12)) - math.log(max(cur_p, 1e-12))
            if gain > best_up_gain:
                best_up_gain = gain
                best_upgrade = {
                    "slot_no": slot,
                    "home_team": m["home_team"],
                    "away_team": m["away_team"],
                    "from_code": pk["pick_code"],
                    "to_code": to_code,
                    "gain_prob_pct": round((to_p - cur_p) * 100.0, 1),
                    "cost_multiplier": f"{nxt_k}/{k}x",
                }

        if k > 1:
            prv_k = k - 1
            to_code, to_p, _ = opts[prv_k]
            loss = math.log(max(cur_p, 1e-12)) - math.log(max(to_p, 1e-12))
            if loss < min_down_loss:
                min_down_loss = loss
                safest_downgrade = {
                    "slot_no": slot,
                    "home_team": m["home_team"],
                    "away_team": m["away_team"],
                    "from_code": pk["pick_code"],
                    "to_code": to_code,
                    "loss_prob_pct": round((cur_p - to_p) * 100.0, 1),
                }

    return {"best_upgrade": best_upgrade, "safest_downgrade": safest_downgrade}


def optimize_toto(
    matches: list[dict[str, Any]],
    budget: int,
    mode: str = "hit15",
    locks: Optional[dict[int, str]] = None,
    column_price: float = 10.0,
) -> dict[str, Any]:
    """
    Optimize 15-match Toto picks under budget & lock constraints.
    Modes:
      - hit15: Exact DP global optimum for sum(ln P)
      - shield13: Exact DP init + deterministic hill-climb maximizing exact Poisson-Binomial p13_plus
      - contrarian: Exact DP prioritizing trap favorites (if real public % exist) or high-chaos matches
    """
    if budget < 1 or budget > 1024:
        raise ValueError("Bütçe 1 ile 1024 kolon arasında olmalıdır")
    if mode not in {"hit15", "shield13", "contrarian"}:
        raise ValueError("Geçersiz strateji modu")
    if not matches:
        raise ValueError("Maç listesi boş olamaz")

    ordered = sorted(matches, key=lambda m: int(m["slot_no"]))
    clean_locks: dict[int, str] = {}
    if locks:
        for k_slot, v_code in locks.items():
            clean_locks[int(k_slot)] = canonical_pick_code(v_code)

    locked_cols = 1
    per_match_choices: list[list[tuple[int, str, float, float, str]]] = []

    for m in ordered:
        slot = int(m["slot_no"])
        if m.get("prob_1") is None or m.get("prob_x") is None or m.get("prob_2") is None:
            raise ValueError(f"{slot}. maçta olasılık verisi eksik")
        p1, px, p2 = float(m["prob_1"]), float(m["prob_x"]), float(m["prob_2"])
        opts = _best_options_for_match(p1, px, p2)

        if slot in clean_locks:
            lcode = clean_locks[slot]
            lk = len(lcode)
            locked_cols *= lk
            lprob = pick_prob_for_code(p1, px, p2, lcode)
            lkind = "single" if lk == 1 else ("double" if lk == 2 else "triple")
            per_match_choices.append([(lk, lcode, lprob, math.log(max(lprob, 1e-12)), lkind)])
        else:
            chaos = compute_chaos_score(
                p1,
                px,
                p2,
                public_pcts=(m.get("public_pct_1"), m.get("public_pct_x"), m.get("public_pct_2")),
            )["score"]
            trap = compute_trap_signal(
                (p1, px, p2),
                (m.get("public_pct_1"), m.get("public_pct_x"), m.get("public_pct_2")),
            )
            row_opts: list[tuple[int, str, float, float, str]] = []
            for k in (1, 2, 3):
                code, prob, kind = opts[k]
                score = math.log(max(prob, 1e-12))
                if mode == "contrarian" and k > 1:
                    if trap and trap["is_trap"]:
                        score += 0.35 * (k - 1)
                    elif trap and trap["max_leverage"] >= 1.25:
                        score += 0.22 * (k - 1)
                    else:
                        score += (chaos / 100.0) * 0.20 * (k - 1)
                row_opts.append((k, code, prob, score, kind))
            per_match_choices.append(row_opts)

    if locked_cols > budget:
        raise ValueError(
            f"Kilitli maçların kolon çarpımı ({locked_cols}) seçilen bütçeyi ({budget}) aşıyor"
        )

    def _solve_dp_for_budget(target_budget: int) -> tuple[int, list[tuple[int, str, float, str]]]:
        dp: dict[int, tuple[float, tuple[tuple[int, str, float, str], ...]]] = {1: (0.0, ())}
        for choices in per_match_choices:
            next_dp: dict[int, tuple[float, tuple[tuple[int, str, float, str], ...]]] = {}
            for cols, (sc_sum, path) in dp.items():
                for k, code, prob, score, kind in choices:
                    nc = cols * k
                    if nc > target_budget:
                        continue
                    new_sc = sc_sum + score
                    prev = next_dp.get(nc)
                    if prev is None or new_sc > prev[0] + 1e-12:
                        next_dp[nc] = (new_sc, path + ((k, code, prob, kind),))
            dp = next_dp
        if not dp:
            raise ValueError("Seçilen bütçe için geçerli kolon dağılımı bulunamadı")
        b_cols, (_, b_path) = max(dp.items(), key=lambda item: (round(item[1][0], 11), -item[0]))
        return b_cols, list(b_path)

    def _metrics_for_path(path_list: list[tuple[int, str, float, str]]) -> dict[str, float]:
        return exact_poisson_binomial([x[2] for x in path_list])

    def _is_monotone_ge(cand_m: dict[str, float], base_m: dict[str, float]) -> bool:
        return (
            cand_m["expected_hits"] >= base_m["expected_hits"] - 1e-9
            and cand_m["p15"] >= base_m["p15"] - 1e-9
            and cand_m["p14_plus"] >= base_m["p14_plus"] - 1e-9
            and cand_m["p13_plus"] >= base_m["p13_plus"] - 1e-9
        )

    def _refine_shield13(
        init_cols: int,
        init_path: list[tuple[int, str, float, str]],
        target_budget: int,
        floor_metrics: Optional[dict[str, float]] = None,
    ) -> tuple[int, list[tuple[int, str, float, str]]]:
        cur_chosen = list(init_path)
        cur_cols = init_cols
        cur_pb = _metrics_for_path(cur_chosen)
        cur_score = (cur_pb["p13_plus"], cur_pb["p14_plus"])
        improved = True
        while improved:
            improved = False
            for i in range(len(ordered)):
                slot_i = int(ordered[i]["slot_no"])
                if slot_i in clean_locks:
                    continue
                ki, _, _, _ = cur_chosen[i]
                for k_new, c_new, p_new, _, kind_new in per_match_choices[i]:
                    if k_new == ki:
                        continue
                    new_cols = (cur_cols // ki) * k_new
                    if new_cols <= target_budget:
                        cand = list(cur_chosen)
                        cand[i] = (k_new, c_new, p_new, kind_new)
                        cand_pb = _metrics_for_path(cand)
                        cand_sc = (cand_pb["p13_plus"], cand_pb["p14_plus"])
                        if cand_sc > cur_score and (floor_metrics is None or _is_monotone_ge(cand_pb, floor_metrics)):
                            cur_chosen = cand
                            cur_cols = new_cols
                            cur_score = cand_sc
                            improved = True
                            break
                if improved:
                    break
                if ki > 1:
                    for k_down, c_down, p_down, _, kind_down in per_match_choices[i]:
                        if k_down >= ki:
                            continue
                        for j in range(len(ordered)):
                            if i == j or int(ordered[j]["slot_no"]) in clean_locks:
                                continue
                            kj = cur_chosen[j][0]
                            for k_up, c_up, p_up, _, kind_up in per_match_choices[j]:
                                if k_up <= kj:
                                    continue
                                new_cols = (cur_cols // (ki * kj)) * (k_down * k_up)
                                if new_cols <= target_budget:
                                    cand = list(cur_chosen)
                                    cand[i] = (k_down, c_down, p_down, kind_down)
                                    cand[j] = (k_up, c_up, p_up, kind_up)
                                    cand_pb = _metrics_for_path(cand)
                                    cand_sc = (cand_pb["p13_plus"], cand_pb["p14_plus"])
                                    if cand_sc > cur_score and (
                                        floor_metrics is None or _is_monotone_ge(cand_pb, floor_metrics)
                                    ):
                                        cur_chosen = cand
                                        cur_cols = new_cols
                                        cur_score = cand_sc
                                        improved = True
                                        break
                            if improved:
                                break
                        if improved:
                            break
                    if improved:
                        break
        return cur_cols, cur_chosen

    def _upgrade_from_prev(
        prev_cols: int,
        prev_path: list[tuple[int, str, float, str]],
        target_budget: int,
    ) -> tuple[int, list[tuple[int, str, float, str]]]:
        cur_chosen = list(prev_path)
        cur_cols = prev_cols
        while True:
            best_gain = -1e18
            best_move: Optional[tuple[int, int, tuple[int, str, float, str]]] = None
            for i in range(len(ordered)):
                if int(ordered[i]["slot_no"]) in clean_locks:
                    continue
                ki, _, cur_p, _ = cur_chosen[i]
                cur_sc = next((sc for k, _, _, sc, _ in per_match_choices[i] if k == ki), math.log(max(cur_p, 1e-12)))
                for k_new, c_new, p_new, sc_new, kind_new in per_match_choices[i]:
                    if k_new <= ki:
                        continue
                    new_cols = (cur_cols // ki) * k_new
                    if new_cols <= target_budget:
                        gain = (sc_new - cur_sc) / max(1, new_cols - cur_cols)
                        if gain > best_gain:
                            best_gain = gain
                            best_move = (i, new_cols, (k_new, c_new, p_new, kind_new))
            if best_move is None:
                break
            idx_m, nc_m, tpl_m = best_move
            cur_chosen[idx_m] = tpl_m
            cur_cols = nc_m
        return cur_cols, cur_chosen

    # Enforce strict monotonicity across standard tiers (16 -> 32 -> 64 -> 128 -> 256) up to budget
    tier_chain = [b for b in (16, 32, 64, 128, 256) if locked_cols <= b < budget] + [budget]
    prev_cols: Optional[int] = None
    prev_chosen: Optional[list[tuple[int, str, float, str]]] = None
    prev_metrics: Optional[dict[str, float]] = None

    for tier_b in tier_chain:
        b_cols, b_chosen = _solve_dp_for_budget(tier_b)
        if mode == "shield13":
            b_cols, b_chosen = _refine_shield13(b_cols, b_chosen, tier_b, floor_metrics=prev_metrics)
        b_metrics = _metrics_for_path(b_chosen)

        if prev_metrics is not None and prev_chosen is not None and prev_cols is not None:
            if not _is_monotone_ge(b_metrics, prev_metrics):
                up_cols, up_chosen = _upgrade_from_prev(prev_cols, prev_chosen, tier_b)
                if mode == "shield13":
                    up_cols, up_chosen = _refine_shield13(up_cols, up_chosen, tier_b, floor_metrics=prev_metrics)
                b_cols, b_chosen = up_cols, up_chosen
                b_metrics = _metrics_for_path(b_chosen)

        prev_cols, prev_chosen, prev_metrics = b_cols, b_chosen, b_metrics

    assert prev_cols is not None and prev_chosen is not None
    best_cols = prev_cols
    chosen = prev_chosen

    picks: list[dict[str, Any]] = []
    single_count = double_count = triple_count = 0
    settled_count = hits = 0
    pick_probs: list[float] = []

    for m, (k, code, prob, kind) in zip(ordered, chosen):
        if k == 1:
            single_count += 1
        elif k == 2:
            double_count += 1
        else:
            triple_count += 1
        pick_probs.append(prob)

        actual = (m.get("actual_result") or "").strip().upper() or None
        hit: Optional[bool] = None
        if actual in VALID_RESULTS:
            settled_count += 1
            hit = actual in code
            if hit:
                hits += 1

        picks.append(
            {
                "slot_no": int(m["slot_no"]),
                "pick_code": code,
                "pick_prob": round(prob, 4),
                "kind": kind,
                "locked": int(m["slot_no"]) in clean_locks,
                "hit": hit,
            }
        )

    probs_dist = exact_poisson_binomial(pick_probs)
    advice = compute_marginal_advice(ordered, picks)
    survival = compute_live_survival(ordered, picks)

    return {
        "budget": int(budget),
        "mode": mode,
        "used_columns": int(best_cols),
        "estimated_cost": round(float(best_cols) * float(column_price), 2),
        "single_count": single_count,
        "double_count": double_count,
        "triple_count": triple_count,
        "expected_hits": probs_dist["expected_hits"],
        "full_hit_prob_pct": probs_dist["p15"],
        "probs": probs_dist,
        "settled_count": settled_count,
        "hits": hits if settled_count > 0 else None,
        "picks": picks,
        "advice": advice,
        "survival": survival,
    }


def optimize_sportoto_columns(matches: list[dict[str, Any]], max_columns: int) -> dict[str, Any]:
    """Backward-compatible wrapper for default hit15 DP optimization."""
    return optimize_toto(matches, budget=max_columns, mode="hit15")


def normalize_team_name(name: str) -> str:
    txt = (name or "").translate(_TR_TRANS).lower()
    txt = re.sub(r"[^a-z0-9\s]", " ", txt)
    tokens = [t for t in txt.split() if t and t not in _STOP_TOKENS]
    return " ".join(tokens)


def _team_similarity(a: str, b: str) -> float:
    na = normalize_team_name(a)
    nb = normalize_team_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.92
    return SequenceMatcher(None, na, nb).ratio()


def parse_sportoto_raw_text(raw_text: str) -> list[dict[str, Any]]:
    """
    Parse 15 lines of:
      'Home - Away'
      'Home - Away | 1.85 3.40 4.10'
      'Home - Away | 1.85 3.40 4.10 | 68 18 14'
    """
    lines = [ln.strip() for ln in (raw_text or "").splitlines() if ln.strip()]
    if len(lines) != 15:
        raise ValueError(f"Tam 15 maç satırı girilmelidir (şu an {len(lines)} satır var)")

    parsed: list[dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        clean = re.sub(r"^\d{1,2}\s*[\.\)\-:]\s*", "", line).strip()
        segments = [seg.strip() for seg in clean.split("|")]
        teams_part = segments[0]
        odd_1 = odd_x = odd_2 = None
        pub_1 = pub_x = pub_2 = None

        if len(segments) >= 2 and segments[1]:
            odds_nums = re.findall(r"\d+(?:[.,]\d+)?", segments[1])
            if len(odds_nums) >= 3:
                o1, ox, o2 = (float(x.replace(",", ".")) for x in odds_nums[:3])
                if o1 > 1.0 and ox > 1.0 and o2 > 1.0:
                    odd_1, odd_x, odd_2 = o1, ox, o2

        if len(segments) >= 3 and segments[2]:
            pub_nums = re.findall(r"\d+(?:[.,]\d+)?", segments[2])
            if len(pub_nums) >= 3:
                p1, px, p2 = (float(x.replace(",", ".")) for x in pub_nums[:3])
                if p1 > 0 and px > 0 and p2 > 0 and 95.0 <= (p1 + px + p2) <= 105.0:
                    pub_1, pub_x, pub_2 = p1, px, p2

        parts = re.split(r"\s+[-–—]\s+|\s+vs\.?\s+", teams_part.strip(), maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise ValueError(f"{idx}. satır 'Ev - Deplasman' formatında değil: {line}")

        parsed.append(
            {
                "slot_no": idx,
                "home_team": parts[0].strip()[:128],
                "away_team": parts[1].strip()[:128],
                "odd_1": odd_1,
                "odd_x": odd_x,
                "odd_2": odd_2,
                "public_pct_1": pub_1,
                "public_pct_x": pub_x,
                "public_pct_2": pub_2,
            }
        )
    return parsed


def _extract_ms_probs_from_match(match: Match) -> tuple[Optional[tuple[float, float, float]], str, Optional[int]]:
    preds = [p for p in (match.predictions or []) if (p.market_key or "").upper() == "MS"]
    top_pred = next((p for p in (match.predictions or []) if p.is_top_pick), None)
    ms_by_sel: dict[str, Prediction] = {(p.selection_key or "").upper(): p for p in preds}
    ref_pred_id = top_pred.id if top_pred else (preds[0].id if preds else None)

    if all(k in ms_by_sel for k in ("1", "X", "2")):
        raw_probs: list[float] = []
        for k in ("1", "X", "2"):
            pr = ms_by_sel[k]
            val: Optional[float] = None
            if pr.score_breakdown_json:
                try:
                    bd = json.loads(pr.score_breakdown_json)
                    bp = bd.get("blended_prob_pct")
                    tp = bd.get("true_prob_pct")
                    if isinstance(bp, (int, float)) and bp > 0:
                        val = float(bp) / 100.0
                    elif isinstance(tp, (int, float)) and tp > 0:
                        val = float(tp) / 100.0
                except Exception:
                    pass
            if val is None and pr.published_prob and pr.published_prob > 0:
                val = float(pr.published_prob)
            if val is None or val <= 0:
                raw_probs = []
                break
            raw_probs.append(val)
        if len(raw_probs) == 3:
            s = sum(raw_probs)
            if s > 0:
                return (raw_probs[0] / s, raw_probs[1] / s, raw_probs[2] / s), "fikstra_model", ref_pred_id

    odds_ms = {
        (o.selection_key or "").upper(): o
        for o in (match.odds_current or [])
        if (o.market_key or "").upper() == "MS"
    }
    if all(k in odds_ms for k in ("1", "X", "2")):
        try:
            o1 = odds_ms["1"].global_avg_odd or odds_ms["1"].iddaa_odd
            ox = odds_ms["X"].global_avg_odd or odds_ms["X"].iddaa_odd
            o2 = odds_ms["2"].global_avg_odd or odds_ms["2"].iddaa_odd
            return devig_1x2(o1, ox, o2), "market_devig", ref_pred_id
        except ValueError:
            pass

    return None, "missing", ref_pred_id


def match_and_create_sportoto_week(
    db: Session,
    title: str,
    raw_text: str,
    column_price: float = 10.0,
) -> SportotoWeek:
    parsed_lines = parse_sportoto_raw_text(raw_text)
    now_utc = datetime.now(timezone.utc)
    candidates = (
        db.query(Match)
        .options(joinedload(Match.predictions), joinedload(Match.odds_current))
        .filter(Match.start_time_utc >= now_utc - timedelta(days=2))
        .all()
    )

    week = SportotoWeek(
        title=title.strip()[:120] or "Fikstra Toto Haftası",
        status="DRAFT",
        column_price=max(1.0, float(column_price or 10.0)),
    )
    db.add(week)
    db.flush()

    for item in parsed_lines:
        best_match: Optional[Match] = None
        best_score = 0.0
        for cand in candidates:
            sh = _team_similarity(item["home_team"], cand.home_team)
            sa = _team_similarity(item["away_team"], cand.away_team)
            if sh >= 0.72 and sa >= 0.72:
                combined = (sh + sa) / 2.0
                if combined > best_score:
                    best_score = combined
                    best_match = cand

        prob_1 = prob_x = prob_2 = None
        odd_1, odd_x, odd_2 = item["odd_1"], item["odd_x"], item["odd_2"]
        source = "missing"
        pred_id: Optional[int] = None

        if best_match is not None:
            probs, db_source, pred_id = _extract_ms_probs_from_match(best_match)
            if probs is not None:
                prob_1, prob_x, prob_2 = (round(probs[0], 6), round(probs[1], 6), round(probs[2], 6))
                source = db_source

        if source == "missing" and odd_1 and odd_x and odd_2:
            p1, px, p2 = devig_1x2(odd_1, odd_x, odd_2)
            prob_1, prob_x, prob_2 = (round(p1, 6), round(px, 6), round(p2, 6))
            source = "market_devig"

        db.add(
            SportotoMatch(
                week_id=week.id,
                slot_no=item["slot_no"],
                home_team=item["home_team"],
                away_team=item["away_team"],
                prediction_id=pred_id,
                source=source,
                odd_1=odd_1,
                odd_x=odd_x,
                odd_2=odd_2,
                prob_1=prob_1,
                prob_x=prob_x,
                prob_2=prob_2,
                public_pct_1=item["public_pct_1"],
                public_pct_x=item["public_pct_x"],
                public_pct_2=item["public_pct_2"],
                actual_result=None,
            )
        )

    db.commit()
    db.refresh(week)
    return week


def serialize_sportoto_week(week: SportotoWeek, include_plans: bool = True) -> dict[str, Any]:
    ordered = sorted(week.matches, key=lambda m: m.slot_no)
    pred_ids = [m.prediction_id for m in ordered if m.prediction_id]
    match_id_by_pred: dict[int, int] = {}
    if pred_ids:
        from sqlalchemy.orm import object_session
        sess = object_session(week)
        if sess is not None:
            rows = sess.query(Prediction.id, Prediction.match_id).filter(Prediction.id.in_(pred_ids)).all()
            match_id_by_pred = {int(pid): int(mid) for pid, mid in rows}

    matches_payload: list[dict[str, Any]] = []
    for m in ordered:
        chaos = (
            compute_chaos_score(
                m.prob_1,
                m.prob_x,
                m.prob_2,
                public_pcts=(m.public_pct_1, m.public_pct_x, m.public_pct_2),
            )
            if m.prob_1 is not None and m.prob_x is not None and m.prob_2 is not None
            else None
        )
        trap = (
            compute_trap_signal(
                (m.prob_1, m.prob_x, m.prob_2),
                (m.public_pct_1, m.public_pct_x, m.public_pct_2),
            )
            if m.prob_1 is not None and m.prob_x is not None and m.prob_2 is not None
            else None
        )
        matches_payload.append(
            {
                "id": m.id,
                "slot_no": m.slot_no,
                "home_team": m.home_team,
                "away_team": m.away_team,
                "prediction_id": m.prediction_id,
                "match_id": match_id_by_pred.get(m.prediction_id) if m.prediction_id else None,
                "source": m.source,
                "odd_1": m.odd_1,
                "odd_x": m.odd_x,
                "odd_2": m.odd_2,
                "prob_1": m.prob_1,
                "prob_x": m.prob_x,
                "prob_2": m.prob_2,
                "public_pct_1": m.public_pct_1,
                "public_pct_x": m.public_pct_x,
                "public_pct_2": m.public_pct_2,
                "chaos": chaos,
                "trap_signal": trap,
                "actual_result": m.actual_result,
            }
        )

    missing_slots = [m["slot_no"] for m in matches_payload if m["source"] == "missing" or m["prob_1"] is None]
    col_price = float(getattr(week, "column_price", 10.0) or 10.0)
    plans: Optional[dict[str, Any]] = None
    if include_plans and not missing_slots and len(matches_payload) == 15:
        plans = {
            str(b): optimize_toto(matches_payload, budget=b, mode="hit15", column_price=col_price)
            for b in SPORTOTO_BUDGETS
        }

    return {
        "id": week.id,
        "title": week.title,
        "status": week.status,
        "column_price": col_price,
        "published_at": week.published_at.isoformat() if week.published_at else None,
        "created_at": week.created_at.isoformat() if week.created_at else None,
        "missing_count": len(missing_slots),
        "missing_slots": missing_slots,
        "matches": matches_payload,
        "plans": plans,
    }
