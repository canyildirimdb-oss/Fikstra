"""Exact dynamic-programming Spor Toto 15'li column optimizer and DB helpers."""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlalchemy.orm import Session, joinedload

from app.models import Match, OddsCurrent, Prediction, SportotoMatch, SportotoWeek

VALID_RESULTS = {"1", "X", "2"}
SPORTOTO_BUDGETS = (16, 32, 64)

_TR_TRANS = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
_STOP_TOKENS = {"fc", "sk", "fk", "jk", "as", "cf", "sc", "ac", "afc", "cfk", "spor", "kulubu"}


def devig_1x2(odd_1: float, odd_x: float, odd_2: float) -> tuple[float, float, float]:
    """Remove bookmaker overround from 1-X-2 odds and return probabilities summing to 1.0."""
    o1 = float(odd_1)
    ox = float(odd_x)
    o2 = float(odd_2)
    if o1 <= 1.0 or ox <= 1.0 or o2 <= 1.0:
        raise ValueError("All 1-X-2 odds must be strictly greater than 1.0")
    q1 = 1.0 / o1
    qx = 1.0 / ox
    q2 = 1.0 / o2
    total = q1 + qx + q2
    return (q1 / total, qx / total, q2 / total)


def _best_options_for_match(p1: float, px: float, p2: float) -> list[tuple[int, str, float, str]]:
    """Return best (k, pick_code, prob, kind) for k in {1, 2, 3}."""
    singles = [("1", p1), ("X", px), ("2", p2)]
    best_single = max(singles, key=lambda x: x[1])

    doubles = [("1X", p1 + px), ("12", p1 + p2), ("X2", px + p2)]
    best_double = max(doubles, key=lambda x: x[1])

    return [
        (1, best_single[0], float(best_single[1]), "single"),
        (2, best_double[0], float(min(1.0, best_double[1])), "double"),
        (3, "1X2", 1.0, "triple"),
    ]


def optimize_sportoto_columns(matches: list[dict[str, Any]], max_columns: int) -> dict[str, Any]:
    """
    Exact Dynamic Programming optimizer maximizing sum(ln P(pick_i))
    subject to prod(k_i) <= max_columns.
    """
    if max_columns < 1:
        raise ValueError("max_columns must be >= 1")
    if not matches:
        raise ValueError("matches cannot be empty")

    ordered = sorted(matches, key=lambda m: int(m["slot_no"]))
    match_options: list[list[tuple[int, str, float, str]]] = []
    for m in ordered:
        p1 = m.get("prob_1")
        px = m.get("prob_x")
        p2 = m.get("prob_2")
        if p1 is None or px is None or p2 is None:
            raise ValueError(f"Missing probabilities at slot {m.get('slot_no')}")
        match_options.append(_best_options_for_match(float(p1), float(px), float(p2)))

    # dp[used_cols] = (total_log_prob, tuple_of_chosen_options)
    dp: dict[int, tuple[float, tuple[tuple[int, str, float, str], ...]]] = {1: (0.0, ())}

    for opts in match_options:
        next_dp: dict[int, tuple[float, tuple[tuple[int, str, float, str], ...]]] = {}
        for cols, (log_sum, path) in dp.items():
            for k, code, prob, kind in opts:
                nc = cols * k
                if nc > max_columns:
                    continue
                new_log = log_sum + math.log(max(prob, 1e-12))
                prev = next_dp.get(nc)
                if prev is None or new_log > prev[0] + 1e-12:
                    next_dp[nc] = (new_log, path + ((k, code, prob, kind),))
        dp = next_dp

    if not dp:
        raise ValueError("No feasible column allocation found for given budget")

    best_cols, (_, best_path) = max(
        dp.items(),
        key=lambda item: (round(item[1][0], 11), -item[0]),
    )

    picks: list[dict[str, Any]] = []
    single_count = 0
    double_count = 0
    triple_count = 0
    expected_hits = 0.0
    full_hit_prob = 1.0
    settled_count = 0
    hits = 0

    for m, (k, code, prob, kind) in zip(ordered, best_path):
        if k == 1:
            single_count += 1
        elif k == 2:
            double_count += 1
        else:
            triple_count += 1
        expected_hits += prob
        full_hit_prob *= prob

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
                "hit": hit,
            }
        )

    return {
        "budget": int(max_columns),
        "used_columns": int(best_cols),
        "single_count": single_count,
        "double_count": double_count,
        "triple_count": triple_count,
        "expected_hits": round(expected_hits, 2),
        "full_hit_prob_pct": round(full_hit_prob * 100.0, 6),
        "settled_count": settled_count,
        "hits": hits if settled_count > 0 else None,
        "picks": picks,
    }


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
    """Parse 15 lines of 'Home - Away' or 'Home - Away | 1.85 3.40 4.10'."""
    lines = [ln.strip() for ln in (raw_text or "").splitlines() if ln.strip()]
    if len(lines) != 15:
        raise ValueError(f"Tam 15 maç satırı girilmelidir (şu an {len(lines)} satır var)")

    parsed: list[dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        # Strip optional leading numbering like "1. ", "1) ", "01 - "
        clean = re.sub(r"^\d{1,2}\s*[\.\)\-:]\s*", "", line).strip()
        odd_1 = odd_x = odd_2 = None
        if "|" in clean:
            teams_part, odds_part = clean.split("|", 1)
            odds_nums = re.findall(r"\d+(?:[.,]\d+)?", odds_part)
            if len(odds_nums) >= 3:
                o1, ox, o2 = (float(x.replace(",", ".")) for x in odds_nums[:3])
                if o1 > 1.0 and ox > 1.0 and o2 > 1.0:
                    odd_1, odd_x, odd_2 = o1, ox, o2
        else:
            teams_part = clean

        # Split home - away by " - ", " – ", " — ", or " vs "
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
            }
        )
    return parsed


