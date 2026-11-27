"""Autonomous Telegram Topics dispatch engine for Fikstra."""

import hashlib
import html
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models import Coupon, CouponItem, Match, Prediction, ScraperCache, SportotoWeek, SyncLog, TelegramDispatch
from app.sportoto_engine import serialize_sportoto_week
from utils.formatting import format_pick_tr

logger = logging.getLogger("fikstra.telegram")

TR_TZ = timezone(timedelta(hours=3))
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_INVITE_URL = "https://t.me/+yVkUre9BnAo3ZmM0"
REQUEST_TIMEOUT = 8.0
MESSAGE_PACING_SECONDS = 0.35


class TelegramRateLimitError(Exception):
    """Raised when Telegram returns HTTP 429 so dispatch loops can stop cleanly."""

    def __init__(self, retry_after: int = 5):
        super().__init__(f"Telegram rate limited (retry_after={retry_after}s)")
        self.retry_after = retry_after


def _parse_topic_id(raw: Optional[str]) -> Optional[int]:
    val = (raw or "").strip()
    if not val:
        return None
    try:
        return int(val)
    except ValueError:
        return None


def _is_enabled() -> bool:
    return bool((settings.telegram_bot_token or "").strip() and (settings.telegram_chat_id or "").strip())


def _public_url() -> str:
    url = (settings.telegram_public_url or "https://fikstra.onrender.com").strip()
    return url.rstrip("/") or "https://fikstra.onrender.com"


