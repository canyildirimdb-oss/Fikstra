"""Isolated unit tests for the Fikstra Telegram Topics dispatch engine."""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(__file__))

from app.config import settings
from app.database import Base, get_db
from app.main import app as fastapi_app
from app.models import Coupon, CouponItem, Match, Prediction, ScraperCache, SyncLog, TelegramDispatch
from app import telegram_bot


def _make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"ok": True, "result": {"message_id": 100}}
        self.is_success = 200 <= status_code < 300
        self.text = str(self._payload)

    def json(self):
        return self._payload


def test_tg1_idempotent_no_spam():
    db = _make_session()
    now = datetime.now(timezone.utc)
    m1 = Match(
        iddaa_id="tg_m1",
        league="Premier League",
        home_team="Arsenal",
        away_team="Chelsea",
        start_time_utc=now + timedelta(hours=3),
        status="SCHEDULED",
    )
    m2 = Match(
        iddaa_id="tg_m2",
        league="La Liga",
        home_team="Real Madrid",
        away_team="Sevilla",
        start_time_utc=now + timedelta(hours=4),
        status="SCHEDULED",
    )
    db.add_all([m1, m2])
    db.flush()

    p1 = Prediction(
        match_id=m1.id,
        market_key="MS",
        selection_key="1",
        iddaa_odd=1.80,
        published_odd=1.80,
        confidence_score=76.0,
        published_confidence=76.0,
        is_top_pick=True,
        odds_drop_pct=8.5,
        status="PENDING",
    )
    p2 = Prediction(
        match_id=m2.id,
        market_key="AU_2.5",
        selection_key="UST",
        iddaa_odd=1.70,
        published_odd=1.70,
        confidence_score=67.0,
        published_confidence=67.0,
        is_top_pick=True,
        value_edge_pct=7.2,
        status="PENDING",
    )
    db.add_all([p1, p2])
    db.flush()

    coupon = Coupon(
        coupon_date=now.astimezone(telegram_bot.TR_TZ).date().isoformat(),
        tier="BANKO",
        total_odd=round(1.80 * 1.70, 2),
        avg_confidence=71.5,
        status="PENDING",
        is_locked=False,
    )
    db.add(coupon)
    db.flush()
    db.add_all([
        CouponItem(coupon_id=coupon.id, prediction_id=p1.id, odd_at_pick=1.80),
        CouponItem(coupon_id=coupon.id, prediction_id=p2.id, odd_at_pick=1.70),
    ])
    db.commit()

    calls = []
    msg_seq = {"id": 500}

    def fake_post(self, url, json=None):
        method = url.rsplit("/", 1)[-1]
        calls.append((method, json))
        if method == "sendMessage":
            msg_seq["id"] += 1
            return _FakeResponse(200, {"ok": True, "result": {"message_id": msg_seq["id"]}})
        if method == "editMessageText":
            return _FakeResponse(200, {"ok": True, "result": True})
        return _FakeResponse(200, {"ok": True, "result": []})

    with patch.object(settings, "telegram_bot_token", "fake-token"), \
         patch.object(settings, "telegram_chat_id", "-100123456"), \
         patch.object(settings, "telegram_topic_coupons", "11"), \
         patch.object(settings, "telegram_topic_singles", "12"), \
         patch.object(settings, "telegram_topic_value", "13"), \
         patch.object(settings, "telegram_topic_results", "14"), \
         patch("httpx.Client.post", new=fake_post), \
         patch("app.telegram_bot.time.sleep"):

        res1 = telegram_bot.dispatch_telegram_updates(db)
        assert res1["coupons_sent"] == 1
        assert res1["singles"] == 1  # only p1 >= 70.0
        assert res1["value"] == 2    # p1 (drop 8.5%) + p2 (edge 7.2%)

        calls.clear()
        res2 = telegram_bot.dispatch_telegram_updates(db)
        assert res2["coupons_sent"] == 0
        assert res2["coupons_edited"] == 0
        assert res2["singles"] == 0
        assert res2["value"] == 0
        assert len(calls) == 0, f"Expected 0 HTTP calls on idempotent re-run, got {calls}"

        # Lock coupon -> should editMessageText, NOT sendMessage
        coupon.is_locked = True
        db.commit()
        res3 = telegram_bot.dispatch_telegram_updates(db)
        assert res3["coupons_sent"] == 0
        assert res3["coupons_edited"] == 1
        assert len(calls) == 1 and calls[0][0] == "editMessageText"
        assert "Kilitlendi" in calls[0][1]["text"]

    print("TEST_TG1_IDEMPOTENT_NO_SPAM: PASS")


