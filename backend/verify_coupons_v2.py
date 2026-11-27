import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Coupon, CouponItem, Match, Prediction  # noqa: E402
from app.routers.api import clear_api_cache  # noqa: E402
from services.coupons import extract_leg_probability, generate_daily_coupons  # noqa: E402


def _make_session():
    db_path = Path(__file__).resolve().parent / "tmp_verify_coupons_v2.db"
    if db_path.exists():
        db_path.unlink()
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return db_path, engine, SessionLocal


def _add_match_pred(
    db,
    *,
    iddaa_id: str,
    home: str,
    away: str,
    league: str = "İngiltere Premier Ligi",
    is_major: bool = True,
    start_utc: datetime,
    market: str = "AU_15",
    selection: str = "UST",
    odd: float = 1.35,
    global_odd: float | None = 1.38,
    conf: float = 75.0,
    prob_pct: float = 74.0,
    edge_pct: float | None = 2.0,
    data_status: str = "ready",
    is_top_pick: bool = True,
):
    m = Match(
        iddaa_id=iddaa_id,
        league=league,
        country="EU",
        is_major_league=is_major,
        home_team=home,
        away_team=away,
        start_time_utc=start_utc,
        status="SCHEDULED",
        data_status=data_status,
    )
    db.add(m)
    db.flush()
    p = Prediction(
        match_id=m.id,
        market_key=market,
        selection_key=selection,
        iddaa_odd=odd,
        global_odd=global_odd,
        confidence_score=conf,
        value_edge_pct=edge_pct,
        score_breakdown_json=json.dumps({"blended_prob_pct": prob_pct, "true_prob_pct": prob_pct}),
        is_top_pick=is_top_pick,
        status="PENDING",
    )
    db.add(p)
    db.flush()
    return m, p


