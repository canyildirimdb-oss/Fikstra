import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from rapidfuzz import fuzz

TURKISH_MAP = str.maketrans({
    "ç": "c", "Ç": "c",
    "ğ": "g", "Ğ": "g",
    "ı": "i", "İ": "i",
    "ö": "o", "Ö": "o",
    "ş": "s", "Ş": "s",
    "ü": "u", "Ü": "u",
})

TEAM_ALIASES = {
    "g saray": "galatasaray",
    "gsaray": "galatasaray",
    "f bahce": "fenerbahce",
    "fbahce": "fenerbahce",
    "trabzon": "trabzonspor",
    "basaksehir": "istanbul basaksehir",
    "gaziantep": "gaziantep fk",
    "karagumruk": "fatih karagumruk",
    "a demirspor": "adana demirspor",
    "man city": "manchester city",
    "man utd": "manchester united",
    "man united": "manchester united",
    "nottm forest": "nottingham forest",
    "wolves": "wolverhampton wanderers",
    "atl madrid": "atletico madrid",
    "atletico": "atletico madrid",
    "ath bilbao": "athletic club",
    "real sociedad": "real sociedad",
    "b munih": "bayern munich",
    "bayern munih": "bayern munich",
    "b leverkusen": "bayer leverkusen",
    "b dortmund": "borussia dortmund",
    "m gladbach": "borussia monchengladbach",
    "e frankfurt": "eintracht frankfurt",
    "inter": "internazionale",
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "marsilya": "marseille",
    "ol lyon": "lyon",
    "sp lizbon": "sporting cp",
    "sporting lizbon": "sporting cp",
    "s prag": "slavia prague",
    "slavia prag": "slavia prague",
    "sparta prag": "sparta prague",
    "kopenhag": "fc copenhagen",
    "kizilyildiz": "crvena zvezda",
    "olympiakos": "olympiacos",
    "panathinaikos": "panathinaikos",
    "celtic": "celtic",
    "gl rangers": "rangers",
    "az alkmaar": "az alkmaar",
    "psv": "psv eindhoven",
    "st gilloise": "union saint gilloise",
    "bologna": "bologna",
    "atalanta": "atalanta",
    "juventus": "juventus",
    "milan": "ac milan",
    "roma": "as roma",
    "lazio": "lazio",
    "napoli": "napoli",
    "sofya": "sofia",
    "cska sofya": "cska sofia",
    "munih": "munich",
    "lizbon": "lisbon",
    "atina": "athens",
    "viyana": "vienna",
    "bukres": "bucharest",
    "varsova": "warsaw",
    "moskova": "moscow",
    "selanik": "thessaloniki",
    "belgrad": "belgrade",
    "budapeste": "budapest",
    "bruksel": "brussels",
    "zurih": "zurich",
    "cenevre": "geneva",
    "bruj": "brugge",
    "anvers": "antwerp",
    "sevil": "sevilla",
    "valensiya": "valencia",
    "benfika": "benfica",
    "zalgiris kaunas": "kauno zalgiris",
    "un craiova": "universitatea craiova",
    "kuopion": "kups",
    "st truidense": "sint truiden",
    "lincoln red": "lincoln red imps",
    "kf egnatia rrogozhine": "egnatia",
}

NOISE_TOKENS = {"fc", "fk", "sk", "cf", "sc", "ac", "as", "ss", "afc", "cfc", "bk", "ik", "if", "jk", "sv", "vfb", "tsg", "rc", "cd", "ud", "sd", "ca", "real", "club", "kf"}

NON_SENIOR_MEN_KEYWORDS = {
    "women", "femenino", "feminin", "feminine", "frauen", "ladies", "damer",
    "kvinner", "femminile", "kadin", "reserves", "reserve", "youth", "juvenil",
    "academy", "akatemia", "primavera", "castilla", "u17", "u18", "u19", "u20",
    "u21", "u22", "u23", "sub20", "sub23", "sub-20", "sub-23",
}


def is_non_senior_men_team(candidate_name: str, query_name: str = "", league_name: str = "") -> bool:
    """Return True if candidate looks like a women's, youth, or reserve team not requested in query."""
    q_low = (query_name or "").lower().translate(TURKISH_MAP)
    c_low = (candidate_name or "").lower().translate(TURKISH_MAP)
    l_low = (league_name or "").lower().translate(TURKISH_MAP)

    if "(w)" in c_low and "(w)" not in q_low:
        return True
    c_tokens = re.findall(r"[a-z0-9]+", c_low)
    q_tokens = set(re.findall(r"[a-z0-9]+", q_low))
    l_tokens = re.findall(r"[a-z0-9]+", l_low)

    for kw in NON_SENIOR_MEN_KEYWORDS:
        if (kw in c_tokens or kw in l_tokens) and kw not in q_tokens:
            return True

    # Trailing 'B', 'W', or 'II' reserve/women markers
    if c_tokens and c_tokens[-1] in {"b", "ii", "iii", "w"} and c_tokens[-1] not in q_tokens:
        return True

    return False


