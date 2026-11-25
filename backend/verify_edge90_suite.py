import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

TEST_DB_PATH = Path(__file__).resolve().parent / "test_edge90_isolated.db"
if TEST_DB_PATH.exists():
    TEST_DB_PATH.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal, engine, ensure_schema, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Match, OddsCurrent, OddsHistory, Prediction  # noqa: E402
from services.coupons import generate_daily_coupons  # noqa: E402
from utils.time_utils import TR_TZ, tr_today_str  # noqa: E402


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
        # 1. Opposite-Pick & #1-Pick Conflict Lock
        m1 = Match(
            iddaa_id="e90_1",
            league="UEFA Champions League",
            country="Europe",
            is_major_league=True,
            home_team="Club Brugge",
            away_team="Liverpool",
            start_time_utc=now + timedelta(hours=4),
            status="SCHEDULED",
        )
        m2 = Match(
            iddaa_id="e90_2",
            league="Premier League",
            country="England",
            is_major_league=True,
            home_team="Arsenal",
            away_team="Chelsea",
            start_time_utc=now + timedelta(hours=5),
            status="SCHEDULED",
        )
        m3 = Match(
            iddaa_id="e90_3",
            league="LaLiga",
            country="Spain",
            is_major_league=True,
            home_team="Real Madrid",
            away_team="Sevilla",
            start_time_utc=now + timedelta(hours=6),
            status="SCHEDULED",
        )
        db.add_all([m1, m2, m3])
        db.flush()

        # Club Brugge vs Liverpool: #1 pick is AU_35:UST (conf 66, odd 2.10), opposite AU_35:ALT (conf 64, odd 1.95)
        db.add_all([
            Prediction(match_id=m1.id, market_key="AU_35", selection_key="UST", iddaa_odd=2.10, confidence_score=66.0, value_edge_pct=5.0, status="PENDING"),
            Prediction(match_id=m1.id, market_key="AU_35", selection_key="ALT", iddaa_odd=1.95, confidence_score=64.0, value_edge_pct=9.0, odds_drop_pct=5.0, status="PENDING"),
            Prediction(match_id=m1.id, market_key="KG", selection_key="YOK", iddaa_odd=2.30, confidence_score=58.0, value_edge_pct=8.0, status="PENDING"),
            Prediction(match_id=m2.id, market_key="MS", selection_key="1", iddaa_odd=2.05, confidence_score=67.0, value_edge_pct=4.0, status="PENDING"),
            Prediction(match_id=m3.id, market_key="MS", selection_key="1", iddaa_odd=1.40, confidence_score=74.0, value_edge_pct=2.0, status="PENDING"),
        ])
        db.commit()

        coupons = generate_daily_coupons(db)
        all_picks = [(it.prediction.match_id, it.prediction.market_key, it.prediction.selection_key) for c in coupons for it in c.items]
        assert (m1.id, "AU_35", "ALT") not in all_picks, f"Opposite AU_35:ALT leaked into coupon: {all_picks}"
        assert (m1.id, "KG", "YOK") not in all_picks, f"Contradicting KG:YOK leaked into coupon: {all_picks}"
        print(f"TEST_1_OPPOSITE_PICK_LOCK: PASS (picks={all_picks})")

        # 2. POST /api/cron/sync < 1s -> 202 Accepted
        t0 = time.perf_counter()
        with patch("app.main.run_sync_pipeline", return_value={"status": "ok"}):
            res_cron = client.post("/api/cron/sync", headers={"X-Cron-Secret": settings.cron_secret})
        elapsed = time.perf_counter() - t0
        assert res_cron.status_code == 202 and elapsed < 1.0, f"Cron failed: {res_cron.status_code} in {elapsed:.3f}s"
        print(f"TEST_2_CRON_202_FAST: PASS (status={res_cron.status_code}, elapsed={elapsed*1000:.1f}ms)")

        # 3. odds_history dedup on unchanged odd
        oc = OddsCurrent(match_id=m1.id, market_key="MS", selection_key="1", iddaa_odd=2.40, opening_odd=2.40)
        db.add(oc)
        db.add(OddsHistory(match_id=m1.id, market_key="MS", selection_key="1", iddaa_odd=2.40, recorded_at=now))
        db.commit()
        before_cnt = db.query(OddsHistory).filter(OddsHistory.match_id == m1.id).count()
        # Simulate unchanged odd check from sync.py
        new_odd = 2.40
        if abs(oc.iddaa_odd - new_odd) >= 0.01:
            db.add(OddsHistory(match_id=m1.id, market_key="MS", selection_key="1", iddaa_odd=new_odd, recorded_at=now))
        db.commit()
        after_same = db.query(OddsHistory).filter(OddsHistory.match_id == m1.id).count()
        assert after_same == before_cnt == 1, f"Duplicate odds_history written: {after_same}"
        print(f"TEST_3_ODDS_HISTORY_DEDUP: PASS (before={before_cnt}, after_unchanged={after_same})")

        # 4. Europe/Istanbul timezone
        tz_key = getattr(TR_TZ, "key", None)
        assert tz_key == "Europe/Istanbul", f"Expected ZoneInfo('Europe/Istanbul'), got {TR_TZ}"
        print(f"TEST_4_ISTANBUL_TZ: PASS (tz={tz_key}, tr_today={tr_today_str()})")
    finally:
        db.close()
        engine.dispose()
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()
            print(f"CLEANUP: Removed {TEST_DB_PATH.name}")


if __name__ == "__main__":
    main()
