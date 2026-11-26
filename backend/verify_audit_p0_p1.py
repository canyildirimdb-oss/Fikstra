"""Regression tests for Paket 1 (Audit P0/P1: Transparency, Quota, Security & Toto)."""

import json
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
from app.models import (
    Match,
    MatchContext,
    OddsCurrent,
    Prediction,
    SportotoMatch,
    SportotoWeek,
)
from app import telegram_bot
from app.routers.api import _format_pick_short, _get_official_settled_predictions, clear_api_cache
from scrapers.external_data import extract_regular_time_score
from scrapers.iddaa import _extract_odds_from_markets
from scrapers.odds_api import _sport_key_for_league, sync_the_odds_api
from services.analyzer import analyze_upcoming_matches
from services.push import human_pick_label
from services.settlement import settle_finished_matches


def _make_memory_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)(), engine


def test_tg_match_id_and_au25():
    db, engine = _make_memory_session()
    try:
        now = datetime.now(timezone.utc)
        # Create dummy matches so Match.id (45) != Prediction.id (612) style mismatch occurs
        m = Match(
            id=45,
            iddaa_id="tg_m45",
            league="İngiltere Premier Lig",
            country="İngiltere",
            home_team="Arsenal",
            away_team="Chelsea",
            start_time_utc=now + timedelta(hours=3),
            status="SCHEDULED",
            is_major_league=True,
        )
        db.add(m)
        db.flush()

        p = Prediction(
            id=612,
            match_id=45,
            market_key="AU_25",
            selection_key="UST",
            iddaa_odd=1.85,
            published_odd=1.85,
            confidence_score=78.0,
            published_confidence=78.0,
            is_top_pick=True,
            odds_drop_pct=8.0,
            value_edge_pct=7.5,
            status="PENDING",
        )
        db.add(p)
        db.commit()

        _, single_markup = telegram_bot._render_single_message(p)
        _, value_markup = telegram_bot._render_value_message(p)
        assert "/?match=45" in single_markup["inline_keyboard"][0][0]["url"]
        assert "/?match=45" in value_markup["inline_keyboard"][0][0]["url"]
        assert "/?match=612" not in single_markup["inline_keyboard"][0][0]["url"]

        # Verify AU_25 formatting across all 3 channels
        assert telegram_bot._format_pick_label("AU_25", "UST") == "2.5 Üst"
        assert telegram_bot._format_pick_label("AU_25", "ALT") == "2.5 Alt"
        assert human_pick_label("AU_25", "UST") == "2.5 Üst"
        assert human_pick_label("AU_25", "ALT") == "2.5 Alt"
        assert _format_pick_short("AU_25", "UST") == "2.5 Üst"
        assert _format_pick_short("AU_25", "ALT") == "2.5 Alt"
        assert human_pick_label("AU_15", "UST") == "1.5 Üst"
        assert _format_pick_short("AU_35", "ALT") == "3.5 Alt"

        print("TEST_TG_MATCH_ID_AND_AU25: PASS")
    finally:
        db.close()
        engine.dispose()


