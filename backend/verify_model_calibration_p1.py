"""
Isolated synthetic verification suite for Paket 2 (P1-1):
- TEST_CAL1_NO_FREE_KG_BIAS
- TEST_CAL2_NEGATIVE_EV_GUARD
- TEST_CAL3_NO_DOUBLE_HOME_ELO
- TEST_CAL4_PUBLISHED_IMMUTABLE

Runs against an in-memory SQLite DB with zero Odds API calls.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import Base
from app.models import Match, MatchContext, OddsCurrent, Prediction
from services.analyzer import (
    _compute_dixon_coles_probs,
    _compute_form_goals_component,
    _compute_value_component,
    _quarter_kelly_units,
    analyze_upcoming_matches,
)


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_cal1_no_free_kg_bias():
    # Neutral match with identical true_prob for MS:1 (with global_odd = iddaa_odd)
    # and KG:VAR (without global_odd). KG must NOT get free edge_pts > 4.5 or form_pts = 7.5.
    kg_odd = OddsCurrent(
        match_id=1,
        market_key="KG",
        selection_key="VAR",
        iddaa_odd=1.80,
        opening_odd=1.80,
        global_avg_odd=None,
    )
    kg_yok = OddsCurrent(
        match_id=1,
        market_key="KG",
        selection_key="YOK",
        iddaa_odd=1.80,
        opening_odd=1.80,
        global_avg_odd=None,
    )
    ms_1 = OddsCurrent(
        match_id=1,
        market_key="MS",
        selection_key="1",
        iddaa_odd=1.80,
        opening_odd=1.80,
        global_avg_odd=1.85,
    )
    odds_map = {("KG", "VAR"): kg_odd, ("KG", "YOK"): kg_yok, ("MS", "1"): ms_1}
    val_kg, tp_kg, _, _, _ = _compute_value_component(kg_odd, odds_map, calibrated_prob=0.50, true_prob=0.50)
    # Without global_odd: prob_pts = 0.50 * 23 = 11.5, neutral edge_pts = 4.5 -> 16.0
    assert val_kg == 16.0, f"Expected neutral KG value 16.0 (no free prob bonus), got {val_kg}"

    ctx_neutral = MatchContext(
        match_id=1,
        home_form=json.dumps(["W", "D", "L", "W", "L"]),  # 7 pts
        away_form=json.dumps(["W", "D", "L", "W", "L"]),  # 7 pts
        home_goals_avg=1.2,
        away_goals_avg=1.2,
        home_conceded_avg=1.1,
        away_conceded_avg=1.1,
    )
    form_kg = _compute_form_goals_component("KG", "VAR", ctx_neutral, blended_prob=0.50, conflict_flag=False)
    form_ms1 = _compute_form_goals_component("MS", "1", ctx_neutral, blended_prob=0.50, conflict_flag=False)
    assert form_kg == form_ms1, f"Expected neutral KG form_pts ({form_kg}) == MS:1 ({form_ms1})"
    print("TEST_CAL1_NO_FREE_KG_BIAS: PASS")


def test_cal2_negative_ev_guard():
    db = _make_db()
    now = datetime.now(timezone.utc)
    m = Match(
        iddaa_id="cal2_neg_ev",
        home_team="Home FC",
        away_team="Away FC",
        league="Premier League",
        country="England",
        is_major_league=True,
        start_time_utc=now + timedelta(hours=6),
        status="SCHEDULED",
    )
    db.add(m)
    db.flush()

    ctx = MatchContext(
        match_id=m.id,
        home_form=json.dumps(["W", "W", "D", "W", "W"]),
        away_form=json.dumps(["L", "D", "L", "L", "W"]),
        home_goals_avg=1.8,
        away_goals_avg=0.9,
        home_conceded_avg=0.8,
        away_conceded_avg=1.5,
        home_elo=1720.0,
        away_elo=1620.0,
    )
    db.add(ctx)

    # true_prob ~ 0.543 -> global_avg_odd = 1.56, iddaa_odd = 1.39 (EV = -24.5%)
    db.add_all([
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="1", iddaa_odd=1.39, opening_odd=1.42, global_avg_odd=1.56),
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="X", iddaa_odd=4.20, opening_odd=4.10, global_avg_odd=4.30),
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="2", iddaa_odd=7.20, opening_odd=7.00, global_avg_odd=7.80),
    ])
    db.commit()

    analyze_upcoming_matches(db)
    pred = db.query(Prediction).filter(Prediction.match_id == m.id, Prediction.selection_key == "1").one()
    assert pred.confidence_score < 65.0, f"Expected negative-EV confidence < 65, got {pred.confidence_score}"

    kelly_direct = _quarter_kelly_units(odd_val=1.39, blended_prob=0.543, confidence=77.8, global_odd=1.56)
    assert kelly_direct == 0.0, f"Expected _quarter_kelly_units == 0.0 for -24.5% EV, got {kelly_direct}"
    assert pred.kelly_units == 0.0, f"Expected stored kelly_units == 0.0, got {pred.kelly_units}"
    print("TEST_CAL2_NEGATIVE_EV_GUARD: PASS")


def test_cal3_no_double_home_elo():
    db = _make_db()
    now = datetime.now(timezone.utc)
    m = Match(
        iddaa_id="cal3_equal_elo",
        home_team="Equal Home",
        away_team="Equal Away",
        league="Bundesliga",
        country="Germany",
        is_major_league=True,
        start_time_utc=now + timedelta(hours=5),
        status="SCHEDULED",
    )
    db.add(m)
    db.flush()
    db.add(MatchContext(
        match_id=m.id,
        home_form=json.dumps(["W", "D", "L", "W", "D"]),
        away_form=json.dumps(["W", "D", "L", "W", "D"]),
        home_goals_avg=1.4,
        away_goals_avg=1.4,
        home_conceded_avg=1.2,
        away_conceded_avg=1.2,
        home_elo=1700.0,
        away_elo=1700.0,
    ))
    db.add_all([
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="1", iddaa_odd=2.50, opening_odd=2.50, global_avg_odd=2.55),
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="X", iddaa_odd=3.20, opening_odd=3.20, global_avg_odd=3.25),
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="2", iddaa_odd=2.50, opening_odd=2.50, global_avg_odd=2.55),
    ])
    db.commit()

    analyze_upcoming_matches(db)
    p1 = db.query(Prediction).filter(Prediction.match_id == m.id, Prediction.selection_key == "1").one()
    p2 = db.query(Prediction).filter(Prediction.match_id == m.id, Prediction.selection_key == "2").one()
    bd1 = json.loads(p1.score_breakdown_json)
    assert bd1["elo_diff"] == 0.0, f"Expected elo_diff == 0.0 without +55 shift, got {bd1['elo_diff']}"
    # With equal stats and equal Elo, MS:1 must not get an artificial +0.69 nudge over MS:2
    assert abs(p1.confidence_score - p2.confidence_score) < 0.5, (
        f"Expected no artificial home bias on equal Elo: MS1={p1.confidence_score} vs MS2={p2.confidence_score}"
    )

    # Cross-league European cup: weak-league team with inflated domestic stats vs elite club (Elo gap >= 250)
    euro_ctx = MatchContext(
        match_id=999,
        home_goals_avg=2.0,
        home_conceded_avg=0.9,
        away_goals_avg=2.6,  # inflated in weak domestic league
        away_conceded_avg=0.6,
        home_elo=1920.0,
        away_elo=1620.0,  # 300 Elo gap
    )
    dc_euro = _compute_dixon_coles_probs(euro_ctx, league="UEFA Champions League", country="Europe")
    dc_same = _compute_dixon_coles_probs(
        MatchContext(
            match_id=998,
            home_goals_avg=2.0,
            home_conceded_avg=0.9,
            away_goals_avg=2.6,
            away_conceded_avg=0.6,
            home_elo=1700.0,
            away_elo=1700.0,
        ),
        league="Domestic League",
        country="Test",
    )
    assert dc_euro["raw_xg_a"] < dc_same["raw_xg_a"], (
        f"Expected weak-league away xG to be damped in Europe ({dc_euro['raw_xg_a']} < {dc_same['raw_xg_a']})"
    )
    assert dc_euro["adj_xg_h"] > dc_euro["adj_xg_a"], (
        f"Expected elite home club xG > weak-league away xG ({dc_euro['adj_xg_h']} > {dc_euro['adj_xg_a']})"
    )
    print("TEST_CAL3_NO_DOUBLE_HOME_ELO: PASS")


def test_cal4_published_immutable():
    db = _make_db()
    now = datetime.now(timezone.utc)
    pub_time = now - timedelta(hours=2)
    m = Match(
        iddaa_id="cal4_pub_lock",
        home_team="Locked Home",
        away_team="Locked Away",
        league="Serie A",
        country="Italy",
        is_major_league=True,
        start_time_utc=now + timedelta(hours=4),
        status="SCHEDULED",
    )
    db.add(m)
    db.flush()

    db.add_all([
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="1", iddaa_odd=1.40, opening_odd=1.40, global_avg_odd=1.55),
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="X", iddaa_odd=4.20, opening_odd=4.20, global_avg_odd=4.30),
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="2", iddaa_odd=7.00, opening_odd=7.00, global_avg_odd=7.20),
    ])
    locked = Prediction(
        match_id=m.id,
        market_key="MS",
        selection_key="1",
        iddaa_odd=1.45,
        global_odd=1.55,
        confidence_score=74.0,
        is_top_pick=True,
        published_odd=1.45,
        published_confidence=74.0,
        published_prob=0.61,
        published_at=pub_time,
        status="PENDING",
    )
    db.add(locked)
    db.commit()

    analyze_upcoming_matches(db)
    db.refresh(locked)
    assert locked.is_top_pick is True
    assert locked.published_odd == 1.45
    assert locked.published_confidence == 74.0
    assert locked.published_prob == 0.61
    assert locked.published_at is not None
    print("TEST_CAL4_PUBLISHED_IMMUTABLE: PASS")


def test_cal5_iddaa_vig_band_not_trapped_at_595():
    db = _make_db()
    now = datetime.now(timezone.utc)
    m = Match(
        iddaa_id="cal5_vig_band",
        home_team="Strong Home",
        away_team="Weak Away",
        league="Almanya Bundesliga",
        country="Germany",
        is_major_league=True,
        start_time_utc=now + timedelta(hours=6),
        status="SCHEDULED",
    )
    db.add(m)
    db.flush()

    db.add(
        MatchContext(
            match_id=m.id,
            home_form=json.dumps(["W", "W", "D", "W", "W"]),
            away_form=json.dumps(["L", "D", "L", "L", "W"]),
            home_goals_avg=1.9,
            away_goals_avg=0.9,
            home_conceded_avg=0.8,
            away_conceded_avg=1.6,
            home_elo=1720.0,
            away_elo=1580.0,
        )
    )
    # Normal Iddaa vig (-7.38% vs sharp global odd 1.49): should reach 60+ honestly without faking edge/kelly
    db.add_all([
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="1", iddaa_odd=1.38, opening_odd=1.40, global_avg_odd=1.49),
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="X", iddaa_odd=4.30, opening_odd=4.30, global_avg_odd=4.60),
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="2", iddaa_odd=6.20, opening_odd=6.20, global_avg_odd=6.80),
    ])
    db.commit()

    analyze_upcoming_matches(db)
    pred = db.query(Prediction).filter(Prediction.match_id == m.id, Prediction.selection_key == "1").one()
    assert pred.confidence_score >= 60.0, f"Expected Iddaa vig-band strong pick >= 60, got {pred.confidence_score}"
    assert pred.value_edge_pct is not None and pred.value_edge_pct < 0.0, f"Expected honest negative edge, got {pred.value_edge_pct}"
    assert pred.kelly_units == 0.0, f"Expected 0.0 Kelly on negative EV, got {pred.kelly_units}"
    print("TEST_CAL5_IDDAA_VIG_BAND_NOT_TRAPPED: PASS")


if __name__ == "__main__":
    test_cal1_no_free_kg_bias()
    test_cal2_negative_ev_guard()
    test_cal3_no_double_home_elo()
    test_cal4_published_immutable()
    test_cal5_iddaa_vig_band_not_trapped_at_595()
    print("ALL PAKET 2 CALIBRATION TESTS PASSED")
