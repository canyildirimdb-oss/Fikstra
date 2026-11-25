import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(__file__))

from app.database import Base
from app.main import FRONTEND_DIST, app
from app.models import Coupon, CouponItem, Match, MatchContext, OddsCurrent, Prediction
from services.analyzer import _compute_injury_component, _devig, analyze_upcoming_matches


def _make_session():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    Sess = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    return Sess, eng, path


def test_h1_coupon_cascade_protect():
    Sess, eng, path = _make_session()
    try:
        db = Sess()
        now = datetime.now(timezone.utc)
        m = Match(
            iddaa_id="h1_m1",
            league="Premier Lig",
            country="England",
            is_major_league=True,
            home_team="Arsenal",
            away_team="Burnley",
            start_time_utc=now + timedelta(hours=3),
            status="SCHEDULED",
        )
        db.add(m)
        db.flush()

        # Initial odd >= 1.30
        o1 = OddsCurrent(match_id=m.id, market_key="MS", selection_key="1", iddaa_odd=1.35, opening_odd=1.35)
        ox = OddsCurrent(match_id=m.id, market_key="MS", selection_key="X", iddaa_odd=4.80, opening_odd=4.80)
        o2 = OddsCurrent(match_id=m.id, market_key="MS", selection_key="2", iddaa_odd=8.50, opening_odd=8.50)
        db.add_all([o1, ox, o2])
        db.flush()

        pred = Prediction(
            match_id=m.id,
            market_key="MS",
            selection_key="1",
            iddaa_odd=1.35,
            confidence_score=74.0,
            is_top_pick=True,
            status="PENDING",
        )
        db.add(pred)
        db.flush()

        c = Coupon(coupon_date="2026-11-26", tier="BANKO", total_odd=1.35, avg_confidence=74.0, status="PENDING", is_locked=True)
        db.add(c)
        db.flush()
        ci = CouponItem(coupon_id=c.id, prediction_id=pred.id, odd_at_pick=1.35)
        db.add(ci)
        db.commit()

        # Odd drops below MIN_ODD_RULE (1.30)
        o1.iddaa_odd = 1.25
        db.commit()

        analyze_upcoming_matches(db)

        still_pred = db.query(Prediction).filter(Prediction.id == pred.id).first()
        still_item = db.query(CouponItem).filter(CouponItem.id == ci.id).first()
        assert still_pred is not None, "Prediction referenced by CouponItem must NOT be deleted!"
        assert still_item is not None, "CouponItem must remain intact!"
        print("TEST_H1_COUPON_CASCADE_PROTECT: PASS")
        db.close()
    finally:
        eng.dispose()
        if os.path.exists(path):
            os.remove(path)


def test_h2_cs_devig_and_injury():
    odds_map = {
        ("MS", "1"): OddsCurrent(match_id=1, market_key="MS", selection_key="1", iddaa_odd=2.10, opening_odd=2.10, global_avg_odd=2.15),
        ("MS", "X"): OddsCurrent(match_id=1, market_key="MS", selection_key="X", iddaa_odd=3.30, opening_odd=3.30, global_avg_odd=3.40),
        ("MS", "2"): OddsCurrent(match_id=1, market_key="MS", selection_key="2", iddaa_odd=3.40, opening_odd=3.40, global_avg_odd=3.55),
        ("CS", "1X"): OddsCurrent(match_id=1, market_key="CS", selection_key="1X", iddaa_odd=1.28, opening_odd=1.28),
        ("CS", "12"): OddsCurrent(match_id=1, market_key="CS", selection_key="12", iddaa_odd=1.25, opening_odd=1.25),
        ("CS", "X2"): OddsCurrent(match_id=1, market_key="CS", selection_key="X2", iddaa_odd=1.65, opening_odd=1.65),
    }
    cs_probs = _devig("CS", odds_map)
    p_1x = cs_probs["1X"]
    assert 0.65 <= p_1x <= 0.80, f"Expected CS 1X true_prob ~0.70, got {p_1x}"

    ctx_healthy = MatchContext(match_id=1, injuries_json=json.dumps({"home": [], "away": []}))
    ctx_injured = MatchContext(
        match_id=1,
        injuries_json=json.dumps({"home": [{"player": "Saka", "position": "Forward", "missed_games": 1}], "away": []}),
    )
    healthy_score, _ = _compute_injury_component("MS", "1", ctx_healthy, "tm_injuries")
    injured_score, _ = _compute_injury_component("MS", "1", ctx_injured, "tm_injuries")
    assert injured_score <= healthy_score, f"Injured ({injured_score}) must NOT exceed healthy ({healthy_score})"
    print(f"TEST_H2_CS_DEVIG_AND_INJURY: PASS (p_1x={p_1x:.4f}, healthy={healthy_score}, injured={injured_score})")


