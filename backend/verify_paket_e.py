"""Isolated verification suite for Paket E (Fikstra Toto + Human-Readable Notifications)."""

import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import Coupon, CouponItem, Match, Prediction
from app.sportoto_engine import (
    compute_chaos_score,
    compute_live_survival,
    compute_trap_signal,
    devig_1x2,
    exact_poisson_binomial,
    optimize_toto,
)
from services.push import (
    format_coupon_ready_notification,
    format_coupon_settled_notification,
    format_high_confidence_notification,
    format_match_settled_notification,
    format_odds_drop_notification,
)

HEADERS = {"X-Cron-Secret": settings.cron_secret}


def _make_test_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_pe1_exact_poisson_and_dp_optimum():
    probs = [
        (0.62, 0.23, 0.15),
        (0.41, 0.31, 0.28),
        (0.55, 0.25, 0.20),
        (0.36, 0.33, 0.31),
        (0.70, 0.18, 0.12),
        (0.44, 0.29, 0.27),
        (0.35, 0.34, 0.31),
        (0.58, 0.24, 0.18),
        (0.48, 0.27, 0.25),
        (0.38, 0.32, 0.30),
        (0.65, 0.20, 0.15),
        (0.42, 0.30, 0.28),
        (0.51, 0.26, 0.23),
        (0.37, 0.33, 0.30),
        (0.60, 0.22, 0.18),
    ]
    matches = [
        {
            "slot_no": i + 1,
            "home_team": f"Ev{i+1}",
            "away_team": f"Dep{i+1}",
            "prob_1": p1,
            "prob_x": px,
            "prob_2": p2,
        }
        for i, (p1, px, p2) in enumerate(probs)
    ]

    # Exact Poisson-Binomial sanity
    pb = exact_poisson_binomial([max(p) for p in probs])
    assert 0.0 < pb["p15"] <= pb["p14_plus"] <= pb["p13_plus"] <= pb["p12_plus"] <= 100.0
    assert abs(pb["expected_hits"] - sum(max(p) for p in probs)) < 0.02

    # DP global optimum check against recursive search
    def brute_best_log(idx: int, prod_so_far: int, budget: int) -> float:
        if idx == len(probs):
            return 0.0
        p1, px, p2 = probs[idx]
        s_prob = max(p1, px, p2)
        d_prob = max(p1 + px, p1 + p2, px + p2)
        best = math.log(s_prob) + brute_best_log(idx + 1, prod_so_far, budget)
        if prod_so_far * 2 <= budget:
            best = max(best, math.log(d_prob) + brute_best_log(idx + 1, prod_so_far * 2, budget))
        if prod_so_far * 3 <= budget:
            best = max(best, math.log(1.0) + brute_best_log(idx + 1, prod_so_far * 3, budget))
        return best

    for budget in (16, 32, 64):
        plan = optimize_toto(matches, budget=budget, mode="hit15")
        assert plan["used_columns"] <= budget
        dp_log = sum(math.log(p["pick_prob"]) for p in plan["picks"])
        exact_log = brute_best_log(0, 1, budget)
        assert abs(dp_log - exact_log) < 1e-6

    # Locks respected + shield13 improves or equals p13_plus
    locks = {1: "1X", 5: "1"}
    locked_plan = optimize_toto(matches, budget=32, mode="shield13", locks=locks)
    assert locked_plan["used_columns"] <= 32
    p_by_slot = {p["slot_no"]: p for p in locked_plan["picks"]}
    assert p_by_slot[1]["pick_code"] == "1X" and p_by_slot[1]["locked"] is True
    assert p_by_slot[5]["pick_code"] == "1" and p_by_slot[5]["locked"] is True
    hit15_locked = optimize_toto(matches, budget=32, mode="hit15", locks=locks)
    assert locked_plan["probs"]["p13_plus"] >= hit15_locked["probs"]["p13_plus"]

    print("TEST_PE1_EXACT_POISSON_AND_DP_OPTIMUM: PASS")


