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
from app.models import Coupon, CouponItem, Match, Prediction, TelegramDispatch

logger = logging.getLogger("fikstra.telegram")

TR_TZ = timezone(timedelta(hours=3))
TELEGRAM_API_BASE = "https://api.telegram.org"
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
        line = mk.split("_", 1)[1] if "_" in mk else "2.5"
        side = "Üst" if sk == "UST" else "Alt"
        return f"{line} {side}"
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


def _inline_link_markup(label: str = "Fikstra'da İncele") -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": label, "url": _public_url()}]]}


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
    return "\n".join(lines), _inline_link_markup()


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
    return text, _inline_link_markup()


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
    return text, _inline_link_markup()


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
    stats = {"enabled": True, "coupon_replies": 0, "single_replies": 0, "won_showcases": 0, "daily_report": False, "rate_limited": False}

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
                            reply_markup=_inline_link_markup(),
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
                y_preds = (
                    db.query(Prediction)
                    .join(Match, Prediction.match_id == Match.id)
                    .filter(
                        Match.start_time_utc >= y_start_utc,
                        Match.start_time_utc < y_end_utc,
                        Prediction.is_top_pick.is_(True),
                        eff_conf >= 60.0,
                    )
                    .all()
                )
                if y_preds and all(p.status in {"WON", "LOST", "VOID"} for p in y_preds):
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
                        reply_markup=_inline_link_markup("Tüm Performansı Gör"),
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

    except TelegramRateLimitError:
        stats["rate_limited"] = True
        logger.warning("Telegram settlement dispatch stopped early due to HTTP 429 rate limit")
    except Exception as exc:
        logger.warning("Telegram dispatch_telegram_settlements error: %s", exc)

    return stats


def discover_telegram_topics() -> dict[str, Any]:
    """Inspect recent bot updates to list chat_ids and message_thread_ids."""
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured", "chats": [], "threads": []}

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