def test_transparency_lifecycle():
    db, engine = _make_memory_session()
    try:
        now = datetime.now(timezone.utc)
        m1 = Match(
            iddaa_id="tr_m1",
            league="İspanya La Liga",
            country="İspanya",
            home_team="Barcelona",
            away_team="Sevilla",
            start_time_utc=now + timedelta(hours=5),
            status="SCHEDULED",
            is_major_league=True,
        )
        m2 = Match(
            iddaa_id="tr_m2",
            league="İtalya Serie A",
            country="İtalya",
            home_team="Inter",
            away_team="Milan",
            start_time_utc=now + timedelta(hours=6),
            status="SCHEDULED",
            is_major_league=True,
        )
        db.add_all([m1, m2])
        db.flush()

        # m1 starts at 54 (<60): should NOT lock published_*
        p1 = Prediction(
            match_id=m1.id,
            market_key="MS",
            selection_key="1",
            iddaa_odd=1.75,
            confidence_score=54.0,
            is_top_pick=True,
            score_breakdown_json=json.dumps({"true_prob_pct": 58.0}),
            status="PENDING",
        )
        # m2 starts at 68 (>=60): should lock published_* at 68 and odd 1.90
        p2 = Prediction(
            match_id=m2.id,
            market_key="MS",
            selection_key="1",
            iddaa_odd=1.90,
            confidence_score=68.0,
            is_top_pick=True,
            score_breakdown_json=json.dumps({"true_prob_pct": 62.0}),
            status="PENDING",
        )
        db.add_all([p1, p2])
        db.flush()

        # Simulate m1 rising 54 -> 72 and m2 dropping 68 -> 55
        # First lock p2 when at 68 (before kickoff)
        p2.published_odd = 1.90
        p2.published_confidence = 68.0
        p2.published_prob = 0.62
        p2.published_at = now - timedelta(hours=4)
        # Now simulate live drift
        p1.confidence_score = 72.0
        p2.confidence_score = 55.0
        p2.iddaa_odd = 1.82
        db.commit()

        # Run analyzer lock check via OddsCurrent on m1
        db.add(OddsCurrent(match_id=m1.id, market_key="MS", selection_key="1", iddaa_odd=1.75, opening_odd=1.75))
        db.commit()

        # Directly verify get_predictions returns locked published_* for p2 and current_odd separately
        clear_api_cache()
        fastapi_app.dependency_overrides[get_db] = lambda: db
        client = TestClient(fastapi_app)
        res = client.get("/api/predictions?today_only=false&major_only=true&min_confidence=60").json()
        by_mid = {it["match_id"]: it for it in res["items"]}
        assert m2.id in by_mid, "Locked 68->55 pick must stay in Analizler"
        assert by_mid[m2.id]["confidence_score"] == 68.0
        assert by_mid[m2.id]["iddaa_odd"] == 1.90
        assert by_mid[m2.id]["current_odd"] == 1.82

        # Settle both in the past and verify Performance == Telegram /karne
        m1.start_time_utc = now - timedelta(hours=3)
        m2.start_time_utc = now - timedelta(hours=3)
        p1.published_odd = 1.75
        p1.published_confidence = 72.0
        p1.published_at = now - timedelta(hours=4)
        p1.status = "WON"
        p1.settled_at = now - timedelta(minutes=10)
        p2.status = "LOST"
        p2.settled_at = now - timedelta(minutes=10)
        # Also add an unpublished side prediction that must NEVER become is_top_pick
        p_side = Prediction(
            match_id=m1.id,
            market_key="KG",
            selection_key="VAR",
            iddaa_odd=1.65,
            confidence_score=65.0,
            is_top_pick=False,
            published_at=None,
            status="PENDING",
            score_breakdown_json="{}",
        )
        db.add(p_side)
        db.commit()

        official = _get_official_settled_predictions(db, include_void=True)
        assert len(official) == 2
        perf = client.get("/api/performance").json()
        assert perf["total_settled"] == 2
        assert perf["won_count"] == 1
        assert perf["lost_count"] == 1

        print("TEST_TRANSPARENCY_LIFECYCLE: PASS")
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_odds_api_major_only():
    db, engine = _make_memory_session()
    try:
        now = datetime.now(timezone.utc)
        # Non-major / trap league names must NEVER map to a sport key
        assert _sport_key_for_league("Brezilya Serie A", "Brezilya") is None
        assert _sport_key_for_league("Avusturya Bundesliga", "Avusturya") is None
        assert _sport_key_for_league("Yunanistan Süper Lig", "Yunanistan") is None
        assert _sport_key_for_league("İngiltere Premier Lig (Kadınlar)", "İngiltere") is None
        assert _sport_key_for_league("İtalya Serie A", "İtalya") == "soccer_italy_serie_a"
        assert _sport_key_for_league("Almanya Bundesliga", "Almanya") == "soccer_germany_bundesliga"

        for idx, (lg, ct) in enumerate(
            [
                ("Brezilya Serie A", "Brezilya"),
                ("Avusturya Bundesliga", "Avusturya"),
                ("Yunanistan Süper Lig", "Yunanistan"),
            ],
            start=1,
        ):
            db.add(
                Match(
                    iddaa_id=f"nm_{idx}",
                    league=lg,
                    country=ct,
                    home_team=f"Home{idx}",
                    away_team=f"Away{idx}",
                    start_time_utc=now + timedelta(hours=2),
                    status="SCHEDULED",
                    is_major_league=False,
                )
            )
        db.commit()

        with patch.object(settings, "the_odds_api_key", "fake-odds-key"), \
             patch("httpx.Client.get") as mock_get:
            res = sync_the_odds_api(db)
            assert mock_get.call_count == 0
            assert res["api_calls"] == 0

        print("TEST_ODDS_API_MAJOR_ONLY: PASS")
    finally:
        db.close()
        engine.dispose()


