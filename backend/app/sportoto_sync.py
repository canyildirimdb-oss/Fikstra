"""Automatic multi-source Spor Toto 15'li bulletin fetcher, matcher, publisher, and settler."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session, joinedload

from app.models import Match, Prediction, ScraperCache, SportotoMatch, SportotoWeek
from app.sportoto_engine import (
    VALID_RESULTS,
    _extract_ms_probs_from_match,
    _team_similarity,
    devig_1x2,
    normalize_team_name,
)

logger = logging.getLogger(__name__)

IDDAA_SPORTOTO_URL = "https://sportotov2.iddaa.com/SporToto"
IDDAA_SPORTOTO_RESULT_URL = "https://sportotov2.iddaa.com/SporToto/result?gameCycleNo={cycle}"
IDDAA_SPORTSBOOK_URL = "https://sportsbookv2.iddaa.com/sportsbook/events?st=1&type=0&version=0"

SPORTOTO_LAST_SYNC_KEY = "sportoto:last_sync"
HTTP_TIMEOUT = 10.0

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.iddaa.com",
    "Referer": "https://www.iddaa.com/",
}


def _clean_team_for_match(name: str) -> str:
    txt = normalize_team_name(name)
    tokens = [t for t in txt.split() if t not in {"a", "s", "as"}]
    return " ".join(tokens)


def _team_match_score(a: str, b: str) -> float:
    base = _team_similarity(a, b)
    ca = _clean_team_for_match(a)
    cb = _clean_team_for_match(b)
    if not ca or not cb:
        return base
    if ca == cb:
        return 1.0
    if ca in cb or cb in ca:
        return max(base, 0.92)
    ta = ca.split()
    tb = cb.split()
    # Handle abbreviated prefix like "B. Dortmund" vs "Borussia Dortmund"
    if len(ta) >= 2 and len(tb) >= 2 and ta[-1] == tb[-1] and len(ta[-1]) >= 4:
        if ta[0][0] == tb[0][0]:
            return max(base, 0.85)
    return max(base, _team_similarity(ca, cb))


def _split_event_teams(event_name: str) -> tuple[str, str]:
    raw = (event_name or "").strip()
    parts = re.split(r"\s+[-–—]\s+", raw, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip()[:128], parts[1].strip()[:128]
    if "-" in raw:
        left, right = raw.split("-", 1)
        if left.strip() and right.strip():
            return left.strip()[:128], right.strip()[:128]
    raise ValueError(f"Takım adları ayrıştırılamadı: '{event_name}'")


def _normalize_winner(raw_winner: Any) -> Optional[str]:
    if raw_winner is None:
        return None
    w = str(raw_winner).strip().upper()
    if w == "0":
        return "X"
    if w in VALID_RESULTS:
        return w
    return None


def _valid_public_pcts(p1: Any, px: Any, p2: Any) -> tuple[Optional[float], Optional[float], Optional[float]]:
    try:
        if p1 is None or px is None or p2 is None:
            return None, None, None
        f1, fx, f2 = float(p1), float(px), float(p2)
        if f1 > 0.0 and fx > 0.0 and f2 > 0.0 and 95.0 <= (f1 + fx + f2) <= 105.0:
            return round(f1, 1), round(fx, 1), round(f2, 1)
    except (TypeError, ValueError):
        pass
    return None, None, None


def _parse_iddaa_sportoto_payload(payload: dict[str, Any], source_name: str = "iddaa.com") -> Optional[dict[str, Any]]:
    if not isinstance(payload, dict) or not payload.get("isSuccess"):
        return None
    data = payload.get("data") or {}
    events = data.get("events") or []
    cycle_no = data.get("gameCycleNo")
    if not cycle_no or len(events) != 15:
        return None

    parsed_events: list[dict[str, Any]] = []
    for idx, ev in enumerate(sorted(events, key=lambda x: int(x.get("eventNo") or 0)), start=1):
        slot_no = int(ev.get("eventNo") or idx)
        home, away = _split_event_teams(ev.get("eventName") or "")
        pub_1, pub_x, pub_2 = _valid_public_pcts(
            ev.get("homePercentage"),
            ev.get("drawPercentage"),
            ev.get("awayPercentage"),
        )
        parsed_events.append(
            {
                "slot_no": slot_no,
                "home_team": home,
                "away_team": away,
                "event_date": ev.get("eventDate"),
                "actual_result": _normalize_winner(ev.get("winner")),
                "public_pct_1": pub_1,
                "public_pct_x": pub_x,
                "public_pct_2": pub_2,
                "odd_1": None,
                "odd_x": None,
                "odd_2": None,
            }
        )

    col_price = 10.0
    for price_key in ("columnPrice", "unitPrice", "columnAmount", "ticketPrice", "price"):
        raw_price = data.get(price_key)
        if raw_price is not None:
            try:
                f_price = float(raw_price)
                if f_price > 0.0:
                    col_price = f_price
                    break
            except (TypeError, ValueError):
                pass

    return {
        "source": source_name,
        "external_id": f"iddaa:{cycle_no}",
        "cycle_no": int(cycle_no),
        "title": f"{cycle_no}. Hafta",
        "column_price": col_price,
        "events": parsed_events,
    }


def _fetch_from_iddaa_primary() -> dict[str, Any]:
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=_DEFAULT_HEADERS, follow_redirects=True) as client:
        resp = client.get(IDDAA_SPORTOTO_URL)
        resp.raise_for_status()
        parsed = _parse_iddaa_sportoto_payload(resp.json(), source_name="iddaa.com")
        if not parsed:
            raise ValueError("iddaa.com SporToto yanıtı geçersiz veya 15 maç içermiyor")
        return parsed


def _fetch_iddaa_sportsbook_odds() -> list[dict[str, Any]]:
    """Fetch current 1-X-2 odds from iddaa sportsbook JSON (0 Odds API credits)."""
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, headers=_DEFAULT_HEADERS, follow_redirects=True) as client:
            resp = client.get(IDDAA_SPORTSBOOK_URL)
            resp.raise_for_status()
            data = resp.json()
            events = (data.get("data") or {}).get("events") or []
            out: list[dict[str, Any]] = []
            for ev in events:
                hn = (ev.get("hn") or "").strip()
                an = (ev.get("an") or "").strip()
                if not hn or not an:
                    continue
                ms_odds: dict[str, float] = {}
                for m in ev.get("m") or []:
                    if m.get("t") == 1 and m.get("st") == 1:
                        for o in m.get("o") or []:
                            name = str(o.get("n") or "").strip().upper()
                            if name == "0":
                                name = "X"
                            if name in {"1", "X", "2"} and o.get("odd"):
                                try:
                                    val = float(o["odd"])
                                    if val > 1.0:
                                        ms_odds[name] = val
                                except (TypeError, ValueError):
                                    pass
                        break
                if all(k in ms_odds for k in ("1", "X", "2")):
                    out.append(
                        {
                            "home_team": hn,
                            "away_team": an,
                            "odd_1": ms_odds["1"],
                            "odd_x": ms_odds["X"],
                            "odd_2": ms_odds["2"],
                        }
                    )
            return out
    except Exception as exc:
        logger.warning("iddaa sportsbook odds fetch failed: %s", exc)
        return []


def _fetch_iddaa_cycle_results(cycle_no: int) -> dict[int, str]:
    """Fetch official 1/X/2 results for a specific iddaa SporToto gameCycleNo."""
    try:
        url = IDDAA_SPORTOTO_RESULT_URL.format(cycle=int(cycle_no))
        with httpx.Client(timeout=HTTP_TIMEOUT, headers=_DEFAULT_HEADERS, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
            events = ((payload.get("data") or {}).get("events")) or []
            res_map: dict[int, str] = {}
            for ev in events:
                slot = ev.get("eventNo")
                winner = _normalize_winner(ev.get("winner"))
                if slot is not None and winner in VALID_RESULTS:
                    res_map[int(slot)] = winner
            return res_map
    except Exception as exc:
        logger.warning("iddaa SporToto result fetch failed for cycle %s: %s", cycle_no, exc)
        return {}


def _fetch_bulletin_multi_source() -> tuple[Optional[dict[str, Any]], list[str]]:
    """Try primary and backup sources cleanly with full error isolation."""
    errors: list[str] = []
    providers = [
        ("iddaa.com", _fetch_from_iddaa_primary),
    ]
    for name, fn in providers:
        try:
            data = fn()
            if data and len(data.get("events") or []) == 15:
                return data, errors
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            logger.warning("SporToto source %s failed: %s", name, exc)
    return None, errors


def _find_best_db_match(home: str, away: str, candidates: list[Match]) -> Optional[Match]:
    best: Optional[Match] = None
    best_score = 0.0
    for cand in candidates:
        sh = _team_match_score(home, cand.home_team)
        sa = _team_match_score(away, cand.away_team)
        if sh >= 0.72 and sa >= 0.72:
            combined = (sh + sa) / 2.0
            if combined > best_score:
                best_score = combined
                best = cand
    return best


def _find_best_sb_odds(home: str, away: str, sb_events: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    best: Optional[dict[str, Any]] = None
    best_score = 0.0
    for ev in sb_events:
        sh = _team_match_score(home, ev["home_team"])
        sa = _team_match_score(away, ev["away_team"])
        if sh >= 0.72 and sa >= 0.72:
            combined = (sh + sa) / 2.0
            if combined > best_score:
                best_score = combined
                best = ev
    return best


def _save_sync_status(db: Session, payload: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    encoded = json.dumps(payload, ensure_ascii=False)
    row = db.query(ScraperCache).filter(ScraperCache.cache_key == SPORTOTO_LAST_SYNC_KEY).first()
    if row:
        row.payload_json = encoded
        row.updated_at = now
    else:
        db.add(ScraperCache(cache_key=SPORTOTO_LAST_SYNC_KEY, payload_json=encoded, updated_at=now))
    db.commit()


def get_sportoto_sync_status(db: Session) -> dict[str, Any]:
    row = db.query(ScraperCache).filter(ScraperCache.cache_key == SPORTOTO_LAST_SYNC_KEY).first()
    if not row or not row.payload_json:
        return {"synced_at": None, "status": None}
    try:
        return json.loads(row.payload_json)
    except json.JSONDecodeError:
        return {"synced_at": None, "status": None}


def _settle_open_published_weeks(db: Session, skip_week_id: Optional[int] = None) -> int:
    """Auto-settle any PUBLISHED weeks from DB matches or iddaa cycle results."""
    published_weeks = (
        db.query(SportotoWeek)
        .options(joinedload(SportotoWeek.matches))
        .filter(SportotoWeek.status == "PUBLISHED")
        .all()
    )
    updated_weeks = 0
    for week in published_weeks:
        if skip_week_id and week.id == skip_week_id:
            continue
        changed = False
        pred_ids = [m.prediction_id for m in week.matches if m.prediction_id and not m.actual_result]
        if pred_ids:
            preds = (
                db.query(Prediction)
                .options(joinedload(Prediction.match))
                .filter(Prediction.id.in_(pred_ids))
                .all()
            )
            pmap = {p.id: p for p in preds}
            for sm in week.matches:
                if sm.actual_result or not sm.prediction_id:
                    continue
                pr = pmap.get(sm.prediction_id)
                mt = pr.match if pr else None
                if mt and mt.status == "FINISHED" and mt.home_score is not None and mt.away_score is not None:
                    sm.actual_result = "1" if mt.home_score > mt.away_score else ("X" if mt.home_score == mt.away_score else "2")
                    changed = True

        ext_id = getattr(week, "external_id", None) or ""
        if ext_id.startswith("iddaa:") and any(not m.actual_result for m in week.matches):
            try:
                cycle_no = int(ext_id.split(":", 1)[1])
                res_map = _fetch_iddaa_cycle_results(cycle_no)
                for sm in week.matches:
                    if not sm.actual_result and sm.slot_no in res_map:
                        sm.actual_result = res_map[sm.slot_no]
                        changed = True
            except Exception:
                pass

        if len(week.matches) == 15 and all(m.actual_result in VALID_RESULTS for m in week.matches):
            week.status = "SETTLED"
            changed = True

        if changed:
            updated_weeks += 1

    if updated_weeks:
        db.commit()
    return updated_weeks


def sync_sportoto_auto(db: Session, *, trigger_notifications: bool = True) -> dict[str, Any]:
    """
    Fetch active Spor Toto 15'li bulletin, match against DB models + market odds,
    refresh pre-kickoff odds/public percentages, auto-publish when 15/15 ready,
    and auto-settle finished matches.
    """
    now_utc = datetime.now(timezone.utc)
    bulletin, source_errors = _fetch_bulletin_multi_source()
    if not bulletin:
        settled_other = _settle_open_published_weeks(db)
        err_msg = "; ".join(source_errors) or "Tüm Spor Toto kaynakları başarısız oldu"
        status_payload = {
            "synced_at": now_utc.isoformat(),
            "status": "error",
            "source": None,
            "error": err_msg[:300],
            "settled_other_weeks": settled_other,
        }
        try:
            _save_sync_status(db, status_payload)
        except Exception:
            db.rollback()
        try:
            from app.telegram_bot import check_and_send_health_alerts
            check_and_send_health_alerts(db)
        except Exception:
            pass
        return {"ok": False, **status_payload}

    ext_id: str = bulletin["external_id"]
    source_name: str = bulletin["source"]
    events: list[dict[str, Any]] = bulletin["events"]

    sb_odds_events = _fetch_iddaa_sportsbook_odds()
    # Load lightweight Match rows only (no Cartesian joinedload -> 512MB RAM safe)
    candidates = (
        db.query(Match)
        .filter(Match.start_time_utc >= now_utc - timedelta(days=4))
        .all()
    )

    week = (
        db.query(SportotoWeek)
        .options(joinedload(SportotoWeek.matches))
        .filter(SportotoWeek.external_id == ext_id)
        .first()
    )
    created = False
    bulletin_col_price = float(bulletin.get("column_price") or 10.0)
    if not week:
        week = SportotoWeek(
            external_id=ext_id,
            title=bulletin["title"][:120],
            status="DRAFT",
            column_price=bulletin_col_price,
        )
        db.add(week)
        db.flush()
        created = True
    elif week.status != "SETTLED" and bulletin.get("column_price"):
        week.column_price = bulletin_col_price

    existing_by_slot = {m.slot_no: m for m in week.matches}
    model_count = 0
    devig_count = 0
    missing_count = 0
    public_pct_count = 0
    settled_slots = 0

    for item in events:
        slot = int(item["slot_no"])
        home = item["home_team"]
        away = item["away_team"]
        sm = existing_by_slot.get(slot)

        best_match = _find_best_db_match(home, away, candidates)
        sb_match = _find_best_sb_odds(home, away, sb_odds_events) if sb_odds_events else None

        prob_1 = prob_x = prob_2 = None
        src = "missing"
        pred_id: Optional[int] = None
        odd_1 = item.get("odd_1") or (sb_match["odd_1"] if sb_match else None)
        odd_x = item.get("odd_x") or (sb_match["odd_x"] if sb_match else None)
        odd_2 = item.get("odd_2") or (sb_match["odd_2"] if sb_match else None)

        if best_match is not None:
            # Load only MS/top predictions & MS odds for this single matched row
            _ = best_match.predictions
            _ = best_match.odds_current
            probs, db_src, pred_id = _extract_ms_probs_from_match(best_match)
            if probs is not None:
                prob_1, prob_x, prob_2 = round(probs[0], 6), round(probs[1], 6), round(probs[2], 6)
                src = db_src

        if src == "missing" and odd_1 and odd_x and odd_2:
            try:
                p1, px, p2 = devig_1x2(odd_1, odd_x, odd_2)
                prob_1, prob_x, prob_2 = round(p1, 6), round(px, 6), round(p2, 6)
                src = "market_devig"
            except ValueError:
                pass

        # Actual result from source or linked DB match
        actual_res = item.get("actual_result")
        if not actual_res and best_match is not None:
            if best_match.status == "FINISHED" and best_match.home_score is not None and best_match.away_score is not None:
                actual_res = (
                    "1"
                    if best_match.home_score > best_match.away_score
                    else ("X" if best_match.home_score == best_match.away_score else "2")
                )

        pub_1, pub_x, pub_2 = item.get("public_pct_1"), item.get("public_pct_x"), item.get("public_pct_2")

        if sm is None:
            sm = SportotoMatch(
                week_id=week.id,
                slot_no=slot,
                home_team=home,
                away_team=away,
                prediction_id=pred_id,
                source=src,
                odd_1=odd_1,
                odd_x=odd_x,
                odd_2=odd_2,
                prob_1=prob_1,
                prob_x=prob_x,
                prob_2=prob_2,
                public_pct_1=pub_1,
                public_pct_x=pub_x,
                public_pct_2=pub_2,
                actual_result=actual_res,
            )
            db.add(sm)
        else:
            # Refresh unsettled rows before kickoff
            if sm.actual_result is None and week.status != "SETTLED":
                if pred_id and not sm.prediction_id:
                    sm.prediction_id = pred_id
                if src == "fikstra_model" and prob_1 is not None:
                    sm.source = "fikstra_model"
                    sm.prob_1, sm.prob_x, sm.prob_2 = prob_1, prob_x, prob_2
                    if odd_1 and odd_x and odd_2:
                        sm.odd_1, sm.odd_x, sm.odd_2 = odd_1, odd_x, odd_2
                elif src == "market_devig" and prob_1 is not None and sm.source in {"missing", "market_devig"}:
                    sm.source = "market_devig"
                    sm.odd_1, sm.odd_x, sm.odd_2 = odd_1, odd_x, odd_2
                    sm.prob_1, sm.prob_x, sm.prob_2 = prob_1, prob_x, prob_2
                if pub_1 is not None and pub_x is not None and pub_2 is not None:
                    sm.public_pct_1, sm.public_pct_x, sm.public_pct_2 = pub_1, pub_x, pub_2
            if not sm.actual_result and actual_res in VALID_RESULTS:
                sm.actual_result = actual_res

        if sm.source == "fikstra_model":
            model_count += 1
        elif sm.source == "market_devig":
            devig_count += 1
        else:
            missing_count += 1

        if sm.public_pct_1 is not None and sm.public_pct_x is not None and sm.public_pct_2 is not None:
            public_pct_count += 1
        if sm.actual_result in VALID_RESULTS:
            settled_slots += 1

    db.flush()
    db.refresh(week)

    published_now = False
    all_ready = len(week.matches) == 15 and all(
        m.source != "missing" and m.prob_1 is not None for m in week.matches
    )
    all_settled = len(week.matches) == 15 and all(m.actual_result in VALID_RESULTS for m in week.matches)

    if all_ready and week.status == "DRAFT":
        week.status = "SETTLED" if all_settled else "PUBLISHED"
        if not week.published_at:
            week.published_at = now_utc
        published_now = True
    elif all_settled and week.status == "PUBLISHED":
        week.status = "SETTLED"

    db.commit()
    db.refresh(week)

    if published_now and trigger_notifications:
        try:
            from services.push import notify_sportoto_published
            notify_sportoto_published(db, week.id, week.title)
        except Exception:
            pass
        try:
            from app.telegram_bot import dispatch_sportoto_week
            dispatch_sportoto_week(db, week.id)
        except Exception:
            pass

    settled_other = _settle_open_published_weeks(db, skip_week_id=week.id)

    status_label = "ok" if missing_count == 0 else "partial"
    summary = {
        "ok": True,
        "synced_at": now_utc.isoformat(),
        "status": status_label,
        "source": source_name,
        "week_id": week.id,
        "external_id": ext_id,
        "title": week.title,
        "week_status": week.status,
        "created": created,
        "published_now": published_now,
        "matched_count": 15 - missing_count,
        "model_count": model_count,
        "devig_count": devig_count,
        "missing_count": missing_count,
        "public_pct_count": public_pct_count,
        "settled_slots": settled_slots,
        "settled_other_weeks": settled_other,
    }
    _save_sync_status(db, summary)
    return summary


def maybe_run_sportoto_auto_sync(db: Session, min_interval_minutes: int = 60) -> Optional[dict[str, Any]]:
    """Run sync_sportoto_auto at most once per min_interval_minutes unless previous run errored."""
    try:
        last = get_sportoto_sync_status(db)
        last_at_raw = last.get("synced_at")
        if last_at_raw and last.get("status") in {"ok", "partial"}:
            last_dt = datetime.fromisoformat(last_at_raw)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last_dt < timedelta(minutes=min_interval_minutes):
                return None
        return sync_sportoto_auto(db, trigger_notifications=True)
    except Exception as exc:
        logger.warning("maybe_run_sportoto_auto_sync isolated error: %s", exc)
        return None