def test_pe2_no_fake_data_and_trap_honesty():
    # Trap signal must be None when public % absent
    assert compute_trap_signal((0.55, 0.25, 0.20), (None, None, None)) is None
    # Real overloaded favorite -> is_trap = True
    trap = compute_trap_signal((0.55, 0.25, 0.20), (75.0, 15.0, 10.0))
    assert trap is not None and trap["is_trap"] is True and trap["fav_pick"] == "1"

    SessionLocal = _make_test_session()
    db = SessionLocal()
    m1 = Match(
        iddaa_id="st_m1",
        league="Süper Lig",
        country="Turkey",
        is_major_league=True,
        home_team="Galatasaray",
        away_team="Fenerbahçe",
        start_time_utc=datetime.now(timezone.utc) + timedelta(hours=12),
        status="SCHEDULED",
    )
    db.add(m1)
    db.flush()
    for sel, odd, pct in (("1", 2.10, 46.0), ("X", 3.40, 28.0), ("2", 3.50, 26.0)):
        db.add(
            Prediction(
                match_id=m1.id,
                market_key="MS",
                selection_key=sel,
                iddaa_odd=odd,
                confidence_score=70.0 if sel == "1" else 50.0,
                score_breakdown_json=json.dumps({"blended_prob_pct": pct}),
                is_top_pick=(sel == "1"),
                status="PENDING",
            )
        )
    db.commit()
    db.close()

    def override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            lines = ["1. Galatasaray - Fenerbahçe"]
            for i in range(2, 15):
                lines.append(f"{i}. Ev{i} - Dep{i} | 2.10 3.30 3.40")
            lines.append("15. EksikEv - EksikDep")

            w = client.post(
                "/api/admin/sportoto/parse",
                headers=HEADERS,
                json={"title": "Test 15. Hafta", "raw_text": "\n".join(lines)},
            ).json()
            assert w["missing_count"] == 1
            assert w["matches"][1]["trap_signal"] is None

            r_fail = client.post(f"/api/admin/sportoto/weeks/{w['id']}/publish", headers=HEADERS)
            assert r_fail.status_code == 400

            client.patch(
                f"/api/admin/sportoto/weeks/{w['id']}/matches",
                headers=HEADERS,
                json={
                    "matches": [
                        {
                            "slot_no": 15,
                            "odd_1": 1.85,
                            "odd_x": 3.50,
                            "odd_2": 4.20,
                            "public_pct_1": 74.0,
                            "public_pct_x": 16.0,
                            "public_pct_2": 10.0,
                        }
                    ]
                },
            )
            r_ok = client.post(f"/api/admin/sportoto/weeks/{w['id']}/publish", headers=HEADERS)
            assert r_ok.status_code == 200
            pub_w = r_ok.json()
            assert pub_w["status"] == "PUBLISHED"
            assert pub_w["matches"][14]["trap_signal"]["is_trap"] is True
    finally:
        app.dependency_overrides.clear()

    print("TEST_PE2_NO_FAKE_DATA_AND_TRAP_HONESTY: PASS")