def test_h3_path_traversal_blocked():
    if not FRONTEND_DIST.exists():
        print("TEST_H3_PATH_TRAVERSAL_BLOCKED: SKIP (no dist)")
        return
    from fastapi import HTTPException
    from app.main import serve_spa

    client = TestClient(app)
    resp = client.get("/..%2F..%2Fbackend%2Fapp%2Fconfig.py")
    assert resp.status_code == 404, f"Expected 404 for encoded traversal, got {resp.status_code}"

    # Also verify direct handler call with raw '..' segments raises 404
    raised_404 = False
    try:
        serve_spa("../../backend/app/config.py")
    except HTTPException as exc:
        raised_404 = exc.status_code == 404
    assert raised_404, "Expected HTTPException(404) on direct serve_spa traversal"
    print("TEST_H3_PATH_TRAVERSAL_BLOCKED: PASS")


def test_h4_published_snapshot():
    Sess, eng, path = _make_session()
    try:
        db = Sess()
        now = datetime.now(timezone.utc)
        m = Match(
            iddaa_id="h4_m1",
            league="La Liga",
            country="Spain",
            is_major_league=True,
            home_team="Real Madrid",
            away_team="Sevilla",
            start_time_utc=now + timedelta(hours=4),
            status="SCHEDULED",
            data_status="form",
        )
        db.add(m)
        db.flush()
        ctx = MatchContext(
            match_id=m.id,
            home_form=json.dumps(["W", "W", "W", "W", "D"]),
            away_form=json.dumps(["L", "D", "L", "W", "L"]),
            home_goals_avg=2.4,
            away_goals_avg=0.9,
            home_conceded_avg=0.7,
            away_conceded_avg=1.8,
        )
        db.add(ctx)
        o1 = OddsCurrent(match_id=m.id, market_key="MS", selection_key="1", iddaa_odd=1.55, opening_odd=1.55, global_avg_odd=1.60)
        ox = OddsCurrent(match_id=m.id, market_key="MS", selection_key="X", iddaa_odd=4.20, opening_odd=4.20, global_avg_odd=4.30)
        o2 = OddsCurrent(match_id=m.id, market_key="MS", selection_key="2", iddaa_odd=5.80, opening_odd=5.80, global_avg_odd=6.00)
        db.add_all([o1, ox, o2])
        db.commit()

        analyze_upcoming_matches(db)
        top = db.query(Prediction).filter(Prediction.match_id == m.id, Prediction.is_top_pick.is_(True)).first()
        assert top is not None
        first_pub_odd = top.published_odd
        first_pub_conf = top.published_confidence
        assert first_pub_odd is not None and first_pub_conf is not None

        # Simulate odds movement on subsequent sync
        o1.iddaa_odd = 1.42
        db.commit()
        analyze_upcoming_matches(db)
        db.refresh(top)
        assert top.published_odd == first_pub_odd, f"published_odd overwritten: {first_pub_odd} -> {top.published_odd}"
        assert top.published_confidence == first_pub_conf, "published_confidence overwritten!"
        print(f"TEST_H4_PUBLISHED_SNAPSHOT: PASS (published_odd={first_pub_odd}, current_odd={top.iddaa_odd})")
    finally:
        db.close()
        eng.dispose()
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    test_h1_coupon_cascade_protect()
    test_h2_cs_devig_and_injury()
    test_h3_path_traversal_blocked()
    test_h4_published_snapshot()
    print("ALL_AUDIT_FIX_TESTS: PASS")