def _replace_city_tokens(tokens: list[str]) -> list[str]:
    city_map = {
        "sofya": "sofia",
        "munih": "munich",
        "lizbon": "lisbon",
        "atina": "athens",
        "viyana": "vienna",
        "bukres": "bucharest",
        "varsova": "warsaw",
        "kizilyildiz": "crvena",
        "moskova": "moscow",
        "selanik": "thessaloniki",
        "belgrad": "belgrade",
        "prag": "prague",
    }
    return [city_map.get(t, t) for t in tokens]


def normalize_team_name(name: str) -> str:
    if not name:
        return ""
    text = name.translate(TURKISH_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[.\-_/']", " ", text)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    if text in TEAM_ALIASES:
        text = TEAM_ALIASES[text]

    tokens = [t for t in text.split() if t not in NOISE_TOKENS]
    tokens = _replace_city_tokens(tokens)
    cleaned = " ".join(tokens) if tokens else text
    return TEAM_ALIASES.get(cleaned, cleaned)


def strict_team_match_score(query_name: str, candidate_name: str, league_name: str = "") -> float:
    """
    Strict single-team matching score (0-100) with women/youth/reserve rejection,
    distinctive number guard (CSKA Sofia != CSKA 1948), and distinctive city/qualifier guard.
    """
    if is_non_senior_men_team(candidate_name, query_name, league_name):
        return 0.0

    nq = normalize_team_name(query_name)
    nc = normalize_team_name(candidate_name)
    if not nq or not nc:
        return 0.0
    if nq == nc:
        return 100.0

    q_nums = {t for t in nq.split() if t.isdigit()}
    c_nums = {t for t in nc.split() if t.isdigit()}
    if q_nums != c_nums:
        # Distinctive year/number mismatch (e.g. CSKA Sofia vs CSKA 1948, Schalke 04)
        return 0.0

    q_words = set(nq.split())
    c_words = set(nc.split())
    # If both share a generic prefix like 'cska', 'dynamo', 'lokomotiv', 'zalgiris', 'spartak', 'rapid', 'olympik'
    # but have disjoint other non-trivial tokens (e.g. sofia vs moscow, kaunas vs vilnius), reject!
    shared = q_words & c_words
    if not shared:
        s = fuzz.token_sort_ratio(nq, nc)
        return float(s) if s >= 82 else 0.0

    q_diff = {w for w in (q_words - c_words) if len(w) >= 3}
    c_diff = {w for w in (c_words - q_words) if len(w) >= 3}
    if q_diff and c_diff:
        # Check if differing tokens are actually fuzzy-similar (e.g. truidense vs truiden)
        best_diff_sim = max(fuzz.ratio(qd, cd) for qd in q_diff for cd in c_diff)
        if best_diff_sim < 72:
            return 0.0

    s1 = fuzz.token_sort_ratio(nq, nc)
    s2 = fuzz.token_set_ratio(nq, nc) if len(shared) >= 1 and min(len(nq), len(nc)) >= 5 else 0.0
    return float(max(s1, s2 * 0.94))


def match_score(name_a: str, name_b: str) -> float:
    na = normalize_team_name(name_a)
    nb = normalize_team_name(name_b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    s1 = fuzz.token_sort_ratio(na, nb)
    s2 = fuzz.partial_ratio(na, nb) if min(len(na), len(nb)) >= 5 else 0.0
    return max(s1, s2 * 0.92)


def find_best_external_match(
    home_team: str,
    away_team: str,
    start_time_utc: datetime,
    candidates: list[dict],
    time_tolerance_hours: float = 2.0,
    min_score: float = 70.0,
) -> Optional[dict]:
    best = None
    best_combined = 0.0
    max_delta = timedelta(hours=time_tolerance_hours)

    if start_time_utc.tzinfo is None:
        start_time_utc = start_time_utc.replace(tzinfo=timezone.utc)

    for cand in candidates:
        cand_time: datetime = cand["start_time_utc"]
        if cand_time.tzinfo is None:
            cand_time = cand_time.replace(tzinfo=timezone.utc)
        if abs(cand_time - start_time_utc) > max_delta:
            continue

        hs = match_score(home_team, cand["home_team"])
        aws = match_score(away_team, cand["away_team"])
        if hs < min_score or aws < min_score:
            continue

        combined = (hs + aws) / 2.0
        if combined > best_combined:
            best_combined = combined
            best = cand

    return best
