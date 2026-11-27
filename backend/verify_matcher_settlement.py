"""Comprehensive unit tests for shared matcher, multi-market settlement, 48h VOID ban, AET 90m rule, and idempotent coupons."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Coupon, CouponItem, Match, Prediction
from services.settlement import (
    cleanup_multi_market_settled_predictions,
    evaluate_prediction_outcome,
    settle_coupons,
    settle_finished_matches,
)
from utils.team_matcher import (
    build_team_search_queries,
    find_best_external_match,
    match_score,
    normalize_team_name,
    strict_team_match_score,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_shared_matcher_abbreviations_and_guards():
    assert normalize_team_name("Samsunspor A.Ş.") == "samsunspor"
    assert normalize_team_name("A. de Viseu") == "academico de viseu"
    assert normalize_team_name("Aus Lustenau") == "austria lustenau"
    assert normalize_team_name("O.H. Leuven") == "oud heverlee leuven"

    assert strict_team_match_score("Aus Lustenau", "Austria Lustenau") >= 90.0
    assert strict_team_match_score("A. de Viseu", "Académico de Viseu") >= 90.0
    assert strict_team_match_score("O.H. Leuven", "OH Leuven") >= 90.0
    assert strict_team_match_score("Samsunspor A.Ş.", "Samsunspor") == 100.0

    # Youth / reserve / women protection
    assert strict_team_match_score("Werder Bremen", "Werder Bremen U21") == 0.0
    assert strict_team_match_score("Werder Bremen", "Werder Bremen II") == 0.0
    assert strict_team_match_score("Galatasaray", "Galatasaray (W)") == 0.0
    assert match_score("Alverca", "Alverca B") == 0.0
    assert match_score("Rapid Wien", "Rapid Wien Women") == 0.0

    # Search queries include expanded & distinctive tokens
    q_lust = build_team_search_queries("Aus Lustenau")
    assert "austria lustenau" in [q.lower() for q in q_lust]
    assert "lustenau" in [q.lower() for q in q_lust]

    # Paired opponent + kickoff match across UTC/TR day boundary
    kickoff = datetime(2026, 11, 27, 21, 30, tzinfo=timezone.utc)
    cands = [
        {
            "espn_id": "999",
            "home_team": "Oud-Heverlee Leuven",
            "away_team": "KV Kortrijk",
            "start_time_utc": datetime(2026, 11, 27, 21, 45, tzinfo=timezone.utc),
        },
        {
            "espn_id": "998",
            "home_team": "OH Leuven U21",
            "away_team": "KV Kortrijk U21",
            "start_time_utc": datetime(2026, 11, 27, 21, 30, tzinfo=timezone.utc),
        },
    ]
    matched = find_best_external_match("O.H. Leuven", "Kortrijk", kickoff, cands)
    assert matched is not None and matched["espn_id"] == "999"


def test_turkish_locale_market_evaluation():
    assert evaluate_prediction_outcome("kg", "Var", 2, 3) == "WON"
    assert evaluate_prediction_outcome("KG", "yok", 2, 3) == "LOST"
    assert evaluate_prediction_outcome("au_25", "Üst", 2, 3) == "WON"
    assert evaluate_prediction_outcome("AU_25", "Alt", 2, 3) == "LOST"
    assert evaluate_prediction_outcome("ms", "2", 2, 3) == "WON"
    assert evaluate_prediction_outcome("CS", "1 ve 0", 1, 1) == "WON"


def test_multi_market_grazer_ak_and_void_recovery():
    db = _make_session()
    now = datetime.now(timezone.utc)
    m = Match(
        iddaa_id="546",
        league="Avusturya Bundesliga",
        home_team="Grazer AK",
        away_team="Altach",
        start_time_utc=now - timedelta(hours=3),
        status="SCHEDULED",
    )
    db.add(m)
    db.flush()

    p_ms2 = Prediction(
        match_id=m.id,
        market_key="MS",
        selection_key="2",
        iddaa_odd=2.03,
        confidence_score=64.6,
        is_top_pick=True,
        published_odd=2.03,
        published_confidence=64.6,
        published_at=now - timedelta(hours=5),
        status="PENDING",
    )
    p_kg = Prediction(
        match_id=m.id,
        market_key="KG",
        selection_key="VAR",
        iddaa_odd=1.75,
        confidence_score=61.0,
        is_top_pick=False,
        status="PENDING",
    )
    p_ms1 = Prediction(
        match_id=m.id,
        market_key="MS",
        selection_key="1",
        iddaa_odd=3.10,
        confidence_score=40.0,
        is_top_pick=False,
        status="PENDING",
    )
    db.add_all([p_ms2, p_kg, p_ms1])
    db.commit()

    espn = [
        {
            "espn_id": "401881739",
            "home_team": "Grazer AK",
            "away_team": "SC Rheindorf Altach",
            "start_time_utc": m.start_time_utc,
            "completed": True,
            "state": "post",
            "home_score": 2,
            "away_score": 3,
            "is_void_status": False,
            "is_aet_or_pen": False,
            "reg_home_score": 2,
            "reg_away_score": 3,
        }
    ]
    settled = settle_finished_matches(db, espn_events=espn, fotmob_events=[])
    assert settled == 1
    db.refresh(m)
    db.refresh(p_ms2)
    db.refresh(p_kg)
    db.refresh(p_ms1)

    assert m.status == "FINISHED"
    assert (m.home_score, m.away_score) == (2, 3)
    assert p_ms2.status == "WON" and p_ms2.is_top_pick is True
    assert p_kg.status == "WON" and p_kg.is_top_pick is False
    assert p_ms1.status == "LOST" and p_ms1.is_top_pick is False
    # published_* untouched
    assert p_ms2.published_odd == 2.03

    # Legacy wrongly-VOIDed secondary prediction recovery
    p_kg.status = "VOID"
    db.commit()
    cleanup_multi_market_settled_predictions(db)
    db.refresh(p_kg)
    assert p_kg.status == "WON"


def test_premature_void_ban_and_postponed_and_aet():
    db = _make_session()
    now = datetime.now(timezone.utc)

    # 1) 36h old with no score -> must remain PENDING (no premature VOID before 48h)
    m_36h = Match(
        iddaa_id="m36",
        league="Test",
        home_team="Team A",
        away_team="Team B",
        start_time_utc=now - timedelta(hours=36),
        status="SCHEDULED",
    )
    # 2) 49h old with no score -> must become VOID with score_unavailable_48h
    m_49h = Match(
        iddaa_id="m49",
        league="Test",
        home_team="Team C",
        away_team="Team D",
        start_time_utc=now - timedelta(hours=49),
        status="SCHEDULED",
    )
    # 3) 3h old explicitly POSTPONED -> immediate VOID
    m_post = Match(
        iddaa_id="mpost",
        league="Test",
        home_team="Team E",
        away_team="Team F",
        start_time_utc=now - timedelta(hours=3),
        status="SCHEDULED",
    )
    # 4) AET match: 90m 1-1, final AET 2-1 -> must settle with 1-1 (MS X WON, MS 1 LOST)
    m_aet = Match(
        iddaa_id="maet",
        league="Cup",
        home_team="Team G",
        away_team="Team H",
        start_time_utc=now - timedelta(hours=4),
        status="SCHEDULED",
    )
    db.add_all([m_36h, m_49h, m_post, m_aet])
    db.flush()

    p_36 = Prediction(match_id=m_36h.id, market_key="MS", selection_key="1", iddaa_odd=2.0, confidence_score=65.0, is_top_pick=True)
    p_49 = Prediction(match_id=m_49h.id, market_key="MS", selection_key="1", iddaa_odd=2.0, confidence_score=65.0, is_top_pick=True)
    p_post = Prediction(match_id=m_post.id, market_key="MS", selection_key="1", iddaa_odd=2.0, confidence_score=65.0, is_top_pick=True)
    p_aet_x = Prediction(match_id=m_aet.id, market_key="MS", selection_key="X", iddaa_odd=3.2, confidence_score=65.0, is_top_pick=True)
    db.add_all([p_36, p_49, p_post, p_aet_x])
    db.commit()

    espn = [
        {
            "espn_id": "epost",
            "home_team": "Team E",
            "away_team": "Team F",
            "start_time_utc": m_post.start_time_utc,
            "completed": False,
            "state": "post",
            "is_void_status": True,
            "status_name": "STATUS_POSTPONED",
        },
        {
            "espn_id": "eaet",
            "home_team": "Team G",
            "away_team": "Team H",
            "start_time_utc": m_aet.start_time_utc,
            "completed": True,
            "state": "post",
            "home_score": 2,
            "away_score": 1,
            "is_void_status": False,
            "is_aet_or_pen": True,
            "reg_home_score": 1,
            "reg_away_score": 1,
        },
    ]
    settle_finished_matches(db, espn_events=espn, fotmob_events=[])
    for obj in (m_36h, m_49h, m_post, m_aet, p_36, p_49, p_post, p_aet_x):
        db.refresh(obj)

    assert m_36h.status == "SCHEDULED" and p_36.status == "PENDING"
    assert m_49h.status == "VOID" and m_49h.void_reason == "score_unavailable_48h" and p_49.status == "VOID"
    assert m_post.status == "VOID" and "POSTPONED" in (m_post.void_reason or "") and p_post.status == "VOID"
    assert m_aet.status == "FINISHED" and (m_aet.home_score, m_aet.away_score) == (1, 1) and p_aet_x.status == "WON"


def test_idempotent_coupon_settlement():
    db = _make_session()
    now = datetime.now(timezone.utc)
    m1 = Match(iddaa_id="c1", league="L1", home_team="Werder Bremen", away_team="Monchengladbach", start_time_utc=now - timedelta(hours=3), status="LIVE", home_score=1, away_score=3)
    m2 = Match(iddaa_id="c2", league="L2", home_team="O.H. Leuven", away_team="Kortrijk", start_time_utc=now - timedelta(hours=3), status="SCHEDULED")
    db.add_all([m1, m2])
    db.flush()

    p1 = Prediction(match_id=m1.id, market_key="AU_25", selection_key="UST", iddaa_odd=1.38, confidence_score=71.3, is_top_pick=True)
    p2 = Prediction(match_id=m2.id, market_key="MS", selection_key="1", iddaa_odd=1.76, confidence_score=70.2, is_top_pick=True)
    db.add_all([p1, p2])
    db.flush()

    cp = Coupon(coupon_date="2026-11-27", tier="BANKO", total_odd=2.43, avg_confidence=70.8, status="PENDING", is_locked=True)
    db.add(cp)
    db.flush()
    db.add_all([
        CouponItem(coupon_id=cp.id, prediction_id=p1.id, odd_at_pick=1.38),
        CouponItem(coupon_id=cp.id, prediction_id=p2.id, odd_at_pick=1.76),
    ])
    db.commit()

    espn = [
        {"espn_id": "401884720", "home_team": "Werder Bremen", "away_team": "Borussia Mönchengladbach", "start_time_utc": m1.start_time_utc, "completed": True, "state": "post", "home_score": 3, "away_score": 3, "is_void_status": False, "is_aet_or_pen": False, "reg_home_score": 3, "reg_away_score": 3},
        {"espn_id": "401878930", "home_team": "OH Leuven", "away_team": "KV Kortrijk", "start_time_utc": m2.start_time_utc, "completed": True, "state": "post", "home_score": 2, "away_score": 2, "is_void_status": False, "is_aet_or_pen": False, "reg_home_score": 2, "reg_away_score": 2},
    ]
    settle_finished_matches(db, espn_events=espn, fotmob_events=[])
    db.refresh(cp)
    assert cp.status == "LOST"
    # Idempotent re-run
    assert settle_coupons(db) == 0
    assert cp.status == "LOST"


if __name__ == "__main__":
    test_shared_matcher_abbreviations_and_guards()
    test_turkish_locale_market_evaluation()
    test_multi_market_grazer_ak_and_void_recovery()
    test_premature_void_ban_and_postponed_and_aet()
    test_idempotent_coupon_settlement()
    print("ALL VERIFY_MATCHER_SETTLEMENT TESTS PASSED")
