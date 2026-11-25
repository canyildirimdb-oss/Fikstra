from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, SessionLocal, ensure_schema, get_db
from app.main import app
from app.models import Coupon, ScraperCache
from scrapers.external_data import _TM_INJURY_CACHE, fetch_transfermarkt_injuries
from services.coupons import generate_daily_coupons
from utils.time_utils import to_tr_datetime, tr_today_str

EMPTY_DB_PATH = Path(__file__).resolve().parent / "test_empty_neon_boot.db"


def test_transfermarkt_suspensions_and_tournament_match():
    _TM_INJURY_CACHE.clear()
    search_html = '<a href="/galatasaray/startseite/verein/141">Galatasaray</a>'
    inj_html = """
    <div class="box">
      <h2 class="content-box-headline">Injuries &amp; Suspensions</h2>
      <table class="items">
        <thead>
          <tr><th>Player</th><th>Age</th><th>Reason</th><th>Competition</th><th>Until</th><th>Missed</th></tr>
        </thead>
        <tbody>
          <tr class="odd">
            <td class="hauptlink"><a href="#">Player UCL Red</a></td>
            <td>25</td>
            <td>Red card suspension</td>
            <td><img title="UEFA Champions League" /></td>
            <td>01.12.2026</td>
            <td>1</td>
          </tr>
          <tr class="even">
            <td class="hauptlink"><a href="#">Player SuperLig Yellow</a></td>
            <td>27</td>
            <td>Yellow card suspension</td>
            <td><img title="Süper Lig" /></td>
            <td>30.11.2026</td>
            <td>1</td>
          </tr>
          <tr class="odd">
            <td class="hauptlink"><a href="#">Player Injured</a></td>
            <td>24</td>
            <td>Hamstring injury</td>
            <td>-</td>
            <td>15.12.2026</td>
            <td>2</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="box">
      <h2 class="content-box-headline">Players at risk of suspension</h2>
      <table class="items">
        <thead>
          <tr><th>Player</th><th>Reason</th><th>Competition</th></tr>
        </thead>
        <tbody>
          <tr class="odd">
            <td class="hauptlink"><a href="#">Player At Risk</a></td>
            <td>Threatened with suspension</td>
            <td>Süper Lig</td>
          </tr>
        </tbody>
      </table>
    </div>
    """

    class _MockResp:
        def __init__(self, text):
            self.status_code = 200
            self.text = text

    def _mock_get(url, **kwargs):
        return _MockResp(search_html if "schnellsuche" in url else inj_html)

    with patch("scrapers.external_data.cffi_requests.get", side_effect=_mock_get):
        ucl_list, _ = fetch_transfermarkt_injuries("GalatasarayTest", allow_squad_fetch=False, match_league="UEFA Şampiyonlar Ligi")
        ucl_names = {x["player"] for x in ucl_list}
        assert ucl_names == {"Player UCL Red", "Player Injured"}, f"Unexpected UCL list: {ucl_names}"

        sl_list, _ = fetch_transfermarkt_injuries("GalatasarayTest", allow_squad_fetch=False, match_league="Türkiye Süper Lig")
        sl_names = {x["player"] for x in sl_list}
        assert sl_names == {"Player SuperLig Yellow", "Player Injured"}, f"Unexpected SL list: {sl_names}"

    _TM_INJURY_CACHE.clear()
    print("TEST_TRANSFERMARKT_SUSPENSIONS_TOURNAMENT: PASS")


def test_empty_db_all_endpoints_200():
    if EMPTY_DB_PATH.exists():
        EMPTY_DB_PATH.unlink()
    eng = create_engine(f"sqlite:///{EMPTY_DB_PATH}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    EmptySession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    def _override_get_db():
        s = EmptySession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        client = TestClient(app)
        endpoints = [
            "/api/health",
            "/api/dashboard/summary",
            "/api/predictions",
            "/api/coupons",
            "/api/matches",
            "/api/performance",
        ]
        for ep in endpoints:
            r = client.get(ep)
            assert r.status_code == 200, f"{ep} returned {r.status_code}: {r.text}"
        print(f"TEST_EMPTY_DB_6_ENDPOINTS_200: PASS ({len(endpoints)}/6 HTTP 200)")
    finally:
        app.dependency_overrides.clear()
        eng.dispose()
        if EMPTY_DB_PATH.exists():
            EMPTY_DB_PATH.unlink()


def test_europe_istanbul_timezone():
    utc_dt = datetime(2026, 11, 25, 18, 45, tzinfo=timezone.utc)
    tr_dt = to_tr_datetime(utc_dt)
    assert tr_dt.strftime("%H:%M") == "21:45", f"Expected 21:45, got {tr_dt}"
    assert "+03:00" in tr_dt.isoformat()
    print(f"TEST_EUROPE_ISTANBUL_TZ: PASS ({utc_dt.isoformat()} -> {tr_dt.isoformat()})")


if __name__ == "__main__":
    test_transfermarkt_suspensions_and_tournament_match()
    test_empty_db_all_endpoints_200()
    test_europe_istanbul_timezone()

    # Live DB: regenerate coupons with strict quality thresholds (BANKO>=68, DENGELI>=62, SURPRIZ>=58)
    ensure_schema()
    db = SessionLocal()
    try:
        today_str = tr_today_str()
        db.query(Coupon).filter(Coupon.coupon_date == today_str, Coupon.status == "PENDING").update(
            {Coupon.is_locked: False}, synchronize_session=False
        )
        db.commit()

        coupons = generate_daily_coupons(db)
        min_req = {"BANKO": 68.0, "DENGELI": 62.0, "SURPRIZ": 58.0}
        print("\n=== GUNCEL KALITE BARAJLI KUPONLAR ===")
        for tier in ("BANKO", "DENGELI", "SURPRIZ"):
            c = db.query(Coupon).filter(Coupon.coupon_date == today_str, Coupon.tier == tier).first()
            if not c:
                print(f"[{tier}] BOS (Bugun bu profil icin kalite barajini gecen yeterli mac yok)")
                continue
            assert len(c.items) >= 2
            for it in c.items:
                assert it.prediction.confidence_score >= min_req[tier]
                assert it.prediction.confidence_score >= 58.0
            print(f"[{c.tier}] Toplam Oran: {c.total_odd:.2f} | Ort. Guven: {c.avg_confidence:.1f} / 100")
            for it in c.items:
                p = it.prediction
                m = p.match
                print(f"  - {m.home_team} vs {m.away_team} | {p.market_key}:{p.selection_key} @ {p.iddaa_odd:.2f} (Guven {p.confidence_score:.0f})")

        cred = db.query(ScraperCache).filter(ScraperCache.cache_key == "odds_api:remaining_credits").first()
        print("\nODDS_API_CREDITS:", cred.payload_json if cred else "N/A")
    finally:
        db.close()