def test_tg2_settle_reply_and_daily_report():
    db = _make_session()
    now_tr = datetime(2026, 11, 26, 2, 0, tzinfo=telegram_bot.TR_TZ)
    now_utc = now_tr.astimezone(timezone.utc)
    yesterday_utc = (now_tr - timedelta(days=1)).replace(hour=18, minute=0).astimezone(timezone.utc)

    m_won = Match(
        iddaa_id="tg_s1",
        league="Serie A",
        home_team="Inter",
        away_team="Milan",
        start_time_utc=yesterday_utc,
        status="FINISHED",
        home_score=2,
        away_score=1,
    )
    m_lost = Match(
        iddaa_id="tg_s2",
        league="Bundesliga",
        home_team="Bayern",
        away_team="Dortmund",
        start_time_utc=yesterday_utc,
        status="FINISHED",
        home_score=1,
        away_score=1,
    )
    db.add_all([m_won, m_lost])
    db.flush()

    p_won = Prediction(
        match_id=m_won.id,
        market_key="MS",
        selection_key="1",
        iddaa_odd=1.95,
        published_odd=1.95,
        confidence_score=74.0,
        published_confidence=74.0,
        published_at=m_won.start_time_utc - timedelta(hours=1),
        is_top_pick=True,
        status="WON",
    )
    p_lost = Prediction(
        match_id=m_lost.id,
        market_key="MS",
        selection_key="1",
        iddaa_odd=1.60,
        published_odd=1.60,
        confidence_score=64.0,
        published_confidence=64.0,
        published_at=m_lost.start_time_utc - timedelta(hours=1),
        is_top_pick=True,
        status="LOST",
    )
    db.add_all([p_won, p_lost])
    db.flush()

    coupon = Coupon(
        coupon_date=(now_tr - timedelta(days=1)).date().isoformat(),
        tier="BANKO",
        total_odd=1.95,
        avg_confidence=74.0,
        status="WON",
        is_locked=True,
    )
    db.add(coupon)
    db.flush()
    db.add(CouponItem(coupon_id=coupon.id, prediction_id=p_won.id, odd_at_pick=1.95))

    db.add_all([
        TelegramDispatch(
            dispatch_key=f"coupon:{coupon.id}",
            kind="coupon",
            chat_id="-100123456",
            thread_id=11,
            message_id=701,
            content_hash="abc",
            settled_replied=False,
        ),
        TelegramDispatch(
            dispatch_key=f"single:{p_won.id}",
            kind="single",
            chat_id="-100123456",
            thread_id=12,
            message_id=702,
            content_hash="def",
            settled_replied=False,
        ),
    ])
    db.commit()

    calls = []
    msg_seq = {"id": 800}

    def fake_post(self, url, json=None):
        calls.append(json)
        msg_seq["id"] += 1
        return _FakeResponse(200, {"ok": True, "result": {"message_id": msg_seq["id"]}})

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now_utc if tz else now_utc.replace(tzinfo=None)

    with patch.object(settings, "telegram_bot_token", "fake-token"), \
         patch.object(settings, "telegram_chat_id", "-100123456"), \
         patch.object(settings, "telegram_topic_coupons", "11"), \
         patch.object(settings, "telegram_topic_singles", "12"), \
         patch.object(settings, "telegram_topic_value", "13"), \
         patch.object(settings, "telegram_topic_results", "14"), \
         patch("app.telegram_bot.datetime", _FakeDatetime), \
         patch("httpx.Client.post", new=fake_post), \
         patch("app.telegram_bot.time.sleep"):

        res = telegram_bot.dispatch_telegram_settlements(db)
        assert res["coupon_replies"] == 1
        assert res["won_showcases"] == 1
        assert res["single_replies"] == 1
        assert res["daily_report"] is True

        # Verify reply_to_message_id targets
        reply_targets = [c.get("reply_to_message_id") for c in calls if c.get("reply_to_message_id")]
        assert 701 in reply_targets and 702 in reply_targets

        # Second run must be completely idempotent
        calls.clear()
        res_again = telegram_bot.dispatch_telegram_settlements(db)
        assert res_again["coupon_replies"] == 0
        assert res_again["single_replies"] == 0
        assert res_again["daily_report"] is False
        assert len(calls) == 0

    print("TEST_TG2_SETTLE_REPLY_AND_DAILY_REPORT: PASS")


