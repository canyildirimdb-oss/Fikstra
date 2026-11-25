"""Isolated unit tests for the Fikstra Telegram Topics dispatch engine."""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(__file__))

from app.config import settings
from app.database import Base
from app.models import Coupon, CouponItem, Match, Prediction, TelegramDispatch
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


if __name__ == "__main__":
    test_tg1_idempotent_no_spam()
    test_tg2_settle_reply_and_daily_report()
    test_tg3_failsafe_offline()
    print("ALL_TELEGRAM_TESTS: PASS")