def _extract_ms_probs_from_match(match: Match) -> tuple[Optional[tuple[float, float, float]], str, Optional[int]]:
    """
    Extract real 1-X-2 probabilities from a DB Match:
    1) Prediction MS (blended_prob_pct / true_prob_pct / published_prob) -> 'fikstra_model'
    2) OddsCurrent MS (global_avg_odd or iddaa_odd) -> 'market_devig'
    """
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


def match_and_create_sportoto_week(db: Session, title: str, raw_text: str) -> SportotoWeek:
    """Parse 15 lines, match against live DB predictions/odds, and save DRAFT week."""
    parsed_lines = parse_sportoto_raw_text(raw_text)

    now_utc = datetime.now(timezone.utc)
    candidates = (
        db.query(Match)
        .options(joinedload(Match.predictions), joinedload(Match.odds_current))
        .filter(Match.start_time_utc >= now_utc - timedelta(days=2))
        .all()
    )

    week = SportotoWeek(title=title.strip()[:120] or "Spor Toto Haftası", status="DRAFT")
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
        odd_1 = item["odd_1"]
        odd_x = item["odd_x"]
        odd_2 = item["odd_2"]
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
                actual_result=None,
            )
        )

    db.commit()
    db.refresh(week)
    return week


def serialize_sportoto_week(week: SportotoWeek, include_plans: bool = True) -> dict[str, Any]:
    ordered = sorted(week.matches, key=lambda m: m.slot_no)
    matches_payload = [
        {
            "id": m.id,
            "slot_no": m.slot_no,
            "home_team": m.home_team,
            "away_team": m.away_team,
            "prediction_id": m.prediction_id,
            "source": m.source,
            "odd_1": m.odd_1,
            "odd_x": m.odd_x,
            "odd_2": m.odd_2,
            "prob_1": m.prob_1,
            "prob_x": m.prob_x,
            "prob_2": m.prob_2,
            "actual_result": m.actual_result,
        }
        for m in ordered
    ]

    missing_slots = [m["slot_no"] for m in matches_payload if m["source"] == "missing" or m["prob_1"] is None]
    plans: Optional[dict[str, Any]] = None
    if include_plans and not missing_slots and len(matches_payload) == 15:
        plans = {
            str(b): optimize_sportoto_columns(matches_payload, b)
            for b in SPORTOTO_BUDGETS
        }

    return {
        "id": week.id,
        "title": week.title,
        "status": week.status,
        "published_at": week.published_at.isoformat() if week.published_at else None,
        "created_at": week.created_at.isoformat() if week.created_at else None,
        "missing_count": len(missing_slots),
        "missing_slots": missing_slots,
        "matches": matches_payload,
        "plans": plans,
    }
