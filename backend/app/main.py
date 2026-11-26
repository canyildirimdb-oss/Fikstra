import csv
import hmac
import io
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import or_, text
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import SessionLocal, ensure_schema, get_db
from app.models import Coupon, CouponItem, Match, Prediction, PushSubscription, ScraperCache, SiteAnnouncement, SportotoMatch, SportotoWeek, SyncLog, TelegramDispatch
from app.routers.api import _enforce_ip_rate_limit, clear_api_cache, router as api_router

logger = logging.getLogger("fikstra.main")
from app.sportoto_engine import (
    VALID_RESULTS,
    devig_1x2,
    match_and_create_sportoto_week,
    serialize_sportoto_week,
)
from app.sportoto_sync import (
    get_sportoto_sync_status,
    maybe_run_sportoto_auto_sync,
    sync_sportoto_auto,
)
from app.telegram_bot import (
    check_and_send_health_alerts,
    discover_telegram_topics,
    dispatch_sportoto_week,
    dispatch_telegram_settlements,
    handle_telegram_webhook,
    send_telegram_test_messages,
    setup_telegram_webhook,
)
from services.ai_summary import is_gemini_circuit_open
from services.push import notify_sportoto_published
from services.settlement import settle_coupons, settle_finished_matches
from services.sync import is_sync_running, run_sync_pipeline

_SETTLE_LOCK = threading.Lock()
_PG_SETTLE_LOCK_KEY = 84729102
_ALLOWED_PUSH_HOSTS = {
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
}
_ALLOWED_PUSH_SUFFIXES = (
    ".notify.windows.com",
    ".push.services.mozilla.com",
    ".push.apple.com",
)
MAX_PUSH_SUBSCRIPTIONS = 500


def _verify_cron_secret(x_cron_secret: Optional[str]) -> None:
    secret = settings.cron_secret or ""
    is_prod = bool(os.getenv("RENDER") or (not settings.resolved_database_url.startswith("sqlite")))
    if not secret or (is_prod and secret.startswith("change-me")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cron secret not configured",
        )
    if not hmac.compare_digest(x_cron_secret or "", secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid cron secret",
        )


def _is_allowed_push_endpoint(endpoint: str) -> bool:
    try:
        parsed = urlparse((endpoint or "").strip())
    except Exception:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in _ALLOWED_PUSH_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in _ALLOWED_PUSH_SUFFIXES)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema()
    yield


