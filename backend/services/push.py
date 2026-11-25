import json
from typing import Any

from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Coupon, Match, NotificationLog, Prediction, PushSubscription
from utils.time_utils import tr_today_str


def send_push_notification(
    db: Session,
    dedupe_key: str,
    title: str,
    body: str,
    url: str = "/",
) -> dict[str, Any]:
    """Send a Web Push notification once per dedupe_key to all active subscribers."""
    already = db.query(NotificationLog).filter(NotificationLog.dedupe_key == dedupe_key).first()
    if already:
        return {"status": "deduped", "dedupe_key": dedupe_key, "sent": 0}

    subs = db.query(PushSubscription).all()
    sent_count = 0

    if subs and settings.vapid_private_key and settings.vapid_public_key:
        payload = json.dumps({"title": title, "body": body, "url": url})
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
                )
                sent_count += 1
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code in (404, 410):
                    db.delete(sub)

    db.add(
        NotificationLog(
            dedupe_key=dedupe_key,
            title=title,
            body=body,
            sent_count=sent_count,
        )
    )
    db.commit()
    return {"status": "logged", "dedupe_key": dedupe_key, "sent": sent_count, "subscribers": len(subs)}


def dispatch_sync_alerts(db: Session, coupons: list[Coupon]) -> list[dict[str, Any]]:
    """
    Trigger alerts for:
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
        res = send_push_notification(
            db,
            dedupe_key=key,
            title=f"Fikstra • Yüksek Güven (%{p.confidence_score:.0f}) • {p.match.home_team} - {p.match.away_team}",
            body=f"{p.market_key} {p.selection_key} ({p.iddaa_odd:.2f} oran)",
        )
        results.append(res)

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
        res = send_push_notification(
            db,
            dedupe_key=key,
            title=f"Fikstra • Sert Oran Düşüşü (-%{p.odds_drop_pct:.1f}) • {p.match.home_team} - {p.match.away_team}",
            body=f"{p.market_key} {p.selection_key} güncel oran {p.iddaa_odd:.2f}",
        )
        results.append(res)

    if coupons:
        today_str = tr_today_str()
        res = send_push_notification(
            db,
            dedupe_key=f"daily_coupons_{today_str}",
            title="Fikstra • Günün Otomatik Kuponları Hazır",
            body=f"{today_str} için Banko, Dengeli ve Sürpriz kuponlar güncellendi.",
            url="/coupons",
        )
        results.append(res)

    return results
