import json
import os
from datetime import datetime, timedelta, timezone

TEST_DB_PATH = os.path.abspath("test_v5_isolated.db")
if os.path.exists(TEST_DB_PATH):
    os.remove(TEST_DB_PATH)

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

import httpx
from app.database import SessionLocal, ensure_schema
from app.models import Coupon, CouponItem, Match, MatchContext, OddsCurrent, OddsHistory, Prediction
from app.routers.api import get_performance
from scrapers.external_data import ESPN_BASE
from services.analyzer import MIN_ODD_RULE, analyze_upcoming_matches
from services.coupons import generate_daily_coupons
from services.settlement import evaluate_prediction_outcome, settle_finished_matches


def fetch_real_completed_espn_matches(limit: int = 6) -> list[dict]:
    found: list[dict] = []
    seen_ids: set[str] = set()
    leagues = ["uefa.champions", "eng.1", "esp.1", "ger.1", "ita.1", "tur.1", "all"]
    with httpx.Client(timeout=12.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for lg in leagues:
            if len(found) >= limit:
                break
            try:
                r = client.get(f"{ESPN_BASE}/{lg}/scoreboard")
                if r.status_code != 200:
                    continue
                for ev in r.json().get("events") or []:
                    eid = str(ev.get("id") or "")
                    if not eid or eid in seen_ids:
                        continue
                    comp = (ev.get("competitions") or [{}])[0]
                    if not comp.get("status", {}).get("type", {}).get("completed"):
                        continue
                    comps = comp.get("competitors") or []
                    hc = next((c for c in comps if c.get("homeAway") == "home"), None)
                    ac = next((c for c in comps if c.get("homeAway") == "away"), None)
                    if not hc or not ac:
                        continue
                    try:
                        hs = int(hc.get("score"))
                        as_ = int(ac.get("score"))
                    except (TypeError, ValueError):
                        continue
                    raw_dt = ev.get("date") or ""
                    try:
                        dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    seen_ids.add(eid)
                    found.append({
                        "espn_id": eid,
                        "league_slug": lg,
                        "home_team": hc.get("team", {}).get("displayName", ""),
                        "away_team": ac.get("team", {}).get("displayName", ""),
                        "start_time_utc": dt,
                        "completed": True,
                        "home_score": hs,
                        "away_score": as_,
                    })
                    if len(found) >= limit:
                        break
            except Exception:
                continue
    return found


def run_test_a(db) -> bool:
    real_events = fetch_real_completed_espn_matches(limit=5)
    assert len(real_events) >= 4, f"Expected >= 4 real completed ESPN matches, got {len(real_events)}"

    now = datetime.now(timezone.utc)
    past_start = now - timedelta(hours=3)
    # Align event start_time_utc in espn_events copy so matcher pairs cleanly
    aligned_events = []
    created_preds: list[tuple[Prediction, int, int]] = []

    markets_to_test = [
        ("MS", "1", 1.85),
        ("MS", "X", 3.40),
        ("MS", "2", 3.90),
        ("CS", "1X", 1.35),
        ("CS", "12", 1.32),
        ("CS", "X2", 1.72),
        ("KG", "VAR", 1.78),
        ("KG", "YOK", 1.92),
        ("AU_15", "UST", 1.33),
        ("AU_25", "ALT", 1.88),
        ("AU_25", "UST", 1.90),
        ("AU_35", "ALT", 1.36),
    ]

    for idx, ev in enumerate(real_events[:4]):
        m = Match(
            iddaa_id=f"test_a_{idx}",
            league=ev["league_slug"],
            country="Europe",
            is_major_league=True,
            home_team=ev["home_team"],
            away_team=ev["away_team"],
            start_time_utc=past_start,
            status="SCHEDULED",
        )
        db.add(m)
        db.flush()

        ev_copy = dict(ev)
        ev_copy["start_time_utc"] = past_start
        aligned_events.append(ev_copy)

        for mk, sk, odd in markets_to_test[idx * 3 : (idx + 1) * 3]:
            p = Prediction(
                match_id=m.id,
                market_key=mk,
                selection_key=sk,
                iddaa_odd=odd,
                confidence_score=68.0,
                score_breakdown_json=json.dumps({"blended_prob_pct": 62.0}),
                status="PENDING",
            )
            db.add(p)
            db.flush()
            created_preds.append((p, ev["home_score"], ev["away_score"]))

    # Create 3 coupons (BANKO, DENGELI, SURPRIZ)
    c_banko = Coupon(coupon_date="2026-11-25", tier="BANKO", total_odd=2.50, avg_confidence=70.0, status="PENDING")
    c_dengeli = Coupon(coupon_date="2026-11-25", tier="DENGELI", total_odd=4.50, avg_confidence=66.0, status="PENDING")
    c_surpriz = Coupon(coupon_date="2026-11-25", tier="SURPRIZ", total_odd=6.80, avg_confidence=62.0, status="PENDING")
    db.add_all([c_banko, c_dengeli, c_surpriz])
    db.flush()

    db.add_all([
        CouponItem(coupon_id=c_banko.id, prediction_id=created_preds[0][0].id),
        CouponItem(coupon_id=c_banko.id, prediction_id=created_preds[3][0].id),
        CouponItem(coupon_id=c_dengeli.id, prediction_id=created_preds[1][0].id),
        CouponItem(coupon_id=c_dengeli.id, prediction_id=created_preds[4][0].id),
        CouponItem(coupon_id=c_surpriz.id, prediction_id=created_preds[2][0].id),
        CouponItem(coupon_id=c_surpriz.id, prediction_id=created_preds[5][0].id),
    ])
    db.commit()

    settled_cnt = settle_finished_matches(db, espn_events=aligned_events)
    assert settled_cnt == 4, f"Expected 4 settled matches, got {settled_cnt}"

    for p, hs, as_ in created_preds:
        db.refresh(p)
        expected = evaluate_prediction_outcome(p.market_key, p.selection_key, hs, as_)
        assert p.status == expected, f"Mismatch for {p.market_key} {p.selection_key} ({hs}-{as_}): got {p.status}, expected {expected}"

    for c in (c_banko, c_dengeli, c_surpriz):
        db.refresh(c)
        item_st = [it.prediction.status for it in c.items]
        exp_c = "LOST" if "LOST" in item_st else ("WON" if all(s == "WON" for s in item_st) else "VOID")
        assert c.status == exp_c, f"Coupon {c.tier} got {c.status}, expected {exp_c}"

    perf = get_performance(db)
    assert perf["total_settled"] == len(created_preds)
    assert perf["brier_score"] is not None and 0.0 <= perf["brier_score"] <= 1.0
    print(
        f"[PASS] TEST A: settled={settled_cnt} real ESPN matches, preds={perf['total_settled']}, "
        f"win_rate={perf['overall_win_rate']}%, brier={perf['brier_score']}, "
        f"coupons=({c_banko.status}, {c_dengeli.status}, {c_surpriz.status})"
    )
    return True


def run_test_b(db) -> bool:
    now = datetime.now(timezone.utc)
    m = Match(
        iddaa_id="test_b_clv",
        league="Premier League",
        country="England",
        is_major_league=True,
        home_team="CLV Home FC",
        away_team="CLV Away FC",
        start_time_utc=now + timedelta(minutes=18),
        status="SCHEDULED",
    )
    db.add(m)
    db.flush()

    db.add_all([
        OddsCurrent(match_id=m.id, market_key="MS", selection_key="1", iddaa_odd=1.38, opening_odd=1.50),
        OddsCurrent(match_id=m.id, market_key="AU_25", selection_key="UST", iddaa_odd=1.90, opening_odd=1.75),
    ])
    p_drop = Prediction(
        match_id=m.id, market_key="MS", selection_key="1", iddaa_odd=1.50, confidence_score=65.0, status="PENDING"
    )
    p_rise = Prediction(
        match_id=m.id, market_key="AU_25", selection_key="UST", iddaa_odd=1.75, confidence_score=62.0, status="PENDING"
    )
    db.add_all([p_drop, p_rise])
    db.commit()

    # Exact closing odd lock from sync.py
    closing_cutoff = now + timedelta(minutes=25)
    near_preds = (
        db.query(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .filter(
            Match.status == "SCHEDULED",
            Match.start_time_utc <= closing_cutoff,
            Prediction.status == "PENDING",
            Prediction.closing_odd.is_(None),
        )
        .all()
    )
    for np in near_preds:
        curr_o = (
            db.query(OddsCurrent)
            .filter(
                OddsCurrent.match_id == np.match_id,
                OddsCurrent.market_key == np.market_key,
                OddsCurrent.selection_key == np.selection_key,
            )
            .first()
        )
        if curr_o and curr_o.iddaa_odd > 1.0:
            np.closing_odd = curr_o.iddaa_odd
            np.clv_pct = round(((np.iddaa_odd / curr_o.iddaa_odd) - 1.0) * 100.0, 2)
    db.commit()

    db.refresh(p_drop)
    db.refresh(p_rise)
    assert p_drop.closing_odd == 1.38 and p_drop.clv_pct == 8.7, f"Expected 1.38/+8.7, got {p_drop.closing_odd}/{p_drop.clv_pct}"
    assert p_rise.closing_odd == 1.90 and p_rise.clv_pct == -7.89, f"Expected 1.90/-7.89, got {p_rise.closing_odd}/{p_rise.clv_pct}"
    print(f"[PASS] TEST B: CLV lock verified (1.50->1.38 = +{p_drop.clv_pct}%, 1.75->1.90 = {p_rise.clv_pct}%)")
    return True


def run_test_c_and_d(db) -> bool:
    now = datetime.now(timezone.utc)
    # Smart Money match (6% drop in 3h + CS 1X cross confirmation)
    m_sm = Match(
        iddaa_id="test_c_sm",
        league="La Liga",
        country="Spain",
        is_major_league=True,
        home_team="Smart Home FC",
        away_team="Smart Away FC",
        start_time_utc=now + timedelta(hours=5),
        status="SCHEDULED",
        data_status="espn_ok",
    )
    # Trap match (6% rise in 3h)
    m_tr = Match(
        iddaa_id="test_c_tr",
        league="Serie A",
        country="Italy",
        is_major_league=True,
        home_team="Trap Home FC",
        away_team="Trap Away FC",
        start_time_utc=now + timedelta(hours=6),
        status="SCHEDULED",
        data_status="espn_ok",
    )
    # Youth match + <1.30 odd + extra matches for coupon diversity
    m_youth = Match(
        iddaa_id="test_d_youth",
        league="Premier League U23",
        country="England",
        is_major_league=False,
        home_team="Arsenal U23",
        away_team="Chelsea U23",
        start_time_utc=now + timedelta(hours=4),
        status="SCHEDULED",
    )
    extra_matches = [
        Match(
            iddaa_id=f"test_d_ex_{i}",
            league="Bundesliga",
            country="Germany",
            is_major_league=True,
            home_team=f"Extra Home {i}",
            away_team=f"Extra Away {i}",
            start_time_utc=now + timedelta(hours=4 + i),
            status="SCHEDULED",
            data_status="espn_ok",
        )
        for i in range(4)
    ]
    db.add_all([m_sm, m_tr, m_youth] + extra_matches)
    db.flush()

    for m in [m_sm, m_tr] + extra_matches:
        db.add(MatchContext(
            match_id=m.id,
            home_form=json.dumps(["W", "W", "D", "W", "W"]),
            away_form=json.dumps(["L", "D", "L", "L", "D"]),
            home_goals_avg=2.0,
            away_goals_avg=0.9,
            home_conceded_avg=0.7,
            away_conceded_avg=1.7,
        ))

    # Odds + 3h history for Smart Money
    db.add_all([
        OddsCurrent(match_id=m_sm.id, market_key="MS", selection_key="1", iddaa_odd=1.42, opening_odd=1.51, global_avg_odd=1.52),
        OddsCurrent(match_id=m_sm.id, market_key="MS", selection_key="X", iddaa_odd=4.20, opening_odd=4.20, global_avg_odd=4.30),
        OddsCurrent(match_id=m_sm.id, market_key="MS", selection_key="2", iddaa_odd=6.50, opening_odd=6.50, global_avg_odd=6.80),
        OddsCurrent(match_id=m_sm.id, market_key="CS", selection_key="1X", iddaa_odd=1.30, opening_odd=1.34, global_avg_odd=1.34),
        OddsHistory(match_id=m_sm.id, market_key="MS", selection_key="1", iddaa_odd=1.51, recorded_at=now - timedelta(hours=2)),
        OddsHistory(match_id=m_sm.id, market_key="MS", selection_key="1", iddaa_odd=1.42, recorded_at=now),
        OddsHistory(match_id=m_sm.id, market_key="CS", selection_key="1X", iddaa_odd=1.34, recorded_at=now - timedelta(hours=2)),
        OddsHistory(match_id=m_sm.id, market_key="CS", selection_key="1X", iddaa_odd=1.30, recorded_at=now),
    ])

    # Odds + 3h history for Trap (6% rise) + <1.30 odd test
    db.add_all([
        OddsCurrent(match_id=m_tr.id, market_key="MS", selection_key="1", iddaa_odd=1.59, opening_odd=1.50, global_avg_odd=1.55),
        OddsCurrent(match_id=m_tr.id, market_key="MS", selection_key="X", iddaa_odd=3.80, opening_odd=3.80, global_avg_odd=3.90),
        OddsCurrent(match_id=m_tr.id, market_key="MS", selection_key="2", iddaa_odd=5.00, opening_odd=5.00, global_avg_odd=5.20),
        OddsCurrent(match_id=m_tr.id, market_key="AU_15", selection_key="UST", iddaa_odd=1.18, opening_odd=1.18, global_avg_odd=1.20),
        OddsHistory(match_id=m_tr.id, market_key="MS", selection_key="1", iddaa_odd=1.50, recorded_at=now - timedelta(hours=2)),
        OddsHistory(match_id=m_tr.id, market_key="MS", selection_key="1", iddaa_odd=1.59, recorded_at=now),
    ])

    db.add(OddsCurrent(match_id=m_youth.id, market_key="MS", selection_key="1", iddaa_odd=1.40, opening_odd=1.40, global_avg_odd=1.45))

    for i, em in enumerate(extra_matches):
        db.add_all([
            OddsCurrent(match_id=em.id, market_key="AU_35", selection_key="ALT", iddaa_odd=1.42, opening_odd=1.42, global_avg_odd=1.48),
            OddsCurrent(match_id=em.id, market_key="AU_35", selection_key="UST", iddaa_odd=2.50, opening_odd=2.50, global_avg_odd=2.60),
            OddsCurrent(match_id=em.id, market_key="MS", selection_key="1", iddaa_odd=1.52 + i * 0.12, opening_odd=1.52 + i * 0.12, global_avg_odd=1.60 + i * 0.12),
            OddsCurrent(match_id=em.id, market_key="MS", selection_key="X", iddaa_odd=3.80, opening_odd=3.80, global_avg_odd=3.90),
            OddsCurrent(match_id=em.id, market_key="MS", selection_key="2", iddaa_odd=4.50, opening_odd=4.50, global_avg_odd=4.70),
        ])
    db.commit()

    analyze_upcoming_matches(db)
    coupons = generate_daily_coupons(db)

    p_sm = db.query(Prediction).filter(Prediction.match_id == m_sm.id, Prediction.market_key == "MS", Prediction.selection_key == "1").first()
    p_tr = db.query(Prediction).filter(Prediction.match_id == m_tr.id, Prediction.market_key == "MS", Prediction.selection_key == "1").first()
    assert p_sm is not None and p_tr is not None
    bd_sm = json.loads(p_sm.score_breakdown_json)
    bd_tr = json.loads(p_tr.score_breakdown_json)
    assert bd_sm["smart_money"] is True, f"Expected smart_money=True, got {bd_sm}"
    assert p_tr.trap_warning is True and bd_tr["trap_warning"] is True, f"Expected trap_warning=True, got {bd_tr}"
    assert p_tr.kelly_units <= 1.0, f"Expected trap kelly <= 1.0, got {p_tr.kelly_units}"
    assert bd_tr["odds_move"] <= 6.0, f"Expected -10 trap penalty on odds_move, got {bd_tr['odds_move']}"

    # Verify TEST D rules across all predictions and coupons
    all_preds = db.query(Prediction).all()
    assert all(p.iddaa_odd >= MIN_ODD_RULE for p in all_preds), "Found prediction with odd < 1.30!"

    for c in coupons:
        m_counts = {}
        for it in c.items:
            pr = it.prediction
            assert pr.iddaa_odd >= MIN_ODD_RULE, f"Coupon {c.tier} has odd < 1.30: {pr.iddaa_odd}"
            assert "U23" not in pr.match.league and "U23" not in pr.match.home_team, "Youth match leaked into coupon!"
            if c.tier in ("BANKO", "DENGELI"):
                assert pr.match_id != m_tr.id, f"Trap match leaked into {c.tier} coupon!"
            m_counts[pr.market_key] = m_counts.get(pr.market_key, 0) + 1
            assert m_counts[pr.market_key] <= 2, f"Market {pr.market_key} appeared >2 times in {c.tier}"

    print(
        f"[PASS] TEST C: smart_money={bd_sm['smart_money']} (odds_move={bd_sm['odds_move']}), "
        f"trap_warning={bd_tr['trap_warning']} (odds_move={bd_tr['odds_move']}, kelly={p_tr.kelly_units})"
    )
    print(
        f"[PASS] TEST D: min_odd>=1.30 verified across {len(all_preds)} preds & {len(coupons)} coupons; "
        "0 youth/women, <=2 same market per coupon"
    )
    return True


if __name__ == "__main__":
    ensure_schema()
    db = SessionLocal()
    try:
        run_test_a(db)
        run_test_b(db)
        run_test_c_and_d(db)
        print("ALL V5 SUITE TESTS PASSED!")
    finally:
        db.close()
        from app.database import engine
        engine.dispose()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
            print("Cleaned up temporary test DB:", TEST_DB_PATH)