def test_pe3_live_survival_and_telegram_idempotent():
    matches = [
        {"slot_no": i, "prob_1": 0.6, "prob_x": 0.25, "prob_2": 0.15, "actual_result": ("1" if i <= 3 else None)}
        for i in range(1, 16)
    ]
    matches[2]["actual_result"] = "2"  # 1 miss out of 3 settled
    picks = [{"slot_no": i, "pick_code": "1", "pick_prob": 0.6} for i in range(1, 16)]
    surv = compute_live_survival(matches, picks)
    assert surv is not None
    assert surv["settled_count"] == 3 and surv["settled_hits"] == 2 and surv["settled_misses"] == 1
    assert surv["can_hit_15"] is False and surv["p15_live"] == 0.0
    assert surv["can_hit_14"] is True and surv["p14_live"] > 0.0

    SessionLocal = _make_test_session()

    def override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    orig_post = httpx.Client.post
    orig_token, orig_chat, orig_topic = settings.telegram_bot_token, settings.telegram_chat_id, settings.telegram_topic_coupons
    settings.telegram_bot_token = "dummy"
    settings.telegram_chat_id = "-100123"
    settings.telegram_topic_coupons = "2"
    sent_payloads = []

    def fake_post(self, url, *args, **kwargs):
        if "api.telegram.org" in str(url):
            sent_payloads.append(kwargs.get("json", {}))
            resp = MagicMock()
            resp.status_code = 200
            resp.is_success = True
            resp.json.return_value = {"ok": True, "result": {"message_id": 777}}
            return resp
        return orig_post(self, url, *args, **kwargs)

    try:
        with patch.object(httpx.Client, "post", fake_post), TestClient(app) as client:
            lines = [f"{i}. Ev{i} - Dep{i} | 1.90 3.40 3.90" for i in range(1, 16)]
            w = client.post(
                "/api/admin/sportoto/parse",
                headers=HEADERS,
                json={"title": "16. Hafta", "raw_text": "\n".join(lines)},
            ).json()
            client.post(f"/api/admin/sportoto/weeks/{w['id']}/publish", headers=HEADERS)
            t1 = client.post(f"/api/admin/sportoto/weeks/{w['id']}/telegram", headers=HEADERS).json()
            t2 = client.post(f"/api/admin/sportoto/weeks/{w['id']}/telegram", headers=HEADERS).json()
            assert t1["sent"] is True and t2["sent"] is False and t2["reason"] == "already_sent"
            assert len(sent_payloads) == 1
            assert "tab=toto" in json.dumps(sent_payloads[0])
    finally:
        settings.telegram_bot_token, settings.telegram_chat_id, settings.telegram_topic_coupons = (
            orig_token,
            orig_chat,
            orig_topic,
        )
        app.dependency_overrides.clear()

    print("TEST_PE3_LIVE_SURVIVAL_AND_TELEGRAM_IDEMPOTENT: PASS")


def test_pe4_human_readable_notifications():
    m = Match(
        id=42,
        iddaa_id="x42",
        league="Süper Lig",
        home_team="Trabzonspor",
        away_team="Beşiktaş",
        home_score=2,
        away_score=1,
        start_time_utc=datetime.now(timezone.utc),
    )
    p = Prediction(
        id=99,
        match_id=42,
        market_key="MS",
        selection_key="1",
        iddaa_odd=1.95,
        confidence_score=84.0,
        odds_drop_pct=9.5,
        status="WON",
        match=m,
    )
    c = Coupon(id=7, coupon_date="2026-11-26", tier="BANKO", total_odd=3.45, avg_confidence=78.0, status="WON")
    c.items = [CouponItem(coupon_id=7, prediction_id=99), CouponItem(coupon_id=7, prediction_id=100)]

    samples = [
        format_high_confidence_notification(p),
        format_odds_drop_notification(p),
        format_coupon_ready_notification(c),
        format_match_settled_notification(p),
        format_coupon_settled_notification(c, 2, 2),
    ]
    forbidden = re.compile(r"\b(steam|edge|CLV|de-vig|EV|MS 1|MS 2)\b", re.IGNORECASE)
    for title, body, url in samples:
        combined = f"{title} {body}"
        assert not forbidden.search(combined), f"Technical jargon leaked: {combined}"
        assert url.startswith("/?"), f"Invalid deep-link URL: {url}"
    assert "Trabzonspor Kazanır" in samples[0][1]
    assert "1.95" in samples[0][1]

    print("TEST_PE4_HUMAN_READABLE_NOTIFICATIONS: PASS")


