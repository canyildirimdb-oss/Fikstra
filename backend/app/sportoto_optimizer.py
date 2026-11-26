"""Backward-compatible re-exports from sportoto_engine."""

from app.sportoto_engine import (  # noqa: F401
    SPORTOTO_BUDGETS,
    VALID_RESULTS,
    compute_chaos_score,
    compute_live_survival,
    compute_marginal_advice,
    compute_trap_signal,
    devig_1x2,
    exact_poisson_binomial,
    match_and_create_sportoto_week,
    normalize_team_name,
    optimize_sportoto_columns,
    optimize_toto,
    parse_sportoto_raw_text,
    serialize_sportoto_week,
)