app = FastAPI(
    title="Fikstra API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


LAST_SETTLE_KEY = "system:last_settle_at"


@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    last_sync = db.query(SyncLog).order_by(SyncLog.started_at.desc()).first()
    last_ts = None
    if last_sync:
        dt = last_sync.finished_at or last_sync.started_at
        if dt:
            last_ts = dt.isoformat()

    last_settle_at = None
    settle_row = db.query(ScraperCache).filter(ScraperCache.cache_key == LAST_SETTLE_KEY).first()
    if settle_row and settle_row.payload_json:
        try:
            last_settle_at = json.loads(settle_row.payload_json).get("settled_at")
        except json.JSONDecodeError:
            last_settle_at = None

    pending_coupons_count = db.query(Coupon).filter(Coupon.status == "PENDING").count()

    return {
        "status": "ok",
        "brand": "Fikstra",
        "sync_running": is_sync_running(db),
        "last_sync_at": last_ts,
        "last_sync_status": last_sync.status if last_sync else None,
        "last_settle_at": last_settle_at,
        "pending_coupons_count": pending_coupons_count,
    }


@app.api_route("/api/cron/settle", methods=["GET", "POST"], status_code=status.HTTP_200_OK)
def trigger_cron_settle(
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """
    Lightweight, 0-Odds-API-credit settlement endpoint.
    Only fetches external scores if there are unsettled matches with active predictions/coupons
    started in the last 48h (and >=95m ago).
    """
    _verify_cron_secret(x_cron_secret)

    if not _SETTLE_LOCK.acquire(blocking=False):
        return {"status": "already_running", "skipped_external": True, "settled_matches": 0, "odds_api_credits_used": 0}

    is_pg = db.bind is not None and db.bind.dialect.name == "postgresql"
    pg_locked = False
    try:
        if is_pg:
            pg_locked = bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _PG_SETTLE_LOCK_KEY}).scalar())
            if not pg_locked:
                return {"status": "already_running", "skipped_external": True, "settled_matches": 0, "odds_api_credits_used": 0}

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=48)
        window_end = now - timedelta(minutes=95)
        has_unsettled = (
            db.query(Match.id)
            .filter(
                Match.start_time_utc >= window_start,
                Match.start_time_utc <= window_end,
                Match.status.notin_(["FINISHED", "VOID"]),
                or_(
                    Match.id.in_(db.query(Prediction.match_id).filter(Prediction.status == "PENDING")),
                    Match.id.in_(
                        db.query(Prediction.match_id)
                        .join(CouponItem, CouponItem.prediction_id == Prediction.id)
                        .join(Coupon, CouponItem.coupon_id == Coupon.id)
                        .filter(Coupon.status == "PENDING")
                    ),
                ),
            )
            .first()
            is not None
        )

        settled_matches = 0
        if has_unsettled:
            settled_matches = settle_finished_matches(db)
            settled_coupons = 0
        else:
            settled_coupons = settle_coupons(db)
            if settled_coupons:
                db.commit()

        if settled_matches or settled_coupons:
            clear_api_cache()

        payload = json.dumps({"settled_at": now.isoformat(), "settled_matches": settled_matches})
        row = db.query(ScraperCache).filter(ScraperCache.cache_key == LAST_SETTLE_KEY).first()
        if row:
            row.payload_json = payload
            row.updated_at = now
        else:
            db.add(ScraperCache(cache_key=LAST_SETTLE_KEY, payload_json=payload, updated_at=now))
        db.commit()

        try:
            st_auto = maybe_run_sportoto_auto_sync(db, min_interval_minutes=60)
            if st_auto and st_auto.get("ok"):
                clear_api_cache()
        except Exception:
            pass

        try:
            dispatch_telegram_settlements(db)
            check_and_send_health_alerts(db)
        except Exception:
            pass

        return {
            "status": "ok",
            "skipped_external": not has_unsettled,
            "settled_matches": settled_matches,
            "odds_api_credits_used": 0,
        }
    finally:
        if is_pg and pg_locked:
            try:
                db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _PG_SETTLE_LOCK_KEY})
            except Exception:
                pass
        _SETTLE_LOCK.release()


@app.api_route("/api/cron/sync", methods=["GET", "POST"], status_code=status.HTTP_202_ACCEPTED)
def trigger_cron_sync(
    background_tasks: BackgroundTasks,
    response: Response,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)

    if is_sync_running(db):
        response.status_code = status.HTTP_200_OK
        return {
            "status": "already_running",
            "queued": False,
            "message": "Sync already running in background",
        }

    background_tasks.add_task(run_sync_pipeline)
    response.status_code = status.HTTP_202_ACCEPTED
    return {
        "status": "accepted",
        "queued": True,
        "message": "Sync started in background",
    }


class PushSubscribeIn(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


@app.get("/api/push/vapid-public-key")
def get_vapid_public_key():
    return {"public_key": settings.vapid_public_key}


@app.post("/api/push/subscribe")
def subscribe_push(payload: PushSubscribeIn, request: Request, db: Session = Depends(get_db)):
    _enforce_ip_rate_limit(request, "push_subscribe", max_calls=15, window_sec=60.0)
    if not _is_allowed_push_endpoint(payload.endpoint):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported push endpoint",
        )
    existing = (
        db.query(PushSubscription)
        .filter(PushSubscription.endpoint == payload.endpoint)
        .first()
    )
    if existing:
        existing.p256dh = payload.p256dh
        existing.auth = payload.auth
    else:
        if db.query(PushSubscription).count() >= MAX_PUSH_SUBSCRIPTIONS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Push subscription limit reached",
            )
        db.add(
            PushSubscription(
                endpoint=payload.endpoint,
                p256dh=payload.p256dh,
                auth=payload.auth,
            )
        )
    db.commit()
    return {"status": "subscribed"}


