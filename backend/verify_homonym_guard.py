import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import Base  # noqa: E402
from app.models import Coupon, CouponItem, Match, MatchContext, Prediction, ScraperCache  # noqa: E402
from app.telegram_bot import _render_coupon_message  # noqa: E402
from scrapers.external_data import _fetch_fotmob_team_records, fetch_global_team_recent_matches  # noqa: E402
from services.ai_summary import _build_rule_based_summary  # noqa: E402
from utils.team_matcher import extract_country_context, is_country_compatible  # noqa: E402


class _Resp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def run_tests():
    now_utc = datetime.now(timezone.utc)

    # 1) Homonym guard: Barcelona (ESP) vs Barcelona SC (ECU)
    ctx_esp = extract_country_context("İspanya La Liga", "ES")
    assert is_country_compatible("ESP", "LaLiga", ctx_esp) is True
    assert is_country_compatible("ECU", "Serie A", ctx_esp) is False

    # Cross-country pairs
    assert is_country_compatible("URU", "Primera Division", extract_country_context("İngiltere Premier Lig")) is False
    assert is_country_compatible("ENG", "Premier League", extract_country_context("İngiltere Premier Lig")) is True
    assert is_country_compatible("CHI", "Primera Division", extract_country_context("İngiltere Premier Lig")) is False
    assert is_country_compatible("ARG", "Liga Profesional", extract_country_context("İngiltere Premier Lig")) is False
    assert is_country_compatible("USA", "MLS", extract_country_context("İtalya Serie A")) is False
    assert is_country_compatible("ITA", "Serie A", extract_country_context("İtalya Serie A")) is True
    # UEFA allows European, rejects ECU/USA/ARG
    ctx_ucl = extract_country_context("UEFA Şampiyonlar Ligi")
    assert is_country_compatible("ESP", "LaLiga", ctx_ucl) is True
    assert is_country_compatible("ECU", "Serie A", ctx_ucl) is False
    assert is_country_compatible("USA", "MLS", ctx_ucl) is False
    print("TEST_1_COUNTRY_CONTEXT_HOMONYM_RULES: PASS")

    # 2) Mocked FotMob search returning Barcelona SC (6453, ECU) first and FC Barcelona (8634, ESP) second
    past_dt = (now_utc - timedelta(days=3)).isoformat().replace("+00:00", "Z")

    class _MockClient:
        def __init__(self):
            self.urls = []

        def get(self, url, **kwargs):
            self.urls.append(url)
            if "search/suggest" in url:
                return _Resp(200, [{
                    "suggestions": [
                        {"type": "team", "id": "6453", "name": "Barcelona SC", "leagueName": "Serie A"},
                        {"type": "team", "id": "8634", "name": "Barcelona", "leagueName": "LaLiga"},
                    ]
                }])
            if "teams?id=6453" in url:
                return _Resp(200, {
                    "details": {"country": "ECU", "leagueName": "Serie A"},
                    "fixtures": {"allFixtures": {"fixtures": [{
                        "id": "ecu1",
                        "status": {"finished": True, "utcTime": past_dt},
                        "tournament": {"name": "LigaPro"},
                        "home": {"id": "6453", "name": "Barcelona SC", "score": 2},
                        "away": {"id": "9991", "name": "LDU de Quito", "score": 1},
                    }]}},
                })
            if "teams?id=8634" in url:
                return _Resp(200, {
                    "details": {"country": "ESP", "leagueName": "LaLiga"},
                    "fixtures": {"allFixtures": {"fixtures": [{
                        "id": "esp1",
                        "status": {"finished": True, "utcTime": past_dt},
                        "tournament": {"name": "LaLiga"},
                        "home": {"id": "8634", "name": "Barcelona", "score": 3},
                        "away": {"id": "8633", "name": "Real Sociedad", "score": 1},
                    }]}},
                })
            return _Resp(404, {})

    mc = _MockClient()
    recs = _fetch_fotmob_team_records(mc, "Barcelona", now_utc, country_ctx=ctx_esp)
    assert len(recs) == 1 and recs[0]["opponent"] == "Real Sociedad", f"Expected Real Sociedad (ESP), got {recs}"

    # Direct team_id skips search/suggest
    mc_direct = _MockClient()
    recs_direct = _fetch_fotmob_team_records(mc_direct, "Barcelona", now_utc, team_id="8634", country_ctx=ctx_esp)
    assert len(recs_direct) == 1 and recs_direct[0]["opponent"] == "Real Sociedad"
    assert not any("search/suggest" in u for u in mc_direct.urls), "Direct team_id must skip search/suggest"
    print("TEST_2_FOTMOB_HOMONYM_REJECT_AND_DIRECT_ID: PASS")

    # 3) Cache isolation between Barcelona (ESP) and Barcelona SC (ECU) + ai_summary points vs goals + Telegram V2
    db_path = Path(__file__).resolve().parent / "tmp_verify_homonym.db"
    if db_path.exists():
        db_path.unlink()
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    SessionLocal = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    try:
        with SessionLocal() as db:
            # Verify isolated cache keys
            with patch("scrapers.external_data._fetch_fotmob_team_records", side_effect=lambda c, tn, r, team_id=None, country_ctx=None: (
                [{"event_id": "e1", "dt": now_utc - timedelta(days=2), "competition": "LaLiga", "home_team": "Barcelona", "away_team": "Sevilla", "opponent": "Sevilla", "is_home": True, "home_score": 2, "away_score": 0, "gf": 2, "ga": 0, "result": "W"}]
                if (country_ctx or {}).get("cache_scope") == "ESP"
                else [{"event_id": "e2", "dt": now_utc - timedelta(days=2), "competition": "LigaPro", "home_team": "Barcelona SC", "away_team": "Aucas", "opponent": "Aucas", "is_home": True, "home_score": 1, "away_score": 1, "gf": 1, "ga": 1, "result": "D"}]
            )), patch("scrapers.external_data._fetch_365scores_team_records", return_value=[]):
                esp_prof = fetch_global_team_recent_matches("Barcelona", now_utc, db=db, league="İspanya La Liga")
                ecu_prof = fetch_global_team_recent_matches("Barcelona", now_utc, db=db, league="Ekvador Serie A")
                assert esp_prof["recent_matches"][0]["opponent"] == "Sevilla"
                assert ecu_prof["recent_matches"][0]["opponent"] == "Aucas"
                assert db.query(ScraperCache).filter(ScraperCache.cache_key == "global_team_recent:v5:barcelona:ESP").first() is not None
                assert db.query(ScraperCache).filter(ScraperCache.cache_key == "global_team_recent:v5:barcelona:ECU").first() is not None
            print("TEST_3_CACHE_ISOLATION: PASS")

            # 4) ai_summary points average vs goals average (MBGGM = 7 pts = 1.40 puan/mac, goals = 1.20)
            m = Match(
                iddaa_id="dep_barca_1", league="İspanya La Liga", country="ES", is_major_league=True,
                home_team="Deportivo La Coruna", away_team="Barcelona",
                start_time_utc=now_utc + timedelta(hours=10), status="SCHEDULED",
            )
            db.add(m)
            db.flush()
            ctx = MatchContext(
                match_id=m.id,
                home_form=json.dumps(["L", "D", "W", "W", "L"]),  # MBGGM -> 7 pts / 5 = 1.40
                away_form=json.dumps(["D", "W", "D", "L", "W"]),  # BGBMG -> 8 pts / 5 = 1.60
                home_goals_avg=1.20,
                away_goals_avg=1.40,
            )
            db.add(ctx)
            pred = Prediction(
                match_id=m.id, market_key="MS", selection_key="2", iddaa_odd=1.45,
                global_odd=1.50, confidence_score=72.0,
                score_breakdown_json=json.dumps({"blended_prob_pct": 63.0}),
                is_top_pick=True, status="PENDING",
            )
            db.add(pred)
            db.flush()
            summary = _build_rule_based_summary(m, pred, ctx, db=db)
            assert "1.40 puan/maç" in summary, f"Expected 1.40 puan/maç in summary, got: {summary}"
            assert "1.60 puan/maç" in summary, f"Expected 1.60 puan/maç in summary, got: {summary}"
            assert "1.20 puan" not in summary, f"Goal avg leaked as puan: {summary}"
            print("TEST_4_AI_SUMMARY_POINTS_VS_GOALS: PASS")

            # 5) Telegram Kupon V2 formatting
            m2 = Match(
                iddaa_id="ful_bou_1", league="İngiltere Premier Ligi", country="GB", is_major_league=True,
                home_team="Fulham", away_team="Bournemouth",
                start_time_utc=now_utc + timedelta(hours=12), status="SCHEDULED",
            )
            db.add(m2)
            db.flush()
            p2 = Prediction(
                match_id=m2.id, market_key="CS", selection_key="X2", iddaa_odd=1.31,
                global_odd=1.35, confidence_score=70.6,
                score_breakdown_json=json.dumps({"blended_prob_pct": 68.6}),
                is_top_pick=True, status="PENDING",
            )
            db.add(p2)
            db.flush()
            c = Coupon(
                coupon_date="2026-11-28", tier="BANKO", total_odd=1.90,
                avg_confidence=71.3, status="PENDING", is_locked=False,
            )
            db.add(c)
            db.flush()
            db.add_all([
                CouponItem(coupon_id=c.id, prediction_id=pred.id, odd_at_pick=1.45),
                CouponItem(coupon_id=c.id, prediction_id=p2.id, odd_at_pick=1.31),
            ])
            db.commit()
            db.refresh(c)
            msg, _ = _render_coupon_message(c)
            assert "Dinamik 2–3 maç · 1.20–1.58 oran" in msg, f"Missing V2 subtitle: {msg}"
            assert "Kombine İhtimal:" in msg, f"Missing combined prob: {msg}"
            assert "İhtimal:<b>%69</b>" in msg or "İhtimal:<b>%63</b>" in msg, f"Missing leg prob: {msg}"
            assert "24 saat içinde sonuçlanır" in msg, f"Missing settlement label: {msg}"
            print("TEST_5_TELEGRAM_KUPON_V2_FORMAT: PASS")
    finally:
        eng.dispose()
        if db_path.exists():
            db_path.unlink()

    print("\nALL_HOMONYM_GUARD_TESTS_PASSED")


if __name__ == "__main__":
    run_tests()
