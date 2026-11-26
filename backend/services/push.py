import json
import logging
from typing import Any

from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Coupon, Match, NotificationLog, Prediction, PushSubscription
from utils.time_utils import tr_today_str

logger = logging.getLogger(__name__)

_TIER_LABELS = {
    "BANKO": "Banko Kupon",
    "DENGELI": "Dengeli Kupon",
    "SURPRIZ": "Sürpriz Kupon",
}


def human_pick_label(
    market_key: str,
    selection_key: str,
    home_team: str = "",
    away_team: str = "",
) -> str:
    """Convert raw market/selection keys into natural Turkish without bare MS 1/2."""
    mk = (market_key or "").upper()
    sk = (selection_key or "").upper()
    if mk == "MS":
        if sk == "1":
            return f"{home_team} Kazanır" if home_team else "Ev Sahibi Kazanır"
        if sk == "2":
            return f"{away_team} Kazanır" if away_team else "Deplasman Kazanır"
        return "Beraberlik"
    if mk.startswith("AU_"):
        line = mk.split("_", 1)[1] if "_" in mk else "2.5"
        side = "Üst" if sk == "UST" else "Alt"
        return f"{line} Gol {side}"
    if mk == "KG":
        return "Karşılıklı Gol Var" if sk == "VAR" else "Karşılıklı Gol Yok"
    if mk == "CS":
        if sk == "1X":
            return f"{home_team} Yenilmez" if home_team else "Ev Sahibi Yenilmez"
        if sk == "X2":
            return f"{away_team} Yenilmez" if away_team else "Deplasman Yenilmez"
        if sk == "12":
            return "Beraberlik Olmaz"
    return f"{market_key} {selection_key}".strip()


def format_high_confidence_notification(p: Prediction) -> tuple[str, str, str]:
    m = p.match
    pick_lbl = human_pick_label(p.market_key, p.selection_key, m.home_team, m.away_team)
    odd = p.published_odd if p.published_odd is not None else p.iddaa_odd
    conf = p.published_confidence if p.published_confidence is not None else p.confidence_score
    title = f"🔥 Yüksek Güven · {m.home_team} - {m.away_team}"
    body = f"Öneri: {pick_lbl} ({odd:.2f} oran) · Güven {conf:.0f}/100. Detaylar için dokun."
    return title, body, f"/?match={p.match_id}"


def format_odds_drop_notification(p: Prediction) -> tuple[str, str, str]:
    m = p.match
    pick_lbl = human_pick_label(p.market_key, p.selection_key, m.home_team, m.away_team)
    new_odd = p.iddaa_odd
    drop_pct = float(p.odds_drop_pct or 0.0)
    old_odd = new_odd / max(0.1, (1.0 - drop_pct / 100.0)) if drop_pct > 0 else new_odd
    title = f"📉 Oran Düştü · {m.home_team} - {m.away_team}"
    body = (
        f"{pick_lbl} oranı {old_odd:.2f} seviyesinden {new_odd:.2f} seviyesine geriledi. "
        "Piyasada yoğun ilgi var."
    )
    return title, body, f"/?match={p.match_id}"


def format_coupon_ready_notification(coupon: Coupon) -> tuple[str, str, str]:
    tier_name = _TIER_LABELS.get((coupon.tier or "").upper(), "Günün Kuponu")
    count = len(coupon.items or [])
    title = f"🎟️ Yeni Kupon Hazır · {tier_name}"
    body = f"{count} maçlık kupon toplam {coupon.total_odd:.2f} oranla yayında. Maçları görmek için dokun."
    return title, body, "/?tab=coupons"


def format_match_settled_notification(p: Prediction) -> tuple[str, str, str]:
    m = p.match
    pick_lbl = human_pick_label(p.market_key, p.selection_key, m.home_team, m.away_team)
    odd = p.published_odd if p.published_odd is not None else p.iddaa_odd
    score_txt = (
        f"{m.home_score}-{m.away_score}"
        if m.home_score is not None and m.away_score is not None
        else ""
    )
    won = (p.status or "").upper() == "WON"
    prefix = "✅ Tahmin Tuttu" if won else "❌ Sonuçlandı"
    outcome = "kazandı" if won else "kaybetti"
    mid = f"{m.home_team} {score_txt} {m.away_team}".strip()
    title = f"{prefix} · {mid}"
    body = f"Önerimiz olan {pick_lbl} ({odd:.2f}) {outcome}."
    return title, body, f"/?match={p.match_id}"


