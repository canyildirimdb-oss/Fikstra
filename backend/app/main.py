import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import ensure_schema, get_db
from app.models import Coupon, Match, PushSubscription, ScraperCache, SyncLog
from app.routers.api import clear_api_cache, router as api_router
from services.settlement import settle_coupons, settle_finished_matches
from services.sync import is_sync_running, run_sync_pipeline


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
    allow_credentials=True,
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
    Only fetches external scores if there are unsettled matches started in the last 48h (and >=95m ago).
    """
    if x_cron_secret != settings.cron_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid cron secret",
        )

    now = datetime.now(timezone.utc)
    window_end = now - timedelta(minutes=95)
    has_unsettled = (
        db.query(Match.id)
        .filter(Match.start_time_utc <= window_end, Match.status.notin_(["FINISHED", "VOID"]))
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

    return {
        "status": "ok",
        "skipped_external": not has_unsettled,
        "settled_matches": settled_matches,
        "odds_api_credits_used": 0,
    }


@app.api_route("/api/cron/sync", methods=["GET", "POST"], status_code=status.HTTP_202_ACCEPTED)
def trigger_cron_sync(
    background_tasks: BackgroundTasks,
    response: Response,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    if x_cron_secret != settings.cron_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid cron secret",
        )

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
    existing = (
        db.query(PushSubscription)
        .filter(PushSubscription.endpoint == payload.endpoint)
        .first()
    )
    if existing:
        existing.p256dh = payload.p256dh
        existing.auth = payload.auth
    else:
        db.add(
            PushSubscription(
                endpoint=payload.endpoint,
                p256dh=payload.p256dh,
                auth=payload.auth,
            )
        )
    db.commit()
    return {"status": "subscribed"}


# Serve built frontend (single-service production mode) if dist/ exists
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
