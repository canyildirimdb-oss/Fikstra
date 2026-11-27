import socket
from urllib.parse import urlparse

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

db_url = settings.resolved_database_url
is_sqlite = db_url.startswith("sqlite")
connect_args: dict = {"check_same_thread": False} if is_sqlite else {"connect_timeout": 10}
engine_kwargs: dict = {
    "connect_args": connect_args,
    "pool_pre_ping": True,
}
if not is_sqlite:
    engine_kwargs["pool_recycle"] = 240
    engine_kwargs["pool_size"] = 3
    engine_kwargs["max_overflow"] = 2
    engine_kwargs["pool_timeout"] = 15

engine = create_engine(db_url, **engine_kwargs)


def _resolve_ipv4(host: str, port: int) -> str | None:
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            return str(infos[0][4][0])
    except OSError:
        return None
    return None


if not is_sqlite:
    _parsed_db = urlparse(db_url.replace("postgresql+psycopg://", "postgresql://", 1))
    _db_host = _parsed_db.hostname
    _db_port = _parsed_db.port or 5432

    if _db_host:
        @event.listens_for(engine, "do_connect")
        def _force_ipv4_connect(dialect, conn_rec, cargs, cparams):
            target_host = cparams.get("host") or _db_host
            target_port = int(cparams.get("port") or _db_port)
            if target_host and "hostaddr" not in cparams:
                ipv4 = _resolve_ipv4(str(target_host), target_port)
                if ipv4:
                    cparams["hostaddr"] = ipv4

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
            ("void_reason", "VARCHAR(128)"),
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
            ("is_top_pick", "BOOLEAN DEFAULT FALSE"),
            ("published_odd", "FLOAT"),
            ("published_confidence", "FLOAT"),
            ("published_prob", "FLOAT"),
            ("published_at", "TIMESTAMP"),
        ],
        "coupons": [
            ("is_locked", "BOOLEAN DEFAULT FALSE"),
        ],
        "coupon_items": [
            ("odd_at_pick", "FLOAT"),
        ],
        "notification_logs": [
            ("category", "VARCHAR(32) DEFAULT 'info'"),
            ("url", "VARCHAR(256) DEFAULT '/'"),
        ],
        "sportoto_weeks": [
            ("external_id", "VARCHAR(64)"),
            ("column_price", "FLOAT DEFAULT 10.0"),
        ],
        "sportoto_matches": [
            ("public_pct_1", "FLOAT"),
            ("public_pct_x", "FLOAT"),
            ("public_pct_2", "FLOAT"),
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

    # One-time safe backfill for legacy official settled top picks missing published_at
    if "predictions" in tables:
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE predictions "
                        "SET published_at = created_at, "
                        "published_odd = COALESCE(published_odd, iddaa_odd), "
                        "published_confidence = COALESCE(published_confidence, confidence_score) "
                        "WHERE is_top_pick = :t "
                        "AND status IN ('WON', 'LOST', 'VOID') "
                        "AND published_at IS NULL"
                    ),
                    {"t": True},
                )
        except Exception:
            pass

    # Ensure coupon_items.prediction_id uses ON DELETE RESTRICT on PostgreSQL
    if is_pg and "coupon_items" in tables:
        try:
            for fk in insp.get_foreign_keys("coupon_items"):
                if fk.get("constrained_columns") == ["prediction_id"]:
                    on_del = (fk.get("options") or {}).get("ondelete", "").upper()
                    fk_name = fk.get("name")
                    if fk_name and on_del != "RESTRICT":
                        with engine.begin() as conn:
                            conn.execute(text(f'ALTER TABLE coupon_items DROP CONSTRAINT "{fk_name}"'))
                            conn.execute(
                                text(
                                    'ALTER TABLE coupon_items ADD CONSTRAINT "coupon_items_prediction_id_fkey" '
                                    'FOREIGN KEY (prediction_id) REFERENCES predictions(id) ON DELETE RESTRICT'
                                )
                            )
        except Exception:
            pass

    # Performance indexes (PostgreSQL & SQLite compatible)
    index_stmts = [
        "CREATE INDEX IF NOT EXISTS ix_matches_start_time_utc ON matches (start_time_utc)",
        "CREATE INDEX IF NOT EXISTS ix_predictions_match_id ON predictions (match_id)",
        "CREATE INDEX IF NOT EXISTS ix_predictions_status_top ON predictions (status, is_top_pick)",
        "CREATE INDEX IF NOT EXISTS ix_odds_history_match_recorded ON odds_history (match_id, recorded_at)",
        "CREATE INDEX IF NOT EXISTS ix_coupons_coupon_date ON coupons (coupon_date)",
        "CREATE INDEX IF NOT EXISTS ix_telegram_dispatches_kind_settled ON telegram_dispatches (kind, settled_replied)",
        "CREATE INDEX IF NOT EXISTS ix_site_announcements_active ON site_announcements (is_active, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_match_pulse_votes_pred ON match_pulse_votes (prediction_id)",
        "CREATE INDEX IF NOT EXISTS ix_match_slip_adds_pred ON match_slip_adds (prediction_id)",
        "CREATE INDEX IF NOT EXISTS ix_sportoto_weeks_status_created ON sportoto_weeks (status, created_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_sportoto_weeks_external_id ON sportoto_weeks (external_id)",
        "CREATE INDEX IF NOT EXISTS ix_sportoto_matches_week_slot ON sportoto_matches (week_id, slot_no)",
    ]
    with engine.begin() as conn:
        for stmt in index_stmts:
            conn.execute(text(stmt))

    # Clean up any legacy multi-market settled predictions (keep 1 official #1 per match)
    try:
        from services.settlement import cleanup_multi_market_settled_predictions

        db = SessionLocal()
        try:
            cleanup_multi_market_settled_predictions(db)
        finally:
            db.close()
    except Exception:
        pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
