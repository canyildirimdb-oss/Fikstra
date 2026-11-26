"""Isolated unit tests for Fikstra Paket C (batch status, breakdown honesty, admin & announcements)."""

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
from app.models import Match, Prediction, ScraperCache, SiteAnnouncement, SyncLog, TelegramDispatch
from app.routers.api import clear_api_cache


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


def test_pc1_batch_status():
    client, db = _make_client()
    now = datetime.now(timezone.utc)
    try:
        pred_ids = []
        for i in range(35):
            m = Match(
                iddaa_id=f"pc1_{i}",
                league="Süper Lig",
                home_team=f"Home{i}",
                away_team=f"Away{i}",
                start_time_utc=now - timedelta(hours=3),
                status="FINISHED" if i % 2 == 0 else "SCHEDULED",
                home_score=2 if i % 2 == 0 else None,
                away_score=1 if i % 2 == 0 else None,
            )
            db.add(m)
            db.flush()
            p = Prediction(
                match_id=m.id,
                market_key="MS",
                selection_key="1",
                iddaa_odd=1.75,
                published_odd=1.80,
                confidence_score=72.0,
                status="WON" if i % 2 == 0 else "PENDING",
            )
            db.add(p)
            db.flush()
            pred_ids.append(p.id)
        db.commit()

        ids_param = ",".join(str(x) for x in pred_ids)
        resp = client.get(f"/api/predictions/status?ids={ids_param}")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 30, f"Expected max 30 items, got {len(items)}"
        assert items[0]["home_team"] == "Home0"
        assert items[0]["status"] == "WON"
        assert items[0]["home_score"] == 2
        assert items[0]["away_score"] == 1
        assert items[0]["odd"] == 1.80
        print("TEST_PC1_BATCH_STATUS: PASS")
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()


def test_pc2_breakdown_honesty():
    client, db = _make_client()
    now = datetime.now(timezone.utc)
    try:
        # 5 decided matches in Premier League (4 WON, 1 LOST -> 80.0%)
        for i in range(5):
            m = Match(
                iddaa_id=f"pc2_epl_{i}",
                league="Premier League",
                home_team=f"EPL_H{i}",
                away_team=f"EPL_A{i}",
                start_time_utc=now - timedelta(days=1),
                status="FINISHED",
                home_score=2 if i < 4 else 0,
                away_score=1,
            )
            db.add(m)
            db.flush()
            db.add(
                Prediction(
                    match_id=m.id,
                    market_key="MS",
                    selection_key="1",
                    iddaa_odd=1.70,
                    confidence_score=70.0,
                    published_confidence=70.0,
                    is_top_pick=True,
                    status="WON" if i < 4 else "LOST",
                    settled_at=now - timedelta(hours=2),
                )
            )

        # 3 decided matches in Serie A (< 5 -> low_sample=True, win_rate=None)
        for i in range(3):
            m = Match(
                iddaa_id=f"pc2_sa_{i}",
                league="Serie A",
                home_team=f"SA_H{i}",
                away_team=f"SA_A{i}",
                start_time_utc=now - timedelta(days=1),
                status="FINISHED",
                home_score=2,
                away_score=2,
            )
            db.add(m)
            db.flush()
            db.add(
                Prediction(
                    match_id=m.id,
                    market_key="KG",
                    selection_key="VAR",
                    iddaa_odd=1.65,
                    confidence_score=68.0,
                    published_confidence=68.0,
                    is_top_pick=True,
                    status="WON",
                    settled_at=now - timedelta(hours=2),
                )
            )
        db.commit()

        resp = client.get("/api/performance/breakdown")
        assert resp.status_code == 200
        bd = resp.json()
        epl = next(r for r in bd["by_league"] if r["label"] == "Premier League")
        sa = next(r for r in bd["by_league"] if r["label"] == "Serie A")
        assert epl["decided"] == 5 and epl["low_sample"] is False and epl["win_rate"] == 80.0
        assert sa["decided"] == 3 and sa["low_sample"] is True and sa["win_rate"] is None

        ms = next(r for r in bd["by_market"] if r["label"] == "MS")
        kg = next(r for r in bd["by_market"] if r["label"] == "KG")
        assert ms["win_rate"] == 80.0 and ms["low_sample"] is False
        assert kg["win_rate"] is None and kg["low_sample"] is True
        print("TEST_PC2_BREAKDOWN_HONESTY: PASS")
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()


def test_pc3_admin_and_announcements():
    client, db = _make_client()
    now = datetime.now(timezone.utc)
    try:
        db.add(ScraperCache(cache_key="odds_api:remaining_credits", payload_json='{"remaining": 490, "used": 10}'))
        db.add(SyncLog(status="SUCCESS", finished_at=now, matches_synced=12))
        db.add(
            TelegramDispatch(
                dispatch_key="coupon:1",
                kind="coupon",
                chat_id="-100123",
                thread_id=11,
                message_id=101,
                content_hash="abc",
                settled_replied=False,
            )
        )
        # Pre-seed an expired announcement
        db.add(
            SiteAnnouncement(
                text="Eski süresi dolmuş duyuru",
                is_active=True,
                expires_at=now - timedelta(hours=1),
            )
        )
        db.commit()

        with patch.object(settings, "cron_secret", "test-cron-secret"), \
             patch.object(settings, "telegram_admin_chat_id", "8107018165"):
            # 1. Missing/wrong secret -> 401
            assert client.get("/api/admin/overview").status_code == 401
            assert client.get("/api/admin/overview", headers={"X-Cron-Secret": "bad"}).status_code == 401
            assert client.post("/api/admin/announcements", json={"text": "Hack"}).status_code == 401

            # 2. Valid secret overview
            ov = client.get("/api/admin/overview", headers={"X-Cron-Secret": "test-cron-secret"})
            assert ov.status_code == 200
            ov_data = ov.json()
            assert ov_data["odds_api_remaining"] == 490
            assert ov_data["telegram_admin_configured"] is True
            assert len(ov_data["recent_dispatches"]) == 1

            # 3. Create active announcement
            cr = client.post(
                "/api/admin/announcements",
                json={"text": "Yeni Şampiyonlar Ligi bülteni yayında", "link_url": "/?tab=coupons", "expires_hours": 24},
                headers={"X-Cron-Secret": "test-cron-secret"},
            )
            assert cr.status_code == 200
            ann_id = cr.json()["id"]

            # Active endpoint returns only the non-expired active announcement
            act = client.get("/api/announcements/active").json()
            assert len(act) == 1 and act[0]["id"] == ann_id

            # Toggle passive -> active endpoint returns []
            pt = client.patch(
                f"/api/admin/announcements/{ann_id}",
                json={"is_active": False},
                headers={"X-Cron-Secret": "test-cron-secret"},
            )
            assert pt.status_code == 200 and pt.json()["is_active"] is False
            assert client.get("/api/announcements/active").json() == []

            # Delete announcement
            dl = client.delete(
                f"/api/admin/announcements/{ann_id}",
                headers={"X-Cron-Secret": "test-cron-secret"},
            )
            assert dl.status_code == 200 and dl.json()["ok"] is True

        print("TEST_PC3_ADMIN_AND_ANNOUNCEMENTS: PASS")
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()


if __name__ == "__main__":
    test_pc1_batch_status()
    test_pc2_breakdown_honesty()
    test_pc3_admin_and_announcements()
    print("ALL_PAKET_C_TESTS: PASS")