def test_perf_transparency():
    from datetime import timedelta
    from app.routers.api import _API_CACHE

    SessionLocal = _make_test_session()

    def override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    _API_CACHE.clear()
    app.dependency_overrides[get_db] = override_get_db
    now = datetime.now(timezone.utc)
    kickoff = now - timedelta(hours=3)
    try:
        with SessionLocal() as db:
            m1 = Match(iddaa_id="tp1", league="Süper Lig", home_team="Galatasaray", away_team="Samsunspor", start_time_utc=kickoff, status="FINISHED", home_score=2, away_score=0)
            m2 = Match(iddaa_id="tp2", league="La Liga", home_team="Real Madrid", away_team="Getafe", start_time_utc=kickoff, status="FINISHED", home_score=1, away_score=0)
            m3 = Match(iddaa_id="tp3", league="Serie A", home_team="Inter", away_team="Verona", start_time_utc=kickoff, status="FINISHED", home_score=1, away_score=1)
            m4 = Match(iddaa_id="tp4", league="Bundesliga", home_team="Bayern", away_team="Mainz", start_time_utc=kickoff, status="FINISHED", home_score=3, away_score=1)
            db.add_all([m1, m2, m3, m4])
            db.flush()

            # 1) Official Analizler-published 60+ pre-kickoff pick -> MUST appear
            p1 = Prediction(
                match_id=m1.id, market_key="MS", selection_key="1",
                iddaa_odd=1.55, confidence_score=76.0, is_top_pick=True,
                published_odd=1.55, published_confidence=76.0,
                published_at=kickoff - timedelta(hours=2),
                status="WON", settled_at=now - timedelta(hours=1),
            )
            # 2) <60 confidence background pick -> MUST NOT appear
            p2 = Prediction(
                match_id=m2.id, market_key="MS", selection_key="1",
                iddaa_odd=1.40, confidence_score=54.0, is_top_pick=True,
                published_odd=1.40, published_confidence=54.0,
                published_at=kickoff - timedelta(hours=2),
                status="WON", settled_at=now - timedelta(hours=1),
            )
            # 3) Unpublished / non-top-pick background pick -> MUST NOT appear
            p3 = Prediction(
                match_id=m3.id, market_key="MS", selection_key="1",
                iddaa_odd=1.60, confidence_score=72.0, is_top_pick=False,
                status="LOST", settled_at=now - timedelta(hours=1),
            )
            # 4) Post-kickoff published pick -> MUST NOT appear
            p4 = Prediction(
                match_id=m4.id, market_key="MS", selection_key="1",
                iddaa_odd=1.45, confidence_score=80.0, is_top_pick=True,
                published_odd=1.45, published_confidence=80.0,
                published_at=kickoff + timedelta(minutes=15),
                status="WON", settled_at=now - timedelta(hours=1),
            )
            db.add_all([p1, p2, p3, p4])
            db.commit()

        with TestClient(app) as client:
            perf = client.get("/api/performance").json()
            assert perf["total_settled"] == 1, f"Expected 1 official pick, got {perf['total_settled']}"
            assert perf["won_count"] == 1
            assert len(perf["archive"]) == 1
            assert perf["archive"][0]["home_team"] == "Galatasaray"
            leagues = [x["label"] for x in perf["breakdown"]["by_league"]]
            assert leagues == ["Süper Lig"], f"Unexpected breakdown leagues: {leagues}"
    finally:
        _API_CACHE.clear()
        app.dependency_overrides.clear()

    print("TEST_PERF_TRANSPARENCY: PASS")


