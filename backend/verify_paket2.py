import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Match, MatchContext, Prediction
from app.routers.api import get_match_detail, get_performance
from services.ai_summary import _build_rule_based_summary, form_to_turkish, sanitize_turkish_summary
from services.sync import refresh_all_active_forms


def _make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_prediction_id_lock_and_other_markets():
    db = _make_session()
    now = datetime.now(timezone.utc)
    m = Match(
        iddaa_id="t-1",
        league="Serie A",
        country="Italy",
        is_major_league=True,
        home_team="Torino",
        away_team="Lazio",
        start_time_utc=now + timedelta(hours=3),
        status="SCHEDULED",
    )
    db.add(m)
    db.flush()

    p_top = Prediction(
        match_id=m.id,
        market_key="MS",
        selection_key="2",
        iddaa_odd=1.92,
        global_odd=1.88,
        confidence_score=76.0,
        is_top_pick=True,
        status="PENDING",
        score_breakdown_json=json.dumps({"value": 25, "odds_move": 18, "form_goals": 20, "injury": 13, "blended_prob_pct": 54.0}),
    )
    p_alt = Prediction(
        match_id=m.id,
        market_key="AU_25",
        selection_key="UST",
        iddaa_odd=1.72,
        global_odd=1.75,
        confidence_score=80.0,  # even if alt has higher raw score, top_pick or requested prediction_id must win
        is_top_pick=False,
        status="PENDING",
        score_breakdown_json=json.dumps({"value": 22, "odds_move": 20, "form_goals": 23, "injury": 15, "blended_prob_pct": 58.0}),
    )
    db.add_all([p_top, p_alt])
    db.commit()

    # Default (no prediction_id): is_top_pick=True wins
    d_default = get_match_detail(m.id, db=db)
    assert d_default["top_prediction"]["prediction_id"] == p_top.id
    assert d_default["top_prediction"]["market_key"] == "MS"
    assert d_default["top_prediction"]["selection_key"] == "2"
    assert len(d_default["other_predictions"]) == 1
    assert d_default["other_predictions"][0]["prediction_id"] == p_alt.id

    # Explicit prediction_id=p_alt.id locks onto p_alt
    d_alt = get_match_detail(m.id, prediction_id=p_alt.id, db=db)
    assert d_alt["top_prediction"]["prediction_id"] == p_alt.id
    assert d_alt["top_prediction"]["market_key"] == "AU_25"
    assert d_alt["other_predictions"][0]["prediction_id"] == p_top.id


def test_turkish_summary_and_form_sanitization():
    assert form_to_turkish(["L", "D", "L", "L", "W"]) == "MBMMG"
    assert form_to_turkish(["W", "L", "D", "W", "W"]) == "GMBGG"

    inj_empty = json.dumps({"home": [], "away": []})
    raw = (
        "Takım son 5 maçta LDLLW formuna rağmen eksiklere rağmen ve önemli eksiklere rağmen kazandı."
    )
    cleaned = sanitize_turkish_summary(raw, inj_empty)
    assert "MBMMG" in cleaned
    assert "LDLLW" not in cleaned
    assert cleaned.lower().count("rağmen") <= 1
    assert "önemli eksiklere rağmen" not in cleaned.lower()

    db = _make_session()
    m = Match(
        iddaa_id="t-2",
        league="Bundesliga",
        country="Germany",
        is_major_league=True,
        home_team="Werder Bremen",
        away_team="Monchengladbach",
        start_time_utc=datetime.now(timezone.utc) + timedelta(hours=2),
        status="SCHEDULED",
    )
    db.add(m)
    db.flush()
    ctx = MatchContext(
        match_id=m.id,
        home_form=json.dumps(["W", "D", "L", "W", "L"]),
        away_form=json.dumps(["L", "L", "D", "W", "W"]),
        home_goals_avg=2.67,
        away_goals_avg=1.33,
        injuries_json=inj_empty,
    )
    db.add(ctx)
    db.commit()
    summary = _build_rule_based_summary(m, None, ctx, db=db)
    assert "GBMGM" in summary
    assert "MMBGG" in summary
    assert "sezon genelinde" in summary


