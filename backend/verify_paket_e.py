"""Isolated verification suite for Paket E (Spor Toto 15'li)."""

import json
import math
import os
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
from app.models import Match, Prediction
from app.sportoto_optimizer import devig_1x2, optimize_sportoto_columns

HEADERS = {"X-Cron-Secret": settings.cron_secret}


def _make_test_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_pe1_optimizer_global_optimum():
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
        {"slot_no": i + 1, "prob_1": p1, "prob_x": px, "prob_2": p2}
        for i, (p1, px, p2) in enumerate(probs)
    ]

    # Exact recursive search over all feasible (k_1..k_15) with prod(k_i) <= budget
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
        plan = optimize_sportoto_columns(matches, budget)
        assert plan["used_columns"] <= budget
        assert len(plan["picks"]) == 15
        dp_log = sum(math.log(p["pick_prob"]) for p in plan["picks"])
        exact_log = brute_best_log(0, 1, budget)
        assert abs(dp_log - exact_log) < 1e-6, f"budget={budget}: dp={dp_log} vs exact={exact_log}"

    print("TEST_PE1_OPTIMIZER_GLOBAL_OPTIMUM: PASS")


def test_pe2_no_fake_data_publish_gate():
    SessionLocal = _make_test_session()
    db = SessionLocal()

    # Seed 1 real match with MS 1/X/2 predictions in DB
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
            # Initially no published week
            r0 = client.get("/api/sportoto/current")
            assert r0.status_code == 200
            assert r0.json() == {"active": False, "week": None}

            lines = ["1. Galatasaray - Fenerbahçe"]
            for i in range(2, 15):
                lines.append(f"{i}. Ev{i} - Dep{i} | 2.10 3.30 3.40")
            lines.append("15. EksikEv - EksikDep")  # Missing odds & not in DB

            r_parse = client.post(
                "/api/admin/sportoto/parse",
                headers=HEADERS,
                json={"title": "Test 15. Hafta", "raw_text": "\n".join(lines)},
            )
            assert r_parse.status_code == 200, r_parse.text
            w = r_parse.json()
            assert w["missing_count"] == 1
            assert w["missing_slots"] == [15]
            assert w["matches"][0]["source"] == "fikstra_model"
            assert w["matches"][1]["source"] == "market_devig"
            assert w["matches"][14]["source"] == "missing"

            # Publish must fail with 400
            r_pub_fail = client.post(f"/api/admin/sportoto/weeks/{w['id']}/publish", headers=HEADERS)
            assert r_pub_fail.status_code == 400

            # Patch slot 15 with real odds
            r_patch = client.patch(
                f"/api/admin/sportoto/weeks/{w['id']}/matches",
                headers=HEADERS,
                json={"matches": [{"slot_no": 15, "odd_1": 1.85, "odd_x": 3.50, "odd_2": 4.20}]},
            )
            assert r_patch.status_code == 200
            wp = r_patch.json()
            assert wp["missing_count"] == 0
            m15 = wp["matches"][14]
            assert m15["source"] == "market_devig"
            assert abs((m15["prob_1"] + m15["prob_x"] + m15["prob_2"]) - 1.0) < 1e-4

            # Now publish succeeds
            r_pub_ok = client.post(f"/api/admin/sportoto/weeks/{w['id']}/publish", headers=HEADERS)
            assert r_pub_ok.status_code == 200
            assert r_pub_ok.json()["status"] == "PUBLISHED"

            r_cur = client.get("/api/sportoto/current")
            assert r_cur.status_code == 200
            cur = r_cur.json()
            assert cur["active"] is True
            assert set(cur["week"]["plans"].keys()) == {"16", "32", "64"}
    finally:
        app.dependency_overrides.clear()

    print("TEST_PE2_NO_FAKE_DATA_PUBLISH_GATE: PASS")


def test_pe3_telegram_idempotent_and_settle():
    SessionLocal = _make_test_session()

    def override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    orig_post = httpx.Client.post
    orig_token = settings.telegram_bot_token
    orig_chat = settings.telegram_chat_id
    orig_topic = settings.telegram_topic_coupons
    settings.telegram_bot_token = "dummy-token"
    settings.telegram_chat_id = "-100123456"
    settings.telegram_topic_coupons = "2"

    sent_payloads = []

    def fake_post(self, url, *args, **kwargs):
        if "api.telegram.org" in str(url):
            sent_payloads.append(kwargs.get("json", {}))
            resp = MagicMock()
            resp.status_code = 200
            resp.is_success = True
            resp.json.return_value = {"ok": True, "result": {"message_id": 900 + len(sent_payloads)}}
            return resp
        return orig_post(self, url, *args, **kwargs)

    try:
        with patch.object(httpx.Client, "post", fake_post), TestClient(app) as client:
            lines = [f"{i}. TakimA{i} - TakimB{i} | 1.90 3.40 3.90" for i in range(1, 16)]
            w = client.post(
                "/api/admin/sportoto/parse",
                headers=HEADERS,
                json={"title": "16. Hafta", "raw_text": "\n".join(lines)},
            ).json()
            client.post(f"/api/admin/sportoto/weeks/{w['id']}/publish", headers=HEADERS)

            # First Telegram dispatch -> sends 1 message
            t1 = client.post(f"/api/admin/sportoto/weeks/{w['id']}/telegram", headers=HEADERS)
            assert t1.status_code == 200
            assert t1.json()["sent"] is True
            assert len(sent_payloads) == 1
            assert "tab=coupons&view=sportoto" in json.dumps(sent_payloads[0])

            # Second Telegram dispatch -> blocked by spam lock
            t2 = client.post(f"/api/admin/sportoto/weeks/{w['id']}/telegram", headers=HEADERS)
            assert t2.status_code == 200
            assert t2.json()["sent"] is False
            assert t2.json()["reason"] == "already_sent"
            assert len(sent_payloads) == 1

            # Settle all 15 matches as "1" (since odd_1=1.90 is favorite in all 15, all picks include "1")
            res_patch = client.patch(
                f"/api/admin/sportoto/weeks/{w['id']}/matches",
                headers=HEADERS,
                json={"matches": [{"slot_no": i, "actual_result": "1"} for i in range(1, 16)]},
            )
            assert res_patch.status_code == 200
            settled_w = res_patch.json()
            assert settled_w["status"] == "SETTLED"
            for b in ("16", "32", "64"):
                assert settled_w["plans"][b]["settled_count"] == 15
                assert settled_w["plans"][b]["hits"] == 15
    finally:
        settings.telegram_bot_token = orig_token
        settings.telegram_chat_id = orig_chat
        settings.telegram_topic_coupons = orig_topic
        app.dependency_overrides.clear()

    print("TEST_PE3_TELEGRAM_IDEMPOTENT_AND_SETTLE: PASS")


if __name__ == "__main__":
    p1, px, p2 = devig_1x2(2.0, 3.5, 3.8)
    assert abs((p1 + px + p2) - 1.0) < 1e-9
    test_pe1_optimizer_global_optimum()
    test_pe2_no_fake_data_publish_gate()
    test_pe3_telegram_idempotent_and_settle()
    print("ALL PAKET E TESTS PASSED")
