from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Match, OddsCurrent
from utils.cache import get_cached_json, set_cached_json
from utils.team_matcher import find_best_external_match

ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"
ODDS_CACHE_TTL_HOURS = 12.0
MIN_REMAINING_CREDITS = 30
ACTIVE_WINDOW_HOURS = 24

REMAINING_CACHE_KEY = "odds_api:remaining_credits"

# Map our Turkish/iddaa league names to The Odds API sport keys
LEAGUE_TO_ODDS_API_SPORT = {
    "ingiltere premier lig": "soccer_epl",
    "premier lig": "soccer_epl",
    "ispanya la liga": "soccer_spain_la_liga",
    "la liga": "soccer_spain_la_liga",
    "italya serie a": "soccer_italy_serie_a",
    "serie a": "soccer_italy_serie_a",
    "almanya bundesliga": "soccer_germany_bundesliga",
    "bundesliga": "soccer_germany_bundesliga",
    "fransa ligue 1": "soccer_france_ligue_one",
    "ligue 1": "soccer_france_ligue_one",
    "turkiye super lig": "soccer_turkey_super_league",
    "super lig": "soccer_turkey_super_league",
    "hollanda eredivisie": "soccer_netherlands_eredivisie",
    "portekiz primeira liga": "soccer_portugal_primeira_liga",
    "ingiltere championship": "soccer_efl_champ",
    "uefa sampiyonlar ligi": "soccer_uefa_champs_league",
    "sampiyonlar ligi": "soccer_uefa_champs_league",
    "uefa avrupa ligi": "soccer_uefa_europa_league",
    "avrupa ligi": "soccer_uefa_europa_league",
    "uefa konferans ligi": "soccer_uefa_europa_conference_league",
    "konferans ligi": "soccer_uefa_europa_conference_league",
}


def _norm_league(name: str) -> str:
    tr = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return " ".join((name or "").translate(tr).lower().split())


def _sport_key_for_league(league_name: str) -> Optional[str]:
    norm = _norm_league(league_name)
    for k, sport_key in LEAGUE_TO_ODDS_API_SPORT.items():
        if k in norm:
            return sport_key
    return None