def test_recent_matches_accuracy():
    from datetime import timedelta
    from scrapers.external_data import _fetch_team_schedule_profile, _parse_espn_form

    now = datetime.now(timezone.utc)
    kickoff = now - timedelta(hours=1)
    team_id = "100"

    # Mock ESPN lastFiveGames with:
    # - future/current match (should be excluded!)
    # - duplicate event id (should be deduplicated!)
    # - away win (team_id=100 is away, score 1-3 -> W)
    # - home loss (team_id=100 is home, score 0-2 -> L)
    # - away draw (team_id=100 is away, score 2-2 -> D)
    block = {
        "team": {"id": team_id, "displayName": "Galatasaray"},
        "events": [
            {
                "id": "curr_match",
                "gameDate": kickoff.isoformat(),
                "homeTeamId": team_id,
                "homeTeamScore": "3",
                "awayTeamScore": "1",
                "opponent": {"displayName": "Current Opp"},
            },
            {
                "id": "ev_future",
                "gameDate": (now + timedelta(days=2)).isoformat(),
                "homeTeamId": team_id,
                "homeTeamScore": None,
                "awayTeamScore": None,
                "opponent": {"displayName": "Future Opp"},
            },
            {
                "id": "ev_3",
                "gameDate": (kickoff - timedelta(days=3)).isoformat(),
                "homeTeamId": "200",
                "homeTeamScore": "1",
                "awayTeamScore": "3",
                "opponent": {"displayName": "AwayWinOpp"},
            },
            {
                "id": "ev_3",  # duplicate!
                "gameDate": (kickoff - timedelta(days=3)).isoformat(),
                "homeTeamId": "200",
                "homeTeamScore": "1",
                "awayTeamScore": "3",
                "opponent": {"displayName": "AwayWinOpp"},
            },
            {
                "id": "ev_2",
                "gameDate": (kickoff - timedelta(days=7)).isoformat(),
                "homeTeamId": team_id,
                "homeTeamScore": "0",
                "awayTeamScore": "2",
                "opponent": {"displayName": "HomeLossOpp"},
            },
            {
                "id": "ev_1",
                "gameDate": (kickoff - timedelta(days=12)).isoformat(),
                "homeTeamId": "300",
                "homeTeamScore": "2",
                "awayTeamScore": "2",
                "opponent": {"displayName": "AwayDrawOpp"},
            },
        ],
    }

    parsed = _parse_espn_form(block, match_start_utc=kickoff, exclude_event_id="curr_match")
    assert parsed["form"] == ["W", "L", "D"], f"Unexpected form: {parsed['form']}"
    rm = parsed["recent_matches"]
    assert len(rm) == 3, f"Expected 3 deduplicated past matches, got {len(rm)}"
    assert [m["result"] for m in rm] == parsed["form"], "Form dots & recent matches order mismatch!"
    assert rm[0]["opponent"] == "AwayWinOpp" and rm[0]["is_home"] is False and rm[0]["away_score"] == 3
    assert rm[1]["opponent"] == "HomeLossOpp" and rm[1]["is_home"] is True and rm[1]["home_score"] == 0
    assert rm[2]["opponent"] == "AwayDrawOpp" and rm[2]["home_score"] == 2 and rm[2]["away_score"] == 2

    # Also test _fetch_team_schedule_profile with mocked ESPN schedule response
    schedule_payload = {
        "events": [
            {
                "id": "curr_match",
                "date": kickoff.isoformat(),
                "competitions": [{
                    "status": {"type": {"completed": True}},
                    "competitors": [
                        {"team": {"id": team_id, "displayName": "Galatasaray"}, "homeAway": "home", "score": {"value": 3}},
                        {"team": {"id": "999", "displayName": "Current Opp"}, "homeAway": "away", "score": {"value": 1}},
                    ],
                }],
            },
            {
                "id": "sched_unplayed",
                "date": (kickoff - timedelta(days=1)).isoformat(),
                "competitions": [{
                    "status": {"type": {"completed": False}},
                    "competitors": [
                        {"team": {"id": team_id, "displayName": "Galatasaray"}, "homeAway": "home", "score": {"value": 0}},
                        {"team": {"id": "888", "displayName": "Postponed Opp"}, "homeAway": "away", "score": {"value": 0}},
                    ],
                }],
            },
            {
                "id": "sched_1",
                "date": (kickoff - timedelta(days=4)).isoformat(),
                "competitions": [{
                    "status": {"type": {"completed": True}},
                    "competitors": [
                        {"team": {"id": "777", "displayName": "Rival A"}, "homeAway": "home", "score": {"value": 0}},
                        {"team": {"id": team_id, "displayName": "Galatasaray"}, "homeAway": "away", "score": {"value": 2}},
                    ],
                }],
            },
            {
                "id": "sched_2",
                "date": (kickoff - timedelta(days=9)).isoformat(),
                "competitions": [{
                    "status": {"type": {"completed": True}},
                    "competitors": [
                        {"team": {"id": team_id, "displayName": "Galatasaray"}, "homeAway": "home", "score": {"value": 1}},
                        {"team": {"id": "666", "displayName": "Rival B"}, "homeAway": "away", "score": {"value": 3}},
                    ],
                }],
            },
        ]
    }
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = schedule_payload
    fake_client = MagicMock()
    fake_client.get.return_value = fake_resp

    prof = _fetch_team_schedule_profile(fake_client, team_id, kickoff, exclude_event_id="curr_match")
    assert prof["form_10"] == ["W", "L"], f"Unexpected schedule form: {prof['form_10']}"
    assert [m["result"] for m in prof["recent_matches"]] == prof["form_10"]
    assert prof["recent_matches"][0]["opponent"] == "Rival A" and prof["recent_matches"][0]["is_home"] is False
    assert prof["recent_matches"][1]["opponent"] == "Rival B" and prof["recent_matches"][1]["is_home"] is True

    print("TEST_RECENT_MATCHES_ACCURACY: PASS")