def test_tg3_failsafe_offline():
    db = _make_session()
    now = datetime.now(timezone.utc)
    m = Match(
        iddaa_id="tg_f1",
        league="Ligue 1",
        home_team="PSG",
        away_team="Lyon",
        start_time_utc=now + timedelta(hours=3),
        status="SCHEDULED",
    )
    db.add(m)
    db.flush()
    p = Prediction(
        match_id=m.id,
        market_key="MS",
        selection_key="1",
        iddaa_odd=1.50,
        published_odd=1.50,
        confidence_score=78.0,
        published_confidence=78.0,
        is_top_pick=True,
        status="PENDING",
    )
    db.add(p)
    db.commit()

    # 1. Empty token -> silent no-op
    with patch.object(settings, "telegram_bot_token", ""):
        r_off = telegram_bot.dispatch_telegram_updates(db)
        assert r_off["enabled"] is False
        r_set_off = telegram_bot.dispatch_telegram_settlements(db)
        assert r_set_off["enabled"] is False

    # 2. HTTP 429 -> clean stop without raising
    def fake_429(self, url, json=None):
        return _FakeResponse(429, {"ok": False, "parameters": {"retry_after": 12}})

    with patch.object(settings, "telegram_bot_token", "fake-token"), \
         patch.object(settings, "telegram_chat_id", "-100123456"), \
         patch.object(settings, "telegram_topic_singles", "12"), \
         patch("httpx.Client.post", new=fake_429):
        r_429 = telegram_bot.dispatch_telegram_updates(db)
        assert r_429["rate_limited"] is True
        assert r_429["singles"] == 0

    # 3. Network error -> never raises
    def fake_err(self, url, json=None):
        raise httpx.ConnectError("offline")

    with patch.object(settings, "telegram_bot_token", "fake-token"), \
         patch.object(settings, "telegram_chat_id", "-100123456"), \
         patch.object(settings, "telegram_topic_singles", "12"), \
         patch("httpx.Client.post", new=fake_err):
        r_err = telegram_bot.dispatch_telegram_updates(db)
        assert r_err["singles"] == 0

    print("TEST_TG3_FAILSAFE_OFFLINE: PASS")


