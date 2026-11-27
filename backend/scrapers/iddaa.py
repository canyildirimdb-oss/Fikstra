from datetime import datetime, timezone
from typing import Any

import httpx

from utils.formatting import format_league_name_tr

IDDAA_EVENTS_URL = "https://sportsbookv2.iddaa.com/sportsbook/events?st=1&type=0&version=0&live=true"
IDDAA_COMPETITIONS_URL = "https://sportsbookv2.iddaa.com/sportsbook/competitions"

MAJOR_LEAGUE_KEYWORDS = [
    "süper lig",
    "super lig",
    "premier lig",
    "premier league",
    "la liga",
    "serie a",
    "bundesliga",
    "ligue 1",
    "şampiyonlar ligi",
    "sampiyonlar ligi",
    "champions league",
    "avrupa ligi",
    "europa league",
    "konferans ligi",
    "conference league",
    "eredivisie",
    "portekiz",
    "primeira liga",
]


EXCLUDED_MAJOR_TOKENS = (
    "(k)",
    "(w)",
    "kadın",
    "kadin",
    "bayan",
    "women",
    "youth",
    "u19",
    "u21",
    "u23",
    "next gen",
    "genç",
    "genc",
    "rezerv",
    "reserve",
    "b takımı",
    "b takimi",
    "2. lig",
    "2.lig",
)


def is_youth_or_women_match(league_name: str, home_team: str = "", away_team: str = "") -> bool:
    for text in (league_name, home_team, away_team):
        low = (text or "").lower()
        if any(ex in low for ex in EXCLUDED_MAJOR_TOKENS):
            return True
        if low.endswith(" ii") or " ii " in low:
            return True
    return False


def is_major_league(
    league_name: str,
    country_code: str = "",
    home_team: str = "",
    away_team: str = "",
) -> bool:
    if is_youth_or_women_match(league_name, home_team, away_team):
        return False
    low = (league_name or "").lower()
    cc = (country_code or "").upper()

    # Country-qualified checks to avoid e.g. Brezilya Serie A or Avusturya Bundesliga
    if "serie a" in low:
        return cc == "IT" or "italya" in low
    if "bundesliga" in low:
        return cc == "DE" or "almanya" in low
    if "ligue 1" in low:
        return cc == "FR" or "fransa" in low
    if "premier lig" in low or "premier league" in low:
        return cc in {"GB", "EN", "ENG"} or "ingiltere" in low
    if "la liga" in low:
        return cc == "ES" or "ispanya" in low
    if "süper lig" in low or "super lig" in low:
        return cc == "TR" or "türkiye" in low or "turkiye" in low
    if "eredivisie" in low:
        return cc == "NL" or "hollanda" in low
    if "portekiz" in low or "primeira liga" in low:
        return cc == "PT" or "portekiz" in low

    return any(
        kw in low
        for kw in [
            "şampiyonlar ligi",
            "sampiyonlar ligi",
            "champions league",
            "avrupa ligi",
            "europa league",
            "konferans ligi",
            "conference league",
        ]
    )


def _extract_odds_from_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract normalized MS, CS, AU (1.5/2.5/3.5), KG odds from iddaa event markets."""
    extracted: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for m in markets or []:
        t = m.get("t")
        st = m.get("st")
        status = m.get("s")
        if status is not None and status < 0:
            continue
        sov = str(m.get("sov") or "").strip()
        outcomes = m.get("o") or []

        market_key = None
        if (t, st) in ((1, 1), (4, 4)):
            market_key = "MS"
        elif (t, st) in ((2, 92), (4, 129)):
            market_key = "CS"
        elif (t, st) in ((2, 101), (4, 14)) and sov in {"1.5", "2.5", "3.5"}:
            market_key = f"AU_{sov.replace('.', '')}"
        elif (t, st) in ((2, 89), (4, 131)):
            market_key = "KG"

        if not market_key:
            continue

        for o in outcomes:
            label = str(o.get("n") or "").strip()
            odd = float(o.get("odd") or 0)
            if odd <= 1.01:
                continue

            sel_key = None
            if market_key == "MS":
                if label == "1":
                    sel_key = "1"
                elif label in {"0", "X"}:
                    sel_key = "X"
                elif label == "2":
                    sel_key = "2"
            elif market_key == "CS":
                if label == "1 ve 0":
                    sel_key = "1X"
                elif label == "1 ve 2":
                    sel_key = "12"
                elif label == "0 ve 2":
                    sel_key = "X2"
            elif market_key.startswith("AU_"):
                if label == "Alt":
                    sel_key = "ALT"
                elif label in {"Üst", "Ust"}:
                    sel_key = "UST"
            elif market_key == "KG":
                if label == "Var":
                    sel_key = "VAR"
                elif label == "Yok":
                    sel_key = "YOK"

            if sel_key:
                pair = (market_key, sel_key)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                extracted.append({
                    "market_key": market_key,
                    "selection_key": sel_key,
                    "odd": round(odd, 2),
                })

    return extracted


def fetch_iddaa_bulletin(timeout: float = 20.0) -> list[dict[str, Any]]:
    """Fetch live + pre-match football bulletin from iddaa.com lightweight JSON APIs."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    with httpx.Client(timeout=timeout, headers=headers) as client:
        comp_resp = client.get(IDDAA_COMPETITIONS_URL)
        comp_resp.raise_for_status()
        comp_data = comp_resp.json().get("data") or []

        comp_map: dict[int, dict[str, Any]] = {}
        for c in comp_data:
            cid = c.get("i")
            if cid is not None:
                comp_map[int(cid)] = c

        ev_resp = client.get(IDDAA_EVENTS_URL)
        ev_resp.raise_for_status()
        ev_payload = ev_resp.json().get("data") or {}

    events = ev_payload.get("events") or []
    scores_map = ev_payload.get("sc") or {}

    bulletin: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("sid") != 1:
            continue

        ev_id = str(ev.get("i"))
        comp_info = comp_map.get(int(ev.get("ci") or 0), {})
        raw_league = comp_info.get("n") or comp_info.get("sn") or "Diğer Lig"
        league_name = format_league_name_tr(raw_league)
        country_code = comp_info.get("cid") or ""

        ts = ev.get("d")
        if not ts:
            continue
        start_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)

        # Check live/finished status from sc map if present
        sc_info = scores_map.get(ev_id) or {}
        raw_status = ev.get("s", 0)
        ht_raw = sc_info.get("ht")
        at_raw = sc_info.get("at")
        home_score = ht_raw.get("c") if isinstance(ht_raw, dict) else (ht_raw if isinstance(ht_raw, int) else None)
        away_score = at_raw.get("c") if isinstance(at_raw, dict) else (at_raw if isinstance(at_raw, int) else None)

        if raw_status == 0:
            status = "SCHEDULED"
        elif raw_status in (1, 2, 3):
            status = "LIVE"
        else:
            status = "SCHEDULED"

        odds = _extract_odds_from_markets(ev.get("m") or [])
        if not odds:
            continue

        home_team = str(ev.get("hn") or "").strip()
        away_team = str(ev.get("an") or "").strip()
        bulletin.append({
            "iddaa_id": ev_id,
            "betradar_id": str(ev.get("bri") or ""),
            "league": league_name,
            "country": country_code,
            "is_major_league": is_major_league(league_name, country_code, home_team, away_team),
            "home_team": home_team,
            "away_team": away_team,
            "start_time_utc": start_utc,
            "status": status,
            "home_score": home_score,
            "away_score": away_score,
            "odds": odds,
        })

    return bulletin
