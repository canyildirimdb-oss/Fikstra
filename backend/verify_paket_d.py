"""Isolated unit tests for Fikstra Paket D (pulse dedup/threshold, virtual bankroll, CSV & daily poll)."""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(__file__))

from app.config import settings
from app.database import Base, get_db
from app.main import app as fastapi_app
from app.models import Match, Prediction
from app.routers.api import clear_api_cache
from app import telegram_bot


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"ok": True, "result": {"message_id": 501}}
        self.is_success = 200 <= status_code < 300
        self.text = str(self._payload)

    def json(self):
        return self._payload


def _make_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    db = TestingSession()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = override_get_db
    clear_api_cache()
    return TestClient(fastapi_app), db


def test_pd1_pulse_dedup_and_threshold():
    client, db = _make_client()
    now = datetime.now(timezone.utc)
    try:
        m = Match(
            iddaa_id="pd1_m",
            league="Süper Lig",
            home_team="Fenerbahçe",
            away_team="Beşiktaş",
            start_time_utc=now + timedelta(hours=3),
            status="SCHEDULED",
        )
        db.add(m)
        db.flush()
        p = Prediction(
            match_id=m.id,
            market_key="MS",
            selection_key="1",
            iddaa_odd=1.80,
            confidence_score=74.0,
            is_top_pick=True,
            status="PENDING",
        )
        db.add(p)
        db.commit()

        # Same voter votes twice (agree then risky) -> total_votes stays 1
        r1 = client.post(
            f"/api/predictions/{p.id}/pulse/vote",
            json={"choice": "agree"},
            headers={"X-Forwarded-For": "10.0.0.1", "User-Agent": "UA-1"},
        )
        assert r1.status_code == 200 and r1.json()["total_votes"] == 1 and r1.json()["revealed"] is False
        r2 = client.post(
            f"/api/predictions/{p.id}/pulse/vote",
            json={"choice": "risky"},
            headers={"X-Forwarded-For": "10.0.0.1", "User-Agent": "UA-1"},
        )
        assert r2.json()["total_votes"] == 1 and r2.json()["user_vote"] == "risky"

        # Add 3 more distinct voters (total=4) -> still revealed=False, agree_pct=None
        for i in range(2, 5):
            r = client.post(
                f"/api/predictions/{p.id}/pulse/vote",
                json={"choice": "agree"},
                headers={"X-Forwarded-For": f"10.0.0.{i}", "User-Agent": f"UA-{i}"},
            )
            assert r.json()["revealed"] is False
            assert r.json()["agree_pct"] is None

        # 5th distinct voter -> revealed=True, 4 agree / 1 risky = 80.0% / 20.0%
        r5 = client.post(
            f"/api/predictions/{p.id}/pulse/vote",
            json={"choice": "agree"},
            headers={"X-Forwarded-For": "10.0.0.5", "User-Agent": "UA-5"},
        )
        d5 = r5.json()
        assert d5["total_votes"] == 5 and d5["revealed"] is True
        assert d5["agree_pct"] == 80.0 and d5["risky_pct"] == 20.0

        # Slip adds: 4 distinct clients -> not community favorite; 5th -> community favorite
        for i in range(1, 5):
            client.post(
                f"/api/predictions/{p.id}/pulse/slip-add",
                headers={"X-Forwarded-For": f"10.1.0.{i}", "User-Agent": f"UA-{i}"},
            )
        assert client.get("/api/predictions/community-favorites").json()["prediction_ids"] == []
        assert client.get(f"/api/predictions/{p.id}/pulse").json()["is_community_favorite"] is False

        client.post(
            f"/api/predictions/{p.id}/pulse/slip-add",
            headers={"X-Forwarded-For": "10.1.0.5", "User-Agent": "UA-5"},
        )
        assert client.get("/api/predictions/community-favorites").json()["prediction_ids"] == [p.id]
        assert client.get(f"/api/predictions/{p.id}/pulse").json()["is_community_favorite"] is True
        print("TEST_PD1_PULSE_DEDUP_AND_THRESHOLD: PASS")
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()