def test_tg4_deep_links():
    db = _make_session()
    now = datetime.now(timezone.utc)
    m = Match(
        iddaa_id="tg_dl1",
        league="Süper Lig",
        home_team="Galatasaray",
        away_team="Fenerbahçe",
        start_time_utc=now + timedelta(hours=3),
        status="SCHEDULED",
    )
    db.add(m)
    db.flush()
    p = Prediction(
        match_id=m.id,
        market_key="MS",
        selection_key="1",
        iddaa_odd=2.05,
        published_odd=2.05,
        confidence_score=75.0,
        published_confidence=75.0,
        is_top_pick=True,
        odds_drop_pct=9.0,
        status="PENDING",
    )
    db.add(p)
    db.flush()
    c = Coupon(
        coupon_date=now.astimezone(telegram_bot.TR_TZ).date().isoformat(),
        tier="BANKO",
        total_odd=2.05,
        avg_confidence=75.0,
        status="PENDING",
        is_locked=False,
    )
    db.add(c)
    db.flush()
    db.add(CouponItem(coupon_id=c.id, prediction_id=p.id, odd_at_pick=2.05))
    db.commit()

    _, c_markup = telegram_bot._render_coupon_message(c)
    _, s_markup = telegram_bot._render_single_message(p)
    _, v_markup = telegram_bot._render_value_message(p)

    base = telegram_bot._public_url()
    assert c_markup["inline_keyboard"][0][0]["url"] == f"{base}/?tab=coupons"
    assert s_markup["inline_keyboard"][0][0]["url"] == f"{base}/?match={p.match_id}&pred={p.id}"
    assert v_markup["inline_keyboard"][0][0]["url"] == f"{base}/?match={p.match_id}&pred={p.id}"

    # Verify daily report button uses /?tab=performance
    now_tr = datetime(2026, 11, 26, 2, 0, tzinfo=telegram_bot.TR_TZ)
    now_utc = now_tr.astimezone(timezone.utc)
    y_utc = (now_tr - timedelta(days=1)).replace(hour=18, minute=0).astimezone(timezone.utc)
    m_y = Match(
        iddaa_id="tg_dl_y",
        league="Süper Lig",
        home_team="Beşiktaş",
        away_team="Trabzonspor",
        start_time_utc=y_utc,
        status="FINISHED",
        home_score=1,
        away_score=0,
    )
    db.add(m_y)
    db.flush()
    db.add(Prediction(
        match_id=m_y.id, market_key="MS", selection_key="1",
        iddaa_odd=1.85, published_odd=1.85, confidence_score=72.0, published_confidence=72.0,
        published_at=y_utc - timedelta(hours=2),
        is_top_pick=True, status="WON",
    ))
    db.commit()

    sent_payloads = []
    def fake_post(self, url, json=None):
        sent_payloads.append(json)
        return _FakeResponse(200, {"ok": True, "result": {"message_id": 910}})

    class _FakeDt(datetime):
        @classmethod
        def now(cls, tz=None):
            return now_utc if tz else now_utc.replace(tzinfo=None)

    with patch.object(settings, "telegram_bot_token", "fake-token"), \
         patch.object(settings, "telegram_chat_id", "-100123456"), \
         patch.object(settings, "telegram_topic_results", "14"), \
         patch("app.telegram_bot.datetime", _FakeDt), \
         patch("httpx.Client.post", new=fake_post), \
         patch("app.telegram_bot.time.sleep"):
        res = telegram_bot.dispatch_telegram_settlements(db)
        assert res["daily_report"] is True
        assert any(
            p_item.get("reply_markup", {}).get("inline_keyboard", [[{}]])[0][0].get("url") == f"{base}/?tab=performance"
            for p_item in sent_payloads
        )

    print("TEST_TG4_DEEP_LINKS: PASS")


