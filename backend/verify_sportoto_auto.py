"""Isolated verification suite for automatic Spor Toto sync, publish, settle & failure isolation."""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("CRON_SECRET", "test-cron-secret-sportoto")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import Match, Prediction, SportotoWeek  # noqa: E402
from app.sportoto_sync import sync_sportoto_auto  # noqa: E402


def _make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


TEAM_PAIRS = [
    ("Galatasaray", "Fenerbahçe"),
    ("Beşiktaş", "Trabzonspor"),
    ("Samsunspor", "Konyaspor"),
    ("Alanyaspor", "Antalyaspor"),
    ("Göztepe", "Kasımpaşa"),
    ("Çorum FK", "Kocaelispor"),
    ("Eyüpspor", "Başakşehir"),
    ("Gençlerbirliği", "Amedspor"),
    ("Erzurumspor", "Boluspor"),
    ("Freiburg", "Leipzig"),
    ("Borussia Dortmund", "Bayer Leverkusen"),
    ("Olympique Lyon", "AS Monaco"),
    ("Everton", "Liverpool"),
    ("Arsenal", "Manchester City"),
    ("Real Sociedad", "Sevilla"),
]


def _seed_db_matches(db, count: int = 5):
    now = datetime.now(timezone.utc)
    for idx in range(1, count + 1):
        home, away = TEAM_PAIRS[idx - 1]
        m = Match(
            iddaa_id=f"test-st-{idx}",
            home_team=home,
            away_team=away,
            league="Süper Lig",
            country="TR",
            start_time_utc=now + timedelta(hours=12 + idx),
            status="SCHEDULED",
        )
        db.add(m)
        db.flush()
        probs = [("1", 0.50, 1.85), ("X", 0.28, 3.40), ("2", 0.22, 4.20)]
        for sel, pr, odd in probs:
            db.add(
                Prediction(
                    match_id=m.id,
                    market_key="MS",
                    selection_key=sel,
                    iddaa_odd=odd,
                    confidence_score=75.0,
                    published_prob=pr,
                    is_top_pick=(sel == "1"),
                    status="PENDING",
                )
            )
    db.commit()


def _build_mock_bulletin(cycle_no: int = 370, include_missing_slot: int | None = None, settled: bool = False):
    events = []
    sb_events = []
    for idx in range(1, 16):
        home, away = TEAM_PAIRS[idx - 1]
        winner = None
        if settled:
            winner = "1" if idx % 3 == 1 else ("0" if idx % 3 == 2 else "2")
        events.append(
            {
                "slot_no": idx,
                "home_team": home,
                "away_team": away,
                "event_date": "2026-11-28T16:00:00",
                "actual_result": ("X" if winner == "0" else winner),
                "public_pct_1": 68.0 if idx in (6, 7) else None,
                "public_pct_x": 18.0 if idx in (6, 7) else None,
                "public_pct_2": 14.0 if idx in (6, 7) else None,
                "odd_1": None,
                "odd_x": None,
                "odd_2": None,
            }
        )
        if idx > 5 and idx != include_missing_slot:
            sb_events.append(
                {
                    "home_team": home,
                    "away_team": away,
                    "odd_1": 1.95,
                    "odd_x": 3.40,
                    "odd_2": 3.70,
                }
            )
    bulletin = {
        "source": "iddaa.com",
        "external_id": f"iddaa:{cycle_no}",
        "cycle_no": cycle_no,
        "title": f"{cycle_no}. Hafta",
        "events": events,
    }
    return bulletin, sb_events


