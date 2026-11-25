from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import Coupon, CouponItem, Match, Prediction
from app.routers.api import clear_api_cache
from services.coupons import generate_daily_coupons

ISO_DB_PATH = Path(__file__).resolve().parent / "test_stabilization_isolated.db"


def run_g_tests():
    if ISO_DB_PATH.exists():
        ISO_DB_PATH.unlink()

    eng = create_engine(f"sqlite:///{ISO_DB_PATH}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    IsoSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    db = IsoSession()

    def _override_get_db():
        s = IsoSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db

    try:
        # 25.11 23:00 TR == 2026-11-25 20:00 UTC
        kickoff_25_2300_utc = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)
        # 25.11 20:00 TR == 2026-11-25 17:00 UTC (already finished)
        kickoff_25_2000_utc = datetime(2026, 11, 25, 17, 0, tzinfo=timezone.utc)

        m1 = Match(
            iddaa_id="g_m1",
            league="Süper Lig",
            country="Turkey",
            is_major_league=True,
            home_team="Galatasaray",
            away_team="Fenerbahce",
            start_time_utc=kickoff_25_2300_utc,
            status="SCHEDULED",
        )
        m2 = Match(
            iddaa_id="g_m2",
            league="Premier League",
            country="England",
            is_major_league=True,
            home_team="Arsenal",
            away_team="Chelsea",
            start_time_utc=kickoff_25_2000_utc,
            status="FINISHED",
            home_score=2,
            away_score=0,
        )
        db.add_all([m1, m2])
        db.flush()

        p1 = Prediction(
            match_id=m1.id,
            market_key="MS",
            selection_key="1",
            iddaa_odd=1.45,
            confidence_score=76.0,
            is_top_pick=True,
            status="PENDING",
        )
        p2 = Prediction(
            match_id=m2.id,
            market_key="MS",
            selection_key="1",
            iddaa_odd=1.40,
            confidence_score=78.0,
            is_top_pick=True,
            status="WON",
        )
        db.add_all([p1, p2])
        db.flush()

        c_banko = Coupon(
            coupon_date="2026-11-25",
            tier="BANKO",
            total_odd=2.03,
            avg_confidence=77.0,
            status="PENDING",
            is_locked=True,
        )
        db.add(c_banko)
        db.flush()
        db.add_all([
            CouponItem(coupon_id=c_banko.id, prediction_id=p1.id),
            CouponItem(coupon_id=c_banko.id, prediction_id=p2.id),
        ])

        # Add fresh 26.11 matches that would tempt generate_daily_coupons to create a new BANKO
        kickoff_26_2045_utc = datetime(2026, 11, 26, 17, 45, tzinfo=timezone.utc)
        for idx, (ht, at) in enumerate([("Real Madrid", "Sevilla"), ("Inter", "Roma"), ("Bayern", "Bremen")]):
            nm = Match(
                iddaa_id=f"g_new_{idx}",
                league="Major League",
                country="EU",
                is_major_league=True,
                home_team=ht,
                away_team=at,
                start_time_utc=kickoff_26_2045_utc + timedelta(minutes=idx * 15),
                status="SCHEDULED",
            )
            db.add(nm)
            db.flush()
            db.add(
                Prediction(
                    match_id=nm.id,
                    market_key="MS",
                    selection_key="1",
                    iddaa_odd=1.42,
                    confidence_score=88.0,
                    is_top_pick=True,
                    status="PENDING",
                )
            )
        db.commit()

        # --- TEST_G1_MIDNIGHT_COUPON_LOCK: Clock is 26.11 00:30 TR (2026-11-25 21:30 UTC) ---
        now_0030_tr_utc = datetime(2026, 11, 25, 21, 30, tzinfo=timezone.utc)

        class _MockDT_0030(datetime):
            @classmethod
            def now(cls, tz=None):
                return now_0030_tr_utc if tz else now_0030_tr_utc.replace(tzinfo=None)

        with patch("services.coupons.datetime", _MockDT_0030), \
             patch("services.coupons.tr_today_str", return_value="2026-11-26"), \
             patch("app.routers.api.tr_today_str", return_value="2026-11-26"):
            generate_daily_coupons(db)
            db.expire_all()

            all_banko = db.query(Coupon).filter(Coupon.tier == "BANKO").all()
            assert len(all_banko) == 1, f"Expected 1 BANKO coupon (locked carry-over), got {len(all_banko)}"
            assert all_banko[0].coupon_date == "2026-11-25"
            assert all_banko[0].status == "PENDING"

            clear_api_cache()
            client = TestClient(app)
            resp = client.get("/api/coupons")
            assert resp.status_code == 200
            data = resp.json()["coupons"]
            banko_api = next((c for c in data if c["tier"] == "BANKO"), None)
            assert banko_api is not None, "Active carry-over BANKO missing from /api/coupons"
            assert banko_api["coupon_date"] == "2026-11-25"
            assert banko_api["items"][0]["start_date_tr"] == "25.11"
            assert banko_api["items"][0]["start_time_tr"] == "23:00"
        print("TEST_G1_MIDNIGHT_COUPON_LOCK: PASS")

        # --- TEST_G2_CROSS_MIDNIGHT_SETTLE: Clock is 26.11 00:55 TR (2026-11-25 21:55 UTC) ---
        now_0055_tr_utc = datetime(2026, 11, 25, 21, 55, tzinfo=timezone.utc)

        class _MockDT_0055(datetime):
            @classmethod
            def now(cls, tz=None):
                return now_0055_tr_utc if tz else now_0055_tr_utc.replace(tzinfo=None)

        mock_espn = [
            {
                "home_team": "Galatasaray",
                "away_team": "Fenerbahce",
                "start_time_utc": kickoff_25_2300_utc,
                "completed": True,
                "home_score": 2,
                "away_score": 1,
                "is_void_status": False,
                "is_aet_or_pen": False,
            }
        ]

        with patch("services.settlement.datetime", _MockDT_0055), \
             patch("app.main.datetime", _MockDT_0055), \
             patch("services.settlement.fetch_espn_events_for_dates", return_value=mock_espn), \
             patch("scrapers.odds_api.sync_the_odds_api", side_effect=AssertionError("Odds API must NOT be called!")), \
             patch("app.routers.api.tr_now", return_value=now_0055_tr_utc + timedelta(hours=3)):
            r_settle = client.post("/api/cron/settle", headers={"X-Cron-Secret": settings.cron_secret})
            assert r_settle.status_code == 200, r_settle.text
            body = r_settle.json()
            assert body["odds_api_credits_used"] == 0
            assert body["settled_matches"] == 1

            db.expire_all()
            p1_after = db.query(Prediction).filter(Prediction.id == p1.id).first()
            c_after = db.query(Coupon).filter(Coupon.id == c_banko.id).first()
            assert p1_after.status == "WON"
            assert c_after.status == "WON"

            r_hist = client.get("/api/coupons/history")
            assert r_hist.status_code == 200
            hist_coupons = r_hist.json()["coupons"]
            assert len(hist_coupons) >= 1
            settled_c = hist_coupons[0]
            assert settled_c["status"] == "WON"
            assert settled_c["items"][0]["home_score"] == 2
            assert settled_c["items"][0]["away_score"] == 1

            r_health = client.get("/api/health")
            assert r_health.status_code == 200
            h_body = r_health.json()
            assert h_body["last_settle_at"] is not None
            assert h_body["pending_coupons_count"] == 0
        print("TEST_G2_CROSS_MIDNIGHT_SETTLE: PASS")

    finally:
        app.dependency_overrides.clear()
        clear_api_cache()
        db.close()
        eng.dispose()
        if ISO_DB_PATH.exists():
            ISO_DB_PATH.unlink()


if __name__ == "__main__":
    run_g_tests()