def test_performance_rounding_band_and_tr_kickoff_date():
    db = _make_session()
    # Kickoff: 2026-11-27 20:30 UTC == 23:30 Europe/Istanbul (27.11)
    # Settled: 2026-11-28 00:15 UTC == 03:15 Europe/Istanbul (28.11)
    kickoff_utc = datetime(2026, 11, 27, 20, 30, tzinfo=timezone.utc)
    settled_utc = datetime(2026, 11, 28, 0, 15, tzinfo=timezone.utc)

    m = Match(
        iddaa_id="t-perf-1",
        league="Liga MX",
        country="Mexico",
        is_major_league=True,
        home_team="Club Leon",
        away_team="Cruz Azul",
        start_time_utc=kickoff_utc,
        status="FINISHED",
        home_score=0,
        away_score=4,
    )
    db.add(m)
    db.flush()
    p = Prediction(
        match_id=m.id,
        market_key="MS",
        selection_key="2",
        iddaa_odd=1.80,
        published_odd=1.80,
        confidence_score=69.7,
        published_confidence=69.7,  # rounds to 70 -> MUST be in 70-79 band
        published_at=kickoff_utc - timedelta(hours=2),
        settled_at=settled_utc,
        is_top_pick=True,
        status="WON",
        score_breakdown_json=json.dumps({"blended_prob_pct": 58.0}),
    )
    db.add(p)
    db.commit()

    perf = get_performance(db=db)
    bands = {b["band"]: b for b in perf["calibration_bands"]}
    assert bands["70-79"]["total"] == 1
    assert bands["60-69"]["total"] == 0
    assert perf["trend"] == [{"date": "27.11", "win_rate": 100.0, "total": 1}]
    assert perf["archive"][0]["start_date_tr"] == "27.11"


def test_visible_match_form_priority(monkeypatch=None):
    db = _make_session()
    now = datetime.now(timezone.utc)
    # Far-away match (5 days out) vs visible 72h 60+ match
    m_far = Match(
        iddaa_id="f-1",
        league="Minor League",
        country="X",
        is_major_league=False,
        home_team="Far FC",
        away_team="Away Far",
        start_time_utc=now + timedelta(days=5),
        status="SCHEDULED",
    )
    m_vis = Match(
        iddaa_id="v-1",
        league="Portugal 2",
        country="Portugal",
        is_major_league=False,
        home_team="A. de Viseu",
        away_team="Feirense",
        start_time_utc=now + timedelta(hours=10),
        status="SCHEDULED",
    )
    db.add_all([m_far, m_vis])
    db.flush()
    db.add(
        Prediction(
            match_id=m_vis.id,
            market_key="MS",
            selection_key="1",
            iddaa_odd=1.75,
            confidence_score=65.0,
            is_top_pick=True,
            status="PENDING",
            score_breakdown_json="{}",
        )
    )
    db.commit()

    called_order: list[str] = []
    import services.sync as sync_mod

    orig_fetch = sync_mod.fetch_global_team_recent_matches

    def _fake_fetch(team_name, **kwargs):
        called_order.append(team_name)
        return {
            "form": ["W", "D", "W", "L", "W"],
            "recent_matches": [],
            "goals_avg": 1.6,
            "conceded_avg": 1.0,
            "rest_days": 4,
            "source": "mock",
        }

    sync_mod.fetch_global_team_recent_matches = _fake_fetch
    try:
        refresh_all_active_forms(db, force_refresh=True, only_missing=True)
    finally:
        sync_mod.fetch_global_team_recent_matches = orig_fetch

    assert called_order[:2] == ["A. de Viseu", "Feirense"] or set(called_order[:2]) == {"A. de Viseu", "Feirense"}


if __name__ == "__main__":
    test_prediction_id_lock_and_other_markets()
    test_turkish_summary_and_form_sanitization()
    test_performance_rounding_band_and_tr_kickoff_date()
    test_visible_match_form_priority()
    print("ALL VERIFY_PAKET2 TESTS PASSED")
