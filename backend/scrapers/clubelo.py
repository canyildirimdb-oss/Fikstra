import csv
import io
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from utils.cache import get_cached_json, set_cached_json
from utils.team_matcher import match_score, normalize_team_name

CLUBELO_CACHE_KEY = "clubelo:ratings:v2"
CLUBELO_TTL_HOURS = 24.0

_MEM_ENTRIES: list[dict] = []

NON_EUROPEAN_KEYWORDS = (
    "kolombiya", "ekvador", "arjantin", "brezilya", "meksika", "abd", "mls",
    "japonya", "kore", "cin", "suudi", "avustralya", "misir", "fas", "sili",
    "peru", "uruguay", "paraguay", "bolivya", "venezuela", "kosta rika",
    "guney afrika", "tunus", "cezayir", "nijerya", "hindistan", "tayland",
    "endonezya", "malezya", "katar", "bae", "birlesik arap", "afc",
    "conmebol", "concacaf", "caf", "asya", "afrika", "guney amerika",
)

LEAGUE_TO_COUNTRY = {
    "ingiltere": "ENG",
    "premier lig": "ENG",
    "championship": "ENG",
    "ispanya": "ESP",
    "la liga": "ESP",
    "italya": "ITA",
    "serie a": "ITA",
    "serie b": "ITA",
    "almanya": "GER",
    "bundesliga": "GER",
    "fransa": "FRA",
    "ligue 1": "FRA",
    "ligue 2": "FRA",
    "turkiye": "TUR",
    "super lig": "TUR",
    "portekiz": "POR",
    "primeira liga": "POR",
    "hollanda": "NED",
    "eredivisie": "NED",
    "belcika": "BEL",
    "iskocya": "SCO",
    "cekya": "CZE",
    "isvicre": "SUI",
    "avusturya": "AUT",
    "yunanistan": "GRE",
    "norvec": "NOR",
    "isvec": "SWE",
    "danimarka": "DEN",
    "polonya": "POL",
    "ukrayna": "UKR",
    "rusya": "RUS",
    "hirvatistan": "CRO",
    "sirbistan": "SRB",
    "romanya": "ROU",
    "macaristan": "HUN",
    "bulgaristan": "BUL",
    "slovakya": "SVK",
    "slovenya": "SVN",
    "irlanda": "IRL",
    "israil": "ISR",
    "finlandiya": "FIN",
    "izlanda": "ISL",
}

UEFA_KEYWORDS = ("sampiyonlar ligi", "avrupa ligi", "konferans ligi", "uefa")

# Explicit (normalized_name_or_raw -> (clubelo_norm, country_code))
CLUBELO_EXACT_ALIASES: dict[str, tuple[str, str]] = {
    "sporting lizbon": ("sporting", "POR"),
    "sporting cp": ("sporting", "POR"),
    "sp lizbon": ("sporting", "POR"),
    "psg": ("paris sg", "FRA"),
    "paris sg": ("paris sg", "FRA"),
    "paris saint germain": ("paris sg", "FRA"),
    "fenerbahce": ("fenerbahce", "TUR"),
    "galatasaray": ("galatasaray", "TUR"),
    "besiktas": ("besiktas", "TUR"),
    "trabzonspor": ("trabzonspor", "TUR"),
    "shakhtar donetsk": ("shakhtar", "UKR"),
    "slavia prag": ("slavia praha", "CZE"),
    "slavia prague": ("slavia praha", "CZE"),
    "sparta prag": ("sparta praha", "CZE"),
    "manchester united": ("man united", "ENG"),
    "manchester city": ("man city", "ENG"),
    "atletico madrid": ("atletico", "ESP"),
    "inter": ("inter", "ITA"),
    "internazionale": ("inter", "ITA"),
    "ac milan": ("milan", "ITA"),
    "as roma": ("roma", "ITA"),
    "bayern munih": ("bayern", "GER"),
    "bayern munich": ("bayern", "GER"),
    "bayer leverkusen": ("leverkusen", "GER"),
    "borussia dortmund": ("dortmund", "GER"),
    "eintracht frankfurt": ("frankfurt", "GER"),
    "rb leipzig": ("rb leipzig", "GER"),
    "stuttgart": ("stuttgart", "GER"),
    "psv eindhoven": ("psv", "NED"),
    "az alkmaar": ("az alkmaar", "NED"),
    "union saint gilloise": ("st gilloise", "BEL"),
    "fc copenhagen": ("koebenhavn", "DEN"),
    "kopenhag": ("koebenhavn", "DEN"),
    "crvena zvezda": ("crvena zvezda", "SRB"),
}

GENERIC_SINGLE_TOKENS = {
    "sporting", "barcelona", "america", "inter", "united", "city",
    "real", "athletic", "atletico", "dynamo", "lokomotiv", "spartak",
    "cska", "olympic", "olympique", "racing", "nacional", "independiente",
}


def _tr_ascii(text: str) -> str:
    tr = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return " ".join((text or "").translate(tr).lower().split())