def test_pe5_monotonicity_and_ties():
    matches = [
        {"slot_no": 1, "home_team": "Arsenal", "away_team": "City", "prob_1": 0.48, "prob_x": 0.255, "prob_2": 0.265},
        {"slot_no": 2, "home_team": "Alanya", "away_team": "Konya", "prob_1": 0.37, "prob_x": 0.29, "prob_2": 0.34},
        {"slot_no": 3, "home_team": "Rize", "away_team": "Kasimpasa", "prob_1": 0.50, "prob_x": 0.25, "prob_2": 0.25},
        {"slot_no": 4, "home_team": "Samsun", "away_team": "Gencler", "prob_1": 0.50, "prob_x": 0.26, "prob_2": 0.24},
        {"slot_no": 5, "home_team": "Gaziantep", "away_team": "Amed", "prob_1": 0.28, "prob_x": 0.26, "prob_2": 0.46},
        {"slot_no": 6, "home_team": "Corum", "away_team": "Kocaeli", "prob_1": 0.49, "prob_x": 0.27, "prob_2": 0.24},
        {"slot_no": 7, "home_team": "Eyup", "away_team": "Basaksehir", "prob_1": 0.16, "prob_x": 0.20, "prob_2": 0.64},
        {"slot_no": 8, "home_team": "Fenerbahce", "away_team": "Erzurum", "prob_1": 0.78, "prob_x": 0.14, "prob_2": 0.08},
        {"slot_no": 9, "home_team": "Goztepe", "away_team": "Trabzon", "prob_1": 0.30, "prob_x": 0.25, "prob_2": 0.45},
        {"slot_no": 10, "home_team": "Besiktas", "away_team": "Galatasaray", "prob_1": 0.46, "prob_x": 0.24, "prob_2": 0.30},
        {"slot_no": 11, "home_team": "Freiburg", "away_team": "Leipzig", "prob_1": 0.35, "prob_x": 0.25, "prob_2": 0.40},
        {"slot_no": 12, "home_team": "Dortmund", "away_team": "Leverkusen", "prob_1": 0.45, "prob_x": 0.24, "prob_2": 0.31, "public_pct_1": 69.0, "public_pct_x": 18.0, "public_pct_2": 13.0},
        {"slot_no": 13, "home_team": "Lyon", "away_team": "Monaco", "prob_1": 0.40, "prob_x": 0.28, "prob_2": 0.32},
        {"slot_no": 14, "home_team": "Everton", "away_team": "Liverpool", "prob_1": 0.22, "prob_x": 0.25, "prob_2": 0.53},
        {"slot_no": 15, "home_team": "Sociedad", "away_team": "Sevilla", "prob_1": 0.45, "prob_x": 0.27, "prob_2": 0.28},
    ]

    for mode in ("hit15", "shield13", "contrarian"):
        prev = None
        for b in (16, 32, 64, 128):
            plan = optimize_toto(matches, budget=b, mode=mode)
            if prev is not None:
                assert plan["expected_hits"] >= prev["expected_hits"], f"{mode} b={b} exp decreased"
                assert plan["probs"]["p15"] >= prev["probs"]["p15"], f"{mode} b={b} p15 decreased"
                assert plan["probs"]["p14_plus"] >= prev["probs"]["p14_plus"], f"{mode} b={b} p14 decreased"
                assert plan["probs"]["p13_plus"] >= prev["probs"]["p13_plus"], f"{mode} b={b} p13 decreased"
            prev = plan

    # Slot 1 has 12=0.745 vs 1X=0.735 (diff 0.010 <= 0.015) -> must prefer 1X over 12 when doubled
    p64 = optimize_toto(matches, budget=64, mode="hit15", locks={1: "1X"})
    p_ars = optimize_toto(matches, budget=128, mode="hit15")
    ars_pick = next(pk for pk in p_ars["picks"] if pk["slot_no"] == 1)
    assert ars_pick["pick_code"] != "12", f"Expected 1X instead of 12 on <=1.5% tie, got {ars_pick['pick_code']}"
    assert p64["used_columns"] <= 64

    print("TEST_PE5_MONOTONICITY_AND_TIES: PASS")


