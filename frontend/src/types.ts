export interface ScoreBreakdown {
  value: number;
  odds_move: number;
  form_goals: number;
  injury: number;
  true_prob_pct: number;
  poisson_prob_pct?: number | null;
  blended_prob_pct?: number;
  poisson_edge_pct?: number | null;
  raw_xg_home?: number | null;
  raw_xg_away?: number | null;
  xg_home?: number | null;
  xg_away?: number | null;
  home_rest_days?: number | null;
  away_rest_days?: number | null;
  vel_3h_pct?: number;
  smart_money?: boolean;
  trap_warning?: boolean;
  trap_reason?: string;
  kelly_units?: number;
  injury_warning?: boolean;
}

export interface PredictionItem {
  prediction_id: number;
  match_id: number;
  iddaa_id: string;
  league: string;
  country: string;
  is_major_league: boolean;
  home_team: string;
  away_team: string;
  home_logo_url?: string | null;
  away_logo_url?: string | null;
  league_logo_url?: string | null;
  start_time_utc: string;
  start_time_tr: string;
  start_date_tr: string;
  is_today_24h: boolean;
  data_status: string;
  market_key: string;
  selection_key: string;
  iddaa_odd: number;
  opening_odd: number;
  global_odd: number | null;
  confidence_score: number;
  value_edge_pct: number | null;
  odds_drop_pct: number | null;
  breakdown: ScoreBreakdown;
  home_form: string[];
  away_form: string[];
  injuries_count: number;
}

export interface DashboardSummary {
  last_sync_at: string | null;
  minutes_ago: number | null;
  overall_win_rate: number | null;
  settled_total: number;
  active_matches_count: number;
  top_confidence: {
    match_id: number;
    match_name: string;
    league: string;
    market_key: string;
    selection_key: string;
    iddaa_odd: number;
    confidence_score: number;
  } | null;
  best_value: {
    match_id: number;
    match_name: string;
    market_key: string;
    selection_key: string;
    iddaa_odd: number;
    global_odd: number | null;
    value_edge_pct: number | null;
  } | null;
  sharpest_drop: {
    match_id: number;
    match_name: string;
    market_key: string;
    selection_key: string;
    iddaa_odd: number;
    odds_drop_pct: number | null;
  } | null;
}

export interface InjuryEntry {
  player: string;
  reason: string;
  until: string;
  missed_games?: number | null;
  market_value?: string | null;
  market_value_eur?: number | null;
  value_m?: number | null;
  squad_value_share_pct?: number | null;
}

export interface MatchDetail {
  match_id: number;
  iddaa_id: string;
  league: string;
  country: string;
  is_major_league: boolean;
  home_team: string;
  away_team: string;
  home_logo_url?: string | null;
  away_logo_url?: string | null;
  league_logo_url?: string | null;
  status: string;
  home_score: number | null;
  away_score: number | null;
  data_status: string;
  start_time_tr: string;
  start_date_tr: string;
  context: {
    home_form: string[];
    away_form: string[];
    home_goals_avg: number | null;
    away_goals_avg: number | null;
    home_conceded_avg: number | null;
    away_conceded_avg: number | null;
    injuries: {
      home: InjuryEntry[];
      away: InjuryEntry[];
    };
    ai_summary: string | null;
    ai_source: string | null;
    home_elo?: number | null;
    away_elo?: number | null;
    home_rank?: number | null;
    away_rank?: number | null;
    home_true_xg?: number | null;
    home_true_xga?: number | null;
    away_true_xg?: number | null;
    away_true_xga?: number | null;
    xg_source?: string | null;
    home_goal_std?: number | null;
    away_goal_std?: number | null;
    home_shots_pg?: number | null;
    home_sot_pg?: number | null;
    away_shots_pg?: number | null;
    away_sot_pg?: number | null;
  };
  top_prediction: {
    prediction_id: number;
    market_key: string;
    selection_key: string;
    iddaa_odd: number;
    global_odd: number | null;
    confidence_score: number;
    value_edge_pct: number | null;
    odds_drop_pct: number | null;
    breakdown: ScoreBreakdown;
  } | null;
  odds: {
    market_key: string;
    selection_key: string;
    iddaa_odd: number;
    opening_odd: number;
    global_avg_odd: number | null;
    global_source?: string | null;
  }[];
  odds_history_market: string;
  odds_history_selection: string;
  odds_history: Record<string, string | number>[];
}

export interface CouponItemData {
  prediction_id: number;
  match_id: number;
  league: string;
  home_team: string;
  away_team: string;
  home_logo_url?: string | null;
  away_logo_url?: string | null;
  league_logo_url?: string | null;
  start_date_tr?: string;
  start_time_tr: string;
  market_key: string;
  selection_key: string;
  iddaa_odd: number;
  confidence_score: number;
  status: string;
  match_status?: string;
  home_score?: number | null;
  away_score?: number | null;
  leg_result?: "WON" | "LOST" | "LIVE" | "PENDING" | "VOID";
}

export interface CouponData {
  id: number;
  coupon_date: string;
  tier: "BANKO" | "DENGELI" | "SURPRIZ";
  total_odd: number;
  avg_confidence: number;
  status: string;
  is_locked?: boolean;
  items: CouponItemData[];
}

export interface BulletinLeague {
  league: string;
  matches: {
    match_id: number;
    league: string;
    is_major_league: boolean;
    home_team: string;
    away_team: string;
    home_logo_url?: string | null;
    away_logo_url?: string | null;
    league_logo_url?: string | null;
    status: string;
    home_score: number | null;
    away_score: number | null;
    start_time_tr: string;
    start_date_tr: string;
    top_confidence: number | null;
    odds: {
      market_key: string;
      selection_key: string;
      iddaa_odd: number;
      opening_odd: number;
      global_avg_odd: number | null;
    }[];
  }[];
}

export interface PerformanceData {
  overall_win_rate: number | null;
  won_count: number;
  lost_count: number;
  total_settled: number;
  avg_clv_pct: number | null;
  brier_score: number | null;
  calibration_bands: { band: string; total: number; won: number; win_rate: number | null }[];
  trend: { date: string; win_rate: number; total: number }[];
  coupon_tiers: Record<string, { total: number; won: number; win_rate: number | null }>;
  archive: {
    prediction_id: number;
    match_id: number;
    league: string;
    home_team: string;
    away_team: string;
    home_score: number | null;
    away_score: number | null;
    market_key: string;
    selection_key: string;
    iddaa_odd: number;
    confidence_score: number;
    status: string;
  }[];
}

export interface CustomSlipItem {
  match_id: number;
  home_team: string;
  away_team: string;
  market_key: string;
  selection_key: string;
  iddaa_odd: number;
  start_time_utc?: string;
}