def _parse_iso_utc(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _extract_sharp_odds(event: dict[str, Any]) -> dict[str, Any]:
    """Extract Pinnacle (preferred) or EU average odds for h2h (MS) and totals 2.5 (AU_25)."""
    home_team = event.get("home_team") or ""
    away_team = event.get("away_team") or ""
    bookmakers = event.get("bookmakers") or []

    pinnacle = next((b for b in bookmakers if b.get("key") == "pinnacle"), None)

    def _collect_market(bms: list[dict], market_key: str) -> dict[str, list[float]]:
        buckets: dict[str, list[float]] = {}
        for bm in bms:
            mkt = next((m for m in (bm.get("markets") or []) if m.get("key") == market_key), None)
            if not mkt:
                continue
            for out in mkt.get("outcomes") or []:
                name = str(out.get("name") or "")
                price = out.get("price")
                point = out.get("point")
                if not price or float(price) <= 1.01:
                    continue
                if market_key == "h2h":
                    if name == home_team:
                        buckets.setdefault("1", []).append(float(price))
                    elif name == away_team:
                        buckets.setdefault("2", []).append(float(price))
                    elif name.lower() == "draw":
                        buckets.setdefault("X", []).append(float(price))
                elif market_key == "totals":
                    if point is not None and abs(float(point) - 2.5) < 0.01:
                        if name.lower() == "under":
                            buckets.setdefault("ALT", []).append(float(price))
                        elif name.lower() == "over":
                            buckets.setdefault("UST", []).append(float(price))
        return buckets

    out: dict[str, Any] = {"ms": {}, "au25": {}, "ms_source": None, "au25_source": None}

    if pinnacle:
        pin_h2h = _collect_market([pinnacle], "h2h")
        if len(pin_h2h) == 3:
            out["ms"] = {k: round(v[0], 2) for k, v in pin_h2h.items()}
            out["ms_source"] = "pinnacle"
        pin_tot = _collect_market([pinnacle], "totals")
        if len(pin_tot) == 2:
            out["au25"] = {k: round(v[0], 2) for k, v in pin_tot.items()}
            out["au25_source"] = "pinnacle"

    if not out["ms"]:
        eu_h2h = _collect_market(bookmakers, "h2h")
        if len(eu_h2h) == 3:
            out["ms"] = {k: round(sum(v) / len(v), 2) for k, v in eu_h2h.items()}
            out["ms_source"] = "eu_avg"

    if not out["au25"]:
        eu_tot = _collect_market(bookmakers, "totals")
        if len(eu_tot) == 2:
            out["au25"] = {k: round(sum(v) / len(v), 2) for k, v in eu_tot.items()}
            out["au25_source"] = "eu_avg"

    return out


def sync_the_odds_api(db: Session) -> dict[str, Any]:
    """
    Fetch Pinnacle / EU sharp odds for active Major & UEFA leagues within 36h.
    Protected by 6h DB cache per sport_key and x-requests-remaining <= 30 lock.
    """
    api_key = (settings.the_odds_api_key or "").strip()
    if not api_key:
        return {"status": "disabled_no_key", "api_calls": 0, "cache_hits": 0, "matched": 0}

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=ACTIVE_WINDOW_HOURS)

    upcoming_matches = (
        db.query(Match)
        .filter(
            Match.status == "SCHEDULED",
            Match.start_time_utc >= now - timedelta(hours=1),
            Match.start_time_utc <= cutoff,
        )
        .all()
    )

    sport_keys_needed: set[str] = set()
    for m in upcoming_matches:
        sk = _sport_key_for_league(m.league)
        if sk:
            sport_keys_needed.add(sk)

    if not sport_keys_needed:
        return {"status": "no_active_major_leagues", "api_calls": 0, "cache_hits": 0, "matched": 0}

    rem_info = get_cached_json(db, REMAINING_CACHE_KEY, ttl_hours=720.0)
    remaining_credits: Optional[int] = (
        int(rem_info["remaining"]) if isinstance(rem_info, dict) and rem_info.get("remaining") is not None else None
    )

    api_calls = 0
    cache_hits = 0
    events_by_sport: dict[str, list[dict[str, Any]]] = {}

    with httpx.Client(timeout=12.0) as client:
        for sk in sorted(sport_keys_needed):
            cache_key = f"odds_api:v1:{sk}"
            cached_events = get_cached_json(db, cache_key, ttl_hours=ODDS_CACHE_TTL_HOURS)
            if isinstance(cached_events, list):
                events_by_sport[sk] = cached_events
                cache_hits += 1
                continue

            if remaining_credits is not None and remaining_credits <= MIN_REMAINING_CREDITS:
                continue

            try:
                resp = client.get(
                    f"{ODDS_API_BASE}/{sk}/odds",
                    params={
                        "apiKey": api_key,
                        "regions": "eu",
                        "markets": "h2h,totals",
                        "oddsFormat": "decimal",
                    },
                )
                api_calls += 1
                rem_hdr = resp.headers.get("x-requests-remaining")
                used_hdr = resp.headers.get("x-requests-used")
                if rem_hdr is not None:
                    try:
                        remaining_credits = int(float(rem_hdr))
                        set_cached_json(
                            db,
                            REMAINING_CACHE_KEY,
                            {"remaining": remaining_credits, "used": used_hdr},
                        )
                    except ValueError:
                        pass

                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        events_by_sport[sk] = data
                        set_cached_json(db, cache_key, data)
            except Exception:
                continue

    # Build candidate list for team matching
    candidates: list[dict[str, Any]] = []
    for sk, evs in events_by_sport.items():
        for ev in evs:
            dt = _parse_iso_utc(ev.get("commence_time") or "")
            if not dt:
                continue
            parsed = _extract_sharp_odds(ev)
            if not parsed["ms"] and not parsed["au25"]:
                continue
            candidates.append(
                {
                    "sport_key": sk,
                    "home_team": ev.get("home_team") or "",
                    "away_team": ev.get("away_team") or "",
                    "start_time_utc": dt,
                    "parsed": parsed,
                }
            )

    matched_matches = 0
    for m in upcoming_matches:
        st = m.start_time_utc
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        best = find_best_external_match(
            m.home_team,
            m.away_team,
            st,
            candidates,
            time_tolerance_hours=4.0,
            min_score=72.0,
        )
        if not best:
            continue

        parsed = best["parsed"]
        updated_any = False
        odds_rows = db.query(OddsCurrent).filter(OddsCurrent.match_id == m.id).all()
        for row in odds_rows:
            if row.market_key == "MS" and row.selection_key in parsed["ms"]:
                row.global_avg_odd = parsed["ms"][row.selection_key]
                row.global_source = parsed["ms_source"]
                updated_any = True
            elif row.market_key == "AU_25" and row.selection_key in parsed["au25"]:
                row.global_avg_odd = parsed["au25"][row.selection_key]
                row.global_source = parsed["au25_source"]
                updated_any = True

        if updated_any:
            matched_matches += 1

    db.flush()
    return {
        "status": "ok",
        "sport_keys_needed": sorted(sport_keys_needed),
        "api_calls": api_calls,
        "cache_hits": cache_hits,
        "matched": matched_matches,
        "remaining_credits": remaining_credits,
    }
