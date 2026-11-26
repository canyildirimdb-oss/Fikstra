import hmac
import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import ensure_schema, get_db
from app.models import Coupon, CouponItem, Match, Prediction, PushSubscription, ScraperCache, SyncLog
from app.routers.api import clear_api_cache, router as api_router
from app.telegram_bot import (
    check_and_send_health_alerts,
    discover_telegram_topics,
    dispatch_telegram_settlements,
    handle_telegram_webhook,
    send_telegram_test_messages,
    setup_telegram_webhook,
)
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
def subscribe_push(payload: PushSubscribeIn, db: Session = Depends(get_db)):
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


@app.post("/api/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    secret = (settings.telegram_webhook_secret or "").strip()
    if secret and not hmac.compare_digest(x_telegram_bot_api_secret_token or "", secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram webhook secret",
        )
    try:
        payload = await request.json()
    except Exception:
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