def run_tests():
    # Fixed reference clock: 2026-11-28 09:00 UTC = 12:00 TR
    fixed_now = datetime(2026, 11, 28, 9, 0, tzinfo=timezone.utc)

    class _FixedDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

    # 1) Dynamic 3-leg Banko when 3rd leg is ultra-safe (>=70% & <=1.48 & P_total>=40%)
    db_path, engine, SessionLocal = _make_session()
    try:
        with SessionLocal() as db:
            with patch("services.coupons.datetime", _FixedDT), patch(
                "services.coupons.tr_today_str", return_value="2026-11-28"
            ):
                # 19:00, 20:00, 21:00 TR = 16:00, 17:00, 18:00 UTC
                _add_match_pred(
                    db, iddaa_id="b3_1", home="Arsenal", away="Everton",
                    start_utc=fixed_now + timedelta(hours=7), odd=1.32, global_odd=1.35, conf=78, prob_pct=76,
                )
                _add_match_pred(
                    db, iddaa_id="b3_2", home="Inter", away="Verona",
                    start_utc=fixed_now + timedelta(hours=8), odd=1.35, global_odd=1.38, conf=77, prob_pct=75,
                )
                _add_match_pred(
                    db, iddaa_id="b3_3", home="Bayern", away="Mainz",
                    start_utc=fixed_now + timedelta(hours=9), odd=1.34, global_odd=1.36, conf=76, prob_pct=74,
                )
                db.commit()
                coupons = generate_daily_coupons(db)
                banko = next((c for c in coupons if c.tier == "BANKO"), None)
                assert banko is not None, "Expected BANKO coupon"
                assert len(banko.items) == 3, f"Expected 3 legs for ultra-safe Banko, got {len(banko.items)}"
                comb_p = math.prod(extract_leg_probability(it.prediction) for it in banko.items)
                assert comb_p >= 0.40, f"Combined prob {comb_p:.3f} < 0.40"
                assert 1.70 <= banko.total_odd <= 2.65, f"Banko total_odd {banko.total_odd} out of 1.70-2.65"
        print("TEST_1_BANKO_DYNAMIC_3_LEGS: PASS")
    finally:
        engine.dispose()
        if db_path.exists():
            db_path.unlink()

    # 2) Weak 3rd candidate -> Banko stays at 2 legs (does not force 3rd)
    db_path, engine, SessionLocal = _make_session()
    try:
        with SessionLocal() as db:
            with patch("services.coupons.datetime", _FixedDT), patch(
                "services.coupons.tr_today_str", return_value="2026-11-28"
            ):
                _add_match_pred(
                    db, iddaa_id="b2_1", home="Real Madrid", away="Getafe",
                    start_utc=fixed_now + timedelta(hours=7), odd=1.38, global_odd=1.40, conf=79, prob_pct=75,
                )
                _add_match_pred(
                    db, iddaa_id="b2_2", home="Barcelona", away="Alaves",
                    start_utc=fixed_now + timedelta(hours=8), odd=1.36, global_odd=1.38, conf=78, prob_pct=74,
                )
                # 3rd leg only 66% prob (<70%) and odd 1.52 (>1.48) -> must stay 2 legs
                _add_match_pred(
                    db, iddaa_id="b2_3", home="Milan", away="Empoli",
                    start_utc=fixed_now + timedelta(hours=9), odd=1.52, global_odd=1.55, conf=70, prob_pct=66,
                )
                db.commit()
                coupons = generate_daily_coupons(db)
                banko = next((c for c in coupons if c.tier == "BANKO"), None)
                assert banko is not None
                assert len(banko.items) == 2, f"Expected Banko to stay 2 legs, got {len(banko.items)}"
        print("TEST_2_BANKO_STAYS_2_LEGS_WHEN_3RD_WEAK: PASS")
    finally:
        engine.dispose()
        if db_path.exists():
            db_path.unlink()

    # 3) Banko Quality Armor: reject 1.76 odd, Jong Ajax, dead odd (edge < -10.5%), <60m kickoff, no global_odd
    db_path, engine, SessionLocal = _make_session()
    try:
        with SessionLocal() as db:
            with patch("services.coupons.datetime", _FixedDT), patch(
                "services.coupons.tr_today_str", return_value="2026-11-28"
            ):
                # Excluded candidates:
                _add_match_pred(
                    db, iddaa_id="ex_odd", home="TeamA", away="TeamB",
                    start_utc=fixed_now + timedelta(hours=7), odd=1.76, global_odd=1.80, conf=90, prob_pct=80,
                )
                _add_match_pred(
                    db, iddaa_id="ex_res", home="Jong Ajax", away="FC Dordrecht", league="Hollanda 2. Ligi",
                    is_major=False, start_utc=fixed_now + timedelta(hours=7), odd=1.35, global_odd=1.38, conf=90, prob_pct=82,
                )
                _add_match_pred(
                    db, iddaa_id="ex_dead", home="TeamC", away="TeamD",
                    start_utc=fixed_now + timedelta(hours=7), odd=1.32, global_odd=1.58, conf=90, prob_pct=82, edge_pct=-16.4,
                )
                _add_match_pred(
                    db, iddaa_id="ex_soon", home="TeamE", away="TeamF",
                    start_utc=fixed_now + timedelta(minutes=45), odd=1.32, global_odd=1.35, conf=90, prob_pct=82,
                )
                _add_match_pred(
                    db, iddaa_id="ex_noglob", home="TeamG", away="TeamH",
                    start_utc=fixed_now + timedelta(hours=7), odd=1.32, global_odd=None, conf=90, prob_pct=82, edge_pct=None,
                )
                # Only 1 valid candidate -> Banko must NOT be created ("Kalite koruması aktif")
                _add_match_pred(
                    db, iddaa_id="valid_1", home="Liverpool", away="Fulham",
                    start_utc=fixed_now + timedelta(hours=7), odd=1.35, global_odd=1.38, conf=76, prob_pct=75,
                )
                db.commit()
                coupons = generate_daily_coupons(db)
                banko = next((c for c in coupons if c.tier == "BANKO"), None)
                assert banko is None, f"Expected no BANKO due to quality armor, got {[it.prediction.match.home_team for it in banko.items]}"
        print("TEST_3_BANKO_QUALITY_ARMOR_AND_PROTECTION: PASS")
    finally:
        engine.dispose()
        if db_path.exists():
            db_path.unlink()

    # 4) 0-24h priority vs 24-48h fallback & 48h+ exclusion + TR prime-time (13-23) vs 06:00 TR
    db_path, engine, SessionLocal = _make_session()
    try:
        with SessionLocal() as db:
            with patch("services.coupons.datetime", _FixedDT), patch(
                "services.coupons.tr_today_str", return_value="2026-11-28"
            ):
                # 06:00 TR next day = fixed_now + 15h (03:00 UTC) -> morning match!
                _add_match_pred(
                    db, iddaa_id="m_06tr", home="Sydney FC", away="Perth Glory",
                    start_utc=fixed_now + timedelta(hours=15), odd=1.35, global_odd=1.37, conf=80, prob_pct=76,
                )
                # 19:00 TR & 21:00 TR today (prime-time, 0-24h)
                _add_match_pred(
                    db, iddaa_id="p_19tr", home="Fenerbahce", away="Antalya",
                    start_utc=fixed_now + timedelta(hours=7), odd=1.36, global_odd=1.38, conf=76, prob_pct=74,
                )
                _add_match_pred(
                    db, iddaa_id="p_21tr", home="Galatasaray", away="Kayseri",
                    start_utc=fixed_now + timedelta(hours=9), odd=1.38, global_odd=1.40, conf=75, prob_pct=73,
                )
                # 36h match with only +2 conf / +1% prob (must NOT displace 0-24h)
                _add_match_pred(
                    db, iddaa_id="m_36h", home="Napoli", away="Torino",
                    start_utc=fixed_now + timedelta(hours=36), odd=1.35, global_odd=1.37, conf=78, prob_pct=75,
                )
                # 52h match (>48h, never allowed)
                _add_match_pred(
                    db, iddaa_id="m_52h", home="Chelsea", away="Wolves",
                    start_utc=fixed_now + timedelta(hours=52), odd=1.30, global_odd=1.35, conf=95, prob_pct=88,
                )
                db.commit()
                coupons = generate_daily_coupons(db)
                banko = next((c for c in coupons if c.tier == "BANKO"), None)
                assert banko is not None
                picked_ids = {it.prediction.match.iddaa_id for it in banko.items}
                assert picked_ids == {"p_19tr", "p_21tr"}, f"Expected prime-time 0-24h picks, got {picked_ids}"
        print("TEST_4_PRIMETIME_AND_24H_PRIORITY: PASS")
    finally:
        engine.dispose()
        if db_path.exists():
            db_path.unlink()

    # 5) 0-24h insufficient -> 36h candidate used; 52h still excluded
    db_path, engine, SessionLocal = _make_session()
    try:
        with SessionLocal() as db:
            with patch("services.coupons.datetime", _FixedDT), patch(
                "services.coupons.tr_today_str", return_value="2026-11-28"
            ):
                _add_match_pred(
                    db, iddaa_id="only_24h", home="Atletico", away="Celta",
                    start_utc=fixed_now + timedelta(hours=8), odd=1.36, global_odd=1.38, conf=76, prob_pct=74,
                )
                _add_match_pred(
                    db, iddaa_id="qual_36h", home="Juventus", away="Udinese",
                    start_utc=fixed_now + timedelta(hours=30), odd=1.38, global_odd=1.40, conf=77, prob_pct=74,
                )
                _add_match_pred(
                    db, iddaa_id="too_far_52h", home="PSG", away="Nice",
                    start_utc=fixed_now + timedelta(hours=52), odd=1.30, global_odd=1.34, conf=95, prob_pct=88,
                )
                db.commit()
                coupons = generate_daily_coupons(db)
                banko = next((c for c in coupons if c.tier == "BANKO"), None)
                assert banko is not None
                picked_ids = {it.prediction.match.iddaa_id for it in banko.items}
                assert picked_ids == {"only_24h", "qual_36h"}, f"Expected 24-48h fallback, got {picked_ids}"
        print("TEST_5_24_48H_FALLBACK_AND_48H_CEILING: PASS")
    finally:
        engine.dispose()
        if db_path.exists():
            db_path.unlink()

    # 6) Sharp tier separation + cross-coupon match_id protection + locked preservation + API serialization
    db_path, engine, SessionLocal = _make_session()
    try:
        with SessionLocal() as db:
            with patch("services.coupons.datetime", _FixedDT), patch(
                "services.coupons.tr_today_str", return_value="2026-11-28"
            ):
                # Locked historical coupon must remain untouched
                _, p_hist = _add_match_pred(
                    db, iddaa_id="hist_1", home="OldHome", away="OldAway",
                    start_utc=fixed_now - timedelta(hours=5), odd=1.40, global_odd=1.42, conf=80, prob_pct=75,
                )
                c_hist = Coupon(
                    coupon_date="2026-11-27", tier="BANKO", total_odd=1.40, avg_confidence=80.0,
                    status="WON", is_locked=True,
                )
                db.add(c_hist)
                db.flush()
                db.add(CouponItem(coupon_id=c_hist.id, prediction_id=p_hist.id, odd_at_pick=1.40))

                # Fresh pool for BANKO, DENGELI, SURPRIZ
                _add_match_pred(db, iddaa_id="bk1", home="City", away="Brighton", league="EPL", start_utc=fixed_now + timedelta(hours=7), odd=1.36, global_odd=1.38, conf=78, prob_pct=75)
                _add_match_pred(db, iddaa_id="bk2", home="Dortmund", away="Augsburg", league="Bundesliga", start_utc=fixed_now + timedelta(hours=8), odd=1.38, global_odd=1.40, conf=76, prob_pct=74)
                _add_match_pred(db, iddaa_id="dg1", home="Roma", away="Lazio", league="Serie A", start_utc=fixed_now + timedelta(hours=7), odd=1.75, global_odd=1.78, conf=68, prob_pct=56)
                _add_match_pred(db, iddaa_id="dg2", home="Sevilla", away="Betis", league="La Liga", start_utc=fixed_now + timedelta(hours=9), odd=1.80, global_odd=1.82, conf=67, prob_pct=55)
                _add_match_pred(db, iddaa_id="sp1", home="Lyon", away="Marseille", league="Ligue 1", start_utc=fixed_now + timedelta(hours=8), odd=2.30, global_odd=2.15, conf=64, prob_pct=46, edge_pct=7.0)
                _add_match_pred(db, iddaa_id="sp2", home="Porto", away="Benfica", league="Portugal", start_utc=fixed_now + timedelta(hours=10), odd=2.40, global_odd=2.22, conf=63, prob_pct=45, edge_pct=8.1)
                db.commit()

                coupons = generate_daily_coupons(db)
                by_tier = {c.tier: c for c in coupons}
                assert set(by_tier.keys()) == {"BANKO", "DENGELI", "SURPRIZ"}, f"Got tiers {list(by_tier.keys())}"
                assert 1.70 <= by_tier["BANKO"].total_odd <= 2.65
                assert 2.80 <= by_tier["DENGELI"].total_odd <= 4.80
                assert 4.80 <= by_tier["SURPRIZ"].total_odd <= 10.00

                all_mids = [it.prediction.match_id for c in coupons for it in c.items]
                assert len(all_mids) == len(set(all_mids)), "Cross-coupon match overlap should be 0 here"

                # Historical locked coupon untouched
                hist_check = db.query(Coupon).filter(Coupon.id == c_hist.id).first()
                assert hist_check.status == "WON" and hist_check.is_locked is True

                # API /api/coupons check
                def _override_db():
                    yield db

                app.dependency_overrides[get_db] = _override_db
                clear_api_cache()
                with patch("app.routers.api.tr_today_str", return_value="2026-11-28"):
                    client = TestClient(app)
                    r = client.get("/api/coupons")
                    assert r.status_code == 200
                    payload = r.json()["coupons"]
                    assert len(payload) == 3
                    for c_json in payload:
                        assert c_json["combined_probability"] is not None
                        assert c_json["settlement_label"] == "24 saat içinde sonuçlanır"
                        for it_json in c_json["items"]:
                            assert it_json["leg_probability"] is not None
                app.dependency_overrides.clear()
        print("TEST_6_TIER_SEPARATION_LOCKED_AND_API: PASS")
    finally:
        engine.dispose()
        if db_path.exists():
            db_path.unlink()

    print("\nALL_KUPON_V2_TESTS_PASSED")


if __name__ == "__main__":
    run_tests()