def test_tg5_weekly_report():
    db = _make_session()
    # Monday 2026-11-30 09:30 TR -> ISO week 2026-W49
    mon_tr = datetime(2026, 11, 30, 9, 30, tzinfo=telegram_bot.TR_TZ)
    mon_utc = mon_tr.astimezone(timezone.utc)

    # 3 matches in Premier League (last 7 days: Nov 24-28)
    for idx, (st, odd) in enumerate([("WON", 2.40), ("WON", 1.80), ("LOST", 1.70)]):
        mt_start = (mon_tr - timedelta(days=2 + idx)).replace(hour=18, minute=0).astimezone(timezone.utc)
        mt = Match(
            iddaa_id=f"tg_w_{idx}",
            league="Premier League",
            home_team=f"Home{idx}",
            away_team=f"Away{idx}",
            start_time_utc=mt_start,
            status="FINISHED",
            home_score=2 if st == "WON" else 0,
            away_score=1,
        )
        db.add(mt)
        db.flush()
        db.add(Prediction(
            match_id=mt.id, market_key="MS", selection_key="1",
            iddaa_odd=odd, published_odd=odd, confidence_score=70.0, published_confidence=70.0,
            published_at=mt_start - timedelta(hours=2),
            is_top_pick=True, status=st,
        ))

    # Add settled coupon in last week
    c = Coupon(
        coupon_date=(mon_tr - timedelta(days=2)).date().isoformat(),
        tier="BANKO", total_odd=2.40, avg_confidence=70.0, status="WON", is_locked=True,
    )
    db.add(c)
    db.commit()

    calls = []
    def fake_post(self, url, json=None):
        calls.append(json)
        return _FakeResponse(200, {"ok": True, "result": {"message_id": 920}})

    class _FakeMonDt(datetime):
        @classmethod
        def now(cls, tz=None):
            return mon_utc if tz else mon_utc.replace(tzinfo=None)

    with patch.object(settings, "telegram_bot_token", "fake-token"), \
         patch.object(settings, "telegram_chat_id", "-100123456"), \
         patch.object(settings, "telegram_topic_results", "14"), \
         patch("app.telegram_bot.datetime", _FakeMonDt), \
         patch("httpx.Client.post", new=fake_post), \
         patch("app.telegram_bot.time.sleep"):
        res1 = telegram_bot.dispatch_telegram_settlements(db)
        assert res1["weekly_report"] is True
        weekly_msgs = [x for x in calls if "HAFTANIN ŞEFFAF BİLANÇOSU" in x.get("text", "")]
        assert len(weekly_msgs) == 1
        txt = weekly_msgs[0]["text"]
        assert "2026-W49" in txt
        assert "Az veri" in txt  # only 3 decided (<5), no fake percentage!
        assert "2.40" in txt
        assert "Premier League" in txt
        assert "1/1" in txt
        assert weekly_msgs[0]["reply_markup"]["inline_keyboard"][0][0]["url"].endswith("/?tab=performance")

        # Idempotent re-run must NOT send weekly report again
        calls.clear()
        res2 = telegram_bot.dispatch_telegram_settlements(db)
        assert res2["weekly_report"] is False
        assert not any("HAFTANIN ŞEFFAF BİLANÇOSU" in x.get("text", "") for x in calls)

    print("TEST_TG5_WEEKLY_REPORT: PASS")


def test_tg6_watchdog_cooldown():
    db = _make_session()
    now_utc = datetime.now(timezone.utc)

    # Seed low credit + 5 VOIDs in last 24h
    db.add(ScraperCache(cache_key="odds_api:remaining_credits", payload_json='{"remaining": 30, "used": 470}'))
    for i in range(5):
        mt = Match(
            iddaa_id=f"tg_void_{i}", league="Serie A", home_team=f"VH{i}", away_team=f"VA{i}",
            start_time_utc=now_utc - timedelta(hours=3), status="VOID",
        )
        db.add(mt)
        db.flush()
        db.add(Prediction(
            match_id=mt.id, market_key="MS", selection_key="1",
            iddaa_odd=1.90, confidence_score=65.0, is_top_pick=True, status="VOID", settled_at=now_utc - timedelta(hours=1),
        ))
    db.commit()

    calls = []
    def fake_post(self, url, json=None):
        calls.append(json)
        return _FakeResponse(200, {"ok": True, "result": {"message_id": 930 + len(calls)}})

    with patch.object(settings, "telegram_bot_token", "fake-token"), \
         patch.object(settings, "telegram_admin_chat_id", "999888777"), \
         patch.object(settings, "telegram_low_credit_threshold", 50), \
         patch("httpx.Client.post", new=fake_post), \
         patch("app.telegram_bot.time.sleep"):
        res1 = telegram_bot.check_and_send_health_alerts(db)
        assert set(res1["alerts_sent"]) == {"low_credit", "high_void"}
        assert all(c["chat_id"] == "999888777" and "message_thread_id" not in c for c in calls)

        # Second run same day -> cooldown blocks duplicates
        calls.clear()
        res2 = telegram_bot.check_and_send_health_alerts(db)
        assert res2["alerts_sent"] == []
        assert len(calls) == 0

    # Network error must never raise
    def fake_err(self, url, json=None):
        raise httpx.ConnectError("offline")

    db.add(SyncLog(status="ERROR", message="Simulated failure"))
    db.commit()
    with patch.object(settings, "telegram_bot_token", "fake-token"), \
         patch.object(settings, "telegram_admin_chat_id", "999888777"), \
         patch("httpx.Client.post", new=fake_err):
        res3 = telegram_bot.check_and_send_health_alerts(db)
        assert res3["enabled"] is True

    print("TEST_TG6_WATCHDOG_COOLDOWN: PASS")


