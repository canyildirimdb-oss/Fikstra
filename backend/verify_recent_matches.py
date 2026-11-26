import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Match, MatchContext, Prediction, ScraperCache
from app.routers.api import _build_match_detail_ctx
from scrapers.external_data import fetch_global_team_recent_matches
from utils.cache import get_cached_json, set_cached_json
from utils.team_matcher import strict_team_match_score


class _FakeResp:
    def __init__(self, status_code: int, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def test_rm1_all_leagues_no_friendlies():
    """
    Lig + resmi kupa maclari en yeniden eskiye birlesiyor;
    Club Friendly / Hazirlik maclari kesinlikle eleniyor.
    """
    now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)

    def fake_get(self, url, *args, **kwargs):
        if "webws.365scores.com" in url:
            raise ConnectionError("blocked")
        if "fotmob.com/api/data/search/suggest" in url:
            return _FakeResp(
                200,
                [{"suggestions": [{"type": "team", "id": "9752", "name": "Trabzonspor", "leagueName": "Super Lig"}]}],
            )
        if "fotmob.com/api/data/teams?id=9752" in url:
            return _FakeResp(
                200,
                {
                    "fixtures": {
                        "allFixtures": {
                            "fixtures": [
                                {
                                    "id": 101,
                                    "home": {"id": 9752, "name": "Trabzonspor", "score": 1},
                                    "away": {"id": 4681, "name": "Eyupspor", "score": 0},
                                    "tournament": {"name": "Super Lig"},
                                    "status": {"utcTime": "2026-11-22T16:00:00.000Z", "finished": True},
                                },
                                {
                                    "id": 102,
                                    "home": {"id": 8698, "name": "Zenit", "score": 3},
                                    "away": {"id": 9752, "name": "Trabzonspor", "score": 0},
                                    "tournament": {"name": "Club Friendlies"},
                                    "status": {"utcTime": "2026-11-15T14:00:00.000Z", "finished": True},
                                },
                                {
                                    "id": 103,
                                    "home": {"id": 4678, "name": "Alanyaspor", "score": 0},
                                    "away": {"id": 9752, "name": "Trabzonspor", "score": 2},
                                    "tournament": {"name": "Super Lig"},
                                    "status": {"utcTime": "2026-11-08T15:30:00.000Z", "finished": True},
                                },
                                {
                                    "id": 104,
                                    "home": {"id": 9752, "name": "Trabzonspor", "score": 0},
                                    "away": {"id": 8358, "name": "Freiburg", "score": 1},
                                    "tournament": {"name": "Conference League"},
                                    "status": {"utcTime": "2026-11-05T17:45:00.000Z", "finished": True},
                                },
                                {
                                    "id": 105,
                                    "home": {"id": 9752, "name": "Trabzonspor", "score": 2},
                                    "away": {"id": 4081, "name": "Gaziantep FK", "score": 0},
                                    "tournament": {"name": "Super Lig"},
                                    "status": {"utcTime": "2026-11-01T13:00:00.000Z", "finished": True},
                                },
                                {
                                    "id": 106,
                                    "home": {"id": 2166, "name": "Rizespor", "score": 3},
                                    "away": {"id": 9752, "name": "Trabzonspor", "score": 4},
                                    "tournament": {"name": "Super Lig"},
                                    "status": {"utcTime": "2026-10-26T16:00:00.000Z", "finished": True},
                                },
                            ]
                        }
                    }
                },
            )
        return _FakeResp(404, {})

    with patch("httpx.Client.get", new=fake_get):
        prof = fetch_global_team_recent_matches("Trabzonspor", match_start_utc=now, prefer_home=True)

    assert prof["source"] == "fotmob"
    assert len(prof["recent_matches"]) == 5
    opps = [m["opponent"] for m in prof["recent_matches"]]
    assert "Zenit" not in opps, f"Friendly leaked: {opps}"
    assert prof["form"] == ["W", "W", "L", "W", "W"], f"Unexpected form: {prof['form']}"
    assert [m["result"] for m in prof["recent_matches"]] == prof["form"]
    print("TEST_RM1_ALL_LEAGUES_NO_FRIENDLIES: PASS")


