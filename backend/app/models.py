from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    iddaa_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    league: Mapped[str] = mapped_column(String(128), index=True)
    country: Mapped[str] = mapped_column(String(64), default="")
    is_major_league: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    home_team: Mapped[str] = mapped_column(String(128))
    away_team: Mapped[str] = mapped_column(String(128))
    start_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(24), default="SCHEDULED", index=True)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    external_source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    external_match_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    home_logo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    away_logo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    league_logo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    data_status: Mapped[str] = mapped_column(String(128), default="pending")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    odds_current: Mapped[list["OddsCurrent"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
    odds_history: Mapped[list["OddsHistory"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
    context: Mapped[Optional["MatchContext"]] = relationship(
        back_populates="match", uselist=False, cascade="all, delete-orphan"
    )
    predictions: Mapped[list["Prediction"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )


class OddsCurrent(Base):
    __tablename__ = "odds_current"
    __table_args__ = (
        UniqueConstraint("match_id", "market_key", "selection_key", name="uq_odds_current"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    market_key: Mapped[str] = mapped_column(String(32))
    selection_key: Mapped[str] = mapped_column(String(32))
    iddaa_odd: Mapped[float] = mapped_column(Float)
    opening_odd: Mapped[float] = mapped_column(Float)
    global_avg_odd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    global_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    match: Mapped["Match"] = relationship(back_populates="odds_current")


class OddsHistory(Base):
    __tablename__ = "odds_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    market_key: Mapped[str] = mapped_column(String(32))
    selection_key: Mapped[str] = mapped_column(String(32))
    iddaa_odd: Mapped[float] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    match: Mapped["Match"] = relationship(back_populates="odds_history")


class MatchContext(Base):
    __tablename__ = "match_context"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), unique=True, index=True
    )
    home_form: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list e.g. ["W","D","L"]
    away_form: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    home_goals_avg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_goals_avg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_conceded_avg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_conceded_avg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    injuries_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_summary_pick: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ai_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ai_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    context_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    home_rest_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_rest_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_elo: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_elo: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    season_home_gf: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    season_home_ga: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    season_away_gf: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    season_away_ga: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    season_home_played: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    season_away_played: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_true_xg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_true_xga: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_true_xg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_true_xga: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    xg_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    home_goal_std: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_goal_std: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_shots_pg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_sot_pg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_shots_pg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_sot_pg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    match: Mapped["Match"] = relationship(back_populates="context")


class ScraperCache(Base):
    __tablename__ = "scraper_cache"

    cache_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("match_id", "market_key", "selection_key", name="uq_prediction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    market_key: Mapped[str] = mapped_column(String(32))
    selection_key: Mapped[str] = mapped_column(String(32))
    iddaa_odd: Mapped[float] = mapped_column(Float)
    global_odd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, index=True)
    score_breakdown_json: Mapped[str] = mapped_column(Text, default="{}")
    value_edge_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odds_drop_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    closing_odd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    clv_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    kelly_units: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trap_warning: Mapped[bool] = mapped_column(Boolean, default=False)
    is_top_pick: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    published_odd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    published_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    published_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    match: Mapped["Match"] = relationship(back_populates="predictions")


class Coupon(Base):
    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("coupon_date", "tier", name="uq_coupon_date_tier"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coupon_date: Mapped[str] = mapped_column(String(16), index=True)  # YYYY-MM-DD (UTC+3)
    tier: Mapped[str] = mapped_column(String(24))  # BANKO / DENGELI / SURPRIZ
    total_odd: Mapped[float] = mapped_column(Float)
    avg_confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    items: Mapped[list["CouponItem"]] = relationship(
        back_populates="coupon", cascade="all, delete-orphan"
    )


class CouponItem(Base):
    __tablename__ = "coupon_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coupon_id: Mapped[int] = mapped_column(ForeignKey("coupons.id", ondelete="CASCADE"), index=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id", ondelete="RESTRICT"))
    odd_at_pick: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    coupon: Mapped["Coupon"] = relationship(back_populates="items")
    prediction: Mapped["Prediction"] = relationship()


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING")
    matches_synced: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(256))
    auth: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TelegramDispatch(Base):
    __tablename__ = "telegram_dispatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dispatch_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    chat_id: Mapped[str] = mapped_column(String(64))
    thread_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    message_id: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    settled_replied: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class SiteAnnouncement(Base):
    __tablename__ = "site_announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(220))
    link_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MatchPulseVote(Base):
    __tablename__ = "match_pulse_votes"
    __table_args__ = (
        UniqueConstraint("prediction_id", "voter_hash", name="uq_pulse_vote_pred_voter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id", ondelete="CASCADE"), index=True)
    voter_hash: Mapped[str] = mapped_column(String(64))
    choice: Mapped[str] = mapped_column(String(16))  # agree | risky
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MatchSlipAdd(Base):
    __tablename__ = "match_slip_adds"
    __table_args__ = (
        UniqueConstraint("prediction_id", "client_hash", name="uq_slip_add_pred_client"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id", ondelete="CASCADE"), index=True)
    client_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SportotoWeek(Base):
    __tablename__ = "sportoto_weeks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)  # DRAFT | PUBLISHED | SETTLED
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    matches: Mapped[list["SportotoMatch"]] = relationship(
        back_populates="week",
        cascade="all, delete-orphan",
        order_by="SportotoMatch.slot_no",
    )


class SportotoMatch(Base):
    __tablename__ = "sportoto_matches"
    __table_args__ = (
        UniqueConstraint("week_id", "slot_no", name="uq_sportoto_week_slot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_id: Mapped[int] = mapped_column(ForeignKey("sportoto_weeks.id", ondelete="CASCADE"), index=True)
    slot_no: Mapped[int] = mapped_column(Integer)
    home_team: Mapped[str] = mapped_column(String(128))
    away_team: Mapped[str] = mapped_column(String(128))
    prediction_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("predictions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(24), default="missing")  # fikstra_model | market_devig | missing
    odd_1: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odd_x: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odd_2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prob_1: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prob_x: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prob_2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_result: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)  # 1 | X | 2 | None

    week: Mapped["SportotoWeek"] = relationship(back_populates="matches")