def resolve_european_context(league: str, country: str = "") -> Optional[str]:
    """
    Return ClubElo country code (e.g. 'ENG', 'TUR') for European domestic leagues,
    'UEFA' for UEFA club competitions, or None for non-European leagues.
    """
    combined = f"{_tr_ascii(league)} {_tr_ascii(country)}"
    if any(k in combined for k in NON_EUROPEAN_KEYWORDS):
        return None
    if any(k in combined for k in UEFA_KEYWORDS):
        return "UEFA"
    for kw, code in LEAGUE_TO_COUNTRY.items():
        if kw in combined:
            return code
    return None


def _parse_api_csv(text: str) -> list[dict]:
    entries: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        club = (row.get("Club") or "").strip()
        ctry = (row.get("Country") or "").strip().upper()
        elo_raw = (row.get("Elo") or "").strip()
        if not club or not elo_raw:
            continue
        try:
            elo = round(float(elo_raw), 1)
        except ValueError:
            continue
        norm = normalize_team_name(club)
        if norm:
            entries.append({"club": club, "norm": norm, "country": ctry, "elo": elo})
    return entries


def _parse_ranking_html(html: str) -> list[dict]:
    entries: list[dict] = []
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        elo_txt = tds[1].get_text(strip=True)
        if not elo_txt.isdigit():
            continue
        a = tds[0].find("a")
        href = (a.get("href") or "") if a else ""
        ctry = href.strip("/").upper()[:3] if href.startswith("/") else ""
        raw_name = tds[0].get_text(" ", strip=True)
        clean_name = re.sub(r"^\d+\s+[A-Z0-9]{2,4}\s+", "", raw_name).strip()
        if not clean_name:
            continue
        norm = normalize_team_name(clean_name)
        if norm:
            entries.append({"club": clean_name, "norm": norm, "country": ctry, "elo": float(elo_txt)})
    return entries


def load_clubelo_ratings(db: Session) -> list[dict]:
    """Load ClubElo entries [{club, norm, country, elo}] with 24h DB cache."""
    global _MEM_ENTRIES
    if _MEM_ENTRIES:
        return _MEM_ENTRIES

    cached = get_cached_json(db, CLUBELO_CACHE_KEY, ttl_hours=CLUBELO_TTL_HOURS)
    if isinstance(cached, list) and cached:
        _MEM_ENTRIES = cached
        return _MEM_ENTRIES

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entries: list[dict] = []
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        resp = httpx.get(
            f"http://api.clubelo.com/{today}",
            timeout=5.0,
            follow_redirects=True,
            headers=headers,
        )
        if resp.status_code == 200 and "Club,Country,Level,Elo" in resp.text[:200]:
            entries = _parse_api_csv(resp.text)
    except Exception:
        entries = []

    if not entries:
        try:
            resp = httpx.get(
                "http://clubelo.com/Ranking",
                timeout=12.0,
                follow_redirects=True,
                headers=headers,
            )
            if resp.status_code == 200 and len(resp.text) > 50_000:
                entries = _parse_ranking_html(resp.text)
        except Exception:
            entries = []

    if entries:
        _MEM_ENTRIES = entries
        set_cached_json(db, CLUBELO_CACHE_KEY, entries)
    return entries


def lookup_clubelo(
    team_name: str,
    entries: list[dict],
    league: str = "",
    country: str = "",
) -> Optional[float]:
    """
    Strictly match a European team against ClubElo entries.
    Returns None for non-European leagues or ambiguous/unverified matches.
    """
    if not team_name or not entries:
        return None

    eu_ctx = resolve_european_context(league, country)
    if eu_ctx is None:
        return None

    raw_clean = _tr_ascii(team_name)
    norm = normalize_team_name(team_name)
    if not norm:
        return None

    # 1) Explicit verified alias check
    alias = CLUBELO_EXACT_ALIASES.get(raw_clean) or CLUBELO_EXACT_ALIASES.get(norm)
    if alias:
        target_norm, target_ctry = alias
        for e in entries:
            if e["country"] == target_ctry and (
                e["norm"] == target_norm or _tr_ascii(e["club"]) == target_norm
            ):
                return float(e["elo"])

    # Filter candidate pool by domestic country if not UEFA
    pool = entries if eu_ctx == "UEFA" else [e for e in entries if e["country"] == eu_ctx]
    if not pool:
        return None

    # 2) Exact normalized match in pool
    for e in pool:
        if e["norm"] == norm or _tr_ascii(e["club"]) == raw_clean:
            if eu_ctx == "UEFA" and norm in GENERIC_SINGLE_TOKENS:
                continue
            return float(e["elo"])

    # 3) Strict fuzzy match (never allow generic single-token cross-country guesses)
    is_generic_single = len(norm.split()) == 1 and norm in GENERIC_SINGLE_TOKENS
    if eu_ctx == "UEFA" and is_generic_single:
        return None

    min_thresh = 90.0 if eu_ctx == "UEFA" else 86.0
    best_elo: Optional[float] = None
    best_score = 0.0
    for e in pool:
        if eu_ctx == "UEFA" and e["norm"] in GENERIC_SINGLE_TOKENS:
            continue
        score = match_score(norm, e["norm"])
        if score >= min_thresh and score > best_score:
            best_score = score
            best_elo = float(e["elo"])
    return best_elo