def format_coupon_settled_notification(coupon: Coupon, won_legs: int, total_legs: int) -> tuple[str, str, str]:
    tier_name = _TIER_LABELS.get((coupon.tier or "").upper(), "Kupon")
    won = (coupon.status or "").upper() == "WON"
    prefix = "🏆 Kupon Tuttu" if won else "📊 Kupon Sonuçlandı"
    title = f"{prefix} · {tier_name}"
    body = f"{won_legs}/{total_legs} maç tuttu · Toplam oran {coupon.total_odd:.2f}."
    return title, body, "/?tab=coupons"


def send_push_notification(
    db: Session,
    dedupe_key: str,
    title: str,
    body: str,
    url: str = "/",
    category: str = "info",
) -> dict[str, Any]:
    """Send a Web Push notification once per dedupe_key to all active subscribers."""
    already = db.query(NotificationLog).filter(NotificationLog.dedupe_key == dedupe_key).first()
    if already:
        return {"status": "deduped", "dedupe_key": dedupe_key, "sent": 0}

    subs = db.query(PushSubscription).all()
    sent_count = 0

    if subs and settings.vapid_private_key and settings.vapid_public_key:
        payload = json.dumps(
            {
                "title": title,
                "body": body,
                "icon": "/push-icon.png",
                "badge": "/push-badge.png",
                "url": url,
            }
        )
        for sub in subs:
            sub_info = {
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            }
            try:
                webpush(
                    subscription_info=sub_info,
                    data=payload,
                    vapid_private_key=settings.vapid_private_key,
                    vapid_claims={"sub": settings.vapid_subject},
                    timeout=8,
                )
                sent_count += 1
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code in (404, 410):
                    db.delete(sub)
            except Exception as exc:
                logger.warning("[Fikstra Push] Failed sending to %s: %s", sub.endpoint[:60], exc)

    db.add(
        NotificationLog(
            dedupe_key=dedupe_key,
            title=title,
            body=body,
            category=category,
            url=url,
            sent_count=sent_count,
        )
    )
    db.commit()
    return {"status": "logged", "dedupe_key": dedupe_key, "sent": sent_count, "subscribers": len(subs)}


def notify_sportoto_published(db: Session, week_id: int, week_title: str) -> dict[str, Any]:
    return send_push_notification(
        db,
        dedupe_key=f"toto_published_{week_id}",
        title=f"🎯 Fikstra Toto Yayında · {week_title}",
        body="Haftanın 15 maçı eklendi. 16–128 kolon şablonunu hemen kur.",
        url="/?tab=toto",
        category="toto",
    )


def dispatch_sync_alerts(db: Session, coupons: list[Coupon]) -> list[dict[str, Any]]:
    """
    Trigger human-readable alerts for:
    (a) High confidence >= 82
    (b) Sharp odds drop >= 7%
    (c) Daily coupons ready
    """
    results: list[dict[str, Any]] = []

    high_conf = (
        db.query(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .filter(Prediction.status == "PENDING", Prediction.confidence_score >= 82.0)
        .order_by(Prediction.confidence_score.desc())
        .limit(5)
        .all()
    )
    for p in high_conf:
        key = f"high_conf_{p.match_id}_{p.market_key}_{p.selection_key}"
        title, body, url = format_high_confidence_notification(p)
        results.append(send_push_notification(db, dedupe_key=key, title=title, body=body, url=url, category="high_conf"))

    sharp_drops = (
        db.query(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .filter(Prediction.status == "PENDING", Prediction.odds_drop_pct >= 7.0)
        .order_by(Prediction.odds_drop_pct.desc())
        .limit(5)
        .all()
    )
    for p in sharp_drops:
        key = f"odds_drop_{p.match_id}_{p.market_key}_{p.selection_key}"
        title, body, url = format_odds_drop_notification(p)
        results.append(send_push_notification(db, dedupe_key=key, title=title, body=body, url=url, category="odds_drop"))

    if coupons:
        today_str = tr_today_str()
        top_coupon = coupons[0]
        title, body, url = format_coupon_ready_notification(top_coupon)
        results.append(
            send_push_notification(
                db,
                dedupe_key=f"daily_coupons_{today_str}",
                title=title,
                body=body,
                url=url,
                category="coupon",
            )
        )

    return results
