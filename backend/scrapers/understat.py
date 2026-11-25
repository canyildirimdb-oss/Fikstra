import json
import re
from datetime import datetime, timezone
from typing import Optional

from curl_cffi import requests as cffi_requests
from sqlalchemy.orm import Session

from utils.cache import get_cached_json, set_cached_json
from utils.team_matcher import match_score, normalize_team_name

UNDERSTAT_LEAGUES = {
    "eng.1": "EPL",
    "esp.1": "La_liga",
    "ita.1": "Serie_A",
    "ger.1": "Bundesliga",
    "fra.1": "Ligue_1",
}

UNDERSTAT_TTL_HOURS = 12.0


def _current_season_year() -> int:
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def fetch_understat_league_xg(
    db: Session, espn_league_slug: str
) -> tuple[dict[str, dict[str, float]], str]:
    """
    Fetch True xG/xGA per game for a top-5 European league from Understat with 12h DB cache.
    Returns (team_xg_map, status_note) without fabricating any fallback numbers.
    """
    u_league = UNDERSTAT_LEAGUES.get(espn_league_slug)
    if not u_league:
        return {}, "unsupported_league"

    cache_key = f"understat:xg:{u_league}"
    cached = get_cached_json(db, cache_key, ttl_hours=UNDERSTAT_TTL_HOURS)
    if isinstance(cached, dict) and "teams" in cached:
        return cached.get("teams") or {}, str(cached.get("status") or "cached")

    season = _current_season_year()
    url = f"https://understat.com/league/{u_league}/{season}"
    teams_map: dict[str, dict[str, float]] = {}
    status = "ok"

    try:
        resp = cffi_requests.get(
            url,
            impersonate="chrome",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        html = resp.text or ""
        if resp.status_code != 200:
            status = f"http_{resp.status_code}"
        elif "errors.css" in html and "teamsData" not in html:
            status = "blocked_or_maintenance_page"
        else:
            m = re.search(r"var\s+teamsData\s*=\s*JSON\.parse\('([^']+)'\)", html)
            if not m:
                status = "teamsData_missing"
            else:
                raw_json = m.group(1).encode("utf-8").decode("unicode_escape")
                tdata = json.loads(raw_json)
                for _tid, info in tdata.items():
                    title = info.get("title") or ""
                    hist = info.get("history") or []
                    if not title or not hist:
                        continue
                    xg_sum = sum(float(h.get("xG") or 0.0) for h in hist)
                    xga_sum = sum(float(h.get("xGA") or 0.0) for h in hist)
                    n = len(hist)
                    norm = normalize_team_name(title)
                    if norm and n > 0:
                        teams_map[norm] = {
                            "xg_per_game": round(xg_sum / n, 3),
                            "xga_per_game": round(xga_sum / n, 3),
                            "matches": n,
                        }
    except Exception as exc:
        status = f"error:{type(exc).__name__}"

    set_cached_json(db, cache_key, {"status": status, "teams": teams_map})
    return teams_map, status


def lookup_understat_xg(
    team_name: str, teams_map: dict[str, dict[str, float]]
) -> Optional[dict[str, float]]:
    if not team_name or not teams_map:
        return None
    norm = normalize_team_name(team_name)
    if norm in teams_map:
        return teams_map[norm]
    best_val = None
    best_score = 0.0
    for cand_norm, vals in teams_map.items():
        score = match_score(norm, cand_norm)
        if score >= 84.0 and score > best_score:
            best_score = score
            best_val = vals
    return best_val