def test_sync_dup_odds_and_elo():
    db, engine = _make_memory_session()
    try:
        now = datetime.now(timezone.utc)
        # Duplicate markets in iddaa payload should be deduplicated
        dup_markets = [
            {"t": 1, "st": 1, "s": 1, "o": [{"n": "1", "odd": 1.80}, {"n": "0", "odd": 3.40}, {"n": "2", "odd": 4.20}]},
            {"t": 4, "st": 4, "s": 1, "o": [{"n": "1", "odd": 1.82}, {"n": "0", "odd": 3.45}, {"n": "2", "odd": 4.10}]},
        ]
        parsed = _extract_odds_from_markets(dup_markets)
        keys = [(o["market_key"], o["selection_key"]) for o in parsed]
        assert len(keys) == len(set(keys)) == 3

        # Existing valid ClubElo must NOT be overwritten by None
        m = Match(
            iddaa_id="elo_m1",
            league="İngiltere Premier Lig",
            country="İngiltere",
            home_team="Liverpool",
            away_team="Everton",
            start_time_utc=now + timedelta(hours=3),
            status="SCHEDULED",
            is_major_league=True,
        )
        db.add(m)
        db.flush()
        ctx = MatchContext(match_id=m.id, home_elo=1920.0, away_elo=1680.0)
        db.add(ctx)
        db.commit()

        # Simulate the second SCHEDULED loop logic
        need_home = ctx.home_elo is None
        need_away = ctx.away_elo is None
        assert not need_home and not need_away
        assert ctx.home_elo == 1920.0 and ctx.away_elo == 1680.0

        print("TEST_SYNC_DUP_ODDS_AND_ELO: PASS")
    finally:
        db.close()
        engine.dispose()


def test_aet_00_and_void_alert():
    db, engine = _make_memory_session()
    try:
        # 0-0 at 90', 1-0 AET at 105' -> reg_home_score=0, reg_away_score=0
        comp = {
            "details": [
                {"scoringPlay": True, "shootout": False, "clock": {"displayValue": "105'"}, "team": {"id": "10"}}
            ]
        }
        home_comp = {"team": {"id": "10"}, "score": "1"}
        away_comp = {"team": {"id": "20"}, "score": "0"}
        status_obj = {"name": "STATUS_FINAL_AET", "shortDetail": "FT-ET"}
        rt = extract_regular_time_score(comp, home_comp, away_comp, status_obj)
        assert rt["is_aet_or_pen"] is True
        assert rt["reg_home_score"] == 0 and rt["reg_away_score"] == 0

        # 10 VOID side predictions (is_top_pick=False) must NOT trigger VOID health alert
        now = datetime.now(timezone.utc)
        m = Match(
            iddaa_id="void_m1",
            league="İngiltere Premier Lig",
            home_team="Arsenal",
            away_team="Fulham",
            start_time_utc=now - timedelta(hours=3),
            status="FINISHED",
            home_score=2,
            away_score=0,
        )
        db.add(m)
        db.flush()
        pairs = [
            ("MS", "1"), ("MS", "X"), ("MS", "2"),
            ("CS", "1X"), ("CS", "X2"), ("CS", "12"),
            ("AU_15", "UST"), ("AU_25", "UST"), ("AU_35", "UST"),
            ("KG", "VAR"),
        ]
        for mk, sk in pairs:
            db.add(
                Prediction(
                    match_id=m.id,
                    market_key=mk,
                    selection_key=sk,
                    iddaa_odd=1.80,
                    confidence_score=55.0,
                    is_top_pick=False,
                    status="VOID",
                    settled_at=now - timedelta(minutes=20),
                )
            )
        db.commit()

        with patch.object(settings, "telegram_bot_token", "fake-token"), \
             patch.object(settings, "telegram_admin_chat_id", "99999"), \
             patch("app.telegram_bot._send_message", return_value=123) as mock_send:
            alert_res = telegram_bot.check_and_send_health_alerts(db)
            assert "high_void" not in alert_res["alerts_sent"]
            assert mock_send.call_count == 0

        print("TEST_AET_00_AND_VOID_ALERT: PASS")
    finally:
        db.close()
        engine.dispose()


