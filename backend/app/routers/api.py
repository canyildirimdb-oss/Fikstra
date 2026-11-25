import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    Coupon,
    Match,
    MatchContext,
    OddsCurrent,
    OddsHistory,
    Prediction,
    ScraperCache,
    SyncLog,
)
from scrapers.iddaa import is_youth_or_women_match
from services.ai_summary import generate_match_summary
from services.analyzer import MAX_HIGHLIGHT_ODD, MIN_ODD_RULE
from services.sync import is_sync_running, run_sync_pipeline
from utils.time_utils import is_tr_today_or_next_24h, to_tr_datetime, tr_today_str

router = APIRouter(prefix="/api", tags=["api"])


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

    settled_preds = (
        db.query(Prediction)
        .filter(Prediction.status.in_(["WON", "LOST"]))
        .all()
    )
    won_cnt = sum(1 for p in settled_preds if p.status == "WON")
    win_rate = round((won_cnt / len(settled_preds)) * 100.0, 1) if settled_preds else None

    kickoff_lock = datetime.now(timezone.utc) + timedelta(minutes=5)
    pending = (
        db.query(Prediction)
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

    return {
        "last_sync_at": last_sync_iso,
        "minutes_ago": minutes_ago,
        "overall_win_rate": win_rate,
        "settled_total": len(settled_preds),
        "active_matches_count": db.query(Match).filter(Match.status == "SCHEDULED").count(),
        "top_confidence": _mini(top_conf),
        "best_value": _mini(best_val),
        "sharpest_drop": _mini(sharpest_drop),
    }


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
    kickoff_lock = datetime.now(timezone.utc) + timedelta(minutes=5)
    q = (
        db.query(Prediction)
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
    items: list[dict] = []

    for p in preds:
        m = p.match
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

        bd = json.loads(p.score_breakdown_json) if p.score_breakdown_json else {}
        ctx_data = _serialize_ctx(m.context)
        curr_odd_row = (
            db.query(OddsCurrent)
            .filter(
                OddsCurrent.match_id == m.id,
                OddsCurrent.market_key == p.market_key,
                OddsCurrent.selection_key == p.selection_key,
            )
            .first()
        )

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
        if len(items) >= limit:
            break

    return {"count": len(items), "items": items}


@router.get("/matches")
def get_matches_bulletin(
    today_only: bool = Query(default=False),
    major_only: bool = Query(default=False),
    min_odd: float = Query(default=1.01, ge=1.0),
    market: str = Query(default="MS"),
    search: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(Match).filter(Match.status.in_(["SCHEDULED", "LIVE"]))
    if major_only:
        q = q.filter(Match.is_major_league.is_(True))
    matches = q.order_by(Match.is_major_league.desc(), Match.start_time_utc.asc()).all()

    search_low = (search or "").strip().lower()
    grouped: dict[str, list[dict]] = defaultdict(list)

    for m in matches:
        if today_only and not is_tr_today_or_next_24h(m.start_time_utc):
            continue
        if search_low and (
            search_low not in m.home_team.lower()
            and search_low not in m.away_team.lower()
            and search_low not in m.league.lower()
        ):
            continue

        odds_rows = (
            db.query(OddsCurrent)
            .filter(OddsCurrent.match_id == m.id, OddsCurrent.market_key == market)
            .all()
        )
        if not odds_rows:
            continue
        if min_odd > 1.01 and not any(o.iddaa_odd >= min_odd for o in odds_rows):
            continue

        top_pred = (
            db.query(Prediction)
            .filter(Prediction.match_id == m.id, Prediction.status == "PENDING")
            .order_by(Prediction.confidence_score.desc())
            .first()
        )

        grouped[m.league].append({
            "match_id": m.id,
            "league": m.league,
            "is_major_league": m.is_major_league,
            "home_team": m.home_team,
            "away_team": m.away_team,
            "home_logo_url": m.home_logo_url,
            "away_logo_url": m.away_logo_url,
            "league_logo_url": m.league_logo_url,
            "status": m.status,
            "home_score": m.home_score,
            "away_score": m.away_score,
            **_match_time_info(m.start_time_utc),
            "top_confidence": top_pred.confidence_score if top_pred else None,
            "odds": [
                {
                    "market_key": o.market_key,
                    "selection_key": o.selection_key,
                    "iddaa_odd": o.iddaa_odd,
                    "opening_odd": o.opening_odd,
                    "global_avg_odd": o.global_avg_odd,
                }
                for o in odds_rows
            ],
        })

    leagues_list = [{"league": lg, "matches": ms} for lg, ms in grouped.items()]
    return {"leagues_count": len(leagues_list), "leagues": leagues_list}


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


@router.get("/coupons")
def get_coupons(db: Session = Depends(get_db)):
    today_str = tr_today_str()
    coupons = (
        db.query(Coupon)
        .filter(Coupon.coupon_date == today_str)
        .order_by(Coupon.id.asc())
        .all()
    )
    if not coupons:
        coupons = db.query(Coupon).order_by(Coupon.coupon_date.desc(), Coupon.id.asc()).limit(3).all()

    out = []
    for c in coupons:
        out.append({
            "id": c.id,
            "coupon_date": c.coupon_date,
            "tier": c.tier,
            "total_odd": c.total_odd,
            "avg_confidence": c.avg_confidence,
            "status": c.status,
            "is_locked": bool(c.is_locked),
            "items": [
                {
                    "prediction_id": item.prediction.id,
                    "match_id": item.prediction.match_id,
                    "league": item.prediction.match.league,
                    "home_team": item.prediction.match.home_team,
                    "away_team": item.prediction.match.away_team,
                    "home_logo_url": item.prediction.match.home_logo_url,
                    "away_logo_url": item.prediction.match.away_logo_url,
                    "league_logo_url": item.prediction.match.league_logo_url,
                    "start_time_tr": to_tr_datetime(item.prediction.match.start_time_utc).strftime("%H:%M"),
                    "market_key": item.prediction.market_key,
                    "selection_key": item.prediction.selection_key,
                    "iddaa_odd": item.prediction.iddaa_odd,
                    "confidence_score": item.prediction.confidence_score,
                    "status": item.prediction.status,
                }
                for item in c.items
            ],
        })
    return {"coupons": out}


@router.get("/performance")
def get_performance(db: Session = Depends(get_db)):
    settled = (
        db.query(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .filter(Prediction.status.in_(["WON", "LOST"]))
        .order_by(Prediction.settled_at.desc())
        .all()
    )
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

    # CLV, Brier Score, and Confidence Band Calibration
    clv_vals = [p.clv_pct for p in settled if p.clv_pct is not None]
    avg_clv_pct = round(sum(clv_vals) / len(clv_vals), 2) if clv_vals else None

    brier_terms = []
    bands = {
        "60-69": {"min": 60, "max": 69.99, "won": 0, "total": 0},
        "70-79": {"min": 70, "max": 79.99, "won": 0, "total": 0},
        "80+": {"min": 80, "max": 100, "won": 0, "total": 0},
    }
    for p in settled:
        outcome = 1.0 if p.status == "WON" else 0.0
        bd = json.loads(p.score_breakdown_json) if p.score_breakdown_json else {}
        prob_pct = bd.get("blended_prob_pct") or bd.get("true_prob_pct")
        if prob_pct is not None:
            prob = float(prob_pct) / 100.0
            brier_terms.append((prob - outcome) ** 2)

        for b_cfg in bands.values():
            if b_cfg["min"] <= p.confidence_score <= b_cfg["max"]:
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
            "iddaa_odd": p.iddaa_odd,
            "confidence_score": p.confidence_score,
            "status": p.status,
            "settled_at": p.settled_at.isoformat() if p.settled_at else None,
        }
        for p in settled[:50]
    ]

    return {
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
    }