def test_pd2_virtual_bankroll_honesty():
    client, db = _make_client()
    now = datetime.now(timezone.utc)
    try:
        # 14 decided matches -> ready=False
        for i in range(14):
            m = Match(
                iddaa_id=f"pd2_{i}",
                league="La Liga",
                home_team=f"H{i}",
                away_team=f"A{i}",
                start_time_utc=now - timedelta(days=20 - i),
                status="FINISHED",
                home_score=1,
                away_score=0,
            )
            db.add(m)
            db.flush()
            db.add(
                Prediction(
                    match_id=m.id,
                    market_key="MS",
                    selection_key="1",
                    iddaa_odd=2.00,
                    published_odd=2.00,
                    confidence_score=70.0,
                    published_confidence=70.0,
                    is_top_pick=True,
                    status="WON",
                    settled_at=now - timedelta(days=20 - i),
                )
            )
        db.commit()

        perf1 = client.get("/api/performance").json()
        vb1 = perf1["virtual_bankroll"]
        assert vb1["ready"] is False and vb1["decided_count"] == 14 and vb1["net_profit"] is None

        # Add 1 LOST (odd=2.00 -> -100) + 1 VOID (-> 0) -> 15 decided, net = 14*100 - 100 = +1300
        for idx, st in enumerate(["LOST", "VOID"]):
            m = Match(
                iddaa_id=f"pd2_extra_{idx}",
                league="La Liga",
                home_team=f"EH{idx}",
                away_team=f"EA{idx}",
                start_time_utc=now - timedelta(days=2 - idx),
                status="FINISHED" if st == "LOST" else "VOID",
            )
            db.add(m)
            db.flush()
            db.add(
                Prediction(
                    match_id=m.id,
                    market_key="MS",
                    selection_key="1",
                    iddaa_odd=2.00,
                    published_odd=2.00,
                    confidence_score=70.0,
                    published_confidence=70.0,
                    is_top_pick=True,
                    status=st,
                    settled_at=now - timedelta(days=2 - idx),
                )
            )
        db.commit()
        clear_api_cache()

        perf2 = client.get("/api/performance").json()
        vb2 = perf2["virtual_bankroll"]
        assert vb2["ready"] is True and vb2["decided_count"] == 15
        assert vb2["net_profit"] == 1300.0
        assert vb2["peak_profit"] == 1400.0
        assert vb2["max_drawdown"] == 100.0
        assert len(vb2["series"]) >= 1
        print("TEST_PD2_VIRTUAL_BANKROLL_HONESTY: PASS")
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()


def test_pd3_calibration_csv_and_poll():
    client, db = _make_client()
    now_tr = datetime(2026, 11, 26, 11, 30, tzinfo=telegram_bot.TR_TZ)
    now_utc = now_tr.astimezone(timezone.utc)
    try:
        m_fin = Match(
            iddaa_id="pd3_fin",
            league="Premier League",
            home_team="Arsenal",
            away_team="Everton",
            start_time_utc=now_utc - timedelta(days=1),
            status="FINISHED",
            home_score=3,
            away_score=1,
        )
        m_sched = Match(
            iddaa_id="pd3_sch",
            league="Süper Lig",
            home_team="Galatasaray",
            away_team="Trabzonspor",
            start_time_utc=now_utc + timedelta(hours=4),
            status="SCHEDULED",
        )
        db.add_all([m_fin, m_sched])
        db.flush()
        db.add(
            Prediction(
                match_id=m_fin.id,
                market_key="MS",
                selection_key="1",
                iddaa_odd=1.55,
                published_odd=1.55,
                confidence_score=78.0,
                published_confidence=78.0,
                is_top_pick=True,
                status="WON",
            )
        )
        db.add(
            Prediction(
                match_id=m_sched.id,
                market_key="MS",
                selection_key="1",
                iddaa_odd=1.65,
                published_odd=1.65,
                confidence_score=80.0,
                published_confidence=80.0,
                is_top_pick=True,
                status="PENDING",
            )
        )
        db.commit()

        with patch.object(settings, "cron_secret", "test-cron-secret"):
            assert client.get("/api/admin/export/calibration.csv").status_code == 401
            assert client.get("/api/admin/export/calibration.csv", headers={"X-Cron-Secret": "bad"}).status_code == 401

            csv_resp = client.get("/api/admin/export/calibration.csv", headers={"X-Cron-Secret": "test-cron-secret"})
            assert csv_resp.status_code == 200
            assert "text/csv" in csv_resp.headers["content-type"]
            body = csv_resp.content.decode("utf-8-sig")
            assert "Arsenal" in body and "WON" in body and "id,match_date_tr" in body

        calls = []
        orig_post = telegram_bot.httpx.Client.post

        def fake_post(self, url, *args, **kwargs):
            if "api.telegram.org" not in str(url):
                return orig_post(self, url, *args, **kwargs)
            calls.append((url.rsplit("/", 1)[-1], kwargs.get("json")))
            return _FakeResponse(200, {"ok": True, "result": {"message_id": 777}})

        class _FakeDt(datetime):
            @classmethod
            def now(cls, tz=None):
                return now_utc if tz else now_utc.replace(tzinfo=None)

        with patch.object(settings, "telegram_bot_token", "fake-token"), \
             patch.object(settings, "telegram_chat_id", "-100123456"), \
             patch.object(settings, "telegram_topic_chat", "99"), \
             patch("app.telegram_bot.datetime", _FakeDt), \
             patch("httpx.Client.post", new=fake_post), \
             patch("app.telegram_bot.time.sleep"):
            r1 = telegram_bot.dispatch_daily_chat_poll(db)
            assert r1["sent"] is True
            assert len(calls) == 1 and calls[0][0] == "sendPoll"
            assert calls[0][1]["message_thread_id"] == 99
            assert "Galatasaray - Trabzonspor" in calls[0][1]["question"]

            # Second run same day -> idempotent
            calls.clear()
            r2 = telegram_bot.dispatch_daily_chat_poll(db)
            assert r2["sent"] is False and len(calls) == 0

        print("TEST_PD3_CALIBRATION_CSV_AND_POLL: PASS")
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()


if __name__ == "__main__":
    test_pd1_pulse_dedup_and_threshold()
    test_pd2_virtual_bankroll_honesty()
    test_pd3_calibration_csv_and_poll()
    print("ALL_PAKET_D_TESTS: PASS")
