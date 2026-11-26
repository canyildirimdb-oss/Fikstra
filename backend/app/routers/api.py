import json
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models import (
    Coupon,
    CouponItem,
    Match,
    MatchContext,
    OddsCurrent,
    OddsHistory,
    Prediction,
    ScraperCache,
    SiteAnnouncement,
    SyncLog,
)
from scrapers.iddaa import is_youth_or_women_match
from services.ai_summary import generate_match_summary
from services.analyzer import MAX_HIGHLIGHT_ODD, MIN_ODD_RULE
from services.sync import is_sync_running, run_sync_pipeline
from utils.time_utils import is_tr_today_or_next_24h, to_tr_datetime, tr_now, tr_today_str

router = APIRouter(prefix="/api", tags=["api"])

API_CACHE_TTL_SEC = 60.0
API_CACHE_MAX_ENTRIES = 200
_API_CACHE: dict[str, tuple[float, Any]] = {}
_API_CACHE_LOCK = threading.Lock()


def clear_api_cache() -> None:
    with _API_CACHE_LOCK:
        _API_CACHE.clear()


def _cache_get(key: str) -> Any:
    now = time.monotonic()
    with _API_CACHE_LOCK:
        entry = _API_CACHE.get(key)
        if not entry:
            return None
        exp, val = entry
        if now > exp:
            _API_CACHE.pop(key, None)
            return None
        # Refresh LRU position
        _API_CACHE[key] = _API_CACHE.pop(key)
        return val


def _cache_set(key: str, val: Any, ttl: float = API_CACHE_TTL_SEC) -> Any:
    with _API_CACHE_LOCK:
        if key in _API_CACHE:
            _API_CACHE.pop(key, None)
        while len(_API_CACHE) >= API_CACHE_MAX_ENTRIES:
            oldest_key = next(iter(_API_CACHE))
            _API_CACHE.pop(oldest_key, None)
        _API_CACHE[key] = (time.monotonic() + ttl, val)
    return val


def _serialize_ctx(ctx: Optional[MatchContext], expected_pick: Optional[str] = None) -> dict:
    if not ctx:
        return {
            "home_form": [],
            "away_form": [],
            "home_goals_avg": None,
            "away_goals_avg": None,
            "home_conceded_avg": None,
            "away_conceded_avg": None,
            "injuries": {"home": [], "away": []},
            "ai_summary": None,
            "ai_source": None,
            "home_elo": None,
            "away_elo": None,
            "home_rank": None,
            "away_rank": None,
            "home_true_xg": None,
            "home_true_xga": None,
            "away_true_xg": None,
            "away_true_xga": None,
            "xg_source": None,
            "home_goal_std": None,
            "away_goal_std": None,
            "home_shots_pg": None,
            "home_sot_pg": None,
            "away_shots_pg": None,
            "away_sot_pg": None,
        }
    summary_valid = bool(
        ctx.ai_summary
        and (expected_pick is None or ctx.ai_summary_pick == expected_pick)
    )
    return {
        "home_form": json.loads(ctx.home_form) if ctx.home_form else [],
        "away_form": json.loads(ctx.away_form) if ctx.away_form else [],
        "home_goals_avg": ctx.home_goals_avg,
        "away_goals_avg": ctx.away_goals_avg,
        "home_conceded_avg": ctx.home_conceded_avg,
        "away_conceded_avg": ctx.away_conceded_avg,
        "injuries": json.loads(ctx.injuries_json) if ctx.injuries_json else {"home": [], "away": []},
        "ai_summary": ctx.ai_summary if summary_valid else None,
        "ai_source": ctx.ai_source if summary_valid else None,
        "home_elo": ctx.home_elo,
        "away_elo": ctx.away_elo,
        "home_rank": ctx.home_rank,
        "away_rank": ctx.away_rank,
        "home_true_xg": ctx.home_true_xg,
        "home_true_xga": ctx.home_true_xga,
        "away_true_xg": ctx.away_true_xg,
        "away_true_xga": ctx.away_true_xga,
        "xg_source": ctx.xg_source,
        "home_goal_std": ctx.home_goal_std,
        "away_goal_std": ctx.away_goal_std,
        "home_shots_pg": ctx.home_shots_pg,
        "home_sot_pg": ctx.home_sot_pg,
        "away_shots_pg": ctx.away_shots_pg,
        "away_sot_pg": ctx.away_sot_pg,
    }