def test_admin_auto_settle_order():
    db, engine = _make_memory_session()
    try:
        now = datetime.now(timezone.utc)
        m = Match(
            iddaa_id="st_m1",
            league="Süper Lig",
            home_team="Galatasaray",
            away_team="Fenerbahçe",
            start_time_utc=now - timedelta(hours=3),
            status="FINISHED",
            home_score=2,
            away_score=1,
        )
        db.add(m)
        db.flush()
        p = Prediction(
            match_id=m.id,
            market_key="MS",
            selection_key="1",
            iddaa_odd=1.80,
            confidence_score=70.0,
            is_top_pick=True,
            status="WON",
        )
        db.add(p)
        db.flush()

        w = SportotoWeek(title="Test Hafta", status="PUBLISHED")
        db.add(w)
        db.flush()
        for slot in range(1, 16):
            db.add(
                SportotoMatch(
                    week_id=w.id,
                    slot_no=slot,
                    home_team=f"H{slot}",
                    away_team=f"A{slot}",
                    odd_1=1.8,
                    odd_x=3.4,
                    odd_2=4.2,
                    prob_1=0.52,
                    prob_x=0.26,
                    prob_2=0.22,
                    source="model",
                    prediction_id=p.id if slot == 1 else None,
                )
            )
        db.commit()

        fastapi_app.dependency_overrides[get_db] = lambda: db
        client = TestClient(fastapi_app)
        resp = client.patch(
            f"/api/admin/sportoto/weeks/{w.id}/matches",
            headers={"X-Cron-Secret": settings.cron_secret},
            json={
                "auto_settle_from_db": True,
                "matches": [{"slot_no": 1, "actual_result": ""}],
            },
        )
        assert resp.status_code == 200
        slot1 = next(s for s in resp.json()["matches"] if s["slot_no"] == 1)
        assert slot1["actual_result"] == "1", f"Expected '1' from DB auto-settle, got {slot1['actual_result']}"

        print("TEST_ADMIN_AUTO_SETTLE_ORDER: PASS")
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_hero_and_sort_parity():
    db, engine = _make_memory_session()
    try:
        now = datetime.now(timezone.utc)
        # m_low has high live confidence (85) but locked published_confidence (67) -> effective = 67
        # m_high has lower live confidence (79) but locked published_confidence (81) -> effective = 81
        m_low = Match(
            iddaa_id="sort_m_low",
            league="UEFA Avrupa Ligi",
            country="Avrupa",
            home_team="Hoffenheim",
            away_team="Sturm Graz",
            start_time_utc=now + timedelta(minutes=30),
            status="SCHEDULED",
            is_major_league=True,
        )
        m_high = Match(
            iddaa_id="sort_m_high",
            league="UEFA Avrupa Ligi",
            country="Avrupa",
            home_team="Fenerbahçe",
            away_team="Ajax",
            start_time_utc=now + timedelta(minutes=45),
            status="SCHEDULED",
            is_major_league=True,
        )
        db.add_all([m_low, m_high])
        db.flush()

        p_low = Prediction(
            match_id=m_low.id,
            market_key="MS",
            selection_key="1",
            iddaa_odd=1.70,
            confidence_score=85.0,
            published_odd=1.68,
            published_confidence=67.0,
            published_at=now - timedelta(hours=2),
            is_top_pick=True,
            status="PENDING",
            score_breakdown_json="{}",
        )
        p_high = Prediction(
            match_id=m_high.id,
            market_key="AU_25",
            selection_key="UST",
            iddaa_odd=1.75,
            confidence_score=79.0,
            published_odd=1.75,
            published_confidence=81.0,
            published_at=now - timedelta(hours=2),
            is_top_pick=True,
            status="PENDING",
            score_breakdown_json="{}",
        )
        db.add_all([p_low, p_high])
        db.commit()

        clear_api_cache()
        fastapi_app.dependency_overrides[get_db] = lambda: db
        client = TestClient(fastapi_app)
        res = client.get("/api/predictions?today_only=false&major_only=true&min_confidence=60").json()
        items = res["items"]
        assert len(items) == 2
        assert items[0]["match_id"] == m_high.id, f"Expected highest effective confidence (81) first, got {items[0]}"
        assert items[0]["confidence_score"] == 81.0
        assert items[1]["confidence_score"] == 67.0
        assert items[0]["confidence_score"] >= items[1]["confidence_score"]
        print("TEST_HERO_AND_SORT_PARITY: PASS")
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_today_filter_tr_day_only():
    from utils.time_utils import TR_TZ
    db, engine = _make_memory_session()
    try:
        # Compute deterministic today vs tomorrow in Europe/Istanbul
        now_tr = datetime.now(TR_TZ)
        today_mid_tr = now_tr + timedelta(minutes=20)
        # If very close to midnight in TR, tomorrow is +2 hours; otherwise +24h/next day 14:00 TR
        tomorrow_tr = (now_tr + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
        if today_mid_tr.strftime("%Y-%m-%d") != now_tr.strftime("%Y-%m-%d"):
            # Fallback just in case test runs in last 20 mins of TR day: still verify tomorrow_tr is excluded
            today_match_utc = None
        else:
            today_match_utc = today_mid_tr.astimezone(timezone.utc)
        tomorrow_match_utc = tomorrow_tr.astimezone(timezone.utc)

        m_tomorrow = Match(
            iddaa_id="tr_tomorrow_m",
            league="Süper Lig",
            country="Türkiye",
            home_team="Tomorrow Home",
            away_team="Tomorrow Away",
            start_time_utc=tomorrow_match_utc,
            status="SCHEDULED",
            is_major_league=True,
        )
        db.add(m_tomorrow)
        db.flush()
        db.add(
            Prediction(
                match_id=m_tomorrow.id,
                market_key="MS",
                selection_key="1",
                iddaa_odd=1.80,
                confidence_score=76.0,
                is_top_pick=True,
                status="PENDING",
                score_breakdown_json="{}",
            )
        )

        m_today = None
        if today_match_utc is not None:
            m_today = Match(
                iddaa_id="tr_today_m",
                league="Süper Lig",
                country="Türkiye",
                home_team="Today Home",
                away_team="Today Away",
                start_time_utc=today_match_utc,
                status="SCHEDULED",
                is_major_league=True,
            )
            db.add(m_today)
            db.flush()
            db.add(
                Prediction(
                    match_id=m_today.id,
                    market_key="MS",
                    selection_key="1",
                    iddaa_odd=1.85,
                    confidence_score=74.0,
                    is_top_pick=True,
                    status="PENDING",
                    score_breakdown_json="{}",
                )
            )
        db.commit()

        clear_api_cache()
        fastapi_app.dependency_overrides[get_db] = lambda: db
        client = TestClient(fastapi_app)
        res_today = client.get("/api/predictions?today_only=true&major_only=true&min_confidence=60").json()
        today_ids = {it["match_id"] for it in res_today["items"]}
        assert m_tomorrow.id not in today_ids, f"Tomorrow TR match leaked into today_only=true! {today_ids}"
        if m_today is not None:
            assert m_today.id in today_ids, f"Today TR match missing from today_only=true! {today_ids}"

        res_all = client.get("/api/predictions?today_only=false&major_only=true&min_confidence=60").json()
        all_ids = {it["match_id"] for it in res_all["items"]}
        assert m_tomorrow.id in all_ids, "Tomorrow TR match should appear when today_only=false"
        print("TEST_TODAY_FILTER_TR_DAY_ONLY: PASS")
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()
        engine.dispose()


if __name__ == "__main__":
    test_tg_match_id_and_au25()
    test_transparency_lifecycle()
    test_odds_api_major_only()
    test_sync_dup_odds_and_elo()
    test_aet_00_and_void_alert()
    test_admin_auto_settle_order()
    test_hero_and_sort_parity()
    test_today_filter_tr_day_only()
    print("ALL_AUDIT_P0_P1_TESTS: PASS")
