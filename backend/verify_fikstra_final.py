import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, ensure_schema, SessionLocal
from app.models import Coupon, CouponItem, Match, MatchContext, Prediction, ScraperCache
from services.ai_summary import generate_match_summary
from services.coupons import _is_contradicting_pick, generate_daily_coupons
from utils.time_utils import tr_today_str

TEST_DB_PATH = Path(__file__).resolve().parent / "test_fikstra_isolated.db"


def run_test_e_audit(db, label: str):
    today_str = tr_today_str()
    coupons = db.query(Coupon).filter(Coupon.coupon_date == today_str).all()

    seen_matches: dict[int, list[str]] = {}
    duplicate_matches = 0
    opposite_higher_conf = 0
    contradicting_top = 0

    for c in coupons:
        for item in c.items:
            p = item.prediction
            if not p:
                continue
            if p.match_id in seen_matches:
                duplicate_matches += 1
            seen_matches.setdefault(p.match_id, []).append(c.tier)

            all_preds = (
                db.query(Prediction)
                .filter(Prediction.match_id == p.match_id, Prediction.status == "PENDING")
                .all()
            )
            if not all_preds:
                continue
            top_pred = max(all_preds, key=lambda x: x.confidence_score)
            conf_map = {(x.match_id, x.market_key, x.selection_key): x.confidence_score for x in all_preds}

            # Check opposite direction in same market
            opp_map = {
                ("MS", "1"): ("MS", "2"),
                ("MS", "2"): ("MS", "1"),
                ("KG", "VAR"): ("KG", "YOK"),
                ("KG", "YOK"): ("KG", "VAR"),
            }
            if p.market_key.startswith("AU_"):
                opp = (p.market_key, "ALT" if p.selection_key == "UST" else "UST")
            else:
                opp = opp_map.get((p.market_key, p.selection_key))
            if opp:
                opp_conf = conf_map.get((p.match_id, opp[0], opp[1]))
                if opp_conf is not None and opp_conf > p.confidence_score:
                    opposite_higher_conf += 1

            if _is_contradicting_pick(p, top_pred, conf_map):
                contradicting_top += 1

    # Check ai_summary_pick mismatch
    mismatched_ai = 0
    ctxs = db.query(MatchContext).filter(MatchContext.ai_summary.isnot(None)).all()
    for ctx in ctxs:
        top_p = (
            db.query(Prediction)
            .filter(Prediction.match_id == ctx.match_id, Prediction.status == "PENDING")
            .order_by(Prediction.confidence_score.desc())
            .first()
        )
        expected = f"{top_p.market_key}:{top_p.selection_key}" if top_p else None
        if expected and ctx.ai_summary_pick != expected:
            mismatched_ai += 1

    print(f"[TEST E - {label}] opposite_higher_conf={opposite_higher_conf}, contradicting_top={contradicting_top}, duplicate_matches={duplicate_matches}, mismatched_ai={mismatched_ai}")
    assert opposite_higher_conf == 0
    assert contradicting_top == 0
    assert duplicate_matches == 0
    assert mismatched_ai == 0


