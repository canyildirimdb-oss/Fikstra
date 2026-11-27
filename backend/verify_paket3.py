"""Paket 3 unit/integration checks (0 Odds API credits, local SQLite)."""

from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Match
from services.sync import _backfill_missing_logos_from_fotmob
from utils.formatting import format_league_name_tr


def test_league_name_normalization() -> None:
    assert (
        format_league_name_tr("Meksika Primera Ligi, Açılış Playoffla")
        == "Meksika Primera Ligi, Açılış Playoffları"
    )
    assert (
        format_league_name_tr("Meksika ExpansionMX Lig,Açılış,Playoff")
        == "Meksika Expansion MX Ligi, Açılış Playoff"
    )
    assert format_league_name_tr("Türkiye Süper Lig") == "Türkiye Süper Lig"
    assert format_league_name_tr(None) == "Diğer"
    print("TEST_LEAGUE_NAME_NORMALIZATION: PASS")


def test_fotmob_logo_backfill() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    kickoff = datetime(2026, 11, 28, 18, 30, tzinfo=timezone.utc)
    m = Match(
        iddaa_id="test_austria_1",
        league="Avusturya Bundesliga",
        country="AT",
        is_major_league=True,
        home_team="Aus Lustenau",
        away_team="Rapid Wien",
        start_time_utc=kickoff,
        status="SCHEDULED",
        home_logo_url=None,
        away_logo_url=None,
    )
    db.add(m)
    db.commit()

    fake_fm = [
        {
            "external_id": "fotmob:5846029",
            "fotmob_id": "5846029",
            "league_name": "Bundesliga",
            "home_team": "Austria Lustenau",
            "away_team": "Rapid Wien",
            "home_logo_url": "https://images.fotmob.com/image_resources/logo/teamlogo/9982.png",
            "away_logo_url": "https://images.fotmob.com/image_resources/logo/teamlogo/10015.png",
            "start_time_utc": kickoff,
            "completed": False,
            "state": "pre",
        }
    ]

    with patch("scrapers.external_data.fetch_fotmob_matches_for_dates", return_value=fake_fm):
        updated = _backfill_missing_logos_from_fotmob(db, [m])

    assert updated == 1
    assert m.home_logo_url == "https://images.fotmob.com/image_resources/logo/teamlogo/9982.png"
    assert m.away_logo_url == "https://images.fotmob.com/image_resources/logo/teamlogo/10015.png"
    assert m.external_source == "fotmob"
    assert m.external_match_id == "5846029"
    print("TEST_FOTMOB_LOGO_BACKFILL: PASS")


if __name__ == "__main__":
    test_league_name_normalization()
    test_fotmob_logo_backfill()
    print("ALL VERIFY_PAKET3 TESTS PASSED")
