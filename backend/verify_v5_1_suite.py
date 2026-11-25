import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TEST_DB_PATH = Path(__file__).resolve().parent / "test_v5_1_isolated.db"
if TEST_DB_PATH.exists():
    TEST_DB_PATH.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, engine, ensure_schema, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Coupon, CouponItem, Match, MatchContext, OddsCurrent, Prediction  # noqa: E402
from scrapers.external_data import extract_regular_time_score  # noqa: E402
from services.analyzer import analyze_upcoming_matches  # noqa: E402
from services.coupons import generate_daily_coupons  # noqa: E402
from services.settlement import settle_finished_matches  # noqa: E402
from services.sync import acquire_db_sync_lock, release_db_sync_lock, run_sync_pipeline  # noqa: E402


def override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


def main():
    ensure_schema()
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    client = TestClient(app)

    try:
        # ------------------------------------------------------------------
        # TEST 1: 90m AET/PEN Settlement + POSTPONED -> VOID + Coupon 1.00
        # ------------------------------------------------------------------
        m_aet = Match(
            iddaa_id="v51_aet",
            league="Test Cup",
            country="Test",
            is_major_league=True,
            home_team="Alpha FC",
            away_team="Beta FC",
            start_time_utc=now - timedelta(hours=3),
            status="SCHEDULED",
        )
        m_post = Match(
            iddaa_id="v51_post",
            league="Test Cup",
            country="Test",
            is_major_league=True,
            home_team="Gamma FC",
            away_team="Delta FC",
            start_time_utc=now - timedelta(hours=3),
            status="SCHEDULED",
        )
        m_won = Match(
            iddaa_id="v51_won",
            league="Test Cup",
            country="Test",
            is_major_league=True,
            home_team="Epsilon FC",
            away_team="Zeta FC",
            start_time_utc=now - timedelta(hours=3),
            status="SCHEDULED",
        )
        db.add_all([m_aet, m_post, m_won])
        db.flush()

        p_aet = Prediction(
            match_id=m_aet.id,
            market_key="MS",
            selection_key="X",
            iddaa_odd=3.10,
            confidence_score=70.0,
            score_breakdown_json=json.dumps({"blended_prob_pct": 35.0}),
            status="PENDING",
        )
        p_post = Prediction(
            match_id=m_post.id,
            market_key="MS",
            selection_key="1",
            iddaa_odd=1.85,
            confidence_score=72.0,
            score_breakdown_json=json.dumps({"blended_prob_pct": 60.0}),
            status="PENDING",
        )
        p_won = Prediction(
            match_id=m_won.id,
            market_key="MS",
            selection_key="1",
            iddaa_odd=1.60,
            confidence_score=75.0,
            score_breakdown_json=json.dumps({"blended_prob_pct": 68.0}),
            status="PENDING",
        )
        db.add_all([p_aet, p_post, p_won])
        db.flush()

        coupon = Coupon(
            coupon_date="2026-11-25",
            tier="BANKO",
            total_odd=round(1.85 * 1.60, 2),
            avg_confidence=73.5,
            status="PENDING",
        )
        db.add(coupon)
        db.flush()
        db.add_all([
            CouponItem(coupon_id=coupon.id, prediction_id=p_post.id),
            CouponItem(coupon_id=coupon.id, prediction_id=p_won.id),
        ])
        db.commit()

        # Simulate ESPN events: m_aet ended 2-1 AET (1-1 at 90m, 105' ET winner)
        rt_aet = extract_regular_time_score(
            comp={
                "details": [
                    {"scoringPlay": True, "clock": {"displayValue": "22'"}, "team": {"id": "10"}},
                    {"scoringPlay": True, "clock": {"displayValue": "90'+2'"}, "team": {"id": "20"}},
                    {"scoringPlay": True, "clock": {"displayValue": "105'"}, "team": {"id": "10"}},
                ]
            },
            home_comp={"team": {"id": "10"}, "score": "2"},
            away_comp={"team": {"id": "20"}, "score": "1"},
            status_obj={"name": "STATUS_FINAL_AET", "shortDetail": "AET", "completed": True},
        )
        fake_espn = [
            {
                "espn_id": "e1",
                "home_team": "Alpha FC",
                "away_team": "Beta FC",
                "start_time_utc": m_aet.start_time_utc,
                "completed": True,
                "home_score": 2,
                "away_score": 1,
                **rt_aet,
            },
            {
                "espn_id": "e2",
                "home_team": "Gamma FC",
                "away_team": "Delta FC",
                "start_time_utc": m_post.start_time_utc,
                "completed": False,
                "is_void_status": True,
                "home_score": None,
                "away_score": None,
            },
            {
                "espn_id": "e3",
                "home_team": "Epsilon FC",
                "away_team": "Zeta FC",
                "start_time_utc": m_won.start_time_utc,
                "completed": True,
                "is_void_status": False,
                "is_aet_or_pen": False,
                "home_score": 2,
                "away_score": 0,
            },
        ]
        settled = settle_finished_matches(db, espn_events=fake_espn)
        db.refresh(m_aet)
        db.refresh(m_post)
        db.refresh(p_aet)
        db.refresh(p_post)
        db.refresh(p_won)
        db.refresh(coupon)

        assert settled == 3, f"Expected 3 settled, got {settled}"
        assert (m_aet.home_score, m_aet.away_score) == (1, 1), f"90m score failed: {(m_aet.home_score, m_aet.away_score)}"
        assert p_aet.status == "WON", f"Expected MS X WON at 90m 1-1, got {p_aet.status}"
        assert m_post.status == "VOID" and p_post.status == "VOID", "POSTPONED -> VOID failed"
        assert coupon.status == "WON" and coupon.total_odd == 1.60, f"Coupon VOID=1.00 failed: {coupon.status}, {coupon.total_odd}"

        perf = client.get("/api/performance").json()
        assert perf["total_settled"] == 2 and perf["overall_win_rate"] == 100.0, f"VOID leaked into win_rate: {perf}"
        print(f"TEST_1_SETTLEMENT_90M_VOID: PASS (aet_90m=1-1->WON, postponed->VOID, coupon={coupon.status}@{coupon.total_odd}, win_rate={perf['overall_win_rate']}%)")

        # ------------------------------------------------------------------
        # TEST 2: Live Match Leakage Lock (<= 5 min to kickoff excluded)
        # ------------------------------------------------------------------
        m_soon = Match(
            iddaa_id="v51_soon",
            league="Premier League",
            country="England",
            is_major_league=True,
            home_team="Arsenal",
            away_team="Chelsea",
            start_time_utc=now + timedelta(minutes=3),
            status="SCHEDULED",
        )
        m_later = Match(
            iddaa_id="v51_later",
            league="Premier League",
            country="England",
            is_major_league=True,
            home_team="Liverpool",
            away_team="Everton",
            start_time_utc=now + timedelta(hours=3),
            status="SCHEDULED",
        )
        db.add_all([m_soon, m_later])
        db.flush()
        db.add_all([
            OddsCurrent(match_id=m_soon.id, market_key="MS", selection_key="1", iddaa_odd=1.45, opening_odd=1.50),
            OddsCurrent(match_id=m_soon.id, market_key="MS", selection_key="X", iddaa_odd=4.20, opening_odd=4.20),
            OddsCurrent(match_id=m_soon.id, market_key="MS", selection_key="2", iddaa_odd=6.50, opening_odd=6.50),
            OddsCurrent(match_id=m_later.id, market_key="MS", selection_key="1", iddaa_odd=1.42, opening_odd=1.48),
            OddsCurrent(match_id=m_later.id, market_key="MS", selection_key="X", iddaa_odd=4.40, opening_odd=4.40),
            OddsCurrent(match_id=m_later.id, market_key="MS", selection_key="2", iddaa_odd=7.00, opening_odd=7.00),
            # Pre-existing prediction for m_soon (must still be filtered from /api/predictions & coupons)
            Prediction(
                match_id=m_soon.id,
                market_key="MS",
                selection_key="1",
                iddaa_odd=1.45,
                confidence_score=95.0,
                score_breakdown_json=json.dumps({"blended_prob_pct": 75.0}),
                status="PENDING",
            ),
        ])
        db.commit()

        analyze_upcoming_matches(db)
        generate_daily_coupons(db)
        preds_api = client.get("/api/predictions?today_only=false&major_only=true").json()
        returned_ids = {item["match_id"] for item in preds_api["items"]}
        assert m_soon.id not in returned_ids, f"<=5m match leaked into /api/predictions! {returned_ids}"
        assert m_later.id in returned_ids, "Future match missing from /api/predictions"
        print(f"TEST_2_LIVE_LOCK_5M: PASS (m_soon_excluded={m_soon.id not in returned_ids}, m_later_included={m_later.id in returned_ids})")

        # ------------------------------------------------------------------
        # TEST 3: Double-Sync Lock + force=true Guard + 180s Refresh Cooldown
        # ------------------------------------------------------------------
        # 3a: DB sync_lock blocks concurrent run_sync_pipeline
        assert acquire_db_sync_lock(db) is True
        res_locked = run_sync_pipeline()
        assert res_locked == {"status": "already_running"}, f"Expected already_running, got {res_locked}"
        release_db_sync_lock(db)

        # 3b: force=true without X-Cron-Secret preserves existing DB AI summary
        db.add(MatchContext(match_id=m_later.id, ai_summary="Kilitli DB Ozeti", ai_source="gemini"))
        db.commit()
        r_ai = client.post(f"/api/matches/{m_later.id}/ai-summary?force=true").json()
        assert r_ai["ai_summary"] == "Kilitli DB Ozeti", f"force=true bypassed without secret: {r_ai}"

        # 3c: 180s cooldown on POST /api/refresh without X-Cron-Secret
        from unittest.mock import patch
        with patch("app.routers.api.run_sync_pipeline", return_value={"status": "mocked"}):
            r_ref1 = client.post("/api/refresh").json()
            r_ref2 = client.post("/api/refresh").json()
        assert r_ref1["status"] == "started", f"First refresh failed: {r_ref1}"
        assert r_ref2["status"] == "cooldown" and 1 <= r_ref2["retry_after_sec"] <= 180, f"Cooldown failed: {r_ref2}"
        print(f"TEST_3_LOCKS_AND_QUOTA: PASS (sync_lock={res_locked['status']}, force_blocked=True, refresh_cooldown={r_ref2})")
    finally:
        db.close()
        engine.dispose()
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()
            print(f"CLEANUP: Removed {TEST_DB_PATH.name}")


if __name__ == "__main__":
    main()