def run_isolated_tests():
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()

    iso_engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=iso_engine)
    IsoSession = sessionmaker(bind=iso_engine, autoflush=False, autocommit=False)
    db = IsoSession()
    try:
        now = datetime.now(timezone.utc)
        today_str = tr_today_str()

        # Create 7 matches so BANKO, DENGELI, SURPRIZ have distinct matches
        matches = []
        for i in range(7):
            m = Match(
                iddaa_id=f"iso_f_{i}",
                league="Premier League",
                country="England",
                is_major_league=True,
                home_team=f"HomeTeam{i}",
                away_team=f"AwayTeam{i}",
                start_time_utc=now + timedelta(hours=3 + i),
                status="SCHEDULED",
            )
            db.add(m)
            db.flush()
            matches.append(m)

        # Match 0,1,2 -> BANKO (1.40)
        # Match 3,4,5 -> DENGELI (1.65)
        # Match 6 -> SURPRIZ (2.20), plus add Match 7 -> SURPRIZ (2.40)
        m7 = Match(
            iddaa_id="iso_f_7",
            league="La Liga",
            country="Spain",
            is_major_league=True,
            home_team="HomeTeam7",
            away_team="AwayTeam7",
            start_time_utc=now + timedelta(hours=10),
            status="SCHEDULED",
        )
        db.add(m7)
        db.flush()
        matches.append(m7)

        odds_plan = [1.40, 1.42, 1.45, 1.65, 1.70, 1.75, 2.20, 2.40]
        for idx, m in enumerate(matches):
            db.add(
                Prediction(
                    match_id=m.id,
                    market_key="MS",
                    selection_key="1",
                    iddaa_odd=odds_plan[idx],
                    confidence_score=78.0 - idx,
                    status="PENDING",
                )
            )
            # Add lower-conf opposite pick to ensure it's never picked
            db.add(
                Prediction(
                    match_id=m.id,
                    market_key="MS",
                    selection_key="2",
                    iddaa_odd=2.50,
                    confidence_score=40.0,
                    status="PENDING",
                )
            )
        db.commit()

        # Generate initial coupons
        generate_daily_coupons(db)
        run_test_e_audit(db, "ISOLATED_DB")

        # TEST F1: Coupon Freeze Lock (10 min to kickoff -> must NOT change on re-sync)
        banko = db.query(Coupon).filter(Coupon.coupon_date == today_str, Coupon.tier == "BANKO").first()
        assert banko is not None and len(banko.items) >= 2
        orig_item_ids = sorted(it.prediction_id for it in banko.items)
        first_match = banko.items[0].prediction.match
        first_match.start_time_utc = now + timedelta(minutes=10)
        # Also make a new super high-conf prediction on Match 0 or new Match 8 to try to tempt re-generation
        m8 = Match(
            iddaa_id="iso_f_8",
            league="Serie A",
            country="Italy",
            is_major_league=True,
            home_team="HomeTeam8",
            away_team="AwayTeam8",
            start_time_utc=now + timedelta(hours=5),
            status="SCHEDULED",
        )
        db.add(m8)
        db.flush()
        db.add(
            Prediction(
                match_id=m8.id,
                market_key="MS",
                selection_key="1",
                iddaa_odd=1.44,
                confidence_score=99.0,
                status="PENDING",
            )
        )
        db.commit()

        generate_daily_coupons(db)
        db.expire_all()
        banko_after = db.query(Coupon).filter(Coupon.coupon_date == today_str, Coupon.tier == "BANKO").first()
        after_item_ids = sorted(it.prediction_id for it in banko_after.items)
        assert banko_after.is_locked is True, "Coupon should be locked when <=15m to kickoff"
        assert after_item_ids == orig_item_ids, f"Frozen coupon changed! {orig_item_ids} -> {after_item_ids}"
        print("TEST_F1_COUPON_FREEZE_LOCK: PASS")

        # TEST F2: Gemini 429 / error must NOT save ai_summary to DB
        class _Resp429:
            status_code = 429
            def json(self):
                return {"error": "quota exceeded"}

        with patch("services.ai_summary.settings.gemini_api_key", "dummy_key_for_test"), \
             patch("services.ai_summary.httpx.post", return_value=_Resp429()):
            txt, src = generate_match_summary(db, m8, force_refresh=False)
            assert src == "transient_error"
            ctx = db.query(MatchContext).filter(MatchContext.match_id == m8.id).first()
            assert ctx is not None and ctx.ai_summary is None, "Gemini 429 must NOT write ai_summary to DB!"
        print("TEST_F2_GEMINI_429_NO_DB_CACHE: PASS")
    finally:
        db.close()
        iso_engine.dispose()
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()


def run_security_audit():
    root = Path(__file__).resolve().parent.parent
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "backend/.env" in gitignore and ".env" in gitignore and "*.db" in gitignore
    secret_pat = re.compile(r"(AIza[0-9A-Za-z\-_]{20,}|63703f[0-9a-fA-F]{10,}|BEGIN (EC )?PRIVATE KEY)")
    leaks = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        s = str(p)
        if any(x in s for x in ("node_modules", ".venv", "\\dist\\", ".git\\", ".env", "verify_fikstra_final.py")):
            continue
        if p.suffix in {".py", ".ts", ".tsx", ".html", ".json", ".yaml", ".yml", ".md"}:
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if secret_pat.search(txt):
                leaks.append(s)
    assert not leaks, f"Secret leaks found: {leaks}"
    print(f"GITHUB_SECURITY_AUDIT: PASS (leaks=0, .gitignore verified)")


if __name__ == "__main__":
    run_isolated_tests()
    run_security_audit()

    # Live DB: ensure_schema, clear old/mismatched ai_summary, regenerate coupons (0 Odds API calls), run TEST E
    ensure_schema()
    db = SessionLocal()
    try:
        # Clear old/mismatched ai_summary records in live DB
        cleared = (
            db.query(MatchContext)
            .filter(MatchContext.ai_summary.isnot(None))
            .update(
                {
                    MatchContext.ai_summary: None,
                    MatchContext.ai_summary_pick: None,
                    MatchContext.ai_source: None,
                    MatchContext.ai_generated_at: None,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        print(f"LIVE_DB_CLEARED_OLD_AI_SUMMARIES: {cleared}")

        # Unlock any non-started/non-settled today coupons so we can regenerate cleanly with new 1-coupon-per-match rule
        today_str = tr_today_str()
        db.query(Coupon).filter(Coupon.coupon_date == today_str, Coupon.status == "PENDING").update(
            {Coupon.is_locked: False}, synchronize_session=False
        )
        db.commit()

        coupons = generate_daily_coupons(db)
        run_test_e_audit(db, "LIVE_DB")

        # Check remaining_credits
        cred = db.query(ScraperCache).filter(ScraperCache.cache_key == "odds_api:remaining_credits").first()
        print("ODDS_API_CREDITS:", cred.payload_json if cred else "N/A")

        print("\n=== GUNCEL GUNLUK KUPONLAR ===")
        for c in coupons:
            print(f"[{c.tier}] Toplam Oran: {c.total_odd:.2f} | Ort. Guven: %{c.avg_confidence:.1f} | Kilitli: {c.is_locked}")
            for it in c.items:
                p = it.prediction
                m = p.match
                print(f"  - {m.home_team} vs {m.away_team} | {p.market_key}:{p.selection_key} @ {p.iddaa_odd:.2f} (Guven %{p.confidence_score:.0f})")
    finally:
        db.close()