def test_rm2_cup_only_rejection():
    """
    Yalnizca eski Avrupa maclari donen eksik kaynak (CSKA Sofya senaryosu) reddedilip
    tam lig verisi veren global kaynaga geciliyor.
    """
    now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)

    def fake_get(self, url, *args, **kwargs):
        if "webws.365scores.com/web/search" in url:
            return _FakeResp(200, {"competitors": [{"id": 500, "sportId": 1, "name": "CSKA Sofia"}]})
        if "webws.365scores.com/web/games/results" in url:
            # Cup-only from 365scores mock -> should be rejected!
            return _FakeResp(
                200,
                {
                    "games": [
                        {
                            "id": 1,
                            "statusGroup": 4,
                            "competitionDisplayName": "UEFA Conference League",
                            "startTime": "2026-11-05T20:00:00Z",
                            "homeCompetitor": {"id": 999, "name": "Panathinaikos", "score": 2},
                            "awayCompetitor": {"id": 500, "name": "CSKA Sofia", "score": 1},
                        }
                    ]
                },
            )
        if "fotmob.com/api/data/search/suggest" in url:
            return _FakeResp(
                200,
                [{"suggestions": [{"type": "team", "id": "10144", "name": "CSKA Sofia", "leagueName": "First Professional League"}]}],
            )
        if "fotmob.com/api/data/teams?id=10144" in url:
            return _FakeResp(
                200,
                {
                    "fixtures": {
                        "allFixtures": {
                            "fixtures": [
                                {
                                    "id": 201,
                                    "home": {"id": 800, "name": "Botev Plovdiv", "score": 3},
                                    "away": {"id": 10144, "name": "CSKA Sofia", "score": 3},
                                    "tournament": {"name": "First Professional League"},
                                    "status": {"utcTime": "2026-11-22T16:00:00.000Z", "finished": True},
                                },
                                {
                                    "id": 202,
                                    "home": {"id": 801, "name": "Levski Sofia", "score": 0},
                                    "away": {"id": 10144, "name": "CSKA Sofia", "score": 3},
                                    "tournament": {"name": "First Professional League"},
                                    "status": {"utcTime": "2026-11-08T15:00:00.000Z", "finished": True},
                                },
                            ]
                        }
                    }
                },
            )
        return _FakeResp(404, {})

    with patch("httpx.Client.get", new=fake_get):
        prof = fetch_global_team_recent_matches("CSKA Sofya", match_start_utc=now, prefer_home=True)

    assert prof["source"] == "fotmob", f"Expected fallback to fotmob, got {prof['source']}"
    assert prof["recent_matches"][0]["competition"] == "First Professional League"
    assert prof["form"] == ["D", "W"]
    print("TEST_RM2_CUP_ONLY_REJECTION: PASS")


def test_rm3_strict_team_and_score_parity():
    """
    Women/U21/B ve yanlis CSKA 1948 eslesmeleri engelleniyor;
    ev/dep skorlari, home_team, away_team, result ile %100 tutarli.
    """
    assert strict_team_match_score("CSKA Sofya", "CSKA 1948 Sofia") == 0.0
    assert strict_team_match_score("CSKA Sofya", "CSKA Moscow") == 0.0
    assert strict_team_match_score("Kuopion", "KuPS (W)", "Kansallinen (Women)") == 0.0
    assert strict_team_match_score("Kuopion", "KuPS Akatemia", "Ykkonen") == 0.0
    assert strict_team_match_score("Barcelona", "Barcelona B") == 0.0
    assert strict_team_match_score("Trabzonspor", "Trabzonspor U21") == 0.0
    assert strict_team_match_score("CSKA Sofya", "CSKA Sofia") == 100.0
    assert strict_team_match_score("Kuopion", "KuPS") == 100.0

    # Verify score/home/away parity
    now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)

    def fake_get(self, url, *args, **kwargs):
        if "webws.365scores.com" in url:
            raise ConnectionError("blocked")
        if "fotmob.com/api/data/search/suggest" in url:
            return _FakeResp(
                200,
                [
                    {
                        "suggestions": [
                            {"type": "team", "id": "9991", "name": "CSKA 1948 Sofia", "leagueName": "First Professional League"},
                            {"type": "team", "id": "9992", "name": "CSKA Sofia (W)", "leagueName": "Women League"},
                            {"type": "team", "id": "10144", "name": "CSKA Sofia", "leagueName": "First Professional League"},
                        ]
                    }
                ],
            )
        if "fotmob.com/api/data/teams?id=10144" in url:
            return _FakeResp(
                200,
                {
                    "fixtures": {
                        "allFixtures": {
                            "fixtures": [
                                {
                                    "id": 301,
                                    "home": {"id": 900, "name": "Panathinaikos", "score": 2},
                                    "away": {"id": 10144, "name": "CSKA Sofia", "score": 1},
                                    "tournament": {"name": "Conference League"},
                                    "status": {"utcTime": "2026-11-24T20:00:00.000Z", "finished": True},
                                },
                                {
                                    "id": 302,
                                    "home": {"id": 10144, "name": "CSKA Sofia", "score": 2},
                                    "away": {"id": 901, "name": "Ludogorets", "score": 0},
                                    "tournament": {"name": "First Professional League"},
                                    "status": {"utcTime": "2026-11-20T17:30:00.000Z", "finished": True},
                                },
                            ]
                        }
                    }
                },
            )
        return _FakeResp(404, {})

    with patch("httpx.Client.get", new=fake_get):
        prof = fetch_global_team_recent_matches("CSKA Sofya", match_start_utc=now, prefer_home=True)

    m0, m1 = prof["recent_matches"][0], prof["recent_matches"][1]
    assert m0["home_team"] == "Panathinaikos" and m0["away_team"] == "CSKA Sofia"
    assert m0["home_score"] == 2 and m0["away_score"] == 1 and m0["is_home"] is False and m0["result"] == "L"
    assert m1["home_team"] == "CSKA Sofia" and m1["away_team"] == "Ludogorets"
    assert m1["home_score"] == 2 and m1["away_score"] == 0 and m1["is_home"] is True and m1["result"] == "W"
    assert prof["form"] == ["L", "W"]
    print("TEST_RM3_STRICT_TEAM_AND_SCORE_PARITY: PASS")