def _compute_hash(text: str, reply_markup: Optional[dict[str, Any]] = None) -> str:
    payload = json.dumps({"text": text, "reply_markup": reply_markup or {}}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _format_tr_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "--.-- --:--"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tr_dt = dt.astimezone(TR_TZ)
    return tr_dt.strftime("%d.%m %H:%M")


def _format_pick_label(market_key: str, selection_key: str, home_team: str = "", away_team: str = "") -> str:
    mk = (market_key or "").upper()
    sk = (selection_key or "").upper()
    if mk == "MS":
        if sk == "1" and home_team:
            return f"{home_team} · MS 1"
        if sk == "2" and away_team:
            return f"{away_team} · MS 2"
        return f"MS {sk}"
    if mk.startswith("AU_"):
        return format_pick_tr(mk, sk)
    if mk == "KG":
        return "KG Var" if sk == "VAR" else "KG Yok"
    if mk == "CS":
        return f"ÇŞ {sk}"
    return f"{market_key} {selection_key}".strip()


def _tier_header(tier: str) -> str:
    t = (tier or "").upper()
    if t == "BANKO":
        return "🛡 <b>BANKO KUPON</b>"
    if t == "DENGELI":
        return "⚖️ <b>DENGELİ KUPON</b>"
    if t == "SURPRIZ":
        return "🚀 <b>SÜRPRİZ KUPON</b>"
    return f"🎟️ <b>{html.escape(t)} KUPON</b>"


def _status_badge(status: str) -> str:
    s = (status or "").upper()
    if s == "WON":
        return "✅ KAZANDI"
    if s == "LOST":
        return "❌ KAYBETTİ"
    if s == "VOID":
        return "⚪ İPTAL"
    return f"⏳ {html.escape(s)}"


def _inline_link_markup(label: str = "Fikstra'da İncele", path: str = "") -> dict[str, Any]:
    base = _public_url()
    url = f"{base}{path}" if path else base
    return {"inline_keyboard": [[{"text": label, "url": url}]]}


def _tg_call(method: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        return None
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(url, json=payload)
        if resp.status_code == 429:
            retry_after = 5
            try:
                retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
            except Exception:
                pass
            raise TelegramRateLimitError(retry_after=retry_after)
        data = resp.json()
        if not resp.is_success or not data.get("ok"):
            logger.warning("Telegram %s failed (%s): %s", method, resp.status_code, data.get("description", resp.text))
            return None
        return data.get("result")
    except TelegramRateLimitError:
        raise
    except Exception as exc:
        logger.warning("Telegram %s exception: %s", method, exc)
        return None


def _send_message(
    chat_id: str,
    text: str,
    thread_id: Optional[int] = None,
    reply_to_message_id: Optional[int] = None,
    reply_markup: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    result = _tg_call("sendMessage", payload)
    if isinstance(result, dict) and result.get("message_id"):
        time.sleep(MESSAGE_PACING_SECONDS)
        return int(result["message_id"])
    return None


def _edit_message_text(
    chat_id: str,
    message_id: int,
    text: str,
    reply_markup: Optional[dict[str, Any]] = None,
) -> bool:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    result = _tg_call("editMessageText", payload)
    if result is not None:
        time.sleep(MESSAGE_PACING_SECONDS)
        return True
    return False


def _send_poll(
    chat_id: str,
    question: str,
    options: list[str],
    thread_id: Optional[int] = None,
) -> Optional[int]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "question": question[:300],
        "options": [{"text": opt[:100]} for opt in options[:10]],
        "is_anonymous": True,
    }
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    result = _tg_call("sendPoll", payload)
    if isinstance(result, dict) and result.get("message_id"):
        time.sleep(MESSAGE_PACING_SECONDS)
        return int(result["message_id"])
    return None


def dispatch_daily_chat_poll(db: Session) -> dict[str, Any]:
    """Send 1 daily native match poll to TELEGRAM_TOPIC_CHAT after 11:00 TR."""
    if not _is_enabled():
        return {"enabled": False, "sent": False}

    chat_topic = _parse_topic_id(settings.telegram_topic_chat)
    if chat_topic is None:
        return {"enabled": True, "sent": False, "reason": "no_chat_topic"}

    now_utc = datetime.now(timezone.utc)
    now_tr = now_utc.astimezone(TR_TZ)
    if (now_tr.hour, now_tr.minute) < (11, 0):
        return {"enabled": True, "sent": False, "reason": "before_11_tr"}

    today_tr = now_tr.date().isoformat()
    dkey = f"daily_poll:{today_tr}"
    if db.query(TelegramDispatch.id).filter(TelegramDispatch.dispatch_key == dkey).first():
        return {"enabled": True, "sent": False, "reason": "already_sent"}

    try:
        _, day_end_utc = _tr_day_bounds_utc(now_utc)
        cutoff = now_utc + timedelta(minutes=60)
        eff_conf = func.coalesce(Prediction.published_confidence, Prediction.confidence_score)
        top_pred = (
            db.query(Prediction)
            .options(joinedload(Prediction.match))
            .join(Match, Prediction.match_id == Match.id)
            .filter(
                Match.status == "SCHEDULED",
                Match.start_time_utc >= cutoff,
                Match.start_time_utc < day_end_utc,
                Prediction.status == "PENDING",
                Prediction.is_top_pick.is_(True),
            )
            .order_by(eff_conf.desc(), Match.start_time_utc.asc())
            .first()
        )
        if not top_pred or not top_pred.match:
            return {"enabled": True, "sent": False, "reason": "no_match"}

        m = top_pred.match
        chat_id = settings.telegram_chat_id.strip()
        question = f"📊 Günün Maçı Anketi: {m.home_team} - {m.away_team}"
        options = [f"{m.home_team} Kazanır", "Beraberlik", f"{m.away_team} Kazanır"]
        msg_id = _send_poll(chat_id, question, options, thread_id=chat_topic)
        if msg_id:
            db.add(
                TelegramDispatch(
                    dispatch_key=dkey,
                    kind="daily_poll",
                    chat_id=chat_id,
                    thread_id=chat_topic,
                    message_id=msg_id,
                    content_hash=_compute_hash(question),
                    settled_replied=True,
                )
            )
            db.commit()
            return {"enabled": True, "sent": True, "message_id": msg_id}
    except TelegramRateLimitError:
        logger.warning("Telegram daily poll stopped due to HTTP 429")
    except Exception as exc:
        logger.warning("Telegram daily poll error: %s", exc)

    return {"enabled": True, "sent": False}


def _render_coupon_message(coupon: Coupon) -> tuple[str, dict[str, Any]]:
    lock_tag = "🔒 Kilitlendi" if coupon.is_locked else "🟢 Aktif"
    date_str = coupon.coupon_date
    try:
        parts = coupon.coupon_date.split("-")
        if len(parts) == 3:
            date_str = f"{parts[2]}.{parts[1]}"
    except Exception:
        pass

    lines = [
        f"{_tier_header(coupon.tier)} · <code>{html.escape(date_str)}</code> · {lock_tag}",
        "",
    ]
    sorted_items = sorted(
        coupon.items,
        key=lambda it: (
            it.prediction.match.start_time_utc
            if it.prediction and it.prediction.match and it.prediction.match.start_time_utc
            else datetime.max.replace(tzinfo=timezone.utc)
        ),
    )
    for idx, item in enumerate(sorted_items, start=1):
        pred = item.prediction
        if not pred or not pred.match:
            continue
        m = pred.match
        odd_val = item.odd_at_pick or pred.published_odd or pred.iddaa_odd
        pick_lbl = _format_pick_label(pred.market_key, pred.selection_key, m.home_team, m.away_team)
        lines.append(
            f"{idx}. <b>{html.escape(m.home_team)} - {html.escape(m.away_team)}</b>\n"
            f"   🕒 {_format_tr_dt(m.start_time_utc)} | 🎯 <b>{html.escape(pick_lbl)}</b> (<code>{odd_val:.2f}</code>)"
        )

    lines.append("")
    lines.append(
        f"📊 Toplam Oran: <b>{coupon.total_odd:.2f}</b> · Ort. Güven: <b>%{coupon.avg_confidence:.0f}</b>"
    )
    return "\n".join(lines), _inline_link_markup("Fikstra'da İncele", "/?tab=coupons")


def _render_single_message(pred: Prediction) -> tuple[str, dict[str, Any]]:
    m = pred.match
    conf = pred.published_confidence if pred.published_confidence is not None else pred.confidence_score
    odd_val = pred.published_odd if pred.published_odd is not None else pred.iddaa_odd
    pick_lbl = _format_pick_label(pred.market_key, pred.selection_key, m.home_team, m.away_team)
    text = (
        f"🔥 <b>YÜKSEK GÜVENLİ TEKLİ</b>\n\n"
        f"🏆 {html.escape(m.league)}\n"
        f"⚽ <b>{html.escape(m.home_team)} - {html.escape(m.away_team)}</b>\n"
        f"🕒 {_format_tr_dt(m.start_time_utc)}\n\n"
        f"🎯 Tercih: <b>{html.escape(pick_lbl)}</b>\n"
        f"💰 Oran: <b>{odd_val:.2f}</b> · Güven: <b>%{conf:.0f}</b>"
    )
    return text, _inline_link_markup("Fikstra'da İncele", f"/?match={pred.match_id}")


def _render_value_message(pred: Prediction) -> tuple[str, dict[str, Any]]:
    m = pred.match
    conf = pred.published_confidence if pred.published_confidence is not None else pred.confidence_score
    odd_val = pred.published_odd if pred.published_odd is not None else pred.iddaa_odd
    pick_lbl = _format_pick_label(pred.market_key, pred.selection_key, m.home_team, m.away_team)

    signals: list[str] = []
    if pred.odds_drop_pct is not None and pred.odds_drop_pct >= 7.0:
        signals.append(f"📉 Oran Düşüşü: <b>%{pred.odds_drop_pct:.1f}</b>")
    if pred.value_edge_pct is not None and pred.value_edge_pct >= 6.0:
        signals.append(f"💎 Global Avantaj: <b>+%{pred.value_edge_pct:.1f}</b>")
    signal_block = "\n".join(signals) if signals else "💎 Değer Fırsatı"

    text = (
        f"📉 <b>AKILLI PARA &amp; DEĞER SİNYALİ</b>\n\n"
        f"🏆 {html.escape(m.league)}\n"
        f"⚽ <b>{html.escape(m.home_team)} - {html.escape(m.away_team)}</b>\n"
        f"🕒 {_format_tr_dt(m.start_time_utc)}\n\n"
        f"🎯 Tercih: <b>{html.escape(pick_lbl)}</b>\n"
        f"💰 Oran: <b>{odd_val:.2f}</b> · Güven: <b>%{conf:.0f}</b>\n"
        f"{signal_block}"
    )
    return text, _inline_link_markup("Fikstra'da İncele", f"/?match={pred.match_id}")


def _tr_day_bounds_utc(now_utc: datetime) -> tuple[datetime, datetime]:
    tr_now = now_utc.astimezone(TR_TZ)
    start_tr = tr_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_tr = start_tr + timedelta(days=1)
    return start_tr.astimezone(timezone.utc), end_tr.astimezone(timezone.utc)


def _count_dispatched_today(db: Session, kind: str, now_utc: datetime) -> int:
    start_utc, end_utc = _tr_day_bounds_utc(now_utc)
    return (
        db.query(TelegramDispatch)
        .filter(
            TelegramDispatch.kind == kind,
            TelegramDispatch.created_at >= start_utc,
            TelegramDispatch.created_at < end_utc,
        )
        .count()
    )


def dispatch_telegram_updates(db: Session) -> dict[str, Any]:
    """Send/edit coupons, high-confidence singles, and smart-money value alerts."""
    if not _is_enabled():
        return {"enabled": False, "coupons": 0, "singles": 0, "value": 0}

    chat_id = settings.telegram_chat_id.strip()
    now_utc = datetime.now(timezone.utc)
    stats = {"enabled": True, "coupons_sent": 0, "coupons_edited": 0, "singles": 0, "value": 0, "rate_limited": False}

    try:
        # 1. Günün Kuponları
        coupons_topic = _parse_topic_id(settings.telegram_topic_coupons)
        if coupons_topic is not None:
            tier_rank = {"BANKO": 0, "DENGELI": 1, "SURPRIZ": 2}
            pending_coupons = (
                db.query(Coupon)
                .options(joinedload(Coupon.items).joinedload(CouponItem.prediction).joinedload(Prediction.match))
                .filter(Coupon.status == "PENDING")
                .order_by(Coupon.coupon_date.desc())
                .all()
            )
            pending_coupons.sort(key=lambda c: (c.coupon_date, -tier_rank.get(c.tier, 9)), reverse=True)

            for coupon in pending_coupons:
                if not coupon.items:
                    continue
                dkey = f"coupon:{coupon.id}"
                text, markup = _render_coupon_message(coupon)
                chash = _compute_hash(text, markup)
                existing = db.query(TelegramDispatch).filter(TelegramDispatch.dispatch_key == dkey).first()

                if existing:
                    if existing.content_hash == chash:
                        continue
                    if _edit_message_text(existing.chat_id, existing.message_id, text, reply_markup=markup):
                        existing.content_hash = chash
                        existing.updated_at = now_utc
                        db.commit()
                        stats["coupons_edited"] += 1
                else:
                    msg_id = _send_message(chat_id, text, thread_id=coupons_topic, reply_markup=markup)
                    if msg_id:
                        db.add(
                            TelegramDispatch(
                                dispatch_key=dkey,
                                kind="coupon",
                                chat_id=chat_id,
                                thread_id=coupons_topic,
                                message_id=msg_id,
                                content_hash=chash,
                            )
                        )
                        db.commit()
                        stats["coupons_sent"] += 1

        # 2. Yüksek Güvenli Tekliler
        singles_topic = _parse_topic_id(settings.telegram_topic_singles)
        if singles_topic is not None:
            sent_today = _count_dispatched_today(db, "single", now_utc)
            remaining = max(0, int(settings.telegram_single_daily_cap) - sent_today)
            if remaining > 0:
                eff_conf = func.coalesce(Prediction.published_confidence, Prediction.confidence_score)
                cutoff = now_utc + timedelta(minutes=15)
                candidates = (
                    db.query(Prediction)
                    .options(joinedload(Prediction.match))
                    .join(Match, Prediction.match_id == Match.id)
                    .filter(
                        Match.status == "SCHEDULED",
                        Match.start_time_utc >= cutoff,
                        Prediction.status == "PENDING",
                        Prediction.is_top_pick.is_(True),
                        eff_conf >= float(settings.telegram_single_min_conf),
                    )
                    .order_by(eff_conf.desc(), Match.start_time_utc.asc())
                    .all()
                )
                for pred in candidates:
                    if stats["singles"] >= remaining:
                        break
                    dkey = f"single:{pred.id}"
                    if db.query(TelegramDispatch.id).filter(TelegramDispatch.dispatch_key == dkey).first():
                        continue
                    text, markup = _render_single_message(pred)
                    msg_id = _send_message(chat_id, text, thread_id=singles_topic, reply_markup=markup)
                    if msg_id:
                        db.add(
                            TelegramDispatch(
                                dispatch_key=dkey,
                                kind="single",
                                chat_id=chat_id,
                                thread_id=singles_topic,
                                message_id=msg_id,
                                content_hash=_compute_hash(text, markup),
                            )
                        )
                        db.commit()
                        stats["singles"] += 1

        # 3. Akıllı Para / Değer
        value_topic = _parse_topic_id(settings.telegram_topic_value)
        if value_topic is not None:
            sent_today = _count_dispatched_today(db, "value", now_utc)
            remaining = max(0, int(settings.telegram_value_daily_cap) - sent_today)
            if remaining > 0:
                eff_conf = func.coalesce(Prediction.published_confidence, Prediction.confidence_score)
                candidates = (
                    db.query(Prediction)
                    .options(joinedload(Prediction.match))
                    .join(Match, Prediction.match_id == Match.id)
                    .filter(
                        Match.status == "SCHEDULED",
                        Match.start_time_utc > now_utc,
                        Prediction.status == "PENDING",
                        Prediction.is_top_pick.is_(True),
                        eff_conf >= 65.0,
                    )
                    .order_by(
                        func.coalesce(Prediction.odds_drop_pct, 0.0).desc(),
                        func.coalesce(Prediction.value_edge_pct, 0.0).desc(),
                    )
                    .all()
                )
                for pred in candidates:
                    if stats["value"] >= remaining:
                        break
                    has_drop = pred.odds_drop_pct is not None and pred.odds_drop_pct >= 7.0
                    has_edge = pred.value_edge_pct is not None and pred.value_edge_pct >= 6.0
                    if not (has_drop or has_edge):
                        continue
                    dkey = f"value:{pred.id}"
                    if db.query(TelegramDispatch.id).filter(TelegramDispatch.dispatch_key == dkey).first():
                        continue
                    text, markup = _render_value_message(pred)
                    msg_id = _send_message(chat_id, text, thread_id=value_topic, reply_markup=markup)
                    if msg_id:
                        db.add(
                            TelegramDispatch(
                                dispatch_key=dkey,
                                kind="value",
                                chat_id=chat_id,
                                thread_id=value_topic,
                                message_id=msg_id,
                                content_hash=_compute_hash(text, markup),
                            )
                        )
                        db.commit()
                        stats["value"] += 1

    except TelegramRateLimitError:
        stats["rate_limited"] = True
        logger.warning("Telegram dispatch stopped early due to HTTP 429 rate limit")
    except Exception as exc:
        logger.warning("Telegram dispatch_telegram_updates error: %s", exc)

    if not stats.get("rate_limited"):
        try:
            poll_res = dispatch_daily_chat_poll(db)
            stats["daily_poll"] = bool(poll_res.get("sent"))
        except Exception:
            pass

    return stats


def _render_coupon_settlement_reply(coupon: Coupon) -> str:
    lines = [
        f"{_tier_header(coupon.tier)} · <b>{_status_badge(coupon.status)}</b>",
        "",
    ]
    for idx, item in enumerate(coupon.items, start=1):
        pred = item.prediction
        if not pred or not pred.match:
            continue
        m = pred.match
        score = f"{m.home_score}-{m.away_score}" if m.home_score is not None and m.away_score is not None else "-"
        lines.append(
            f"{idx}. {html.escape(m.home_team)} <b>{score}</b> {html.escape(m.away_team)} · {_status_badge(pred.status)}"
        )
    lines.append("")
    lines.append(f"📊 Kupon Oranı: <b>{coupon.total_odd:.2f}</b>")
    return "\n".join(lines)


def dispatch_telegram_settlements(db: Session) -> dict[str, Any]:
    """Reply to settled coupons/singles, showcase WON coupons, and post daily report after 01:30 TR."""
    if not _is_enabled():
        return {"enabled": False, "coupon_replies": 0, "single_replies": 0, "daily_report": False}

    chat_id = settings.telegram_chat_id.strip()
    results_topic = _parse_topic_id(settings.telegram_topic_results)
    now_utc = datetime.now(timezone.utc)
    stats = {"enabled": True, "coupon_replies": 0, "single_replies": 0, "won_showcases": 0, "daily_report": False, "weekly_report": False, "rate_limited": False}

    try:
        # 1. Settled coupon replies
        coupon_dispatches = (
            db.query(TelegramDispatch)
            .filter(TelegramDispatch.kind == "coupon", TelegramDispatch.settled_replied.is_(False))
            .all()
        )
        for disp in coupon_dispatches:
            try:
                coupon_id = int(disp.dispatch_key.split(":", 1)[1])
            except (IndexError, ValueError):
                continue
            coupon = (
                db.query(Coupon)
                .options(joinedload(Coupon.items).joinedload(CouponItem.prediction).joinedload(Prediction.match))
                .filter(Coupon.id == coupon_id)
                .first()
            )
            if not coupon or coupon.status not in {"WON", "LOST", "VOID"}:
                continue

            reply_text = _render_coupon_settlement_reply(coupon)
            msg_id = _send_message(
                disp.chat_id,
                reply_text,
                thread_id=disp.thread_id,
                reply_to_message_id=disp.message_id,
            )
            if msg_id:
                disp.settled_replied = True
                disp.updated_at = now_utc
                db.commit()
                stats["coupon_replies"] += 1

                if coupon.status == "WON" and results_topic is not None:
                    showcase_key = f"coupon_won:{coupon.id}"
                    if not db.query(TelegramDispatch.id).filter(TelegramDispatch.dispatch_key == showcase_key).first():
                        showcase_text = (
                            f"🏆 <b>KAZANAN KUPON VİTRİNİ</b>\n\n"
                            f"{reply_text}"
                        )
                        s_msg_id = _send_message(
                            chat_id,
                            showcase_text,
                            thread_id=results_topic,
                            reply_markup=_inline_link_markup("Kuponları İncele", "/?tab=coupons"),
                        )
                        if s_msg_id:
                            db.add(
                                TelegramDispatch(
                                    dispatch_key=showcase_key,
                                    kind="coupon_won",
                                    chat_id=chat_id,
                                    thread_id=results_topic,
                                    message_id=s_msg_id,
                                    content_hash=_compute_hash(showcase_text),
                                    settled_replied=True,
                                )
                            )
                            db.commit()
                            stats["won_showcases"] += 1

        # 2. Settled single replies
        single_dispatches = (
            db.query(TelegramDispatch)
            .filter(TelegramDispatch.kind == "single", TelegramDispatch.settled_replied.is_(False))
            .all()
        )
        for disp in single_dispatches:
            try:
                pred_id = int(disp.dispatch_key.split(":", 1)[1])
            except (IndexError, ValueError):
                continue
            pred = (
                db.query(Prediction)
                .options(joinedload(Prediction.match))
                .filter(Prediction.id == pred_id)
                .first()
            )
            if not pred or not pred.match or pred.status not in {"WON", "LOST", "VOID"}:
                continue
            m = pred.match
            score = f"{m.home_score}-{m.away_score}" if m.home_score is not None and m.away_score is not None else "-"
            pick_lbl = _format_pick_label(pred.market_key, pred.selection_key, m.home_team, m.away_team)
            reply_text = (
                f"{_status_badge(pred.status)} · <b>{html.escape(m.home_team)} {score} {html.escape(m.away_team)}</b>\n"
                f"🎯 {html.escape(pick_lbl)}"
            )
            msg_id = _send_message(
                disp.chat_id,
                reply_text,
                thread_id=disp.thread_id,
                reply_to_message_id=disp.message_id,
            )
            if msg_id:
                disp.settled_replied = True
                disp.updated_at = now_utc
                db.commit()
                stats["single_replies"] += 1

        # 3. Daily Report after 01:30 TR for yesterday_tr
        now_tr = now_utc.astimezone(TR_TZ)
        if results_topic is not None and (now_tr.hour, now_tr.minute) >= (1, 30):
            yesterday_date = (now_tr - timedelta(days=1)).date()
            yesterday_str = yesterday_date.isoformat()
            report_key = f"daily_report:{yesterday_str}"
            if not db.query(TelegramDispatch.id).filter(TelegramDispatch.dispatch_key == report_key).first():
                y_start_tr = datetime(yesterday_date.year, yesterday_date.month, yesterday_date.day, tzinfo=TR_TZ)
                y_end_tr = y_start_tr + timedelta(days=1)
                y_start_utc = y_start_tr.astimezone(timezone.utc)
                y_end_utc = y_end_tr.astimezone(timezone.utc)

                eff_conf = func.coalesce(Prediction.published_confidence, Prediction.confidence_score)
                has_pending_y = (
                    db.query(Prediction.id)
                    .join(Match, Prediction.match_id == Match.id)
                    .filter(
                        Match.start_time_utc >= y_start_utc,
                        Match.start_time_utc < y_end_utc,
                        Prediction.is_top_pick.is_(True),
                        eff_conf >= 60.0,
                        Prediction.status == "PENDING",
                    )
                    .first()
                ) is not None
                from app.routers.api import _get_official_settled_predictions
                official_settled = _get_official_settled_predictions(db, include_void=True, order_asc=False)
                y_preds = []
                for p in official_settled:
                    if not p.match or not p.match.start_time_utc:
                        continue
                    ms = p.match.start_time_utc
                    if ms.tzinfo is None:
                        ms = ms.replace(tzinfo=timezone.utc)
                    if y_start_utc <= ms < y_end_utc:
                        y_preds.append(p)
                if y_preds and not has_pending_y:
                    won = sum(1 for p in y_preds if p.status == "WON")
                    lost = sum(1 for p in y_preds if p.status == "LOST")
                    void = sum(1 for p in y_preds if p.status == "VOID")
                    decided = won + lost
                    win_rate = (won / decided * 100.0) if decided > 0 else 0.0
                    date_fmt = yesterday_date.strftime("%d.%m.%Y")
                    report_text = (
                        f"📊 <b>GÜNLÜK KARNE · {date_fmt}</b>\n"
                        f"<i>60+ Resmi Tahmin Performansı</i>\n\n"
                        f"✅ Kazanan: <b>{won}</b>\n"
                        f"❌ Kaybeden: <b>{lost}</b>\n"
                        + (f"⚪ İptal: <b>{void}</b>\n" if void > 0 else "")
                        + f"\n📈 Başarı Oranı: <b>%{win_rate:.1f}</b> ({won}/{decided})"
                    )
                    msg_id = _send_message(
                        chat_id,
                        report_text,
                        thread_id=results_topic,
                        reply_markup=_inline_link_markup("Tüm Performansı Gör", "/?tab=performance"),
                    )
                    if msg_id:
                        db.add(
                            TelegramDispatch(
                                dispatch_key=report_key,
                                kind="daily_report",
                                chat_id=chat_id,
                                thread_id=results_topic,
                                message_id=msg_id,
                                content_hash=_compute_hash(report_text),
                                settled_replied=True,
                            )
                        )
                        db.commit()
                        stats["daily_report"] = True

        # 4. Weekly Transparent Balance on Monday after 09:00 TR
        if results_topic is not None and now_tr.weekday() == 0 and (now_tr.hour, now_tr.minute) >= (9, 0):
            iso_year, iso_week, _ = now_tr.isocalendar()
            weekly_key = f"weekly_report:{iso_year}-W{iso_week:02d}"
            if not db.query(TelegramDispatch.id).filter(TelegramDispatch.dispatch_key == weekly_key).first():
                week_end_tr = now_tr.replace(hour=0, minute=0, second=0, microsecond=0)
                week_start_tr = week_end_tr - timedelta(days=7)
                w_start_utc = week_start_tr.astimezone(timezone.utc)
                w_end_utc = week_end_tr.astimezone(timezone.utc)

                from app.routers.api import _get_official_settled_predictions
                official_settled = _get_official_settled_predictions(db, include_void=True, order_asc=False)
                w_preds = []
                for p in official_settled:
                    if not p.match or not p.match.start_time_utc:
                        continue
                    ms = p.match.start_time_utc
                    if ms.tzinfo is None:
                        ms = ms.replace(tzinfo=timezone.utc)
                    if w_start_utc <= ms < w_end_utc:
                        w_preds.append(p)
                w_won = [p for p in w_preds if p.status == "WON"]
                w_lost = [p for p in w_preds if p.status == "LOST"]
                w_decided = len(w_won) + len(w_lost)

                if w_decided > 0:
                    if w_decided < 5:
                        rate_line = f"📈 Başarı: <b>{len(w_won)}/{w_decided} · Az veri</b>"
                    else:
                        w_rate = len(w_won) / w_decided * 100.0
                        rate_line = f"📈 Başarı Oranı: <b>%{w_rate:.1f}</b> ({len(w_won)}/{w_decided})"

                    lines = [
                        f"📅 <b>HAFTANIN ŞEFFAF BİLANÇOSU · {iso_year}-W{iso_week:02d}</b>",
                        f"<i>{week_start_tr.strftime('%d.%m')} – {(week_end_tr - timedelta(days=1)).strftime('%d.%m.%Y')} · 60+ Resmi Tahmin</i>",
                        "",
                        f"✅ Kazanan: <b>{len(w_won)}</b> · ❌ Kaybeden: <b>{len(w_lost)}</b>",
                        rate_line,
                    ]

                    if w_won:
                        best_pred = max(
                            w_won,
                            key=lambda p: (p.published_odd if p.published_odd is not None else p.iddaa_odd) or 0.0,
                        )
                        best_odd = best_pred.published_odd if best_pred.published_odd is not None else best_pred.iddaa_odd
                        bm = best_pred.match
                        if bm and best_odd:
                            b_pick = _format_pick_label(best_pred.market_key, best_pred.selection_key, bm.home_team, bm.away_team)
                            lines.append(
                                f"🏅 Haftanın En Yüksek Oranı: <b>{html.escape(bm.home_team)} - {html.escape(bm.away_team)}</b> "
                                f"({html.escape(b_pick)} · <code>{best_odd:.2f}</code>)"
                            )

                    league_stats: dict[str, list[int]] = {}
                    for p in w_preds:
                        if p.status not in {"WON", "LOST"} or not p.match:
                            continue
                        bucket = league_stats.setdefault(p.match.league, [0, 0])
                        bucket[1] += 1
                        if p.status == "WON":
                            bucket[0] += 1
                    qualified_leagues = [(lg, cnt[0], cnt[1]) for lg, cnt in league_stats.items() if cnt[1] >= 3]
                    if qualified_leagues:
                        top_lg, lg_won, lg_tot = max(qualified_leagues, key=lambda x: (x[1] / x[2], x[2]))
                        lines.append(
                            f"🏆 En İsabetli Lig: <b>{html.escape(top_lg)}</b> (%{lg_won / lg_tot * 100.0:.0f} · {lg_won}/{lg_tot})"
                        )

                    w_start_date = week_start_tr.date().isoformat()
                    w_end_date = week_end_tr.date().isoformat()
                    w_coupons = (
                        db.query(Coupon)
                        .filter(
                            Coupon.coupon_date >= w_start_date,
                            Coupon.coupon_date < w_end_date,
                            Coupon.status.in_(["WON", "LOST"]),
                        )
                        .all()
                    )
                    if w_coupons:
                        c_won = sum(1 for c in w_coupons if c.status == "WON")
                        lines.append(f"🎟️ Haftalık Kupon Bilançosu: <b>{c_won}/{len(w_coupons)}</b>")

                    weekly_text = "\n".join(lines)
                    w_msg_id = _send_message(
                        chat_id,
                        weekly_text,
                        thread_id=results_topic,
                        reply_markup=_inline_link_markup("Tüm Performansı Gör", "/?tab=performance"),
                    )
                    if w_msg_id:
                        db.add(
                            TelegramDispatch(
                                dispatch_key=weekly_key,
                                kind="weekly_report",
                                chat_id=chat_id,
                                thread_id=results_topic,
                                message_id=w_msg_id,
                                content_hash=_compute_hash(weekly_text),
                                settled_replied=True,
                            )
                        )
                        db.commit()
                        stats["weekly_report"] = True

    except TelegramRateLimitError:
        stats["rate_limited"] = True
        logger.warning("Telegram settlement dispatch stopped early due to HTTP 429 rate limit")
    except Exception as exc:
        logger.warning("Telegram dispatch_telegram_settlements error: %s", exc)

    return stats


def discover_telegram_topics() -> dict[str, Any]:
    """Inspect recent bot updates to list chat_ids and message_thread_ids (safe when webhook is active)."""
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured", "chats": [], "threads": []}

    webhook_info = _tg_call("getWebhookInfo", {})
    if isinstance(webhook_info, dict) and (webhook_info.get("url") or "").strip():
        return {
            "ok": True,
            "webhook_active": True,
            "webhook_url": webhook_info.get("url"),
            "message": "Webhook aktif olduğu için getUpdates çağrılmadı (409 Conflict önlendi).",
            "chats": [],
            "threads": [],
        }

    updates = _tg_call("getUpdates", {"limit": 100})
    if updates is None:
        return {"ok": False, "error": "getUpdates failed", "chats": [], "threads": []}

    chats: dict[str, dict[str, Any]] = {}
    threads: dict[tuple[str, int], dict[str, Any]] = {}

    for upd in updates:
        msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post")
        if not isinstance(msg, dict):
            continue
        chat = msg.get("chat") or {}
        cid = str(chat.get("id", ""))
        if cid:
            chats[cid] = {
                "chat_id": cid,
                "title": chat.get("title") or chat.get("username") or "",
                "type": chat.get("type") or "",
                "is_forum": bool(chat.get("is_forum")),
            }
        tid = msg.get("message_thread_id")
        if cid and tid is not None:
            key = (cid, int(tid))
            entry = threads.setdefault(key, {"chat_id": cid, "thread_id": int(tid), "samples": []})
            if "forum_topic_created" in msg:
                entry["topic_name"] = msg["forum_topic_created"].get("name")
            txt = (msg.get("text") or "").strip()
            if txt and len(entry["samples"]) < 3:
                entry["samples"].append(txt[:80])

    return {
        "ok": True,
        "updates_count": len(updates),
        "chats": list(chats.values()),
        "threads": list(threads.values()),
    }


def send_telegram_test_messages() -> dict[str, Any]:
    """Send a short verification message to each of the 4 configured Fikstra topics."""
    if not _is_enabled():
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured"}

    chat_id = settings.telegram_chat_id.strip()
    topics = [
        ("coupons", "🎟️ Günün Kuponları", _parse_topic_id(settings.telegram_topic_coupons)),
        ("singles", "🔥 Yüksek Güvenli Tekliler", _parse_topic_id(settings.telegram_topic_singles)),
        ("value", "📉 Akıllı Para / Değer", _parse_topic_id(settings.telegram_topic_value)),
        ("results", "📊 Sonuçlar & Günlük Karne", _parse_topic_id(settings.telegram_topic_results)),
    ]

    results: dict[str, Any] = {}
    all_ok = True
    for key, label, tid in topics:
        if tid is None:
            results[key] = {"ok": False, "error": "Topic ID not configured"}
            all_ok = False
            continue
        text = (
            f"✅ <b>Fikstra Otonom Yayın Testi</b>\n"
            f"Konu: <b>{html.escape(label)}</b>\n"
            f"<i>Bu konu sistem tarafından başarıyla doğrulandı.</i>"
        )
        msg_id = _send_message(chat_id, text, thread_id=tid, reply_markup=_inline_link_markup())
        if msg_id:
            results[key] = {"ok": True, "thread_id": tid, "message_id": msg_id}
        else:
            results[key] = {"ok": False, "thread_id": tid, "error": "sendMessage failed"}
            all_ok = False

    return {"ok": all_ok, "chat_id": chat_id, "topics": results}


def check_and_send_health_alerts(db: Session) -> dict[str, Any]:
    """Send private admin health alerts (low credit, high VOID, AI/Sync) with 1/day cooldown per type."""
    token = (settings.telegram_bot_token or "").strip()
    admin_chat_id = (settings.telegram_admin_chat_id or "").strip()
    if not token or not admin_chat_id:
        return {"enabled": False, "alerts_sent": []}

    now_utc = datetime.now(timezone.utc)
    today_tr = now_utc.astimezone(TR_TZ).date().isoformat()
    sent_alerts: list[str] = []

    try:
        candidates: list[tuple[str, str]] = []

        # 1. Low Odds API credit check
        credit_row = db.query(ScraperCache).filter(ScraperCache.cache_key == "odds_api:remaining_credits").first()
        if credit_row and credit_row.payload_json:
            try:
                rem = json.loads(credit_row.payload_json).get("remaining")
                if rem is not None and int(rem) <= int(settings.telegram_low_credit_threshold):
                    candidates.append(
                        (
                            "low_credit",
                            f"⚠️ Düşük Kredi: The Odds API kalan kredi <b>{int(rem)}</b> (eşik: {int(settings.telegram_low_credit_threshold)}).",
                        )
                    )
            except Exception:
                pass

        # 2. High VOID count in last 24h
        cutoff_24h = now_utc - timedelta(hours=24)
        void_rows = (
            db.query(Prediction.id, Match.void_reason)
            .join(Match, Prediction.match_id == Match.id)
            .filter(
                Prediction.status == "VOID",
                Prediction.is_top_pick.is_(True),
                Prediction.settled_at.isnot(None),
                Prediction.settled_at >= cutoff_24h,
            )
            .all()
        )
        void_count = len(void_rows)
        if void_count >= 5:
            reason_counts: dict[str, int] = {}
            for _, vr in void_rows:
                key = vr or "belirsiz"
                if key.startswith("source_status:"):
                    label = "ertelenen/iptal"
                elif "48h" in key:
                    label = "48s skor bulunamadı"
                else:
                    label = key
                reason_counts[label] = reason_counts.get(label, 0) + 1
            breakdown_str = ", ".join(f"{k}: {v}" for k, v in sorted(reason_counts.items()))
            candidates.append(
                ("high_void", f"⚠️ Yüksek VOID Sayısı: Son 24 saatte <b>{void_count}</b> tahmin VOID oldu ({html.escape(breakdown_str)}).")
            )

        # 3. Gemini AI circuit breaker or latest sync ERROR
        ai_open = False
        try:
            from services.ai_summary import is_gemini_circuit_open
            ai_open = is_gemini_circuit_open()
        except Exception:
            pass
        last_sync = db.query(SyncLog).order_by(SyncLog.started_at.desc()).first()
        sync_err = bool(last_sync and last_sync.status == "ERROR")
        if ai_open or sync_err:
            reasons = []
            if ai_open:
                reasons.append("Yapay zeka özeti geçici olarak duraklatıldı")
            if sync_err:
                reasons.append(f"Son sync hata verdi ({html.escape((last_sync.message or '')[:80])})")
            candidates.append(("ai_sync", f"⚠️ AI/Sync Uyarısı: {', '.join(reasons)}."))

        for alert_type, text in candidates:
            dkey = f"alert:{alert_type}:{today_tr}"
            if db.query(TelegramDispatch.id).filter(TelegramDispatch.dispatch_key == dkey).first():
                continue
            msg_id = _send_message(admin_chat_id, text)
            if msg_id:
                db.add(
                    TelegramDispatch(
                        dispatch_key=dkey,
                        kind="health_alert",
                        chat_id=admin_chat_id,
                        thread_id=None,
                        message_id=msg_id,
                        content_hash=_compute_hash(text),
                        settled_replied=True,
                    )
                )
                db.commit()
                sent_alerts.append(alert_type)

    except Exception as exc:
        logger.warning("Telegram health watchdog error: %s", exc)

    return {"enabled": True, "alerts_sent": sent_alerts}


def setup_telegram_webhook() -> dict[str, Any]:
    """Register Telegram webhook URL, secret token, and bot commands."""
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}

    webhook_url = f"{_public_url()}/api/telegram/webhook"
    wh_payload: dict[str, Any] = {
        "url": webhook_url,
        "drop_pending_updates": True,
        "allowed_updates": ["message"],
    }
    secret = (settings.telegram_webhook_secret or "").strip()
    if secret:
        wh_payload["secret_token"] = secret

    wh_res = _tg_call("setWebhook", wh_payload)
    commands_payload = {
        "commands": [
            {"command": "bugun", "description": "Günün en yüksek güvenli 5 maçı"},
            {"command": "kuponlar", "description": "Günün aktif kuponları"},
            {"command": "karne", "description": "Son 7 gün şeffaf 60+ karne"},
            {"command": "yardim", "description": "Bot komutları ve bağlantılar"},
        ]
    }
    cmd_res = _tg_call("setMyCommands", commands_payload)
    return {
        "ok": bool(wh_res is not None and cmd_res is not None),
        "webhook_url": webhook_url,
        "webhook_ok": wh_res is not None,
        "commands_ok": cmd_res is not None,
    }


def handle_telegram_webhook(update: dict[str, Any], db: Session) -> dict[str, Any]:
    """Process private-chat read-only bot commands; silently ignore group/topic messages."""
    if not isinstance(update, dict):
        return {"ok": True, "ignored": True}

    msg = update.get("message")
    if not isinstance(msg, dict):
        return {"ok": True, "ignored": True}

    chat = msg.get("chat") or {}
    if chat.get("type") != "private":
        return {"ok": True, "ignored": True}

    raw_text = (msg.get("text") or "").strip()
    if not raw_text.startswith("/"):
        return {"ok": True, "ignored": True}

    cmd = raw_text.split()[0].split("@")[0].lower()
    chat_id = str(chat.get("id", ""))
    if not chat_id:
        return {"ok": True, "ignored": True}

    now_utc = datetime.now(timezone.utc)
    base_url = _public_url()

    if cmd in {"/start", "/yardim"}:
        text = (
            "⚽ <b>Fikstra Analiz Botu</b>\n\n"
            "Canlı veritabanından anlık, şeffaf analiz sorguları:\n"
            "• /bugun — Başlamamış en yüksek güvenli 5 maç\n"
            "• /kuponlar — Günün aktif hazır kuponları\n"
            "• /karne — Son 7 gün 60+ gerçek isabet özeti\n"
            "• /yardim — Komut listesi"
        )
        markup = {
            "inline_keyboard": [
                [{"text": "Fikstra'yı Aç", "url": base_url}],
                [{"text": "Telegram Grubu", "url": TELEGRAM_INVITE_URL}],
            ]
        }
        _send_message(chat_id, text, reply_markup=markup)
        return {"ok": True, "command": cmd}

    if cmd == "/bugun":
        eff_conf = func.coalesce(Prediction.published_confidence, Prediction.confidence_score)
        preds = (
            db.query(Prediction)
            .options(joinedload(Prediction.match))
            .join(Match, Prediction.match_id == Match.id)
            .filter(
                Match.status == "SCHEDULED",
                Match.start_time_utc > now_utc,
                Prediction.status == "PENDING",
                Prediction.is_top_pick.is_(True),
            )
            .order_by(eff_conf.desc(), Match.start_time_utc.asc())
            .limit(5)
            .all()
        )
        if not preds:
            _send_message(
                chat_id,
                "⏳ Şu an yayında başlamamış resmi tahmin bulunmuyor.",
                reply_markup=_inline_link_markup("Fikstra'yı Aç"),
            )
            return {"ok": True, "command": cmd, "count": 0}

        lines = ["🔥 <b>BUGÜNÜN EN YÜKSEK GÜVENLİ 5 MAÇI</b>", ""]
        buttons: list[list[dict[str, str]]] = []
        for idx, p in enumerate(preds, start=1):
            m = p.match
            if not m:
                continue
            conf = p.published_confidence if p.published_confidence is not None else p.confidence_score
            odd_val = p.published_odd if p.published_odd is not None else p.iddaa_odd
            pick_lbl = _format_pick_label(p.market_key, p.selection_key, m.home_team, m.away_team)
            lines.append(
                f"{idx}. <b>{html.escape(m.home_team)} - {html.escape(m.away_team)}</b>\n"
                f"   🏆 {html.escape(m.league)} · 🕒 {_format_tr_dt(m.start_time_utc)}\n"
                f"   🎯 {html.escape(pick_lbl)} (<code>{odd_val:.2f}</code>) · Güven <b>%{conf:.0f}</b> · "
                f"<a href=\"{base_url}/?match={p.match_id}\">İncele</a>"
            )
            buttons.append([{"text": f"{m.home_team} - {m.away_team}", "url": f"{base_url}/?match={p.match_id}"}])

        _send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": buttons})
        return {"ok": True, "command": cmd, "count": len(preds)}

    if cmd == "/kuponlar":
        tier_rank = {"BANKO": 0, "DENGELI": 1, "SURPRIZ": 2}
        coupons = (
            db.query(Coupon)
            .options(joinedload(Coupon.items).joinedload(CouponItem.prediction).joinedload(Prediction.match))
            .filter(Coupon.status == "PENDING")
            .order_by(Coupon.coupon_date.desc())
            .all()
        )
        coupons.sort(key=lambda c: (c.coupon_date, -tier_rank.get(c.tier, 9)), reverse=True)
        if not coupons:
            _send_message(
                chat_id,
                "🎟️ Şu an aktif bekleyen kupon bulunmuyor.",
                reply_markup=_inline_link_markup("Kuponlara Git", "/?tab=coupons"),
            )
            return {"ok": True, "command": cmd, "count": 0}

        blocks: list[str] = []
        for coupon in coupons[:3]:
            text, _ = _render_coupon_message(coupon)
            blocks.append(text)
        _send_message(
            chat_id,
            "\n\n━━━━━━━━━━\n\n".join(blocks),
            reply_markup=_inline_link_markup("Kuponları İncele", "/?tab=coupons"),
        )
        return {"ok": True, "command": cmd, "count": len(coupons[:3])}

    if cmd == "/karne":
        cutoff_7d = now_utc - timedelta(days=7)
        from app.routers.api import _get_official_settled_predictions
        official_settled = _get_official_settled_predictions(db, include_void=True, order_asc=False)
        preds = []
        for p in official_settled:
            if not p.match or not p.match.start_time_utc:
                continue
            ms = p.match.start_time_utc
            if ms.tzinfo is None:
                ms = ms.replace(tzinfo=timezone.utc)
            if cutoff_7d <= ms < now_utc:
                preds.append(p)
        won = sum(1 for p in preds if p.status == "WON")
        lost = sum(1 for p in preds if p.status == "LOST")
        void = sum(1 for p in preds if p.status == "VOID")
        decided = won + lost
        if decided == 0:
            text = "📊 <b>SON 7 GÜN 60+ KARNE</b>\n\nSon 7 günde sonuçlanmış 60+ resmi tahmin bulunmuyor."
        elif decided < 10:
            text = (
                f"📊 <b>SON 7 GÜN 60+ KARNE</b>\n\n"
                f"✅ Kazanan: <b>{won}</b> · ❌ Kaybeden: <b>{lost}</b>"
                + (f" · ⚪ İptal: <b>{void}</b>" if void > 0 else "")
                + f"\n📈 Sonuç: <b>{won}/{decided}</b> · <i>Az veri (10+ maçta oran hesaplanır)</i>"
            )
        else:
            win_rate = won / decided * 100.0
            text = (
                f"📊 <b>SON 7 GÜN 60+ KARNE</b>\n\n"
                f"✅ Kazanan: <b>{won}</b> · ❌ Kaybeden: <b>{lost}</b>"
                + (f" · ⚪ İptal: <b>{void}</b>" if void > 0 else "")
                + f"\n📈 Başarı Oranı: <b>%{win_rate:.1f}</b> ({won}/{decided})"
            )
        _send_message(
            chat_id,
            text,
            reply_markup=_inline_link_markup("Tüm Performansı Gör", "/?tab=performance"),
        )
        return {"ok": True, "command": cmd, "won": won, "decided": decided}

    _send_message(
        chat_id,
        "Bilinmeyen komut. Kullanılabilir komutlar için /yardim yazabilirsin.",
    )
    return {"ok": True, "command": cmd, "unknown": True}


def dispatch_sportoto_week(db: Session, week_id: int) -> dict[str, Any]:
    """Send 15-match Spor Toto optimization summary once to TELEGRAM_TOPIC_COUPONS."""
    week = (
        db.query(SportotoWeek)
        .options(joinedload(SportotoWeek.matches))
        .filter(SportotoWeek.id == week_id)
        .first()
    )
    if not week:
        return {"ok": False, "sent": False, "error": "week_not_found"}
    if week.status not in ("PUBLISHED", "SETTLED"):
        return {"ok": False, "sent": False, "error": "week_not_published"}

    dkey = f"sportoto:{week.id}"
    if db.query(TelegramDispatch.id).filter(TelegramDispatch.dispatch_key == dkey).first():
        return {"ok": True, "sent": False, "reason": "already_sent"}

    if not _is_enabled():
        return {"ok": False, "sent": False, "reason": "telegram_disabled"}

    payload = serialize_sportoto_week(week, include_plans=True)
    plans = payload.get("plans") or {}
    if not plans:
        return {"ok": False, "sent": False, "error": "incomplete_matches"}

    lines = [f"🎯 <b>SPOR TOTO 15'Lİ — {html.escape(week.title)}</b>\n"]
    p32 = plans.get("32") or {}
    picks_32 = {p["slot_no"]: p["pick_code"] for p in p32.get("picks", [])}
    for m in payload["matches"]:
        slot = m["slot_no"]
        pick = picks_32.get(slot, "-")
        lines.append(
            f"{slot:02d}. {html.escape(m['home_team'])} - {html.escape(m['away_team'])} → <b>{html.escape(pick)}</b>"
        )

    lines.append("\n📐 <b>Matematiksel Kolon Şablonları</b>")
    for b in ("16", "32", "64"):
        pl = plans.get(b)
        if not pl:
            continue
        lines.append(
            f"• <b>{b} Kolon</b> ({pl['used_columns']} kul.): "
            f"{pl['single_count']} Tek / {pl['double_count']} Çifte / {pl['triple_count']} Kapalı · "
            f"Beklenen <b>{pl['expected_hits']:.1f}/15</b>"
        )

    text = "\n".join(lines)
    markup = _inline_link_markup("Fikstra Toto'da İncele", "/?tab=toto")
    chat_id = settings.telegram_chat_id.strip()
    thread_id = _parse_topic_id(settings.telegram_topic_coupons)
    msg_id = _send_message(chat_id, text, thread_id=thread_id, reply_markup=markup)
    if not msg_id:
        return {"ok": False, "sent": False, "error": "telegram_send_failed"}

    db.add(
        TelegramDispatch(
            dispatch_key=dkey,
            kind="sportoto",
            chat_id=chat_id,
            thread_id=thread_id,
            message_id=msg_id,
            content_hash=_compute_hash(text, markup),
            settled_replied=True,
        )
    )
    db.commit()
    return {"ok": True, "sent": True, "message_id": msg_id}
