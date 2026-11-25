from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

db_url = settings.resolved_database_url
is_sqlite = db_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}
engine_kwargs: dict = {
    "connect_args": connect_args,
    "pool_pre_ping": True,
}
if not is_sqlite:
    engine_kwargs["pool_recycle"] = 300

engine = create_engine(db_url, **engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def ensure_schema():
    Base.metadata.create_all(bind=engine)
    insp = inspect(engine)
    tables = set(insp.get_table_names())

    migrations = {
        "matches": [
            ("home_logo_url", "VARCHAR(512)"),
            ("away_logo_url", "VARCHAR(512)"),
            ("league_logo_url", "VARCHAR(512)"),
        ],
        "odds_current": [
            ("global_source", "VARCHAR(32)"),
        ],
        "match_context": [
            ("context_updated_at", "TIMESTAMP"),
            ("home_rest_days", "INTEGER"),
            ("away_rest_days", "INTEGER"),
            ("home_elo", "FLOAT"),
            ("away_elo", "FLOAT"),
            ("home_rank", "INTEGER"),
            ("away_rank", "INTEGER"),
            ("season_home_gf", "FLOAT"),
            ("season_home_ga", "FLOAT"),
            ("season_away_gf", "FLOAT"),
            ("season_away_ga", "FLOAT"),
            ("season_home_played", "INTEGER"),
            ("season_away_played", "INTEGER"),
            ("home_true_xg", "FLOAT"),
            ("home_true_xga", "FLOAT"),
            ("away_true_xg", "FLOAT"),
            ("away_true_xga", "FLOAT"),
            ("xg_source", "VARCHAR(32)"),
            ("home_goal_std", "FLOAT"),
            ("away_goal_std", "FLOAT"),
            ("home_shots_pg", "FLOAT"),
            ("home_sot_pg", "FLOAT"),
            ("away_shots_pg", "FLOAT"),
            ("away_sot_pg", "FLOAT"),
            ("ai_summary_pick", "VARCHAR(32)"),
        ],
        "predictions": [
            ("closing_odd", "FLOAT"),
            ("clv_pct", "FLOAT"),
            ("kelly_units", "FLOAT"),
            ("trap_warning", "BOOLEAN DEFAULT FALSE"),
        ],
        "coupons": [
            ("is_locked", "BOOLEAN DEFAULT FALSE"),
        ],
    }

    is_pg = engine.dialect.name == "postgresql"
    for tbl, cols_spec in migrations.items():
        if tbl not in tables:
            continue
        existing = {c["name"] for c in insp.get_columns(tbl)}
        for col_name, col_type in cols_spec:
            if col_name not in existing:
                sql_type = "TIMESTAMPTZ" if (is_pg and col_type == "TIMESTAMP") else col_type
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col_name} {sql_type}"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
