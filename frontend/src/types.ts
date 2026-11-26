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

export interface RecentMatchItem {
  event_id: string;
  date_tr: string;
  home_team: string;
  away_team: string;
  opponent: string;
  is_home: boolean;
  home_score: number;
  away_score: number;
  result: "W" | "D" | "L";
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
    home_recent_matches?: RecentMatchItem[];
    away_recent_matches?: RecentMatchItem[];
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
    status?: "PENDING" | "WON" | "LOST" | "VOID" | string;
    published_odd?: number | null;
    published_confidence?: number | null;
    closing_odd?: number | null;
    clv_pct?: number | null;
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

export interface BreakdownRow {
  label: string;
  won: number;
  lost: number;
  void: number;
  decided: number;
  low_sample: boolean;
  win_rate: number | null;
}

export interface PerformanceBreakdown {
  by_league: BreakdownRow[];
  by_market: BreakdownRow[];
}

export interface VirtualBankroll {
  unit_stake: number;
  min_sample: number;
  decided_count: number;
  ready: boolean;
  net_profit: number | null;
  roi_pct: number | null;
  peak_profit: number | null;
  max_drawdown: number | null;
  series: { date: string; cumulative_profit: number }[];
}

export interface PulseData {
  prediction_id: number;
  total_votes: number;
  min_votes_to_reveal: number;
  revealed: boolean;
  agree_pct: number | null;
  risky_pct: number | null;
  user_vote: "agree" | "risky" | null;
  slip_adds: number;
  is_community_favorite: boolean;
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
  breakdown?: PerformanceBreakdown;
  virtual_bankroll?: VirtualBankroll;
}

export interface CustomSlipItem {
  match_id: number;
  prediction_id?: number;
  home_team: string;
  away_team: string;
  market_key: string;
  selection_key: string;
  iddaa_odd: number;
  start_time_utc?: string;
}

export interface SavedSlip {
  id: string;
  created_at: string;
  total_odd: number;
  stake: number;
  items: CustomSlipItem[];
}

export interface PredictionStatusItem {
  id: number;
  home_team: string;
  away_team: string;
  league: string;
  kickoff: string | null;
  pick: string;
  market_key: string;
  selection_key: string;
  odd: number;
  status: string;
  home_score: number | null;
  away_score: number | null;
}

export interface SiteAnnouncement {
  id: number;
  text: string;
  link_url: string | null;
  is_active: boolean;
  expires_at: string | null;
  created_at: string | null;
}

export interface AdminOverviewData {
  odds_api_remaining: number | null;
  odds_api_used: string | number | null;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_settle_at: string | null;
  void_24h_count: number;
  gemini_circuit_open: boolean;
  telegram_admin_configured: boolean;
  recent_dispatches: {
    id: number;
    kind: string;
    dispatch_key: string;
    settled_replied: boolean;
    updated_at: string | null;
  }[];
  announcements: SiteAnnouncement[];
}

export interface SportotoPick {
  slot_no: number;
  pick_code: string;
  pick_prob: number;
  kind: "single" | "double" | "triple";
  locked?: boolean;
  hit: boolean | null;
}

export interface TotoProbs {
  p15: number;
  p14_plus: number;
  p13_plus: number;
  p12_plus: number;
  expected_hits: number;
}

export interface TotoAdvice {
  best_upgrade: {
    slot_no: number;
    home_team: string;
    away_team: string;
    from_code: string;
    to_code: string;
    gain_prob_pct: number;
    cost_multiplier: string;
  } | null;
  safest_downgrade: {
    slot_no: number;
    home_team: string;
    away_team: string;
    from_code: string;
    to_code: string;
    loss_prob_pct: number;
  } | null;
}

export interface TotoSurvival {
  settled_count: number;
  settled_hits: number;
  settled_misses: number;
  remaining_count: number;
  can_hit_15: boolean;
  can_hit_14: boolean;
  can_hit_13: boolean;
  can_hit_12: boolean;
  p15_live: number;
  p14_live: number;
  p13_live: number;
  p12_live: number;
}

export interface SportotoPlan {
  budget: number;
  mode?: "hit15" | "shield13" | "contrarian";
  used_columns: number;
  estimated_cost?: number;
  single_count: number;
  double_count: number;
  triple_count: number;
  expected_hits: number;
  full_hit_prob_pct: number;
  probs?: TotoProbs;
  settled_count: number;
  hits: number | null;
  picks: SportotoPick[];
  advice?: TotoAdvice;
  survival?: TotoSurvival | null;
}

export interface TotoChaos {
  score: number;
  label: "Banko" | "Dengeli" | "Çok Riskli";
}

export interface TotoTrapSignal {
  is_trap: boolean;
  fav_pick: "1" | "X" | "2";
  fav_public_pct: number;
  fav_true_pct: number;
  leverage_1: number;
  leverage_x: number;
  leverage_2: number;
  max_leverage: number;
  explanation: string | null;
}

export interface SportotoMatchItem {
  id: number;
  slot_no: number;
  home_team: string;
  away_team: string;
  prediction_id: number | null;
  source: "fikstra_model" | "market_devig" | "missing";
  odd_1: number | null;
  odd_x: number | null;
  odd_2: number | null;
  prob_1: number | null;
  prob_x: number | null;
  prob_2: number | null;
  public_pct_1: number | null;
  public_pct_x: number | null;
  public_pct_2: number | null;
  chaos?: TotoChaos | null;
  trap_signal?: TotoTrapSignal | null;
  actual_result: "1" | "X" | "2" | null;
}

export interface SportotoWeekData {
  id: number;
  title: string;
  status: "DRAFT" | "PUBLISHED" | "SETTLED";
  column_price: number;
  published_at: string | null;
  created_at: string | null;
  missing_count: number;
  missing_slots: number[];
  matches: SportotoMatchItem[];
  plans: Record<"16" | "32" | "64", SportotoPlan> | null;
}

export interface SavedTotoSlip {
  id: string;
  week_id: number;
  week_title: string;
  created_at: string;
  picks: Record<number, string>;
  columns: number;
  estimated_cost: number;
}

export interface NotificationItem {
  id: number;
  title: string;
  body: string;
  category: string;
  url: string;
  sent_at: string | null;
}

export interface SportotoCurrentResponse {
  active: boolean;
  week: SportotoWeekData | null;
}

export interface SportotoHistoryItem {
  id: number;
  title: string;
  status: "DRAFT" | "PUBLISHED" | "SETTLED";
  published_at: string | null;
  settled_count: number;
  plans_summary: Record<
    string,
    {
      used_columns: number;
      expected_hits: number;
      hits: number | null;
      settled_count: number;
    }
  >;
}