def test_st_auto_1_parse_and_autopublish():
    db = _make_session()
    _seed_db_matches(db, count=5)
    bulletin, sb_events = _build_mock_bulletin(cycle_no=370)

    with (
        patch("app.sportoto_sync._fetch_bulletin_multi_source", return_value=(bulletin, [])),
        patch("app.sportoto_sync._fetch_iddaa_sportsbook_odds", return_value=sb_events),
        patch("services.push.notify_sportoto_published", return_value={"sent": 1}),
        patch("app.telegram_bot.dispatch_sportoto_week", return_value={"ok": True, "sent": True}),
    ):
        res1 = sync_sportoto_auto(db, trigger_notifications=True)
        assert res1["ok"] is True
        assert res1["model_count"] == 5
        assert res1["devig_count"] == 10
        assert res1["missing_count"] == 0
        assert res1["public_pct_count"] == 2
        assert res1["week_status"] == "PUBLISHED"
        assert res1["published_now"] is True

        # Second run must be idempotent (no duplicate week)
        res2 = sync_sportoto_auto(db, trigger_notifications=True)
        assert res2["ok"] is True
        assert db.query(SportotoWeek).count() == 1
        assert res2["week_id"] == res1["week_id"]
        assert res2["published_now"] is False

    print("TEST_ST_AUTO_1_PARSE_AND_AUTOPUBLISH: PASS")


def test_st_auto_2_missing_block():
    db = _make_session()
    _seed_db_matches(db, count=5)
    bulletin, sb_events = _build_mock_bulletin(cycle_no=371, include_missing_slot=12)

    with (
        patch("app.sportoto_sync._fetch_bulletin_multi_source", return_value=(bulletin, [])),
        patch("app.sportoto_sync._fetch_iddaa_sportsbook_odds", return_value=sb_events),
    ):
        res = sync_sportoto_auto(db, trigger_notifications=False)
        assert res["ok"] is True
        assert res["missing_count"] == 1
        assert res["week_status"] == "DRAFT"
        assert res["published_now"] is False
        w = db.query(SportotoWeek).filter(SportotoWeek.id == res["week_id"]).first()
        assert w is not None and w.status == "DRAFT"
        slot12 = next(m for m in w.matches if m.slot_no == 12)
        assert slot12.source == "missing"
        assert slot12.prob_1 is None

    print("TEST_ST_AUTO_2_MISSING_BLOCK: PASS")


def test_st_auto_3_auto_settle():
    db = _make_session()
    _seed_db_matches(db, count=5)
    b1, sb = _build_mock_bulletin(cycle_no=372, settled=False)
    b2, _ = _build_mock_bulletin(cycle_no=372, settled=True)

    with (
        patch("app.sportoto_sync._fetch_bulletin_multi_source", return_value=(b1, [])),
        patch("app.sportoto_sync._fetch_iddaa_sportsbook_odds", return_value=sb),
    ):
        res1 = sync_sportoto_auto(db, trigger_notifications=False)
        assert res1["week_status"] == "PUBLISHED"

    with (
        patch("app.sportoto_sync._fetch_bulletin_multi_source", return_value=(b2, [])),
        patch("app.sportoto_sync._fetch_iddaa_sportsbook_odds", return_value=sb),
    ):
        res2 = sync_sportoto_auto(db, trigger_notifications=False)
        assert res2["settled_slots"] == 15
        assert res2["week_status"] == "SETTLED"
        w = db.query(SportotoWeek).filter(SportotoWeek.id == res2["week_id"]).first()
        assert w is not None and w.status == "SETTLED"
        by_slot = {m.slot_no: m.actual_result for m in w.matches}
        assert by_slot[1] == "1"
        assert by_slot[2] == "X"
        assert by_slot[3] == "2"

    print("TEST_ST_AUTO_3_AUTO_SETTLE: PASS")


def test_st_auto_4_failure_isolation():
    db = _make_session()
    with (
        patch("httpx.Client.get", side_effect=RuntimeError("403 Forbidden / Timeout")),
        patch("app.telegram_bot.check_and_send_health_alerts", return_value=None),
    ):
        res = sync_sportoto_auto(db, trigger_notifications=False)
        assert res["ok"] is False
        assert res["status"] == "error"
        assert "403" in (res.get("error") or "")

    print("TEST_ST_AUTO_4_FAILURE_ISOLATION: PASS")


if __name__ == "__main__":
    test_st_auto_1_parse_and_autopublish()
    test_st_auto_2_missing_block()
    test_st_auto_3_auto_settle()
    test_st_auto_4_failure_isolation()
    print("ALL_SPORTOTO_AUTO_TESTS_PASSED")