def test_rm4_cache_v4_invalidation():
    """
    Eski recent_matches:{id} cache kayitlari yeni recent_v4:{id} anahtariyla tamamen devre disi kaliyor.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    m = Match(
        iddaa_id="99999",
        league="UEFA Konferans Ligi",
        country="Avrupa",
        home_team="CSKA Sofya",
        away_team="Trabzonspor",
        start_time_utc=datetime.now(timezone.utc) + timedelta(hours=3),
        status="SCHEDULED",
    )
    db.add(m)
    db.flush()
    db.add(MatchContext(match_id=m.id, home_form=json.dumps(["L", "L", "L", "L", "L"])))
    # Legacy poisoned cache key
    set_cached_json(db, f"recent_matches:{m.id}", {"home": [{"event_id": "old", "opponent": "Poison"}], "away": []})
    db.commit()

    def fake_get(self, url, *args, **kwargs):
        if "webws.365scores.com" in url:
            raise ConnectionError("blocked")
        if "fotmob.com/api/data/search/suggest" in url:
            if "cska" in url.lower():
                return _FakeResp(200, [{"suggestions": [{"type": "team", "id": "10144", "name": "CSKA Sofia", "leagueName": "First Professional League"}]}])
            return _FakeResp(200, [{"suggestions": [{"type": "team", "id": "9752", "name": "Trabzonspor", "leagueName": "Super Lig"}]}])
        if "id=10144" in url:
            return _FakeResp(
                200,
                {
                    "fixtures": {
                        "allFixtures": {
                            "fixtures": [
                                {
                                    "id": 501,
                                    "home": {"id": 10144, "name": "CSKA Sofia", "score": 2},
                                    "away": {"id": 801, "name": "Levski Sofia", "score": 0},
                                    "tournament": {"name": "First Professional League"},
                                    "status": {"utcTime": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), "finished": True},
                                }
                            ]
                        }
                    }
                },
            )
        if "id=9752" in url:
            return _FakeResp(
                200,
                {
                    "fixtures": {
                        "allFixtures": {
                            "fixtures": [
                                {
                                    "id": 502,
                                    "home": {"id": 9752, "name": "Trabzonspor", "score": 1},
                                    "away": {"id": 4681, "name": "Eyupspor", "score": 0},
                                    "tournament": {"name": "Super Lig"},
                                    "status": {"utcTime": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), "finished": True},
                                }
                            ]
                        }
                    }
                },
            )
        return _FakeResp(404, {})

    with patch("httpx.Client.get", new=fake_get):
        ctx_out = _build_match_detail_ctx(db, m, None)

    assert ctx_out["home_recent_matches"][0]["opponent"] == "Levski Sofia"
    assert ctx_out["home_form"] == ["W"]
    assert get_cached_json(db, f"recent_v4:{m.id}", ttl_hours=6.0) is not None
    print("TEST_RM4_CACHE_V4_INVALIDATION: PASS")


if __name__ == "__main__":
    test_rm1_all_leagues_no_friendlies()
    test_rm2_cup_only_rejection()
    test_rm3_strict_team_and_score_parity()
    test_rm4_cache_v4_invalidation()
    print("ALL RECENT MATCHES TESTS PASSED (0 Odds API credits used)")