def test_pe6_calibrated_labels_and_history():
    # Calibrated 4-tier labels
    assert compute_chaos_score(0.78, 0.14, 0.08)["label"] == "Banko"
    assert compute_chaos_score(0.58, 0.24, 0.18)["label"] == "Favori"
    assert compute_chaos_score(0.46, 0.27, 0.27)["label"] == "Ortada"
    assert compute_chaos_score(0.38, 0.32, 0.30)["label"] == "Sürprize Açık"

    # 78% favorite with 97% public load is NOT a trap
    fb_trap = compute_trap_signal((0.78, 0.14, 0.08), (97.0, 2.0, 1.0))
    assert fb_trap is not None and fb_trap["is_trap"] is False

    # 45% favorite with 69% public load IS a trap (<62% true, >=68% pub, >=14pt gap)
    bvb_trap = compute_trap_signal((0.45, 0.24, 0.31), (69.0, 18.0, 13.0))
    assert bvb_trap is not None and bvb_trap["is_trap"] is True

    # Active PUBLISHED week must NOT appear in /api/sportoto/history; SETTLED week MUST appear
    SessionLocal = _make_test_session()

    def override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            lines = [f"{i}. Ev{i} - Dep{i} | 1.90 3.40 3.90" for i in range(1, 16)]
            w = client.post(
                "/api/admin/sportoto/parse",
                headers=HEADERS,
                json={"title": "370. Hafta", "raw_text": "\n".join(lines)},
            ).json()
            client.post(f"/api/admin/sportoto/weeks/{w['id']}/publish", headers=HEADERS)

            hist_pub = client.get("/api/sportoto/history").json()
            assert hist_pub["items"] == [], f"PUBLISHED week leaked into history: {hist_pub['items']}"

            # Settle all 15 matches -> moves to SETTLED -> appears in history
            client.patch(
                f"/api/admin/sportoto/weeks/{w['id']}/matches",
                headers=HEADERS,
                json={"matches": [{"slot_no": i, "actual_result": "1"} for i in range(1, 16)]},
            )
            hist_settled = client.get("/api/sportoto/history").json()
            assert len(hist_settled["items"]) == 1
            assert hist_settled["items"][0]["status"] == "SETTLED"
    finally:
        app.dependency_overrides.clear()

    print("TEST_PE6_CALIBRATED_LABELS_AND_HISTORY: PASS")


if __name__ == "__main__":
    p1, px, p2 = devig_1x2(2.0, 3.5, 3.8)
    assert abs((p1 + px + p2) - 1.0) < 1e-9
    test_pe1_exact_poisson_and_dp_optimum()
    test_pe2_no_fake_data_and_trap_honesty()
    test_pe3_live_survival_and_telegram_idempotent()
    test_pe4_human_readable_notifications()
    test_perf_transparency()
    test_recent_matches_accuracy()
    test_pe5_monotonicity_and_ties()
    test_pe6_calibrated_labels_and_history()
    print("ALL PAKET E TESTS PASSED")
