import json
import re
import statistics
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from rapidfuzz import fuzz

from utils.time_utils import to_tr_datetime

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_LEAGUES = [
    "all",
    "tur.1",
    "eng.1",
    "esp.1",
    "ita.1",
    "ger.1",
    "fra.1",
    "ned.1",
    "por.1",
    "uefa.champions",
    "uefa.europa",
    "uefa.europa.conf",
]

# In-memory cache with 6h TTL for Transfermarkt club injury lookups
_TM_INJURY_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_TM_CACHE_TTL_SEC = 6 * 3600.0


def american_to_decimal(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s or s == "OFF":
        return None
    if s in {"EVEN", "EV"}:
        return 2.00
    try:
        val = int(s.replace("+", ""))
        if val > 0:
            return round(1.0 + (val / 100.0), 2)
        if val < 0:
            return round(1.0 + (100.0 / abs(val)), 2)
    except ValueError:
        return None
    return None


def extract_regular_time_score(
    comp: dict[str, Any],
    home_comp: dict[str, Any],
    away_comp: dict[str, Any],
    status_obj: dict[str, Any],
) -> dict[str, Any]:
    """
    Parse ESPN competition status and details to enforce 90-minute regular-time settlement
    and detect postponed/canceled/abandoned or AET/PEN matches.
    """
    status_name = str(status_obj.get("name") or "").upper()
    status_detail = str(status_obj.get("shortDetail") or status_obj.get("detail") or "").upper()
    is_void_status = any(
        k in status_name or k in status_detail
        for k in ("POSTPONED", "CANCELED", "CANCELLED", "ABANDONED", "SUSPENDED")
    )

    has_shootout = (
        home_comp.get("shootoutScore") is not None
        or away_comp.get("shootoutScore") is not None
    )
    is_aet_or_pen = has_shootout or any(
        k in status_name or k in status_detail
        for k in ("AET", "PEN", "SHOOTOUT", "EXTRA")
    )

    home_score = _extract_score_int(home_comp.get("score"))
    away_score = _extract_score_int(away_comp.get("score"))
    reg_home_score: Optional[int] = home_score
    reg_away_score: Optional[int] = away_score

    if is_aet_or_pen:
        reg_home_score = None
        reg_away_score = None
        home_id = str((home_comp.get("team") or {}).get("id") or "")
        away_id = str((away_comp.get("team") or {}).get("id") or "")
        details = comp.get("details") or []

        # If final score is 0-0 (went to penalties after 0-0 AET), 90m is unambiguously 0-0
        if home_score == 0 and away_score == 0:
            reg_home_score = 0
            reg_away_score = 0
        elif details and home_id and away_id:
            rh = 0
            ra = 0
            parsed_any_goal = False
            unparseable_clock = False
            for d in details:
                is_goal = bool(d.get("scoringPlay"))
                if not is_goal:
                    continue
                if d.get("shootout"):
                    continue
                clk_disp = str((d.get("clock") or {}).get("displayValue") or "").strip()
                m_min = re.match(r"^(\d+)", clk_disp)
                if not m_min:
                    unparseable_clock = True
                    break
                minute = int(m_min.group(1))
                # Stoppage time in 90th min appears as "90'+2'"; extra time starts at "91'"
                if minute <= 90:
                    tid = str((d.get("team") or {}).get("id") or "")
                    if tid == home_id:
                        rh += 1
                        parsed_any_goal = True
                    elif tid == away_id:
                        ra += 1
                        parsed_any_goal = True
            if not unparseable_clock and (parsed_any_goal or (home_score == 0 and away_score == 0)):
                reg_home_score = rh
                reg_away_score = ra

    return {
        "status_name": status_name,
        "status_detail": status_detail,
        "is_void_status": is_void_status,
        "is_aet_or_pen": is_aet_or_pen,
        "reg_home_score": reg_home_score,
        "reg_away_score": reg_away_score,
    }


def fetch_espn_events_for_dates(dates_yyyymmdd: list[str], timeout: float = 15.0) -> list[dict[str, Any]]:
    """Fetch upcoming, live, and recent soccer fixtures from ESPN public API."""
    seen_ids: set[str] = set()
    results: list[dict[str, Any]] = []

    with httpx.Client(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for d_str in dates_yyyymmdd:
            for league in ESPN_LEAGUES:
                url = f"{ESPN_BASE}/{league}/scoreboard?dates={d_str}"
                try:
                    resp = client.get(url)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                except Exception:
                    continue

                leagues_meta = data.get("leagues") or []
                league_logo_url = None
                if leagues_meta:
                    l_logos = leagues_meta[0].get("logos") or []
                    if l_logos and isinstance(l_logos[0], dict):
                        league_logo_url = l_logos[0].get("href")

                for ev in data.get("events") or []:
                    eid = str(ev.get("id") or "")
                    if not eid or eid in seen_ids:
                        continue
                    seen_ids.add(eid)

                    comps = ev.get("competitions") or []
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors") or []
                    home_comp = next((c for c in competitors if c.get("homeAway") == "home"), None)
                    away_comp = next((c for c in competitors if c.get("homeAway") == "away"), None)
                    if not home_comp or not away_comp:
                        continue

                    raw_date = ev.get("date")
                    if not raw_date:
                        continue
                    try:
                        start_utc = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    except ValueError:
                        continue

                    status_obj = comp.get("status", {}).get("type", {})
                    completed = bool(status_obj.get("completed"))
                    state = status_obj.get("state", "pre")
                    home_score = None
                    away_score = None
                    if state in ("in", "post") or completed:
                        home_score = _extract_score_int(home_comp.get("score"))
                        away_score = _extract_score_int(away_comp.get("score"))

                    rt_info = extract_regular_time_score(comp, home_comp, away_comp, status_obj)

                    home_team_obj = home_comp.get("team") or {}
                    away_team_obj = away_comp.get("team") or {}
                    home_logo_url = home_team_obj.get("logo") or (
                        (home_team_obj.get("logos") or [{}])[0].get("href")
                        if home_team_obj.get("logos")
                        else None
                    )
                    away_logo_url = away_team_obj.get("logo") or (
                        (away_team_obj.get("logos") or [{}])[0].get("href")
                        if away_team_obj.get("logos")
                        else None
                    )

                    results.append({
                        "espn_id": eid,
                        "league_slug": league,
                        "home_team": home_team_obj.get("displayName", ""),
                        "away_team": away_team_obj.get("displayName", ""),
                        "home_espn_id": str(home_team_obj.get("id", "")),
                        "away_espn_id": str(away_team_obj.get("id", "")),
                        "home_logo_url": home_logo_url,
                        "away_logo_url": away_logo_url,
                        "league_logo_url": league_logo_url,
                        "start_time_utc": start_utc,
                        "completed": completed,
                        "state": state,
                        "home_score": home_score,
                        "away_score": away_score,
                        **rt_info,
                    })

    return results


FORM_TIME_WEIGHTS = [1.00, 0.85, 0.70, 0.55, 0.45, 0.38, 0.32, 0.27, 0.22, 0.18]


def _extract_score_int(raw_score: Any) -> Optional[int]:
    if isinstance(raw_score, dict):
        val = raw_score.get("value")
        if val is None:
            val = raw_score.get("displayValue")
    else:
        val = raw_score
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _fetch_team_schedule_profile(
    client: httpx.Client,
    team_id: str,
    match_start_utc: Optional[datetime],
    prefer_home: Optional[bool] = None,
    exclude_event_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Fetch last 10 completed official matches across all competitions from ESPN team schedule.
    Returns last_dt, 10-match weighted averages, goal std dev, venue splits, recent_refs, and recent_matches.
    """
    empty: dict[str, Any] = {
        "last_dt": None,
        "form_10": [],
        "goals_avg_10": None,
        "conceded_avg_10": None,
        "goal_std": None,
        "home_played_10": 0,
        "home_gf_10": None,
        "home_ga_10": None,
        "away_played_10": 0,
        "away_gf_10": None,
        "away_ga_10": None,
        "recent_refs": [],
        "recent_matches": [],
    }
    if not team_id:
        return empty
    now_utc = datetime.now(timezone.utc)
    ref_utc = (
        match_start_utc.replace(tzinfo=timezone.utc)
        if (match_start_utc and match_start_utc.tzinfo is None)
        else (match_start_utc or now_utc)
    )
    if ref_utc > now_utc:
        ref_utc = now_utc
    try:
        r = client.get(f"{ESPN_BASE}/all/teams/{team_id}/schedule")
        if r.status_code != 200:
            return empty
        events = r.json().get("events") or []
    except Exception:
        return empty

    completed: list[tuple[datetime, dict[str, Any]]] = []
    seen_eids: set[str] = set()
    excl = str(exclude_event_id or "").strip()
    for ev in events:
        eid = str(ev.get("id") or "").strip()
        if excl and eid == excl:
            continue
        if eid and eid in seen_eids:
            continue
        comp = (ev.get("competitions") or [{}])[0]
        if not comp.get("status", {}).get("type", {}).get("completed"):
            continue
        raw = ev.get("date") or ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt < ref_utc:
            if eid:
                seen_eids.add(eid)
            completed.append((dt, ev))

    if not completed:
        return empty

    completed.sort(key=lambda x: x[0], reverse=True)
    empty["last_dt"] = completed[0][0]

    gf_list: list[int] = []
    ga_list: list[int] = []
    gf_w_sum = 0.0
    ga_w_sum = 0.0
    w_total = 0.0
    venue_gf_sum = 0.0
    venue_ga_sum = 0.0
    venue_w_total = 0.0
    home_gfs: list[int] = []
    home_gas: list[int] = []
    away_gfs: list[int] = []
    away_gas: list[int] = []
    form_10: list[str] = []
    recent_refs: list[dict[str, str]] = []
    recent_matches: list[dict[str, Any]] = []

    for idx, (dt, ev) in enumerate(completed[:10]):
        comp = (ev.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        my_comp = next((c for c in competitors if str((c.get("team") or {}).get("id", "")) == str(team_id)), None)
        opp_comp = next((c for c in competitors if str((c.get("team") or {}).get("id", "")) != str(team_id)), None)
        if not my_comp or not opp_comp:
            continue

        gf = _extract_score_int(my_comp.get("score"))
        ga = _extract_score_int(opp_comp.get("score"))
        if gf is None or ga is None:
            continue

        is_home = my_comp.get("homeAway") == "home"
        w = FORM_TIME_WEIGHTS[idx] if idx < len(FORM_TIME_WEIGHTS) else 0.18

        gf_list.append(gf)
        ga_list.append(ga)
        gf_w_sum += gf * w
        ga_w_sum += ga * w
        w_total += w

        if prefer_home is not None and is_home == prefer_home:
            venue_gf_sum += gf * w
            venue_ga_sum += ga * w
            venue_w_total += w

        if is_home:
            home_gfs.append(gf)
            home_gas.append(ga)
        else:
            away_gfs.append(gf)
            away_gas.append(ga)

        res = "W" if gf > ga else ("D" if gf == ga else "L")
        form_10.append(res)

        eid = str(ev.get("id") or "")
        if len(recent_refs) < 5 and eid:
            lslug = str((ev.get("league") or {}).get("slug") or "all")
            recent_refs.append({"event_id": eid, "league_slug": lslug})

        if len(recent_matches) < 5:
            my_name = (my_comp.get("team") or {}).get("displayName") or (my_comp.get("team") or {}).get("shortDisplayName") or ""
            opp_name = (opp_comp.get("team") or {}).get("displayName") or (opp_comp.get("team") or {}).get("shortDisplayName") or ""
            recent_matches.append({
                "event_id": eid,
                "date_tr": to_tr_datetime(dt).strftime("%d.%m"),
                "home_team": my_name if is_home else opp_name,
                "away_team": opp_name if is_home else my_name,
                "opponent": opp_name,
                "is_home": is_home,
                "home_score": gf if is_home else ga,
                "away_score": ga if is_home else gf,
                "result": res,
            })

    if w_total > 0 and venue_w_total >= 1.2:
        goals_avg_10 = round(((gf_w_sum / w_total) * 0.65) + ((venue_gf_sum / venue_w_total) * 0.35), 2)
        conceded_avg_10 = round(((ga_w_sum / w_total) * 0.65) + ((venue_ga_sum / venue_w_total) * 0.35), 2)
    elif w_total > 0:
        goals_avg_10 = round(gf_w_sum / w_total, 2)
        conceded_avg_10 = round(ga_w_sum / w_total, 2)
    else:
        goals_avg_10 = None
        conceded_avg_10 = None

    goal_std: Optional[float] = None
    if len(gf_list) >= 3:
        std_gf = statistics.pstdev(gf_list)
        std_ga = statistics.pstdev(ga_list)
        goal_std = round((std_gf + std_ga) / 2.0, 3)

    return {
        "last_dt": empty["last_dt"],
        "form_10": form_10,
        "goals_avg_10": goals_avg_10,
        "conceded_avg_10": conceded_avg_10,
        "goal_std": goal_std,
        "home_played_10": len(home_gfs),
        "home_gf_10": round(sum(home_gfs) / len(home_gfs), 3) if home_gfs else None,
        "home_ga_10": round(sum(home_gas) / len(home_gas), 3) if home_gas else None,
        "away_played_10": len(away_gfs),
        "away_gf_10": round(sum(away_gfs) / len(away_gfs), 3) if away_gfs else None,
        "away_ga_10": round(sum(away_gas) / len(away_gas), 3) if away_gas else None,
        "recent_refs": recent_refs,
        "recent_matches": recent_matches,
    }


def fetch_espn_team_shot_xg(
    db: Any,
    team_espn_id: str,
    recent_refs: list[dict[str, str]],
    timeout: float = 10.0,
) -> dict[str, Any]:
    """
    Compute Shot-xG and Shot-xGA from ESPN match summary boxscore (last 3-4 completed matches),
    cached in ScraperCache for 12 hours.
    Calibrated formula (v5.1): shot_xg = 0.22 * shots_on_target + 0.045 * max(0, total_shots - shots_on_target).
    """
    empty = {
        "shot_xg": None,
        "shot_xga": None,
        "shots_pg": None,
        "sot_pg": None,
        "matches_sampled": 0,
    }
    if not team_espn_id or not recent_refs:
        return empty
    from utils.cache import get_cached_json, set_cached_json

    cache_key = f"espn_shot_xg:v2:{team_espn_id}"
    cached = get_cached_json(db, cache_key, ttl_hours=12.0)
    if isinstance(cached, dict) and "matches_sampled" in cached:
        return cached

    xg_vals: list[float] = []
    xga_vals: list[float] = []
    shots_vals: list[float] = []
    sot_vals: list[float] = []

    with httpx.Client(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for ref in recent_refs[:4]:
            eid = ref.get("event_id")
            lslug = ref.get("league_slug") or "all"
            if not eid:
                continue
            try:
                resp = client.get(f"{ESPN_BASE}/{lslug}/summary?event={eid}")
                if resp.status_code != 200:
                    continue
                teams_box = (resp.json().get("boxscore") or {}).get("teams") or []
                if len(teams_box) < 2:
                    continue
                my_box = next((t for t in teams_box if str((t.get("team") or {}).get("id", "")) == str(team_espn_id)), None)
                opp_box = next((t for t in teams_box if str((t.get("team") or {}).get("id", "")) != str(team_espn_id)), None)
                if not my_box or not opp_box:
                    continue

                def _parse_shots(box: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
                    st = {s.get("name"): s.get("displayValue") for s in (box.get("statistics") or [])}
                    try:
                        tot = float(st["totalShots"]) if st.get("totalShots") is not None else None
                        sot = float(st["shotsOnTarget"]) if st.get("shotsOnTarget") is not None else None
                        return tot, sot
                    except (TypeError, ValueError):
                        return None, None

                my_tot, my_sot = _parse_shots(my_box)
                opp_tot, opp_sot = _parse_shots(opp_box)
                if my_tot is None or my_sot is None or opp_tot is None or opp_sot is None:
                    continue

                my_sxg = 0.22 * my_sot + 0.045 * max(0.0, my_tot - my_sot)
                opp_sxg = 0.22 * opp_sot + 0.045 * max(0.0, opp_tot - opp_sot)
                xg_vals.append(my_sxg)
                xga_vals.append(opp_sxg)
                shots_vals.append(my_tot)
                sot_vals.append(my_sot)
            except Exception:
                continue

    if xg_vals:
        res = {
            "shot_xg": round(sum(xg_vals) / len(xg_vals), 3),
            "shot_xga": round(sum(xga_vals) / len(xga_vals), 3),
            "shots_pg": round(sum(shots_vals) / len(shots_vals), 2),
            "sot_pg": round(sum(sot_vals) / len(sot_vals), 2),
            "matches_sampled": len(xg_vals),
        }
    else:
        res = empty

    set_cached_json(db, cache_key, res)
    return res


def _parse_espn_form(
    team_block: dict[str, Any],
    match_start_utc: Optional[datetime] = None,
    prefer_home: Optional[bool] = None,
    schedule_last_dt: Optional[datetime] = None,
    exclude_event_id: Optional[str] = None,
) -> dict[str, Any]:
    team_id = str(team_block.get("team", {}).get("id", ""))
    team_name = str(team_block.get("team", {}).get("displayName") or team_block.get("team", {}).get("shortDisplayName") or "")
    events = team_block.get("events") or []

    # Sort events most recent first by gameDate if available
    def _ev_dt(ev: dict[str, Any]) -> datetime:
        raw = ev.get("gameDate") or ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    now_utc = datetime.now(timezone.utc)
    ref_utc = (
        match_start_utc.replace(tzinfo=timezone.utc)
        if (match_start_utc and match_start_utc.tzinfo is None)
        else (match_start_utc or now_utc)
    )
    if ref_utc > now_utc:
        ref_utc = now_utc

    excl = str(exclude_event_id or "").strip()
    seen_eids: set[str] = set()
    filtered_events: list[dict[str, Any]] = []
    for ev in sorted(events, key=_ev_dt, reverse=True):
        eid = str(ev.get("id") or "").strip()
        if excl and eid == excl:
            continue
        if eid and eid in seen_eids:
            continue
        g_dt = _ev_dt(ev)
        if g_dt <= datetime.min.replace(tzinfo=timezone.utc) or g_dt >= ref_utc:
            continue
        if eid:
            seen_eids.add(eid)
        filtered_events.append(ev)

    rest_days: Optional[int] = None
    if match_start_utc is not None:
        m_utc = match_start_utc.replace(tzinfo=timezone.utc) if match_start_utc.tzinfo is None else match_start_utc
        last_dt: Optional[datetime] = schedule_last_dt if (schedule_last_dt and schedule_last_dt < m_utc) else None
        for g in filtered_events:
            g_dt = _ev_dt(g)
            if g_dt > datetime.min.replace(tzinfo=timezone.utc) and g_dt < m_utc:
                if last_dt is None or g_dt > last_dt:
                    last_dt = g_dt
                break
        if last_dt is not None:
            days = int(round((m_utc - last_dt).total_seconds() / 86400.0))
            # If > 14 days, likely only European cup schedule was returned (domestic matches missing); leave None
            if 0 <= days <= 14:
                rest_days = days

    form_list: list[str] = []
    recent_matches: list[dict[str, Any]] = []
    gf_w_sum = 0.0
    ga_w_sum = 0.0
    w_total = 0.0

    venue_gf_sum = 0.0
    venue_ga_sum = 0.0
    venue_w_total = 0.0

    for idx, g in enumerate(filtered_events[:5]):
        try:
            hs = int(g.get("homeTeamScore"))
            as_ = int(g.get("awayTeamScore"))
        except (TypeError, ValueError):
            continue

        w = FORM_TIME_WEIGHTS[idx] if idx < len(FORM_TIME_WEIGHTS) else 0.45
        is_home = str(g.get("homeTeamId")) == team_id
        gf = hs if is_home else as_
        ga = as_ if is_home else hs
        gf_w_sum += gf * w
        ga_w_sum += ga * w
        w_total += w

        if prefer_home is not None and is_home == prefer_home:
            venue_gf_sum += gf * w
            venue_ga_sum += ga * w
            venue_w_total += w

        res = "W" if gf > ga else ("D" if gf == ga else "L")
        form_list.append(res)
        opp_name = ((g.get("opponent") or {}).get("displayName") or (g.get("opponent") or {}).get("shortDisplayName") or "")
        recent_matches.append({
            "event_id": str(g.get("id") or ""),
            "date_tr": to_tr_datetime(_ev_dt(g)).strftime("%d.%m"),
            "home_team": team_name if is_home else opp_name,
            "away_team": opp_name if is_home else team_name,
            "opponent": opp_name,
            "is_home": is_home,
            "home_score": hs,
            "away_score": as_,
            "result": res,
        })

    if w_total > 0 and venue_w_total >= 1.2:
        goals_avg = round(((gf_w_sum / w_total) * 0.60) + ((venue_gf_sum / venue_w_total) * 0.40), 2)
        conceded_avg = round(((ga_w_sum / w_total) * 0.60) + ((venue_ga_sum / venue_w_total) * 0.40), 2)
    elif w_total > 0:
        goals_avg = round(gf_w_sum / w_total, 2)
        conceded_avg = round(ga_w_sum / w_total, 2)
    else:
        goals_avg = None
        conceded_avg = None

    return {
        "form": form_list,
        "recent_matches": recent_matches,
        "goals_avg": goals_avg,
        "conceded_avg": conceded_avg,
        "rest_days": rest_days,
    }


def fetch_espn_match_details(
    espn_id: str,
    home_espn_id: str,
    away_espn_id: str,
    match_start_utc: Optional[datetime] = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Fetch global odds, time-weighted form, and rest days from ESPN match summary."""
    url = f"{ESPN_BASE}/all/summary?event={espn_id}"
    out: dict[str, Any] = {
        "global_odds": {},
        "home_form": None,
        "away_form": None,
        "home_goals_avg": None,
        "away_goals_avg": None,
        "home_conceded_avg": None,
        "away_conceded_avg": None,
        "home_rest_days": None,
        "away_rest_days": None,
        "provider": None,
    }

    with httpx.Client(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as client:
        resp = client.get(url)
        if resp.status_code != 200:
            return out
        data = resp.json()

        # 1. Global odds (MS 1-X-2 + Total Over/Under)
        odds_list = data.get("odds") or []
        if odds_list:
            o0 = odds_list[0]
            out["provider"] = o0.get("provider", {}).get("name")
            ml = o0.get("moneyline") or {}
            h_odd = american_to_decimal((ml.get("home") or {}).get("close", {}).get("odds"))
            d_odd = american_to_decimal((ml.get("draw") or {}).get("close", {}).get("odds"))
            a_odd = american_to_decimal((ml.get("away") or {}).get("close", {}).get("odds"))

            # Fallback to top-level awayTeamOdds / homeTeamOdds / drawOdds if moneyline lacks draw
            if h_odd is None:
                h_odd = american_to_decimal((o0.get("homeTeamOdds") or {}).get("moneyLine"))
            if a_odd is None:
                a_odd = american_to_decimal((o0.get("awayTeamOdds") or {}).get("moneyLine"))
            if d_odd is None:
                d_odd = american_to_decimal((o0.get("drawOdds") or {}).get("moneyLine"))

            if h_odd:
                out["global_odds"][("MS", "1")] = h_odd
            if d_odd:
                out["global_odds"][("MS", "X")] = d_odd
            if a_odd:
                out["global_odds"][("MS", "2")] = a_odd

            total_block = o0.get("total") or {}
            over_close = (total_block.get("over") or {}).get("close") or {}
            under_close = (total_block.get("under") or {}).get("close") or {}
            line_str = str(over_close.get("line") or "").lstrip("ouOU").strip()
            if line_str in {"1.5", "2.5", "3.5"}:
                mkey = f"AU_{line_str.replace('.', '')}"
                ov_odd = american_to_decimal(over_close.get("odds"))
                un_odd = american_to_decimal(under_close.get("odds"))
                if ov_odd:
                    out["global_odds"][(mkey, "UST")] = ov_odd
                if un_odd:
                    out["global_odds"][(mkey, "ALT")] = un_odd

        # 2. Time-weighted form (up to 10 official matches across all comps) + goal std + rest days
        out["league_slug"] = (
            ((data.get("header") or {}).get("league") or {}).get("slug") or None
        )
        out["home_goal_std"] = None
        out["away_goal_std"] = None
        out["home_recent_refs"] = []
        out["away_recent_refs"] = []
        out["home_recent_matches"] = []
        out["away_recent_matches"] = []
        out["home_sched_venue"] = {}
        out["away_sched_venue"] = {}

        for block in data.get("lastFiveGames") or []:
            tid = str(block.get("team", {}).get("id", ""))
            if tid == home_espn_id:
                prof = _fetch_team_schedule_profile(
                    client, tid, match_start_utc, prefer_home=True, exclude_event_id=espn_id
                )
                parsed = _parse_espn_form(
                    block,
                    match_start_utc=match_start_utc,
                    prefer_home=True,
                    schedule_last_dt=prof["last_dt"],
                    exclude_event_id=espn_id,
                )
                out["home_form"] = prof["form_10"][:5] if prof["form_10"] else parsed["form"]
                out["home_recent_matches"] = prof["recent_matches"][:5] if prof["form_10"] else parsed["recent_matches"]
                out["home_goals_avg"] = prof["goals_avg_10"] if prof["goals_avg_10"] is not None else parsed["goals_avg"]
                out["home_conceded_avg"] = prof["conceded_avg_10"] if prof["conceded_avg_10"] is not None else parsed["conceded_avg"]
                out["home_rest_days"] = parsed["rest_days"]
                out["home_goal_std"] = prof["goal_std"]
                out["home_recent_refs"] = prof["recent_refs"]
                out["home_sched_venue"] = {
                    "home_played": prof["home_played_10"],
                    "home_gf": prof["home_gf_10"],
                    "home_ga": prof["home_ga_10"],
                }
            elif tid == away_espn_id:
                prof = _fetch_team_schedule_profile(
                    client, tid, match_start_utc, prefer_home=False, exclude_event_id=espn_id
                )
                parsed = _parse_espn_form(
                    block,
                    match_start_utc=match_start_utc,
                    prefer_home=False,
                    schedule_last_dt=prof["last_dt"],
                    exclude_event_id=espn_id,
                )
                out["away_form"] = prof["form_10"][:5] if prof["form_10"] else parsed["form"]
                out["away_recent_matches"] = prof["recent_matches"][:5] if prof["form_10"] else parsed["recent_matches"]
                out["away_goals_avg"] = prof["goals_avg_10"] if prof["goals_avg_10"] is not None else parsed["goals_avg"]
                out["away_conceded_avg"] = prof["conceded_avg_10"] if prof["conceded_avg_10"] is not None else parsed["conceded_avg"]
                out["away_rest_days"] = parsed["rest_days"]
                out["away_goal_std"] = prof["goal_std"]
                out["away_recent_refs"] = prof["recent_refs"]
                out["away_sched_venue"] = {
                    "away_played": prof["away_played_10"],
                    "away_gf": prof["away_gf_10"],
                    "away_ga": prof["away_ga_10"],
                }

    return out


def fetch_espn_standings_map(db: Any, league_slug: str, timeout: float = 10.0) -> dict[str, dict[str, Any]]:
    """Fetch and cache (6h) ESPN league standings keyed by team ESPN ID."""
    if not league_slug:
        return {}
    from utils.cache import get_cached_json, set_cached_json

    cache_key = f"espn_standings:v1:{league_slug}"
    cached = get_cached_json(db, cache_key, ttl_hours=6.0)
    if isinstance(cached, dict):
        return cached

    standings_map: dict[str, dict[str, Any]] = {}
    url = f"https://site.api.espn.com/apis/v2/sports/soccer/{league_slug}/standings"
    try:
        resp = httpx.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            d = resp.json()
            for child in d.get("children") or []:
                entries = (child.get("standings") or {}).get("entries") or []
                for entry in entries:
                    tid = str((entry.get("team") or {}).get("id") or "")
                    if not tid:
                        continue
                    stats = {s.get("name"): s.get("value") for s in (entry.get("stats") or [])}
                    gp = float(stats.get("gamesPlayed") or 0)
                    pf = float(stats.get("pointsFor") or 0)
                    pa = float(stats.get("pointsAgainst") or 0)
                    rank_val = stats.get("rank")
                    standings_map[tid] = {
                        "rank": int(rank_val) if rank_val is not None else None,
                        "gp": int(gp),
                        "gf_pg": round(pf / gp, 3) if gp > 0 else None,
                        "ga_pg": round(pa / gp, 3) if gp > 0 else None,
                    }
    except Exception:
        standings_map = {}

    if standings_map:
        set_cached_json(db, cache_key, standings_map)
    return standings_map


def fetch_espn_team_season_stats(
    db: Any, league_slug: Optional[str], team_espn_id: str, timeout: float = 10.0
) -> dict[str, Any]:
    """
    Fetch team rank and seasonal home/away goals-for & goals-against averages from ESPN,
    with 6h DB cache. Returns {rank, home_gf, home_ga, away_gf, away_ga}.
    """
    empty = {
        "rank": None,
        "home_gf": None,
        "home_ga": None,
        "away_gf": None,
        "away_ga": None,
        "home_played": None,
        "away_played": None,
    }
    if not team_espn_id or not league_slug:
        return empty
    from utils.cache import get_cached_json, set_cached_json

    cache_key = f"espn_team_season:v2:{league_slug}:{team_espn_id}"
    cached = get_cached_json(db, cache_key, ttl_hours=6.0)
    if isinstance(cached, dict) and "home_played" in cached:
        return cached

    res = dict(empty)
    try:
        resp = httpx.get(
            f"{ESPN_BASE}/{league_slug}/teams/{team_espn_id}",
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code == 200:
            items = ((resp.json().get("team") or {}).get("record") or {}).get("items") or []
            if items:
                stats = {s.get("name"): s.get("value") for s in (items[0].get("stats") or [])}
                if stats.get("rank") is not None:
                    res["rank"] = int(stats["rank"])
                h_gp = float(stats.get("homeGamesPlayed") or 0)
                a_gp = float(stats.get("awayGamesPlayed") or 0)
                res["home_played"] = int(h_gp)
                res["away_played"] = int(a_gp)
                if h_gp > 0:
                    res["home_gf"] = round(float(stats.get("homePointsFor") or 0) / h_gp, 3)
                    res["home_ga"] = round(float(stats.get("homePointsAgainst") or 0) / h_gp, 3)
                if a_gp > 0:
                    res["away_gf"] = round(float(stats.get("awayPointsFor") or 0) / a_gp, 3)
                    res["away_ga"] = round(float(stats.get("awayPointsAgainst") or 0) / a_gp, 3)
    except Exception:
        pass

    # Backup rank from league standings if teams/{id} omitted rank
    if res["rank"] is None:
        st_map = fetch_espn_standings_map(db, league_slug, timeout=timeout)
        if team_espn_id in st_map:
            res["rank"] = st_map[team_espn_id].get("rank")

    if any(v is not None for v in res.values()):
        set_cached_json(db, cache_key, res)
    return res


def _parse_market_value_eur(val_str: str) -> tuple[Optional[float], Optional[int]]:
    """Parse Transfermarkt currency value (e.g. '€12.50m', '€450k', '€400 Th.') into (millions, eur)."""
    s = (val_str or "").strip().lower().replace(",", ".")
    if not any(sym in s for sym in ("€", "m", "k", "th")):
        return None, None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(m|k|th\.?)", s)
    if not m:
        return None, None
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "m":
        return round(num, 2), int(round(num * 1_000_000))
    return round(num / 1000.0, 3), int(round(num * 1_000))


def _norm_player_name(name: str) -> str:
    txt = unicodedata.normalize("NFKD", (name or "").strip())
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = re.sub(r"[^a-zA-Z0-9\s]", " ", txt).lower()
    return re.sub(r"\s+", " ", txt).strip()


def fetch_transfermarkt_squad_values(
    db: Any,
    team_name: str,
    club_startseite_href: str,
    allow_fetch: bool = True,
    timeout: float = 12.0,
) -> tuple[dict[str, Any], bool]:
    """
    Fetch Transfermarkt squad player market values (/kader/) and cache for 7 days (168h).
    Returns (squad_map, fetched_live_bool).
    """
    empty = {"total_eur": 0, "players": {}}
    if not club_startseite_href:
        return empty, False

    key_slug = _norm_player_name(team_name).replace(" ", "_") or "club"
    cache_key = f"tm_squad_mv:v1:{key_slug}"
    if db is not None:
        from utils.cache import get_cached_json, set_cached_json

        cached = get_cached_json(db, cache_key, ttl_hours=168.0)
        if isinstance(cached, dict) and "players" in cached:
            return cached, False

    if not allow_fetch:
        return empty, False

    kader_href = re.sub(r"/startseite/verein/", "/kader/verein/", club_startseite_href)
    kader_url = f"https://www.transfermarkt.com{kader_href}"
    players: dict[str, dict[str, Any]] = {}
    total_eur = 0
    try:
        rk = cffi_requests.get(kader_url, impersonate="chrome120", timeout=timeout)
        if rk.status_code == 200:
            soup_k = BeautifulSoup(rk.text, "html.parser")
            for row in soup_k.select("table.items > tbody > tr.odd, table.items > tbody > tr.even"):
                name_el = row.select_one("td.hauptlink a")
                if not name_el:
                    continue
                p_name = name_el.get_text(strip=True)
                mv_el = row.select_one("td.rechts.hauptlink a, td.rechts.hauptlink")
                raw_mv = mv_el.get_text(strip=True) if mv_el else ""
                v_m, v_eur = _parse_market_value_eur(raw_mv)
                if v_eur is None:
                    continue
                total_eur += v_eur
                players[_norm_player_name(p_name)] = {
                    "name": p_name,
                    "mv_text": raw_mv,
                    "value_m": v_m,
                    "mv_eur": v_eur,
                }
    except Exception:
        pass

    res = {"total_eur": total_eur, "players": players}
    if db is not None and players:
        set_cached_json(db, cache_key, res)
    return res, bool(players)


def _is_suspension_reason(reason: str) -> bool:
    low = (reason or "").lower()
    return any(k in low for k in ("red card", "yellow card", "indirect card", "suspension"))


def _classify_competition_scope(name: str) -> tuple[str, str]:
    low = (name or "").lower()
    if any(k in low for k in ("champions league", "şampiyonlar ligi", "uefa champions")):
        return ("uefa", "ucl")
    if any(k in low for k in ("europa league", "avrupa ligi", "uefa europa")):
        return ("uefa", "uel")
    if any(k in low for k in ("conference league", "konferans ligi", "uefa conference")):
        return ("uefa", "uecl")
    if "uefa" in low:
        return ("uefa", "uefa")
    if any(k in low for k in ("cup", "pokal", "coppa", "copa", "coupe", "kupa", "kupa")):
        return ("domestic_cup", low)
    return ("domestic_league", low)


def filter_injuries_for_league(
    items: list[dict[str, Any]],
    match_league: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Keep all physical injuries, and keep card suspensions ONLY if their tournament scope
    matches the match's league/competition (domestic league vs UEFA cups).
    Never leak domestic card suspensions into UEFA matches or vice versa.
    """
    if not match_league:
        return list(items)
    m_scope, m_sub = _classify_competition_scope(match_league)
    out: list[dict[str, Any]] = []
    for item in items:
        reason = item.get("reason") or ""
        comp = (item.get("competition") or "").strip()
        if _is_suspension_reason(reason) and comp:
            c_scope, c_sub = _classify_competition_scope(comp)
            if c_scope != m_scope:
                continue
            if c_scope == "uefa" and m_sub != "uefa" and c_sub != "uefa" and c_sub != m_sub:
                continue
        out.append(item)
    return out


def fetch_transfermarkt_injuries(
    team_name: str,
    db: Any = None,
    allow_squad_fetch: bool = True,
    match_league: Optional[str] = None,
    timeout: float = 12.0,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Fetch real injured/suspended players (with position, missed_games, tournament competition,
    and 7d-cached squad market value). Filters out "at risk / threatened" players and
    cross-tournament suspensions when match_league is given.
    Returns (injuries_list, fetched_squad_live_bool).
    """
    key = team_name.strip().lower()
    cached_entry = _TM_INJURY_CACHE.get(key)
    if cached_entry is not None:
        cached_ts, cached_items = cached_entry
        if (time.monotonic() - cached_ts) < _TM_CACHE_TTL_SEC:
            return filter_injuries_for_league(cached_items, match_league), False

    injuries: list[dict[str, Any]] = []
    fetched_squad_live = False
    try:
        search_url = f"https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche?query={quote_plus(team_name)}"
        rs = cffi_requests.get(search_url, impersonate="chrome120", timeout=timeout)
        if rs.status_code != 200:
            return [], False

        soup = BeautifulSoup(rs.text, "html.parser")
        club_link = soup.select_one("a[href*='/startseite/verein/']")
        if not club_link or not club_link.get("href"):
            _TM_INJURY_CACHE[key] = (time.monotonic(), [])
            return [], False

        href = club_link["href"]
        inj_href = re.sub(r"/startseite/verein/", "/sperrenundverletzungen/verein/", href)
        inj_url = f"https://www.transfermarkt.com{inj_href}"

        ri = cffi_requests.get(inj_url, impersonate="chrome120", timeout=timeout)
        if ri.status_code != 200:
            return [], False

        soup_i = BeautifulSoup(ri.text, "html.parser")
        for tbl in soup_i.select("table.items"):
            box = tbl.find_parent("div", class_="box")
            box_title = ""
            if box:
                hdr = box.select_one("h2, .content-box-headline")
                if hdr:
                    box_title = hdr.get_text(" ", strip=True).lower()
            if any(k in box_title for k in ("at risk", "threatened", "risk of suspension")):
                continue

            headers = [th.get_text(strip=True).lower() for th in tbl.select("thead > tr > th")]
            # Skip yellow-card risk tables ("Yellow cards", "Appearances", "Cards/Match")
            if not any(k in h for h in headers for k in ("reason", "injury", "suspension", "missed")):
                continue

            def _find_col(*keywords: str, default: Optional[int] = None) -> Optional[int]:
                for idx, h in enumerate(headers):
                    if any(k in h for k in keywords):
                        return idx
                return default

            col_reason = _find_col("reason", "injury", "suspension", default=2)
            col_comp = _find_col("competition", "tournament", "league", default=None)
            col_until = _find_col("return", "until", default=4)
            col_missed = _find_col("missed", default=5)
            col_mv = _find_col("market value", "value", default=None)

            for row in tbl.select("tbody > tr.odd, tbody > tr.even"):
                name_el = row.select_one("td.hauptlink a")
                if not name_el:
                    continue
                pos_el = row.select("table.inline-table tr")
                position = pos_el[1].get_text(strip=True) if len(pos_el) > 1 else ""
                tds = row.find_all("td", recursive=False)
                if not position and tds:
                    parts = [p.strip() for p in tds[0].get_text(" | ", strip=True).split("|")]
                    if len(parts) > 1:
                        position = parts[-1]

                reason = tds[col_reason].get_text(strip=True) if col_reason is not None and len(tds) > col_reason else ""
                reason_low = reason.lower()
                if any(k in reason_low for k in ("at risk", "threatened", "yellow card risk")):
                    continue

                competition = ""
                if col_comp is not None and len(tds) > col_comp:
                    comp_td = tds[col_comp]
                    comp_img = comp_td.select_one("img[title], img[alt], a[title]")
                    if comp_img:
                        competition = (comp_img.get("title") or comp_img.get("alt") or "").strip()
                    if not competition:
                        competition = comp_td.get_text(strip=True)

                until = tds[col_until].get_text(strip=True) if col_until is not None and len(tds) > col_until else ""

                missed_games: Optional[int] = None
                if col_missed is not None and len(tds) > col_missed:
                    m_txt = tds[col_missed].get_text(strip=True)
                    if m_txt.isdigit():
                        missed_games = int(m_txt)

                mv_text: Optional[str] = None
                value_m: Optional[float] = None
                market_value_eur: Optional[int] = None
                if col_mv is not None and len(tds) > col_mv:
                    raw_mv = tds[col_mv].get_text(strip=True)
                    v_m, v_eur = _parse_market_value_eur(raw_mv)
                    if v_eur is not None:
                        mv_text = raw_mv
                        value_m = v_m
                        market_value_eur = v_eur

                injuries.append({
                    "player": name_el.get_text(strip=True),
                    "position": position,
                    "market_value": mv_text,
                    "market_value_eur": market_value_eur,
                    "value_m": value_m,
                    "squad_value_share_pct": None,
                    "missed_games": missed_games,
                    "reason": reason,
                    "competition": competition or None,
                    "until": until,
                })

        if injuries:
            squad_map, fetched_squad_live = fetch_transfermarkt_squad_values(
                db, team_name, href, allow_fetch=allow_squad_fetch, timeout=timeout
            )
            sq_players = squad_map.get("players") or {}
            total_eur = squad_map.get("total_eur") or 0
            if sq_players and total_eur > 0:
                for inj in injuries:
                    np_name = _norm_player_name(inj["player"])
                    sq_entry = sq_players.get(np_name)
                    if not sq_entry and np_name:
                        best_k = None
                        best_sc = 0.0
                        for k_name in sq_players:
                            sc = fuzz.token_sort_ratio(np_name, k_name)
                            if sc >= 82 and sc > best_sc:
                                best_sc = sc
                                best_k = k_name
                        if best_k:
                            sq_entry = sq_players[best_k]
                    if sq_entry:
                        inj["market_value_eur"] = sq_entry["mv_eur"]
                        inj["market_value"] = sq_entry["mv_text"]
                        inj["value_m"] = sq_entry["value_m"]
                        inj["squad_value_share_pct"] = round((sq_entry["mv_eur"] / total_eur) * 100.0, 2)
                    elif inj.get("market_value_eur") and total_eur > 0:
                        inj["squad_value_share_pct"] = round((inj["market_value_eur"] / total_eur) * 100.0, 2)
    except Exception:
        return [], False

    _TM_INJURY_CACHE[key] = (time.monotonic(), injuries)
    return filter_injuries_for_league(injuries, match_league), fetched_squad_live


def get_target_espn_dates() -> list[str]:
    now = datetime.now(timezone.utc)
    return [
        (now - timedelta(days=1)).strftime("%Y%m%d"),
        now.strftime("%Y%m%d"),
        (now + timedelta(days=1)).strftime("%Y%m%d"),
        (now + timedelta(days=2)).strftime("%Y%m%d"),
    ]
