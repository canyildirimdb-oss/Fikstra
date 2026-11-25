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
}

NOISE_TOKENS = {"fc", "fk", "sk", "cf", "sc", "ac", "as", "ss", "afc", "cfc", "bk", "ik", "if", "jk", "sv", "vfb", "tsg", "rc", "cd", "ud", "sd", "real", "club"}


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
    cleaned = " ".join(tokens) if tokens else text
    return TEAM_ALIASES.get(cleaned, cleaned)


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