@app.get("/api/admin/telegram/discover")
def admin_telegram_discover(x_cron_secret: Optional[str] = Header(default=None)):
    _verify_cron_secret(x_cron_secret)
    return discover_telegram_topics()


@app.post("/api/admin/telegram/test")
def admin_telegram_test(x_cron_secret: Optional[str] = Header(default=None)):
    _verify_cron_secret(x_cron_secret)
    return send_telegram_test_messages()


@app.post("/api/admin/telegram/setup-webhook")
def admin_telegram_setup_webhook(x_cron_secret: Optional[str] = Header(default=None)):
    _verify_cron_secret(x_cron_secret)
    return setup_telegram_webhook()


@app.post("/api/admin/refresh-forms")
def admin_refresh_forms(
    force: bool = False,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    from services.sync import refresh_all_active_forms

    res = refresh_all_active_forms(
        db, force_refresh=force, only_missing=False, only_with_predictions=True
    )
    clear_api_cache()
    return res


class AnnouncementCreateIn(BaseModel):
    text: str
    link_url: Optional[str] = None
    expires_hours: Optional[int] = None


class AnnouncementPatchIn(BaseModel):
    is_active: Optional[bool] = None


def _serialize_announcement(a: SiteAnnouncement) -> dict:
    return {
        "id": a.id,
        "text": a.text,
        "link_url": a.link_url,
        "is_active": a.is_active,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@app.get("/api/admin/overview")
def admin_overview(
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    now_utc = datetime.now(timezone.utc)

    # Odds API credits from ScraperCache (0 external API calls)
    odds_remaining = None
    odds_used = None
    credit_row = db.query(ScraperCache).filter(ScraperCache.cache_key == "odds_api:remaining_credits").first()
    if credit_row and credit_row.payload_json:
        try:
            c_data = json.loads(credit_row.payload_json)
            odds_remaining = c_data.get("remaining")
            odds_used = c_data.get("used")
        except Exception:
            pass

    last_sync = db.query(SyncLog).order_by(SyncLog.started_at.desc()).first()
    last_sync_at = None
    if last_sync:
        dt = last_sync.finished_at or last_sync.started_at
        last_sync_at = dt.isoformat() if dt else None

    last_settle_at = None
    settle_row = db.query(ScraperCache).filter(ScraperCache.cache_key == LAST_SETTLE_KEY).first()
    if settle_row and settle_row.payload_json:
        try:
            last_settle_at = json.loads(settle_row.payload_json).get("settled_at")
        except Exception:
            pass

    void_24h = (
        db.query(Prediction.id)
        .filter(
            Prediction.status == "VOID",
            Prediction.is_top_pick.is_(True),
            Prediction.settled_at.isnot(None),
            Prediction.settled_at >= now_utc - timedelta(hours=24),
        )
        .count()
    )

    dispatches = (
        db.query(TelegramDispatch)
        .order_by(TelegramDispatch.updated_at.desc(), TelegramDispatch.id.desc())
        .limit(15)
        .all()
    )
    announcements = (
        db.query(SiteAnnouncement)
        .order_by(SiteAnnouncement.created_at.desc(), SiteAnnouncement.id.desc())
        .all()
    )

    st_status = get_sportoto_sync_status(db)

    return {
        "odds_api_remaining": odds_remaining,
        "odds_api_used": odds_used,
        "last_sync_at": last_sync_at,
        "last_sync_status": last_sync.status if last_sync else None,
        "last_settle_at": last_settle_at,
        "sportoto_last_sync_at": st_status.get("synced_at"),
        "sportoto_last_status": st_status.get("status"),
        "void_24h_count": void_24h,
        "gemini_circuit_open": is_gemini_circuit_open(),
        "telegram_admin_configured": bool((settings.telegram_admin_chat_id or "").strip()),
        "telegram_webhook_secret_configured": bool((settings.telegram_webhook_secret or "").strip()),
        "recent_dispatches": [
            {
                "id": d.id,
                "kind": d.kind,
                "dispatch_key": d.dispatch_key,
                "settled_replied": d.settled_replied,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in dispatches
        ],
        "announcements": [_serialize_announcement(a) for a in announcements],
    }


@app.post("/api/admin/announcements")
def admin_create_announcement(
    payload: AnnouncementCreateIn,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    txt = (payload.text or "").strip()
    if not txt or len(txt) > 220:
        raise HTTPException(status_code=400, detail="Announcement text must be 1-220 characters")

    link = (payload.link_url or "").strip() or None
    if link and not (link.startswith("https://") or link.startswith("http://") or link.startswith("/")):
        raise HTTPException(status_code=400, detail="Invalid link_url scheme")

    expires_at = None
    if payload.expires_hours is not None and payload.expires_hours > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=int(payload.expires_hours))

    ann = SiteAnnouncement(text=txt, link_url=link[:300] if link else None, is_active=True, expires_at=expires_at)
    db.add(ann)
    db.commit()
    db.refresh(ann)
    return _serialize_announcement(ann)


@app.patch("/api/admin/announcements/{ann_id}")
def admin_patch_announcement(
    ann_id: int,
    payload: Optional[AnnouncementPatchIn] = None,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    ann = db.query(SiteAnnouncement).filter(SiteAnnouncement.id == ann_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="Announcement not found")
    if payload and payload.is_active is not None:
        ann.is_active = bool(payload.is_active)
    else:
        ann.is_active = not ann.is_active
    db.commit()
    db.refresh(ann)
    return _serialize_announcement(ann)


@app.delete("/api/admin/announcements/{ann_id}")
def admin_delete_announcement(
    ann_id: int,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    ann = db.query(SiteAnnouncement).filter(SiteAnnouncement.id == ann_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="Announcement not found")
    db.delete(ann)
    db.commit()
    return {"ok": True, "deleted_id": ann_id}


@app.get("/api/admin/export/calibration.csv")
def admin_export_calibration_csv(
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    preds = (
        db.query(Prediction)
        .options(joinedload(Prediction.match))
        .join(Match, Prediction.match_id == Match.id)
        .filter(Prediction.status.in_(["WON", "LOST", "VOID"]))
        .order_by(Match.start_time_utc.desc(), Prediction.id.desc())
        .all()
    )
    tr_tz = timezone(timedelta(hours=3))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id",
        "match_date_tr",
        "league",
        "home_team",
        "away_team",
        "market",
        "pick",
        "published_odd",
        "closing_odd",
        "confidence",
        "status",
        "home_score",
        "away_score",
        "is_top_pick",
    ])
    for p in preds:
        m = p.match
        if not m:
            continue
        dt_tr = ""
        if m.start_time_utc:
            dt = m.start_time_utc if m.start_time_utc.tzinfo else m.start_time_utc.replace(tzinfo=timezone.utc)
            dt_tr = dt.astimezone(tr_tz).strftime("%Y-%m-%d %H:%M")
        pub_odd = p.published_odd if p.published_odd is not None else p.iddaa_odd
        conf = p.published_confidence if p.published_confidence is not None else p.confidence_score
        writer.writerow([
            p.id,
            dt_tr,
            m.league,
            m.home_team,
            m.away_team,
            p.market_key,
            p.selection_key,
            pub_odd,
            p.closing_odd if p.closing_odd is not None else "",
            conf,
            p.status,
            m.home_score if m.home_score is not None else "",
            m.away_score if m.away_score is not None else "",
            p.is_top_pick,
        ])

    csv_bytes = buf.getvalue().encode("utf-8-sig")
    today_str = datetime.now(tr_tz).date().isoformat()
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="fikstra_kalibrasyon_{today_str}.csv"'},
    )


class SportotoParseIn(BaseModel):
    title: str
    raw_text: str
    column_price: float = 10.0


class SportotoMatchPatchItem(BaseModel):
    slot_no: int
    odd_1: Optional[float] = None
    odd_x: Optional[float] = None
    odd_2: Optional[float] = None
    public_pct_1: Optional[float] = None
    public_pct_x: Optional[float] = None
    public_pct_2: Optional[float] = None
    actual_result: Optional[str] = None


class SportotoMatchesPatchIn(BaseModel):
    matches: list[SportotoMatchPatchItem] = []
    auto_settle_from_db: bool = False


@app.get("/api/admin/sportoto/weeks")
def admin_list_sportoto_weeks(
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    weeks = (
        db.query(SportotoWeek)
        .options(joinedload(SportotoWeek.matches))
        .order_by(SportotoWeek.created_at.desc(), SportotoWeek.id.desc())
        .limit(15)
        .all()
    )
    return {"items": [serialize_sportoto_week(w, include_plans=True) for w in weeks]}


@app.post("/api/admin/sportoto/sync")
def admin_sync_sportoto_now(
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    res = sync_sportoto_auto(db, trigger_notifications=True)
    if res.get("ok"):
        clear_api_cache()
    return res


@app.post("/api/admin/sportoto/parse")
def admin_parse_sportoto_week(
    payload: SportotoParseIn,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    try:
        week = match_and_create_sportoto_week(
            db, payload.title, payload.raw_text, column_price=payload.column_price
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return serialize_sportoto_week(week, include_plans=True)


@app.patch("/api/admin/sportoto/weeks/{week_id}/matches")
def admin_patch_sportoto_matches(
    week_id: int,
    payload: SportotoMatchesPatchIn,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    week = (
        db.query(SportotoWeek)
        .options(joinedload(SportotoWeek.matches))
        .filter(SportotoWeek.id == week_id)
        .first()
    )
    if not week:
        raise HTTPException(status_code=404, detail="Sportoto week not found")

    by_slot = {m.slot_no: m for m in week.matches}

    for item in payload.matches:
        sm = by_slot.get(item.slot_no)
        if not sm:
            continue
        if item.odd_1 is not None and item.odd_x is not None and item.odd_2 is not None:
            try:
                p1, px, p2 = devig_1x2(item.odd_1, item.odd_x, item.odd_2)
                sm.odd_1 = float(item.odd_1)
                sm.odd_x = float(item.odd_x)
                sm.odd_2 = float(item.odd_2)
                sm.prob_1 = round(p1, 6)
                sm.prob_x = round(px, 6)
                sm.prob_2 = round(p2, 6)
                sm.source = "market_devig"
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Slot {item.slot_no}: {exc}")

        if item.public_pct_1 is not None and item.public_pct_x is not None and item.public_pct_2 is not None:
            p_sum = float(item.public_pct_1) + float(item.public_pct_x) + float(item.public_pct_2)
            if item.public_pct_1 <= 0 or item.public_pct_x <= 0 or item.public_pct_2 <= 0 or not (95.0 <= p_sum <= 105.0):
                raise HTTPException(status_code=400, detail=f"Slot {item.slot_no}: Halk yüzdeleri >0 ve toplamı ~100 olmalıdır")
            sm.public_pct_1 = float(item.public_pct_1)
            sm.public_pct_x = float(item.public_pct_x)
            sm.public_pct_2 = float(item.public_pct_2)

        if item.actual_result is not None:
            res = item.actual_result.strip().upper()
            if res == "":
                sm.actual_result = None
            elif res in VALID_RESULTS:
                sm.actual_result = res
            else:
                raise HTTPException(status_code=400, detail=f"Slot {item.slot_no}: Geçersiz sonuç '{res}'")

    if payload.auto_settle_from_db:
        pred_ids = [m.prediction_id for m in week.matches if m.prediction_id]
        if pred_ids:
            preds = (
                db.query(Prediction)
                .options(joinedload(Prediction.match))
                .filter(Prediction.id.in_(pred_ids))
                .all()
            )
            pred_map = {p.id: p for p in preds}
            for sm in week.matches:
                if sm.actual_result is not None or not sm.prediction_id:
                    continue
                pr = pred_map.get(sm.prediction_id)
                mt = pr.match if pr else None
                if mt and mt.status == "FINISHED" and mt.home_score is not None and mt.away_score is not None:
                    if mt.home_score > mt.away_score:
                        sm.actual_result = "1"
                    elif mt.home_score == mt.away_score:
                        sm.actual_result = "X"
                    else:
                        sm.actual_result = "2"

    all_settled = len(week.matches) == 15 and all(m.actual_result in VALID_RESULTS for m in week.matches)
    if all_settled and week.status == "PUBLISHED":
        week.status = "SETTLED"
    elif not all_settled and week.status == "SETTLED":
        week.status = "PUBLISHED"

    db.commit()
    db.refresh(week)
    return serialize_sportoto_week(week, include_plans=True)


def _notify_sportoto_published_bg(week_id: int, week_title: str) -> None:
    bg_db = SessionLocal()
    try:
        notify_sportoto_published(bg_db, week_id, week_title)
    except Exception:
        pass
    finally:
        bg_db.close()


@app.post("/api/admin/sportoto/weeks/{week_id}/publish")
def admin_publish_sportoto_week(
    week_id: int,
    background_tasks: BackgroundTasks,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    week = (
        db.query(SportotoWeek)
        .options(joinedload(SportotoWeek.matches))
        .filter(SportotoWeek.id == week_id)
        .first()
    )
    if not week:
        raise HTTPException(status_code=404, detail="Sportoto week not found")
    if len(week.matches) != 15:
        raise HTTPException(status_code=400, detail="Haftada tam 15 maç bulunmalıdır")
    missing = [m.slot_no for m in sorted(week.matches, key=lambda x: x.slot_no) if m.source == "missing" or m.prob_1 is None]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Eksik verili maçlar var (Slot: {', '.join(map(str, missing))}). Yayınlamak için 1-X-2 oranlarını girin.",
        )

    week.status = "SETTLED" if all(m.actual_result in VALID_RESULTS for m in week.matches) else "PUBLISHED"
    if not week.published_at:
        week.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(week)
    background_tasks.add_task(_notify_sportoto_published_bg, week.id, week.title)
    return serialize_sportoto_week(week, include_plans=True)


@app.post("/api/admin/sportoto/weeks/{week_id}/telegram")
def admin_telegram_sportoto_week(
    week_id: int,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    res = dispatch_sportoto_week(db, week_id)
    if not res.get("ok") and res.get("error") in {"week_not_found", "week_not_published", "incomplete_matches"}:
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res


@app.delete("/api/admin/sportoto/weeks/{week_id}")
def admin_delete_sportoto_week(
    week_id: int,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    _verify_cron_secret(x_cron_secret)
    week = db.query(SportotoWeek).filter(SportotoWeek.id == week_id).first()
    if not week:
        raise HTTPException(status_code=404, detail="Sportoto week not found")
    db.delete(week)
    db.commit()
    return {"ok": True, "deleted_id": week_id}


@app.post("/api/telegram/webhook")
def telegram_webhook(
    payload: dict[str, Any] = Body(default_factory=dict),
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    secret = (settings.telegram_webhook_secret or "").strip()
    if secret:
        if not hmac.compare_digest(x_telegram_bot_api_secret_token or "", secret):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Telegram webhook secret",
            )
    else:
        logger.warning("TELEGRAM_WEBHOOK_SECRET is not set; accepting webhook without token verification")
    if not isinstance(payload, dict) or not payload:
        return {"ok": True, "ignored": True}
    return handle_telegram_webhook(payload, db)


# Serve built frontend (single-service production mode) if dist/ exists
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        dist_root = FRONTEND_DIST.resolve()
        candidate = (dist_root / full_path).resolve()
        if not candidate.is_relative_to(dist_root):
            raise HTTPException(status_code=404, detail="Not found")
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist_root / "index.html")