def _match_time_info(dt_utc: datetime) -> dict:
    tr_dt = to_tr_datetime(dt_utc)
    return {
        "start_time_utc": dt_utc.isoformat(),
        "start_time_tr": tr_dt.strftime("%H:%M"),
        "start_date_tr": tr_dt.strftime("%d.%m"),
        "is_today_24h": is_tr_today_or_next_24h(dt_utc),
    }


@router.get("/dashboard/summary")
def get_dashboard_summary(db: Session = Depends(get_db)):
    cached = _cache_get("dashboard_summary")
    if cached is not None:
        return cached

    last_sync = (
        db.query(SyncLog)
        .filter(SyncLog.status == "SUCCESS")
        .order_by(SyncLog.finished_at.desc())
        .first()
    )
    minutes_ago = None
    last_sync_iso = None
    if last_sync and last_sync.finished_at:
        fin = (
            last_sync.finished_at.replace(tzinfo=timezone.utc)
            if last_sync.finished_at.tzinfo is None
            else last_sync.finished_at
        )
        minutes_ago = max(0, int((datetime.now(timezone.utc) - fin).total_seconds() // 60))
        last_sync_iso = fin.isoformat()

    settled_raw = (
        db.query(Prediction)
        .filter(
            Prediction.status.in_(["WON", "LOST"]),
            Prediction.is_top_pick.is_(True),
            func.coalesce(Prediction.published_confidence, Prediction.confidence_score) >= 60.0,
        )
        .order_by(Prediction.settled_at.desc(), Prediction.confidence_score.desc())
        .all()
    )
    seen_settled_matches: set[int] = set()
    settled_preds: list[Prediction] = []
    for p in settled_raw:
        if p.match_id in seen_settled_matches:
            continue
        seen_settled_matches.add(p.match_id)
        settled_preds.append(p)

    won_cnt = sum(1 for p in settled_preds if p.status == "WON")
    win_rate = round((won_cnt / len(settled_preds)) * 100.0, 1) if settled_preds else None

    kickoff_lock = datetime.now(timezone.utc) + timedelta(minutes=5)
    pending = (
        db.query(Prediction)
        .options(joinedload(Prediction.match))
        .join(Match, Prediction.match_id == Match.id)
        .filter(
            Match.status == "SCHEDULED",
            Match.start_time_utc > kickoff_lock,
            Prediction.status == "PENDING",
            Prediction.iddaa_odd >= MIN_ODD_RULE,
        )
        .all()
    )
    today_pending = [p for p in pending if is_tr_today_or_next_24h(p.match.start_time_utc)]
    pool = today_pending or pending

    top_conf = max(
        [p for p in pool if p.iddaa_odd < MAX_HIGHLIGHT_ODD],
        key=lambda p: p.confidence_score,
        default=None,
    )
    best_value_pool = [
        p
        for p in pool
        if p.value_edge_pct is not None
        and 1.30 <= p.iddaa_odd <= 3.20
        and not is_youth_or_women_match(p.match.league, p.match.home_team, p.match.away_team)
    ]
    best_val = max(
        best_value_pool,
        key=lambda p: (1 if p.match.is_major_league else 0, p.value_edge_pct or -999),
        default=None,
    )
    sharpest_drop = max(
        [
            p
            for p in pool
            if (p.odds_drop_pct or 0) > 0
            and p.iddaa_odd < MAX_HIGHLIGHT_ODD
            and not is_youth_or_women_match(p.match.league, p.match.home_team, p.match.away_team)
        ],
        key=lambda p: p.odds_drop_pct or 0,
        default=None,
    )

    def _mini(p: Optional[Prediction]):
        if not p:
            return None
        return {
            "match_id": p.match_id,
            "match_name": f"{p.match.home_team} - {p.match.away_team}",
            "league": p.match.league,
            "market_key": p.market_key,
            "selection_key": p.selection_key,
            "iddaa_odd": p.iddaa_odd,
            "global_odd": p.global_odd,
            "confidence_score": p.confidence_score,
            "value_edge_pct": p.value_edge_pct,
            "odds_drop_pct": p.odds_drop_pct,
        }

    return _cache_set(
        "dashboard_summary",
        {
            "last_sync_at": last_sync_iso,
            "minutes_ago": minutes_ago,
            "overall_win_rate": win_rate,
            "settled_total": len(settled_preds),
            "active_matches_count": db.query(Match).filter(Match.status == "SCHEDULED").count(),
            "top_confidence": _mini(top_conf),
            "best_value": _mini(best_val),
            "sharpest_drop": _mini(sharpest_drop),
        },
    )


@router.get("/predictions")
def get_predictions(
    today_only: bool = Query(default=True),
    major_only: bool = Query(default=True),
    min_odd: float = Query(default=MIN_ODD_RULE, ge=MIN_ODD_RULE),
    min_confidence: float = Query(default=0.0, ge=0.0, le=100.0),
    market: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=60, ge=1, le=200),
    db: Session = Depends(get_db),
):
    norm_search = " ".join((search or "").strip().lower().split())[:64]
    cache_key = (
        f"predictions:{today_only}:{major_only}:{round(min_odd, 2)}:{round(min_confidence, 1)}:"
        f"{(market or '').strip().upper()}:{norm_search}:{limit}"
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    kickoff_lock = datetime.now(timezone.utc) + timedelta(minutes=5)
    q = (
        db.query(Prediction)
        .options(joinedload(Prediction.match).joinedload(Match.context))
        .join(Match, Prediction.match_id == Match.id)
        .filter(
            Match.status == "SCHEDULED",
            Match.start_time_utc > kickoff_lock,
            Prediction.status == "PENDING",
            Prediction.iddaa_odd >= max(MIN_ODD_RULE, min_odd),
            Prediction.iddaa_odd < MAX_HIGHLIGHT_ODD,
            Prediction.confidence_score >= min_confidence,
        )
    )
    if major_only:
        q = q.filter(Match.is_major_league.is_(True))
    if market and market != "ALL":
        q = q.filter(Prediction.market_key == market)

    preds = q.order_by(Prediction.confidence_score.desc()).all()

    search_low = (search or "").strip().lower()
    seen_matches: set[int] = set()
    selected_preds: list[Prediction] = []
    now_utc = datetime.now(timezone.utc)

    for p in preds:
        m = p.match
        m_start = m.start_time_utc.replace(tzinfo=timezone.utc) if m.start_time_utc.tzinfo is None else m.start_time_utc
        if m_start <= now_utc:
            continue
        if today_only and not is_tr_today_or_next_24h(m.start_time_utc):
            continue
        if search_low and (
            search_low not in m.home_team.lower()
            and search_low not in m.away_team.lower()
            and search_low not in m.league.lower()
        ):
            continue
        # One best pick per match on the main feed
        if m.id in seen_matches:
            continue
        seen_matches.add(m.id)
        selected_preds.append(p)
        if len(selected_preds) >= limit:
            break

    # Batch-load OddsCurrent to avoid N+1 queries
    match_ids = [p.match_id for p in selected_preds]
    odds_map: dict[tuple[int, str, str], OddsCurrent] = {}
    if match_ids:
        for o in db.query(OddsCurrent).filter(OddsCurrent.match_id.in_(match_ids)).all():
            odds_map[(o.match_id, o.market_key, o.selection_key)] = o

    items: list[dict] = []
    for p in selected_preds:
        m = p.match
        bd = json.loads(p.score_breakdown_json) if p.score_breakdown_json else {}
        ctx_data = _serialize_ctx(m.context)
        curr_odd_row = odds_map.get((m.id, p.market_key, p.selection_key))

        items.append({
            "prediction_id": p.id,
            "match_id": m.id,
            "iddaa_id": m.iddaa_id,
            "league": m.league,
            "country": m.country,
            "is_major_league": m.is_major_league,
            "home_team": m.home_team,
            "away_team": m.away_team,
            "home_logo_url": m.home_logo_url,
            "away_logo_url": m.away_logo_url,
            "league_logo_url": m.league_logo_url,
            **_match_time_info(m.start_time_utc),
            "data_status": m.data_status,
            "market_key": p.market_key,
            "selection_key": p.selection_key,
            "iddaa_odd": p.iddaa_odd,
            "opening_odd": curr_odd_row.opening_odd if curr_odd_row else p.iddaa_odd,
            "global_odd": p.global_odd,
            "confidence_score": p.confidence_score,
            "value_edge_pct": p.value_edge_pct,
            "odds_drop_pct": p.odds_drop_pct,
            "breakdown": bd,
            "home_form": ctx_data["home_form"],
            "away_form": ctx_data["away_form"],
            "injuries_count": len(ctx_data["injuries"]["home"]) + len(ctx_data["injuries"]["away"]),
        })

    return _cache_set(cache_key, {"count": len(items), "items": items})


@router.get("/matches/{match_id}")
def get_match_detail(
    match_id: int,
    market_key: Optional[str] = None,
    selection_key: Optional[str] = None,
    db: Session = Depends(get_db),
):
    m = db.query(Match).filter(Match.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    odds_rows = db.query(OddsCurrent).filter(OddsCurrent.match_id == m.id).all()
    preds = (
        db.query(Prediction)
        .filter(Prediction.match_id == m.id)
        .order_by(Prediction.confidence_score.desc())
        .all()
    )
    top_pred = preds[0] if preds else None
    hist_rows = (
        db.query(OddsHistory)
        .filter(OddsHistory.match_id == m.id)
        .order_by(OddsHistory.recorded_at.asc())
        .all()
    )

    # Focus odds history on the requested coupon pick (if provided) or the top prediction
    target_mkt = market_key or (top_pred.market_key if top_pred else "MS")
    target_sel = selection_key or (top_pred.selection_key if top_pred else "1")
    hist_by_time: dict[str, dict] = {}
    for h in hist_rows:
        if h.market_key != target_mkt or h.selection_key != target_sel:
            continue
        t_lbl = to_tr_datetime(h.recorded_at).strftime("%H:%M")
        if t_lbl not in hist_by_time:
            hist_by_time[t_lbl] = {"time": t_lbl}
        hist_by_time[t_lbl][h.selection_key] = h.iddaa_odd

    return {
        "match_id": m.id,
        "iddaa_id": m.iddaa_id,
        "league": m.league,
        "country": m.country,
        "is_major_league": m.is_major_league,
        "home_team": m.home_team,
        "away_team": m.away_team,
        "home_logo_url": m.home_logo_url,
        "away_logo_url": m.away_logo_url,
        "league_logo_url": m.league_logo_url,
        "status": m.status,
        "home_score": m.home_score,
        "away_score": m.away_score,
        "data_status": m.data_status,
        **_match_time_info(m.start_time_utc),
        "context": _serialize_ctx(
            m.context,
            expected_pick=f"{top_pred.market_key}:{top_pred.selection_key}" if top_pred else None,
        ),
        "top_prediction": {
            "prediction_id": top_pred.id,
            "market_key": top_pred.market_key,
            "selection_key": top_pred.selection_key,
            "iddaa_odd": top_pred.iddaa_odd,
            "global_odd": top_pred.global_odd,
            "confidence_score": top_pred.confidence_score,
            "value_edge_pct": top_pred.value_edge_pct,
            "odds_drop_pct": top_pred.odds_drop_pct,
            "breakdown": json.loads(top_pred.score_breakdown_json),
        }
        if top_pred
        else None,
        "odds": [
            {
                "market_key": o.market_key,
                "selection_key": o.selection_key,
                "iddaa_odd": o.iddaa_odd,
                "opening_odd": o.opening_odd,
                "global_avg_odd": o.global_avg_odd,
                "global_source": o.global_source,
            }
            for o in odds_rows
        ],
        "odds_history_market": target_mkt,
        "odds_history_selection": target_sel,
        "odds_history": list(hist_by_time.values()),
    }


@router.post("/matches/{match_id}/ai-summary")
def create_or_get_ai_summary(
    match_id: int,
    force: bool = False,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    m = db.query(Match).filter(Match.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    effective_force = bool(force and x_cron_secret and x_cron_secret == settings.cron_secret)
    text, src = generate_match_summary(db, m, force_refresh=effective_force)
    return {"match_id": m.id, "ai_summary": text, "ai_source": src}


MANUAL_REFRESH_COOLDOWN_SEC = 180
MANUAL_REFRESH_KEY = "system:manual_refresh_at"


@router.post("/refresh")
def trigger_manual_refresh(
    background_tasks: BackgroundTasks,
    x_cron_secret: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    if is_sync_running(db):
        return {"status": "already_running"}

    is_cron = bool(x_cron_secret and x_cron_secret == settings.cron_secret)
    now = datetime.now(timezone.utc)

    if not is_cron:
        row = db.query(ScraperCache).filter(ScraperCache.cache_key == MANUAL_REFRESH_KEY).first()
        if row and row.payload_json:
            try:
                last_iso = json.loads(row.payload_json).get("triggered_at")
                if last_iso:
                    last_dt = datetime.fromisoformat(last_iso)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    elapsed = (now - last_dt).total_seconds()
                    if elapsed < MANUAL_REFRESH_COOLDOWN_SEC:
                        return {
                            "status": "cooldown",
                            "retry_after_sec": max(1, int(MANUAL_REFRESH_COOLDOWN_SEC - elapsed)),
                        }
            except (ValueError, json.JSONDecodeError):
                pass

        payload = json.dumps({"triggered_at": now.isoformat()})
        if row:
            row.payload_json = payload
            row.updated_at = now
        else:
            db.add(ScraperCache(cache_key=MANUAL_REFRESH_KEY, payload_json=payload, updated_at=now))
        db.commit()

    background_tasks.add_task(run_sync_pipeline)
    return {"status": "started", "retry_after_sec": MANUAL_REFRESH_COOLDOWN_SEC}


def _serialize_coupon(c: Coupon, now_utc: datetime) -> dict:
    items_out = []
    for item in c.items:
        p = item.prediction
        m = p.match
        m_start = m.start_time_utc.replace(tzinfo=timezone.utc) if m.start_time_utc.tzinfo is None else m.start_time_utc
        tr_dt = to_tr_datetime(m.start_time_utc)
        if p.status in ("WON", "LOST", "VOID"):
            leg_res = p.status
        elif m.status == "LIVE" or (m.status == "SCHEDULED" and m_start <= now_utc):
            leg_res = "LIVE"
        else:
            leg_res = "PENDING"
        items_out.append({
            "prediction_id": p.id,
            "match_id": p.match_id,
            "league": m.league,
            "home_team": m.home_team,
            "away_team": m.away_team,
            "home_logo_url": m.home_logo_url,
            "away_logo_url": m.away_logo_url,
            "league_logo_url": m.league_logo_url,
            "start_date_tr": tr_dt.strftime("%d.%m"),
            "start_time_tr": tr_dt.strftime("%H:%M"),
            "market_key": p.market_key,
            "selection_key": p.selection_key,
            "iddaa_odd": item.odd_at_pick if item.odd_at_pick is not None else p.iddaa_odd,
            "confidence_score": p.published_confidence if p.published_confidence is not None else p.confidence_score,
            "status": p.status,
            "match_status": m.status,
            "home_score": m.home_score,
            "away_score": m.away_score,
            "leg_result": leg_res,
        })
    return {
        "id": c.id,
        "coupon_date": c.coupon_date,
        "tier": c.tier,
        "total_odd": c.total_odd,
        "avg_confidence": c.avg_confidence,
        "status": c.status,
        "is_locked": bool(c.is_locked),
        "items": items_out,
    }


def _query_coupon_history(db: Session, days: int = 14) -> list[dict]:
    cutoff_date = (tr_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    hist = (
        db.query(Coupon)
        .options(joinedload(Coupon.items).joinedload(CouponItem.prediction).joinedload(Prediction.match))
        .filter(Coupon.status.in_(["WON", "LOST"]), Coupon.coupon_date >= cutoff_date)
        .order_by(Coupon.coupon_date.desc(), Coupon.id.desc())
        .all()
    )
    now_utc = datetime.now(timezone.utc)
    return [_serialize_coupon(c, now_utc) for c in hist]


@router.get("/coupons")
def get_coupons(
    include_history: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    cache_key = f"coupons:{include_history}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    today_str = tr_today_str()
    now_utc = datetime.now(timezone.utc)
    coupons: list[Coupon] = []

    for tier in ("BANKO", "DENGELI", "SURPRIZ"):
        # 1. Active PENDING locked/started coupon (survives midnight until all legs finish)
        active_pending = (
            db.query(Coupon)
            .options(joinedload(Coupon.items).joinedload(CouponItem.prediction).joinedload(Prediction.match))
            .filter(
                Coupon.tier == tier,
                Coupon.status == "PENDING",
                or_(Coupon.coupon_date == today_str, Coupon.is_locked.is_(True)),
            )
            .order_by(Coupon.coupon_date.desc(), Coupon.id.desc())
            .first()
        )
        if active_pending:
            coupons.append(active_pending)
            continue

        # 2. Otherwise today's coupon (e.g. already settled today)
        today_coupon = (
            db.query(Coupon)
            .options(joinedload(Coupon.items).joinedload(CouponItem.prediction).joinedload(Prediction.match))
            .filter(Coupon.tier == tier, Coupon.coupon_date == today_str)
            .order_by(Coupon.id.desc())
            .first()
        )
        if today_coupon:
            coupons.append(today_coupon)

    if not coupons:
        coupons = (
            db.query(Coupon)
            .options(joinedload(Coupon.items).joinedload(CouponItem.prediction).joinedload(Prediction.match))
            .order_by(Coupon.coupon_date.desc(), Coupon.id.asc())
            .limit(3)
            .all()
        )

    out = [_serialize_coupon(c, now_utc) for c in coupons]
    payload: dict[str, Any] = {"coupons": out}
    if include_history:
        payload["history"] = _query_coupon_history(db, days=14)
    return _cache_set(cache_key, payload)


@router.get("/coupons/history")
def get_coupons_history(db: Session = Depends(get_db)):
    cached = _cache_get("coupons_history")
    if cached is not None:
        return cached
    return _cache_set("coupons_history", {"coupons": _query_coupon_history(db, days=14)})


@router.get("/performance")
def get_performance(db: Session = Depends(get_db)):
    cached = _cache_get("performance")
    if cached is not None:
        return cached

    settled_raw = (
        db.query(Prediction)
        .options(joinedload(Prediction.match))
        .join(Match, Prediction.match_id == Match.id)
        .filter(
            Prediction.status.in_(["WON", "LOST"]),
            Prediction.is_top_pick.is_(True),
            func.coalesce(Prediction.published_confidence, Prediction.confidence_score) >= 60.0,
        )
        .order_by(Prediction.settled_at.desc(), Prediction.confidence_score.desc())
        .all()
    )
    # Hard guard: at most 1 official settled row per finished match
    seen_match_ids: set[int] = set()
    settled: list[Prediction] = []
    for p in settled_raw:
        if p.match_id in seen_match_ids:
            continue
        seen_match_ids.add(p.match_id)
        settled.append(p)
    won = sum(1 for p in settled if p.status == "WON")
    total = len(settled)
    win_rate = round((won / total) * 100.0, 1) if total else None

    # Daily trend for Recharts (real settled data only)
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"won": 0, "total": 0})
    for p in settled:
        if p.settled_at:
            d_key = to_tr_datetime(p.settled_at).strftime("%d.%m")
            by_day[d_key]["total"] += 1
            if p.status == "WON":
                by_day[d_key]["won"] += 1

    trend = [
        {
            "date": d,
            "win_rate": round((vals["won"] / vals["total"]) * 100.0, 1),
            "total": vals["total"],
        }
        for d, vals in by_day.items()
    ]

    # Coupon tier stats
    tier_stats = {}
    for tier in ("BANKO", "DENGELI", "SURPRIZ"):
        cs = db.query(Coupon).filter(Coupon.tier == tier, Coupon.status.in_(["WON", "LOST"])).all()
        c_won = sum(1 for x in cs if x.status == "WON")
        tier_stats[tier] = {
            "total": len(cs),
            "won": c_won,
            "win_rate": round((c_won / len(cs)) * 100.0, 1) if cs else None,
        }

    # CLV, Brier Score, and Confidence Band Calibration (prefer write-once published_* snapshots)
    clv_vals: list[float] = []
    for p in settled:
        if p.published_odd is not None and p.closing_odd and p.closing_odd > 1.0:
            clv_vals.append(round(((p.published_odd / p.closing_odd) - 1.0) * 100.0, 2))
        elif p.clv_pct is not None:
            clv_vals.append(p.clv_pct)
    avg_clv_pct = round(sum(clv_vals) / len(clv_vals), 2) if clv_vals else None

    brier_terms = []
    bands = {
        "60-69": {"min": 60, "max": 69.99, "won": 0, "total": 0},
        "70-79": {"min": 70, "max": 79.99, "won": 0, "total": 0},
        "80+": {"min": 80, "max": 100, "won": 0, "total": 0},
    }
    for p in settled:
        outcome = 1.0 if p.status == "WON" else 0.0
        if p.published_prob is not None:
            brier_terms.append((float(p.published_prob) - outcome) ** 2)
        else:
            bd = json.loads(p.score_breakdown_json) if p.score_breakdown_json else {}
            prob_pct = bd.get("blended_prob_pct") or bd.get("true_prob_pct")
            if prob_pct is not None:
                prob = float(prob_pct) / 100.0
                brier_terms.append((prob - outcome) ** 2)

        eff_conf = p.published_confidence if p.published_confidence is not None else p.confidence_score
        for b_cfg in bands.values():
            if b_cfg["min"] <= eff_conf <= b_cfg["max"]:
                b_cfg["total"] += 1
                if p.status == "WON":
                    b_cfg["won"] += 1

    brier_score = round(sum(brier_terms) / len(brier_terms), 4) if brier_terms else None
    calibration_bands = [
        {
            "band": b_name,
            "total": b_cfg["total"],
            "won": b_cfg["won"],
            "win_rate": round((b_cfg["won"] / b_cfg["total"]) * 100.0, 1) if b_cfg["total"] else None,
        }
        for b_name, b_cfg in bands.items()
    ]

    archive = [
        {
            "prediction_id": p.id,
            "match_id": p.match_id,
            "league": p.match.league,
            "home_team": p.match.home_team,
            "away_team": p.match.away_team,
            "home_score": p.match.home_score,
            "away_score": p.match.away_score,
            "market_key": p.market_key,
            "selection_key": p.selection_key,
            "iddaa_odd": p.published_odd if p.published_odd is not None else p.iddaa_odd,
            "confidence_score": p.published_confidence if p.published_confidence is not None else p.confidence_score,
            "status": p.status,
            "settled_at": p.settled_at.isoformat() if p.settled_at else None,
        }
        for p in settled[:50]
    ]

    breakdown = _compute_performance_breakdown(db)

    return _cache_set(
        "performance",
        {
            "overall_win_rate": win_rate,
            "won_count": won,
            "lost_count": total - won,
            "total_settled": total,
            "avg_clv_pct": avg_clv_pct,
            "brier_score": brier_score,
            "calibration_bands": calibration_bands,
            "trend": trend,
            "coupon_tiers": tier_stats,
            "archive": archive,
            "breakdown": breakdown,
        },
    )


def _market_group_label(market_key: str) -> str:
    mk = (market_key or "").upper()
    if mk == "MS":
        return "MS"
    if mk == "CS":
        return "Çifte Şans"
    if mk.startswith("AU_"):
        return "Gol Alt/Üst"
    if mk == "KG":
        return "KG"
    return mk or "Diğer"


def _format_pick_short(market_key: str, selection_key: str) -> str:
    mk = (market_key or "").upper()
    sk = (selection_key or "").upper()
    if mk == "MS":
        return f"MS {sk}"
    if mk == "CS":
        return f"ÇŞ {sk}"
    if mk.startswith("AU_"):
        line = mk.split("_", 1)[1] if "_" in mk else "2.5"
        return f"{line} {'Üst' if sk == 'UST' else 'Alt'}"
    if mk == "KG":
        return "KG Var" if sk == "VAR" else "KG Yok"
    return f"{market_key} {selection_key}".strip()


def _compute_performance_breakdown(db: Session) -> dict[str, list[dict[str, Any]]]:
    rows = (
        db.query(Prediction)
        .options(joinedload(Prediction.match))
        .join(Match, Prediction.match_id == Match.id)
        .filter(
            Prediction.status.in_(["WON", "LOST", "VOID"]),
            Prediction.is_top_pick.is_(True),
            func.coalesce(Prediction.published_confidence, Prediction.confidence_score) >= 60.0,
        )
        .order_by(Prediction.settled_at.desc(), Prediction.confidence_score.desc())
        .all()
    )
    seen_matches: set[int] = set()
    by_league: dict[str, dict[str, int]] = defaultdict(lambda: {"won": 0, "lost": 0, "void": 0})
    by_market: dict[str, dict[str, int]] = defaultdict(lambda: {"won": 0, "lost": 0, "void": 0})

    for p in rows:
        if p.match_id in seen_matches or not p.match:
            continue
        seen_matches.add(p.match_id)
        lg = p.match.league or "Diğer"
        mg = _market_group_label(p.market_key)
        st = (p.status or "").upper()
        for bucket in (by_league[lg], by_market[mg]):
            if st == "WON":
                bucket["won"] += 1
            elif st == "LOST":
                bucket["lost"] += 1
            elif st == "VOID":
                bucket["void"] += 1

    def _serialize(groups: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
        out = []
        for label, counts in groups.items():
            decided = counts["won"] + counts["lost"]
            low_sample = decided < 5
            win_rate = round((counts["won"] / decided) * 100.0, 1) if not low_sample else None
            out.append({
                "label": label,
                "won": counts["won"],
                "lost": counts["lost"],
                "void": counts["void"],
                "decided": decided,
                "low_sample": low_sample,
                "win_rate": win_rate,
            })
        out.sort(key=lambda r: (r["decided"], r["won"]), reverse=True)
        return out

    return {
        "by_league": _serialize(by_league),
        "by_market": _serialize(by_market),
    }


@router.get("/performance/breakdown")
def get_performance_breakdown(db: Session = Depends(get_db)):
    cached = _cache_get("performance_breakdown")
    if cached is not None:
        return cached
    return _cache_set("performance_breakdown", _compute_performance_breakdown(db))


@router.get("/predictions/status")
def get_predictions_batch_status(
    ids: str = Query(default="", description="Comma-separated prediction IDs (max 30)"),
    db: Session = Depends(get_db),
):
    parsed_ids: list[int] = []
    seen: set[int] = set()
    for part in (ids or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            pid = int(part)
        except ValueError:
            continue
        if pid > 0 and pid not in seen:
            seen.add(pid)
            parsed_ids.append(pid)
        if len(parsed_ids) >= 30:
            break

    if not parsed_ids:
        return {"items": []}

    preds = (
        db.query(Prediction)
        .options(joinedload(Prediction.match))
        .filter(Prediction.id.in_(parsed_ids))
        .all()
    )
    by_id = {p.id: p for p in preds}
    items = []
    for pid in parsed_ids:
        p = by_id.get(pid)
        if not p or not p.match:
            continue
        m = p.match
        items.append({
            "id": p.id,
            "home_team": m.home_team,
            "away_team": m.away_team,
            "league": m.league,
            "kickoff": m.start_time_utc.isoformat() if m.start_time_utc else None,
            "pick": _format_pick_short(p.market_key, p.selection_key),
            "market_key": p.market_key,
            "selection_key": p.selection_key,
            "odd": p.published_odd if p.published_odd is not None else p.iddaa_odd,
            "status": p.status,
            "home_score": m.home_score,
            "away_score": m.away_score,
        })
    return {"items": items}


@router.get("/announcements/active")
def get_active_announcements(db: Session = Depends(get_db)):
    now_utc = datetime.now(timezone.utc)
    rows = (
        db.query(SiteAnnouncement)
        .filter(
            SiteAnnouncement.is_active.is_(True),
            or_(SiteAnnouncement.expires_at.is_(None), SiteAnnouncement.expires_at > now_utc),
        )
        .order_by(SiteAnnouncement.created_at.desc(), SiteAnnouncement.id.desc())
        .all()
    )
    return [
        {
            "id": a.id,
            "text": a.text,
            "link_url": a.link_url,
            "is_active": a.is_active,
            "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in rows
    ]