def test_tg7_webhook_commands():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    db = TestingSession()

    now = datetime.now(timezone.utc)
    mt = Match(
        iddaa_id="tg_wh_1", league="La Liga", home_team="Barcelona", away_team="Valencia",
        start_time_utc=now + timedelta(hours=3), status="SCHEDULED",
    )
    db.add(mt)
    db.flush()
    p = Prediction(
        match_id=mt.id, market_key="MS", selection_key="1",
        iddaa_odd=1.55, published_odd=1.55, confidence_score=81.0, published_confidence=81.0,
        is_top_pick=True, status="PENDING",
    )
    db.add(p)
    db.flush()
    c = Coupon(
        coupon_date=now.astimezone(telegram_bot.TR_TZ).date().isoformat(),
        tier="BANKO", total_odd=1.55, avg_confidence=81.0, status="PENDING", is_locked=False,
    )
    db.add(c)
    db.flush()
    db.add(CouponItem(coupon_id=c.id, prediction_id=p.id, odd_at_pick=1.55))
    db.commit()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = override_get_db
    client = TestClient(fastapi_app)

    calls = []
    orig_post = httpx.Client.post

    def fake_post(self, url, *args, **kwargs):
        if "api.telegram.org" not in str(url):
            return orig_post(self, url, *args, **kwargs)
        calls.append(kwargs.get("json"))
        return _FakeResponse(200, {"ok": True, "result": {"message_id": 950 + len(calls)}})

    try:
        with patch.object(settings, "telegram_bot_token", "fake-token"), \
             patch.object(settings, "telegram_webhook_secret", "super-secret-wh"), \
             patch("httpx.Client.post", new=fake_post), \
             patch("app.telegram_bot.time.sleep"):

            # 1. Wrong secret -> 403
            r_bad = client.post(
                "/api/telegram/webhook",
                json={"message": {"chat": {"id": 111, "type": "private"}, "text": "/bugun"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            )
            assert r_bad.status_code == 403

            # 2. Group / topic message -> ignored with 200 OK and 0 sends
            r_grp = client.post(
                "/api/telegram/webhook",
                json={"message": {"chat": {"id": -100123, "type": "supergroup"}, "message_thread_id": 11, "text": "/bugun"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "super-secret-wh"},
            )
            assert r_grp.status_code == 200
            assert r_grp.json().get("ignored") is True
            assert len(calls) == 0

            # 3. Private /bugun, /kuponlar, /karne
            for cmd in ["/bugun", "/kuponlar", "/karne"]:
                r_ok = client.post(
                    "/api/telegram/webhook",
                    json={"message": {"chat": {"id": 555111, "type": "private"}, "text": cmd}},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "super-secret-wh"},
                )
                assert r_ok.status_code == 200
                assert r_ok.json().get("command") == cmd

            assert len(calls) == 3
            assert "Barcelona - Valencia" in calls[0]["text"]
            assert f"/?match={mt.id}" in calls[0]["text"]
            assert "BANKO KUPON" in calls[1]["text"]
            assert "KARNE" in calls[2]["text"]
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()

    print("TEST_TG7_WEBHOOK_COMMANDS: PASS")


if __name__ == "__main__":
    test_tg1_idempotent_no_spam()
    test_tg2_settle_reply_and_daily_report()
    test_tg3_failsafe_offline()
    test_tg4_deep_links()
    test_tg5_weekly_report()
    test_tg6_watchdog_cooldown()
    test_tg7_webhook_commands()
    print("ALL_TELEGRAM_TESTS: PASS")
