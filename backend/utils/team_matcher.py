import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from rapidfuzz import fuzz

TURKISH_MAP = str.maketrans({
    "ç": "c", "Ç": "c",
    "ğ": "g", "Ğ": "g",
    "ı": "i", "İ": "i",
    "ö": "o", "Ö": "o",
    "ş": "s", "Ş": "s",
    "ü": "u", "Ü": "u",
    "ß": "ss",
    "ø": "o", "Ø": "o",
    "ł": "l", "Ł": "l",
    "đ": "d", "Đ": "d",
    "æ": "ae", "Æ": "ae",
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
    "monchengladbach": "borussia monchengladbach",
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
    "a de viseu": "academico de viseu",
    "aus lustenau": "austria lustenau",
    "o h leuven": "oud heverlee leuven",
    "oh leuven": "oud heverlee leuven",
    "oud heverlee leuven": "oud heverlee leuven",
}

NOISE_TOKENS = {
    "fc", "fk", "sk", "cf", "sc", "ac", "as", "ss", "afc", "cfc", "bk", "ik", "if",
    "jk", "sv", "vfb", "tsg", "rc", "cd", "ud", "sd", "ca", "club", "kf",
    "kv", "krc", "rsc", "kvc", "kaa", "spor", "kulubu", "a", "s",
}

NON_SENIOR_MEN_KEYWORDS = {
    "women", "femenino", "feminin", "feminine", "frauen", "ladies", "damer",
    "kvinner", "femminile", "kadin", "bayan", "reserves", "reserve", "rezerv",
    "youth", "juvenil", "academy", "akatemia", "primavera", "castilla",
    "genc", "u17", "u18", "u19", "u20", "u21", "u22", "u23",
    "sub20", "sub23", "sub-20", "sub-23",
}


def is_non_senior_men_team(candidate_name: str, query_name: str = "", league_name: str = "") -> bool:
    """Return True if candidate looks like a women's, youth, or reserve team not requested in query."""
    q_low = (query_name or "").lower().translate(TURKISH_MAP)
    c_low = (candidate_name or "").lower().translate(TURKISH_MAP)
    l_low = (league_name or "").lower().translate(TURKISH_MAP)

    for marker in ("(w)", "(k)"):
        if marker in c_low and marker not in q_low:
            return True

    c_tokens = re.findall(r"[a-z0-9]+", c_low)
    q_tokens = set(re.findall(r"[a-z0-9]+", q_low))
    l_tokens = re.findall(r"[a-z0-9]+", l_low)

    for kw in NON_SENIOR_MEN_KEYWORDS:
        if (kw in c_tokens or kw in l_tokens) and kw not in q_tokens:
            return True

    # Trailing 'B', 'W', 'K', 'II', 'III' reserve/women markers
    if c_tokens and c_tokens[-1] in {"b", "ii", "iii", "w", "k"} and c_tokens[-1] not in q_tokens:
        return True
    # Also check query having reserve/women marker while candidate doesn't (symmetric guard)
    if q_tokens and any(kw in q_tokens for kw in NON_SENIOR_MEN_KEYWORDS):
        for kw in NON_SENIOR_MEN_KEYWORDS:
            if kw in q_tokens and kw not in c_tokens and kw not in l_tokens:
                return True
    q_raw_tokens = re.findall(r"[a-z0-9]+", q_low)
    if q_raw_tokens and q_raw_tokens[-1] in {"b", "ii", "iii", "w", "k"} and q_raw_tokens[-1] not in c_tokens:
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
    # Collapse Turkish corporate suffix "a.ş." / "a.s." / "a ş" before punctuation split
    text = re.sub(r"\ba\.?\s*s\.?\b", " ", text)
    text = re.sub(r"[.\-_/']", " ", text)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    if text in TEAM_ALIASES:
        text = TEAM_ALIASES[text]

    tokens = [t for t in text.split() if t not in NOISE_TOKENS]
    tokens = _replace_city_tokens(tokens)
    cleaned = " ".join(tokens) if tokens else text
    return TEAM_ALIASES.get(cleaned, cleaned)


def _abbreviated_prefix_match_score(nq: str, nc: str) -> float:
    """
    Detect abbreviated prefix / initialism patterns with a shared distinctive tail, e.g.:
    - 'a de viseu' vs 'academico de viseu'
    - 'aus lustenau' vs 'austria lustenau'
    - 'o h leuven' / 'oh leuven' vs 'oud heverlee leuven'
    - 'b dortmund' vs 'borussia dortmund'
    """
    qt = nq.split()
    ct = nc.split()
    if len(qt) < 2 or len(ct) < 2:
        return 0.0

    # Tail token(s) must match strongly (distinctive club/city name, len >= 4)
    if qt[-1] != ct[-1] or len(qt[-1]) < 4:
        return 0.0

    q_head = qt[:-1]
    c_head = ct[:-1]

    # Case 1: Same number of tokens, every head token is prefix/initial of counterpart
    if len(q_head) == len(c_head):
        all_ok = True
        for a, b in zip(q_head, c_head):
            if a == b:
                continue
            if len(a) >= 1 and b.startswith(a):
                continue
            if len(b) >= 1 and a.startswith(b):
                continue
            all_ok = False
            break
        if all_ok:
            return 90.0

    # Case 2: Combined initialism in head, e.g. 'oh' vs ['oud', 'heverlee']
    q_init = "".join(t[0] for t in q_head)
    c_init = "".join(t[0] for t in c_head)
    if "".join(q_head) == c_init or "".join(c_head) == q_init:
        return 89.0

    return 0.0


def build_team_search_queries(team_name: str) -> list[str]:
    """Build ordered search queries for external team lookup (FotMob / 365Scores)."""
    queries: list[str] = []
    seen: set[str] = set()

    def _add(q: str) -> None:
        q = (q or "").strip()
        ql = q.lower()
        if q and ql not in seen:
            seen.add(ql)
            queries.append(q)

    _add(team_name)
    norm_q = normalize_team_name(team_name)
    _add(norm_q)

    # Distinctive last token (e.g. 'Lustenau', 'Viseu', 'Leuven') when abbreviated prefix fails search API
    tokens = [t for t in norm_q.split() if len(t) >= 5]
    if tokens:
        _add(tokens[-1])
        if len(tokens) >= 2:
            _add(" ".join(tokens[-2:]))

    return queries


def strict_team_match_score(query_name: str, candidate_name: str, league_name: str = "") -> float:
    """
    Strict single-team matching score (0-100) with women/youth/reserve rejection,
    distinctive number guard (CSKA Sofia != CSKA 1948), abbreviated prefix support,
    and distinctive city/qualifier guard.
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

    abbr_score = _abbreviated_prefix_match_score(nq, nc)
    if abbr_score >= 88.0:
        return abbr_score

    q_words = set(nq.split())
    c_words = set(nc.split())
    shared = q_words & c_words
    if not shared:
        s = fuzz.token_sort_ratio(nq, nc)
        return float(s) if s >= 82 else 0.0

    q_diff = {w for w in (q_words - c_words) if len(w) >= 3}
    c_diff = {w for w in (c_words - q_words) if len(w) >= 3}
    if q_diff and c_diff:
        best_diff_sim = max(fuzz.ratio(qd, cd) for qd in q_diff for cd in c_diff)
        if best_diff_sim < 72:
            return 0.0

    s1 = fuzz.token_sort_ratio(nq, nc)
    s2 = fuzz.token_set_ratio(nq, nc) if len(shared) >= 1 and min(len(nq), len(nc)) >= 5 else 0.0
    return float(max(s1, s2 * 0.94))


def match_score(name_a: str, name_b: str, league_name: str = "") -> float:
    if is_non_senior_men_team(name_b, name_a, league_name):
        return 0.0
    na = normalize_team_name(name_a)
    nb = normalize_team_name(name_b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    abbr = _abbreviated_prefix_match_score(na, nb)
    if abbr >= 88.0:
        return abbr
    s1 = fuzz.token_sort_ratio(na, nb)
    s2 = fuzz.partial_ratio(na, nb) if min(len(na), len(nb)) >= 5 else 0.0
    return max(s1, s2 * 0.92)


def find_best_external_match(
    home_team: str,
    away_team: str,
    start_time_utc: datetime,
    candidates: list[dict[str, Any]],
    time_tolerance_hours: float = 2.0,
    min_score: float = 70.0,
    external_match_id: Optional[str] = None,
    league_name: str = "",
) -> Optional[dict[str, Any]]:
    if start_time_utc.tzinfo is None:
        start_time_utc = start_time_utc.replace(tzinfo=timezone.utc)

    # 1. Direct external_match_id priority lookup
    if external_match_id:
        ext_id_str = str(external_match_id).strip()
        for cand in candidates:
            cand_id = str(cand.get("espn_id") or cand.get("external_id") or cand.get("id") or "").strip()
            if cand_id and cand_id == ext_id_str:
                cand_league = str(cand.get("league_name") or cand.get("league_slug") or "")
                if not is_non_senior_men_team(cand.get("home_team", ""), home_team, cand_league) and not is_non_senior_men_team(cand.get("away_team", ""), away_team, cand_league):
                    return cand

    best = None
    best_combined = 0.0
    max_delta = timedelta(hours=time_tolerance_hours)

    for cand in candidates:
        cand_time: datetime = cand["start_time_utc"]
        if cand_time.tzinfo is None:
            cand_time = cand_time.replace(tzinfo=timezone.utc)
        if abs(cand_time - start_time_utc) > max_delta:
            continue

        cand_league = str(cand.get("league_name") or cand.get("league_slug") or league_name)
        hs = match_score(home_team, cand["home_team"], cand_league)
        aws = match_score(away_team, cand["away_team"], cand_league)

        # Standard both-sides threshold, OR Opponent + Kickoff paired cross-match:
        # if one side is strongly matched (>= 82) and the other is >= 60 within kickoff window, allow it.
        both_ok = hs >= min_score and aws >= min_score
        paired_ok = (hs >= 82.0 and aws >= 60.0) or (aws >= 82.0 and hs >= 60.0)
        if not (both_ok or paired_ok):
            continue

        combined = (hs + aws) / 2.0
        if combined > best_combined:
            best_combined = combined
            best = cand

    return best
