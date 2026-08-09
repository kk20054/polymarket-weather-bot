export interface BtcPrice {
  price: number
  change_24h: number
  change_7d: number
  market_cap: number
  volume_24h: number
  last_updated: string
}

export interface Microstructure {
  rsi: number
  momentum_1m: number
  momentum_5m: number
  momentum_15m: number
  vwap_deviation: number
  sma_crossover: number
  volatility: number
  price: number
  source: string
}

export interface BtcWindow {
  slug: string
  market_id: string
  up_price: number
  down_price: number
  window_start: string
  window_end: string
  volume: number
  is_active: boolean
  is_upcoming: boolean
  time_until_end: number
  spread: number
}

export interface Signal {
  market_ticker: string
  market_title: string
  platform: string
  direction: string
  model_probability: number
  market_probability: number
  probability_edge?: number
  edge: number
  raw_model_probability?: number | null
  raw_edge?: number | null
  calibrated_probability?: number | null
  calibrated_edge?: number | null
  calibrated_sigma_f?: number | null
  calibration_bias_f?: number | null
  confidence: number
  suggested_size: number
  reasoning: string
  timestamp: string
  category: string
  event_slug?: string
  btc_price: number
  btc_change_24h: number
  window_end?: string
  actionable: boolean
}

export interface Trade {
  id: number
  market_ticker: string
  market_title?: string | null
  platform: string
  event_slug?: string | null
  event_url?: string | null
  direction: string
  entry_price: number
  bid_at_entry?: number | null
  spread?: number | null
  mark_price?: number | null
  unrealized_pnl?: number | null
  exit_price?: number | null
  size: number
  shares?: number | null
  timestamp: string
  settled: boolean
  result: string
  pnl: number | null
  close_reason?: string | null
  source?: string | null
}

export interface BotStats {
  bankroll: number
  cash_balance?: number
  reserved_capital?: number
  realized_pnl?: number
  unrealized_pnl?: number
  total_trades: number
  open_trades?: number
  settled_trades?: number
  winning_trades: number
  win_rate: number
  total_pnl: number
  is_running: boolean
  last_run: string | null
  latest_market_update?: string | null
  data_age_minutes?: number | null
  expired_signal_count?: number
  signal_count?: number
  actionable_count?: number
  live_candidate_count?: number
  live_blocked_count?: number
  strategy_live_ready?: boolean
  strategy_readiness_status?: 'ready' | 'watch' | 'blocked'
  strategy_readiness_reasons?: string[]
  strategy_allowed_pnl?: number
  strategy_allowed_roi?: number
  strategy_allowed_resolved?: number
  simulation_started_at?: string | null
  scanner_status?: string
  auto_simulation?: AutoSimulationStatus
}

export interface AutoSimulationStatus {
  enabled: boolean
  interval_seconds: number
  last_run?: string | null
  last_result?: {
    count: number
    spent: number
    skipped: number
    remaining: number
    orderbooks_refreshed?: number
    orderbook_refresh_failed?: number
  } | null
  last_error?: string | null
}

export interface TruthCityHealth {
  city: string
  city_name: string
  station_id: string
  total_observations: number
  eligible_observations: number
  open_meteo_fallbacks: number
  legacy_unknown?: number
  latest_provider: string
  latest_date: string
  latest_confidence: number
  status?: 'eligible' | 'blocked'
  reasons?: string[]
}

export interface TruthHealth {
  total_observations: number
  eligible_observations: number
  coverage_rate: number
  open_meteo_fallbacks: number
  open_meteo_fallback_rate: number
  legacy_unknown?: number
  cities: TruthCityHealth[]
}

export interface DataReadinessReason {
  code: string
  count: number
}

export interface DataReadinessStage {
  key: string
  label: string
  status: 'ready' | 'blocked'
  reasons: DataReadinessReason[]
  metrics: Record<string, unknown>
}

export interface DataReadinessAction {
  key: string
  priority: number
  label: string
  count: number
  impact: string
  command: string
  apply_command?: string
  requires_operator: boolean
  targets: Array<Record<string, string>>
}

export interface DataReadiness {
  audit_version: string
  generated_at: string
  status: 'ready' | 'blocked'
  score: number
  live_allowed: boolean
  production_phase?: {
    id: string
    label: string
    name: string
    status: 'active' | 'ready_for_next'
    next: string
    operator_action: string
    blocked_keys: string[]
  }
  stages: DataReadinessStage[]
  blockers: DataReadinessReason[]
  next_actions?: DataReadinessAction[]
  cities: Array<{
    city: string
    city_name: string
    station_id: string
    station_name: string
    timezone: string
    unit: string
    market_rules: number
    verified_rules: number
    truth_days: number
    eligible_truth_days: number
    forecast_runs: number
    status: 'eligible' | 'blocked'
    reasons: string[]
  }>
  summary: {
    registered_cities: number
    eligible_cities: number
    market_rules: number
    settlement_contracts?: number
    eligible_truth_days: number
    forecast_runs: number
    forecast_members: number
    orderbook_snapshots: number
  }
}

export interface ProductionRefreshStage {
  name: string
  ok: boolean
  running?: boolean
  elapsed_ms?: number
  skipped?: boolean
  reason?: string
  error?: string
  payload?: Record<string, unknown>
}

export interface ProductionRefreshResult {
  refresh_version: string
  ok: boolean
  running?: boolean
  production_refresh_running?: boolean
  auto_refresh_enabled?: boolean
  auto_refresh_running?: boolean
  last_refresh_was_auto?: boolean
  message?: string
  failed_stages: string[]
  scan_signals: boolean
  target_date?: string
  stages: ProductionRefreshStage[]
  requested_at?: string
  request?: {
    cities: string[]
    days: number
    limit: number
    start_date: string
    end_date: string
    skip_signal_scan: boolean
  }
  readiness?: {
    status?: 'ready' | 'blocked'
    score?: number
    live_allowed?: boolean
    production_phase?: DataReadiness['production_phase']
    blocked_keys?: string[]
    next_actions?: DataReadinessAction[]
  }
  history?: Array<{
    requested_at?: string
    ok: boolean
    failed_stages: string[]
    stage_count: number
    ok_stage_count: number
    blocked_keys: string[]
    scan_signals: boolean
  }>
}

export interface ProductionValidationAction {
  key?: string
  label?: string
  count?: number
  command?: string
  apply_command?: string
  requires_operator?: boolean
  layer?: string
  [key: string]: unknown
}

export interface ProductionValidationLayer {
  key: string
  label: string
  ready: boolean
  status: string
  blockers: string[]
  next_actions: ProductionValidationAction[]
  metrics: Record<string, unknown>
}

export interface ProductionValidationReport {
  validation_version: string
  generated_at: string
  status: string
  score: number
  ready_layers: number
  total_layers: number
  live_allowed: boolean
  hard_blockers: string[]
  layers: ProductionValidationLayer[]
  next_actions: ProductionValidationAction[]
}

export interface ProductionActionRequest {
  actionKey: string
  apply?: boolean
  operatorConfirmed?: boolean
  cities?: string[]
  days?: number
  limit?: number
  startDate?: string
  endDate?: string
  skipSignalScan?: boolean
  note?: string
  archivePath?: string
}

export interface ProductionActionRunResult {
  ok: boolean
  status: string
  action_key: string
  reason?: string
  message?: string
  action?: {
    label?: string
    description?: string
    requires_operator?: boolean
    mutates?: boolean
  }
  params?: Record<string, unknown>
  payload?: Record<string, unknown>
  readiness?: {
    status?: string
    score?: number
    live_allowed?: boolean
    blocked_keys?: string[]
  }
}

export interface SettlementContract {
  contract_id: string
  event_slug: string
  city: string
  city_name: string
  target_local_date: string
  station_id: string
  station_name: string
  timezone: string
  unit: string
  metric: string
  rounding_rule: string
  bucket_boundary: string
  resolution_source_text?: string | null
  source_url?: string | null
  truth_provider_priority?: string[]
  rule_version?: string | null
  registry_version?: string | null
  parse_confidence?: number | null
  confidence_reason?: string | null
  auto_verified_at?: string | null
  manual_verified_at?: string | null
  manual_verified_by?: string | null
  manual_verification_note?: string | null
  manual_verification_snapshot?: Record<string, unknown> | null
  verification_evidence?: string[]
  review_status?: 'verified' | 'mature-auto' | 'future-auto' | 'manual-required'
  review_tags?: string[]
}

export interface SettlementContractList {
  status: string
  city: string
  limit: number
  offset: number
  total: number
  summary: {
    contracts: number
    manual_verified: number
    unverified: number
    auto_verified: number
    manual_progress: number
  }
  contracts: SettlementContract[]
}

export interface BulkContractVerificationResult {
  ok: boolean
  applied: boolean
  selected: number
  verified: number
  skipped_requested: string[]
  require_auto_verified: boolean
  mature_only: boolean
  contracts: SettlementContract[]
}

export interface DistributionItem {
  market_id: string
  question: string
  bucket_low: number
  bucket_high: number
  probability_raw: number
  probability: number
  ask: number
  bid: number
  spread: number
  probability_edge: number
  ev: number
  spread_cost_ratio?: number | null
  is_signal?: boolean
}

export interface MarketBucket {
  id?: number
  bucket_key?: string | null
  market_id: string
  event_slug?: string | null
  event_url?: string | null
  question?: string | null
  city?: string
  city_name?: string
  target_date: string
  station_id?: string | null
  unit?: string
  bucket_label?: string | null
  bucket_direction?: string | null
  bucket_low?: number | null
  bucket_high?: number | null
  yes_token_id?: string | null
  token_id?: string | null
  price?: number | null
  best_bid?: number | null
  best_ask?: number | null
  spread?: number | null
  volume?: number | null
  liquidity?: number | null
  order_min_size?: number | null
  tick_size?: number | null
  neg_risk?: boolean
  enable_order_book?: boolean
  quote_timestamp?: string | null
  orderbook_source?: string | null
  bid_depth?: number | null
  ask_depth?: number | null
  strict_match_status?: string | null
  strict_match_reasons?: string[]
  updated_at?: string | null
}

export interface MarketBucketSummary {
  ok: boolean
  city?: string
  target_date?: string
  bucket_count: number
  matched_bucket_count: number
  blocked_bucket_count: number
  markets: number
  tokens: number
  orderbook_enabled: number
  with_tick_size: number
  with_order_min_size: number
  with_two_sided_depth: number
  reason_counts: CityEvidenceMarketReason[]
  latest: MarketBucket[]
  limit?: number
}

export interface DailyMaxPrediction {
  id?: number
  prediction_key?: string
  city_key: string
  target_date: string
  issued_at?: string | null
  mu?: number | null
  model_mu?: number | null
  effective_mu?: number | null
  mu_basis?: 'model_distribution' | 'observed_floor_adjusted' | string
  sigma?: number | null
  unit?: string
  method?: string
  model_weights?: Record<string, number>
  member_count?: number
  components?: Array<Record<string, unknown>>
  source_run_ids?: Array<number | string>
  observed_floor?: number | null
  sigma_floor?: number | null
  time_decay_factor?: number | null
  mu_observed_floor_applied?: boolean
  sigma_from_spread?: number | null
  sigma_from_history?: number | null
  bias_correction?: number | null
  bias_sample_count?: number
  deb_version?: string
  peak_hour?: string | null
  peak_temp?: number | null
  build_warnings?: string[]
  peak_lock_candidate?: Record<string, unknown>
  updated_at?: string | null
}

export interface DailyMaxPredictionHistoryItem {
  id?: number
  prediction_key?: string
  issued_at?: string | null
  mu?: number | null
  sigma?: number | null
  unit?: string
  components?: Array<Record<string, unknown>>
}

export interface DailyMaxPredictionSummary {
  ok: boolean
  city_key?: string
  target_date?: string
  count: number
  latest?: DailyMaxPrediction | null
  history?: DailyMaxPredictionHistoryItem[]
  quality_ok?: boolean
  quality_reasons?: string[]
  rejected_latest_id?: number | null
}

export interface BucketProbabilityItem {
  bucket_key?: string | null
  market_id?: string | null
  yes_token_id?: string | null
  bucket_label?: string | null
  bucket_direction?: string | null
  bucket_low?: number | null
  bucket_high?: number | null
  bucket_unit?: string | null
  probability_before_observed_floor?: number | null
  observed_floor_excluded?: boolean
  probability_raw?: number | null
  probability?: number | null
  market_probability?: number | null
  edge?: number | null
  best_bid?: number | null
  best_ask?: number | null
  price?: number | null
}

export interface BucketProbabilitySummary {
  ok: boolean
  city_key?: string
  target_date?: string
  method?: string
  mu?: number | null
  model_mu?: number | null
  effective_mu?: number | null
  probability_mu_basis?: string | null
  sigma?: number | null
  unit?: string
  observed_floor?: number | null
  observed_floor_applied_to_distribution?: boolean
  observed_floor_excluded_bucket_count?: number
  normalized?: boolean
  sum_probability?: number | null
  reasons?: string[]
  notes?: string[]
  items: BucketProbabilityItem[]
  prediction?: DailyMaxPrediction | null
}

export type Layer7ResourceStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

export interface Layer7ResourceState {
  status: Layer7ResourceStatus
  refreshing?: boolean
  error?: string
  refresh_error?: string
}

export interface Layer7QueryState {
  deb: Layer7ResourceState
  buckets: Layer7ResourceState
  probabilities?: Layer7ResourceState
  signals: Layer7ResourceState
  aggregate_error?: string
}

export interface SignalDecisionRecord {
  id?: number
  decision_id: string
  decision_version?: string
  city_key?: string
  target_date: string
  market_id?: string | null
  signal_id?: number | null
  bucket_key?: string | null
  bucket_direction?: string | null
  bucket_lower?: number | null
  bucket_upper?: number | null
  token_id?: string | null
  yes_token_id?: string | null
  model_probability?: number | null
  market_implied_probability?: number | null
  market_probability?: number | null
  edge?: number | null
  edge_percent?: number | null
  market_bid?: number | null
  market_ask?: number | null
  market_mid?: number | null
  best_bid?: number | null
  best_ask?: number | null
  spread_bps?: number | null
  book_age_seconds?: number | null
  order_min_size?: number | null
  tick_size?: number | null
  neg_risk?: boolean
  paper_allowed?: boolean
  live_allowed?: boolean
  paper_decision?: string
  live_decision?: string
  gate_status?: string
  strategy_name?: string
  kelly_fraction?: number | null
  position_size_usd?: number | null
  ladder_group_id?: string | null
  strategy_revision_id?: string | null
  strategy_params_hash?: string | null
  blocked_reason_primary?: string | null
  reasons?: string[]
  cautions?: string[]
  gate_reasons?: string[]
  model_distribution?: {
    method?: string
    mu?: number | null
    sigma?: number | null
    unit?: string
    normalized?: boolean
    sum_probability?: number | null
    item_count?: number
    probability_mu_basis?: string | null
    model_mu?: number | null
    effective_mu?: number | null
    observed_floor?: number | null
  }
  model_bucket_probs?: {
    bucket_key?: string
    bucket_label?: string
    bucket_unit?: string
    bucket_direction?: string
    bucket_low?: number | null
    bucket_high?: number | null
    probability?: number | null
    probability_raw?: number | null
    market_probability?: number | null
    price?: number | null
    best_bid?: number | null
    best_ask?: number | null
    edge?: number | null
    market_id?: string
    yes_token_id?: string
  }
  market_bucket_probs?: Array<Record<string, unknown>> | Record<string, unknown>
  edge_by_bucket?: Record<string, unknown>
  orderbook_snapshot?: {
    source?: string
    snapshot_key?: string
    quote_timestamp?: string | null
    best_bid?: number | null
    best_ask?: number | null
    spread?: number | null
    bid_depth?: number | null
    ask_depth?: number | null
  }
  evidence_links?: Record<string, unknown>
  issued_at?: string | null
  updated_at?: string | null
}

export interface SignalDecisionSummary {
  ok: boolean
  city_key?: string
  target_date?: string
  count: number
  status_counts: Record<string, number>
  paper_counts: Record<string, number>
  live_counts: Record<string, number>
  reason_counts: CityEvidenceMarketReason[]
  decisions: SignalDecisionRecord[]
}

export interface PaperOrderRecord {
  id: number
  decision_id?: string
  market_id?: string
  yes_token_id?: string
  bucket_key?: string
  bucket_label?: string
  bucket_unit?: string
  market_question?: string
  strategy_name?: string
  ladder_group_id?: string
  cohort_run_id?: string
  strategy_revision_id?: string
  sizing_snapshot?: Record<string, unknown>
  execution_quote?: Record<string, unknown>
  cap_reasons?: string[]
  city_key?: string
  target_date?: string
  event_url?: string
  side?: string
  limit_price?: number | null
  requested_amount?: number | null
  filled_amount?: number | null
  filled_shares?: number | null
  unfilled_amount?: number | null
  average_fill_price?: number | null
  entry_price?: number | null
  entry_value?: number | null
  mark_price?: number | null
  current_best_ask?: number | null
  mark_timestamp?: string | null
  mark_age_seconds?: number | null
  mark_source?: string
  mark_value?: number | null
  quote_is_stale?: boolean
  unrealized_pnl?: number | null
  realized_pnl?: number | null
  pnl_value?: number | null
  pnl_pct?: number | null
  pnl_kind?: 'unrealized' | 'realized' | 'realized_exit'
  exit_price?: number | null
  exit_time?: string | null
  exit_policy?: string
  force_exit_enabled?: boolean
  exit_details?: {
    trigger?: string
    confirmation_count?: number
    model_probability?: number | null
    observed_high?: number | null
    best_bid?: number | null
    proceeds?: number | null
    realized_pnl?: number | null
    closed_at?: string
    reasons?: string[]
  }
  settlement?: PaperSettlementRecord | null
  status?: string
  lifecycle_status?: string
  fill_status?: string
  model_probability?: number | null
  market_probability?: number | null
  edge?: number | null
  gate_status?: string
  failure_reason?: string | null
  risk_reasons?: string[]
  orderbook_snapshot?: Record<string, unknown>
  evidence_links?: Record<string, unknown>
  opened_at?: string
  closed_at?: string
}

export interface PaperSettlementRecord {
  id?: number
  paper_order_id?: number
  decision_id?: string
  result?: string
  settlement_status?: string
  settlement_source?: string
  payout?: number | null
  pnl?: number | null
  brier_score?: number | null
  settled_at?: string | null
}

export interface PaperExecutionSummary {
  ok: boolean
  execution_version?: string
  city_key?: string
  target_date?: string
  cohort_run_id?: string
  count: number
  open_orders: number
  filled_amount: number
  unrealized_pnl: number
  starting_bankroll?: number
  cash_available?: number
  position_value?: number
  equity?: number
  total_pnl?: number
  equity_curve?: EquityPoint[]
  resolved_orders: number
  exited_orders?: number
  provisional_orders?: number
  wins: number
  losses: number
  win_rate?: number | null
  realized_pnl: number
  brier_score?: number | null
  status_counts?: Record<string, number>
  reason_counts?: Array<{ reason: string; count: number }>
  orders: PaperOrderRecord[]
  settlements: PaperSettlementRecord[]
}

export interface PaperExecutionResult {
  ok: boolean
  status?: string
  reason?: string | null
  dry_run?: boolean
  requested?: number
  executed?: number
  duplicates?: number
  rejected?: number
  decision_id?: string
  ladder_group_id?: string
  run_id?: string
  candidate_count?: number
  skipped_candidates?: Array<{
    decision_id?: string
    ladder_group_id?: string
    reason?: string
    reasons?: string[]
  }>
  metrics?: Partial<PaperValidationStatus>
  order?: PaperOrderRecord
  results?: PaperExecutionResult[]
  summary?: PaperExecutionSummary
}

export interface DashboardRecommendationItem {
  type: 'weather_focus' | 'trade_candidate' | 'observation_only' | string
  city_key: string
  city_name: string
  station_id?: string
  settlement_station_id?: string
  verification_status?: string
  settlement_rule_verified_at?: string | null
  recommendation_class?: string
  metar_age_seconds?: number | null
  metar_report_time?: string | null
  forecast_age_seconds?: number | null
  forecast_time?: string | null
  current_temp?: number | null
  current_temp_unit?: string
  target_date?: string
  deb_mu?: number | null
  deb_sigma?: number | null
  deb_unit?: string
  bucket_label?: string | null
  bucket_key?: string | null
  strategy_name?: string
  strategy_label?: string
  kelly_fraction?: number | null
  position_size_usd?: number | null
  ladder_group_id?: string
  sub_buckets?: Array<{
    bucket_key?: string | null
    bucket_label?: string | null
    model_probability?: number | null
    market_ask?: number | null
    edge?: number | null
    kelly_fraction?: number | null
    position_size_usd?: number | null
    paper_allowed?: boolean
    blocked_reasons?: string[]
  }>
  edge?: number | null
  edge_percent?: number | null
  model_probability?: number | null
  market_probability?: number | null
  market_ask?: number | null
  market_bid?: number | null
  paper_allowed?: boolean
  blocked_reasons?: string[]
  polymarket_url?: string | null
  market_id?: string | null
  token_id?: string | null
  china_live_temp?: number | null
  china_live_observed_at?: string | null
  badge?: string
  observation_source?: string
  prediction_issued_at?: string | null
  remaining_to_max?: number | null
  focus_reason?: string
  expected_high_basis?: string
  observation_role?: string
  attention_only?: boolean
}

export interface DashboardRecommendations {
  ok: boolean
  recommendation_version: string
  generated_at?: string
  scheduler_running?: boolean
  count: number
  trade_candidate_count?: number
  observation_only_count?: number
  weather_focus_count?: number
  empty_reason?: 'scheduler_stopped' | 'no_recommendations_after_gates' | string
  filters?: Record<string, unknown>
  skipped?: Record<string, number>
  items: DashboardRecommendationItem[]
  focus_items?: DashboardRecommendationItem[]
}

export interface EventDistribution {
  items: DistributionItem[]
  sum_probability: number
  normalized: boolean
  forecast_f?: number
  sigma_f?: number
  bias_f?: number
  top_model?: DistributionItem[]
  top_market?: DistributionItem[]
  signal_probability?: number
  signal_probability_edge?: number
  signal_ev?: number
  signal_spread_cost_ratio?: number | null
  notes?: string[]
}

export interface SignalDecision {
  signal_id?: number
  market_id?: string
  action: string
  paper_allowed: boolean
  live_allowed: boolean
  reasons: string[]
  cautions: string[]
  quality_flags?: string[]
  strategy_tags?: string[]
  strategy_score?: number
  distribution_signal_probability?: number | null
  truth_status?: string
}

export interface EquityPoint {
  timestamp: string
  pnl: number
  bankroll: number
  realized_pnl?: number | null
  unrealized_pnl?: number | null
}

export interface CalibrationSummary {
  total_signals: number
  total_with_outcome: number
  settlement_rate?: number
  accuracy: number
  avg_predicted_edge: number
  avg_actual_edge: number
  brier_score: number
}

export interface BacktestBucket {
  bucket: string
  count: number
  resolved: number
  wins: number
  win_rate: number
  pnl: number
}

export interface BacktestSource {
  source: string
  count: number
  resolved: number
  wins: number
  win_rate: number
  pnl: number
}

export interface BacktestRiskSlice {
  kind: string
  name: string
  description?: string
  count: number
  resolved: number
  wins: number
  win_rate: number
  pnl: number
  roi: number
  score?: number
  warnings?: string[]
}

export interface BacktestSummary {
  total_positions: number
  completed_positions: number
  resolved_positions: number
  open_positions: number
  wins: number
  losses: number
  win_rate: number
  settlement_rate: number
  total_pnl: number
  avg_actual_return: number
  avg_predicted_ev: number
  avg_calibrated_ev?: number
  avg_mos_ev?: number
  brier_score: number
  calibrated_brier_score?: number
  mos_brier_score?: number
  buckets: BacktestBucket[]
  sources: BacktestSource[]
  risk_slices?: BacktestRiskSlice[]
  block_reasons?: BacktestRiskSlice[]
  policy_candidates?: BacktestRiskSlice[]
  strategy_readiness?: {
    live_ready: boolean
    status: 'ready' | 'watch' | 'blocked'
    reasons: string[]
    resolved_positions: number
    allowed_resolved: number
    allowed_pnl: number
    allowed_roi: number
    allowed_win_rate: number
    blocked_roi: number
    brier_score: number
  }
  notes: string[]
}

export interface TemperatureFitRecord {
  city_key: string
  city_name: string
  target_date: string
  unit: string
  actual_provider?: string
  actual_station?: string
  truth_confidence?: number
  calibration_eligible?: boolean
  calibration_tier?: string
  reason_if_ineligible?: string
  source: string
  best_source: string
  timestamp?: string | null
  horizon?: string | null
  hours_left: number
  forecast: number
  actual: number
  forecast_f: number
  actual_f: number
  error: number
  error_f: number
  abs_error_f: number
  ensemble_std?: number | null
  ensemble_std_f?: number | null
}

export interface TemperatureFitGroup {
  city_key?: string
  city_name?: string
  source?: string
  unit?: string
  markets: number
  samples: number
  mae_f: number
  bias_f: number
  decayed_bias_f?: number
  rmse_f: number
  mos_slope?: number | null
  mos_intercept_f?: number | null
  mos_mae_f?: number | null
  mos_rmse_f?: number | null
  mos_improvement_f?: number | null
  fit_status?: 'eligible' | 'watch' | 'blocked'
  fit_reasons?: string[]
  trade_score?: number
  latest_date?: string
  latest_forecast?: number
  latest_actual?: number
}

export interface TemperatureFitSummary {
  markets: number
  eligible_markets?: number
  eligible_samples?: number
  observed_samples?: number
  snapshot_samples?: number
  provider_counts?: Record<string, number>
  tier_counts?: Record<string, number>
  ineligible_counts?: Record<string, number>
  samples: number
  mae_f: number
  bias_f: number
  decayed_bias_f?: number
  rmse_f: number
  mos_slope?: number | null
  mos_intercept_f?: number | null
  mos_mae_f?: number | null
  mos_rmse_f?: number | null
  mos_improvement_f?: number | null
}

export interface TemperatureFitData {
  summary: TemperatureFitSummary
  readiness_counts?: {
    eligible: number
    watch: number
    blocked: number
  }
  cities: TemperatureFitGroup[]
  sources: TemperatureFitGroup[]
  records: TemperatureFitRecord[]
  strategy_summary?: {
    near_lock: {
      samples: number
      mae_f: number
      bias_f: number
      rmse_f: number
      description: string
    }
    dispersion: {
      samples: number
      underdispersed_cases: number
      underdispersed_rate: number
      description: string
    }
  }
  notes: string[]
}

export interface WeatherForecast {
  city_key: string
  city_name: string
  target_date: string
  mean_high: number
  std_high: number
  mean_low: number
  std_low: number
  num_members: number
  ensemble_agreement: number | null
}

export interface WeatherCityPoint {
  timestamp: string
  target_date: string
  horizon?: string
  best?: number | null
  ecmwf?: number | null
  hrrr?: number | null
  metar?: number | null
  china_live?: number | null
  ensemble_mean?: number | null
  ensemble_std?: number | null
  humidity?: number | null
  cloud_cover?: number | null
  forecast_cloud_cover?: number | null
  precipitation?: number | null
  precipitation_probability?: number | null
  wind_speed?: number | null
  wind_direction?: number | null
  visibility?: number | null
  pressure?: number | null
  dew_point?: number | null
  shortwave_radiation?: number | null
  condition?: string | null
  source?: string
  member_count?: number
  archive?: boolean
}

export interface HourlySourcePoint extends WeatherCityPoint {
  local_hour?: string
  local_time?: string
  temperature?: number | null
  revision_count?: number
  snapshot_count?: number
  distinct_count?: number
  retrieved_at?: string | null
  raw_text?: string | null
  raw?: Record<string, unknown>
}

export interface ForecastRevisionItem {
  run_id: number
  snapshot_key?: string
  fetched_at: string
  fetched_at_local: string
  valid_at: string
  temperature: number
  display_temperature: number
  delta_from_previous?: number | null
  source_unit?: string
  parser_version?: string
}

export interface ForecastRevisionHistory {
  ok: boolean
  city: string
  target_date: string
  local_hour: string
  timezone?: string
  unit?: string
  reason?: string
  snapshot_count: number
  revision_count: number
  distinct_count: number
  unchanged_snapshot_count: number
  latest_temperature?: number | null
  latest_fetched_at?: string | null
  revisions: ForecastRevisionItem[]
}

export interface HourlySourceSeries {
  forecast?: HourlySourcePoint[]
  metar?: HourlySourcePoint[]
  historical?: HourlySourcePoint[]
  china_live?: HourlySourcePoint[]
  pws?: HourlySourcePoint[]
  historical_fallback?: HourlySourcePoint[]
}

export interface HourlyBiasPair {
  local_time: string
  forecast: number
  observed: number
  delta: number
  cumulative_mean: number
  n: number
}

export interface HourlyBiasSourceStats {
  count: number
  avg_delta?: number | null
  pearson_r?: number | null
  cutoff_hour?: string | null
  pairs?: HourlyBiasPair[]
}

export interface HourlyNativeOverlapStats {
  count: number
  possible: number
  ratio?: number | null
  cutoff_time?: string | null
}

export interface HourlyBiasStats {
  method: string
  metar?: HourlyBiasSourceStats
  historical?: HourlyBiasSourceStats
  historical_metar_overlap?: HourlyNativeOverlapStats
}

export interface HourlyConsensusSummary {
  ok: boolean
  city: string
  target_date: string
  rows: number
  source: string
  points: HourlySourcePoint[]
  series?: HourlySourceSeries
  bias_stats?: HourlyBiasStats
  forecast_peak_marker?: {
    hour_float: number
    date: string
    local_time: string
    temperature: number
    source_hour: string
    method: string
    tie_policy: string
    lookback_hours: number
    snapshot_count: number
    latest_retrieved_at?: string | null
    source: string
  } | null
}

export interface HistoricalWeatherPoint {
  city: string
  city_name: string
  station_id?: string
  target_date: string
  unit: string
  actual_high?: number | null
  humidity_mean?: number | null
  provider?: string
  source_confidence?: number
  calibration_tier?: 'live_truth' | 'research_truth' | string
  source_url?: string
  fetched_at?: string | null
}

export interface WeatherCitySeries {
  city_key: string
  city_name: string
  station_id?: string
  station_name?: string
  settlement_station_id?: string
  settlement_station_name?: string
  settlement_rule_verified_at?: string | null
  settlement_timezone?: string
  settlement_unit?: string
  settlement_time_basis?: string
  primary_settlement_source?: string
  verification_status?: string
  unit: string
  region?: string
  display_enabled?: boolean
  city_scope?: 'market_candidate' | 'observation_only' | string
  enabled?: boolean
  tier?: number
  last_refreshed_at?: string | null
  latest_best?: number | null
  latest_metar?: number | null
  latest_source?: string | null
  latest_timestamp?: string | null
  current_temp?: number | null
  current_temp_source?: string | null
  current_temp_timestamp?: string | null
  forecast_high?: number | null
  summary_target_date?: string | null
  humidity_status?: 'available' | 'not_collected' | string
  history_count?: number
  forecast_count?: number
  hourly_count?: number
  history_points?: HistoricalWeatherPoint[]
  forecast_points?: WeatherCityPoint[]
  hourly_points?: WeatherCityPoint[]
  points: WeatherCityPoint[]
}

export interface SchedulerCityResult {
  city?: string | null
  station_id?: string | null
  ok: boolean
  error?: string | null
  reports_upserted?: number | null
  reports_fetched?: number | null
  rows_upserted?: number | null
  failed?: number | null
}

export interface SchedulerPollerStatus {
  key: 'forecast_poller' | 'metar_poller' | 'derive_poller' | string
  label: string
  interval_seconds: number
  running: boolean
  last_run_at?: string | null
  last_started_at?: string | null
  age_seconds?: number | null
  last_duration_ms?: number | null
  fails_last_hour: number
  next_run_at?: string | null
  last_status?: string
  last_message?: string
  run_count?: number
  consecutive_failures?: number
  last_result?: {
    ok?: boolean
    cities?: number
    ok_cities?: number
    failed_cities?: number
    result_count?: number
    city_results?: SchedulerCityResult[]
  }
}

export interface SchedulerStatus {
  ok: boolean
  scheduler_version: string
  running: boolean
  started_at?: string | null
  message?: string
  city_concurrency?: number
  pollers: Record<string, SchedulerPollerStatus>
}

export interface SourceHealthSource {
  key: string
  label: string
  role: string
  required: boolean
  status: 'healthy' | 'degraded' | 'stale' | 'missing' | 'not_applicable' | string
  reasons?: string[]
  latest_at?: string | null
  age_seconds?: number | null
  sample_count?: number
  coverage_pct?: number
  expected_interval_seconds?: number
  covered_cities?: string[]
  missing_cities?: string[]
  stale_cities?: string[]
  errors_last_hour?: number
}

export interface SourceHealthMatrix {
  ok: boolean
  version: string
  generated_at: string
  overall_status: string
  config: {
    deb_weight_mode: string
    weather_com_forecast_enabled: boolean
    weather_com_configured: boolean
    pws_peak_lock_enabled: boolean
    wunderground_pws_configured: boolean
    live_trading: boolean
  }
  enabled_cities: string[]
  source_keys: string[]
  sources: SourceHealthSource[]
  required_blockers: string[]
  summary: {
    sources: number
    healthy: number
    degraded: number
    stale: number
    missing: number
    required_blockers: number
    optional_gaps: number
  }
}

export interface ApiSettingProvider {
  key: string
  label: string
  description: string
  configured: boolean
  masked_value: string
  docs_url: string
  test_label: string
  test_has_side_effect: boolean
}

export interface ApiSettingsResponse {
  ok: boolean
  storage: string
  updated_at?: string | null
  providers: ApiSettingProvider[]
}

export interface ApiSettingTestResult {
  provider_key: string
  ok: boolean
  status: 'success' | 'missing' | 'confirmation_required' | 'unauthorized' | 'rate_limited' | 'failed' | string
  message: string
  duration_ms: number
  tested_at: string
  reason?: string
}

export interface PaperValidationStatus {
  ok: boolean
  status: 'inactive' | 'active' | 'stopped' | 'completed' | string
  version?: string
  run_id?: string
  started_at?: string
  ends_at?: string
  bankroll_usd?: number
  orders_total?: number
  orders_today?: number
  open_positions?: number
  resolved_orders?: number
  exited_orders?: number
  wins?: number
  losses?: number
  win_rate?: number | null
  realized_pnl?: number
  brier_score?: number | null
  spent_today_usd?: number
  cash_available_usd?: number
  max_per_trade_usd?: number
  daily_max_usd?: number
  max_open_positions?: number
  max_orders_per_day?: number
  strategies?: string[]
  cities?: string[]
  strategy_revision_id?: string
  strategy_profile_snapshot?: StrategyProfileSnapshot
  kelly_multiplier?: number
  bankroll_fraction_cap?: number
}

export interface PaperValidationStartOptions {
  bankroll_usd: number
  duration_days?: number
  max_per_trade_usd?: number
  daily_max_usd?: number
  max_open_positions?: number
  max_orders_per_day?: number
  decision_max_age_minutes?: number
  cities?: string[]
  strategies: string[]
  strategy_revision_id?: string
}

export interface StrategyProfileSnapshot {
  revision_id?: string
  profile_key?: string
  revision_no?: number
  schema_version?: number
  engine_version?: string
  content_sha256?: string
  parameters?: StrategyProfileParameters
}

export interface StrategyProfileParameters {
  schema_version: number
  decision_policy: {
    min_paper_trade_edge: number
    min_live_trade_edge: number
    min_trade_edge?: number
    max_spread_bps: number
    stale_book_seconds: number
    min_bias_sample_days: number
    low_price_tail_ask: number
  }
  sizing: {
    paper_kelly_multiplier: number
    live_kelly_multiplier: number
    max_paper_bankroll_fraction_per_trade: number
    max_live_bankroll_fraction_per_trade: number
    kelly_multiplier?: number
    max_bankroll_fraction_per_trade?: number
  }
  strategies: Record<string, Record<string, boolean | number>>
  exit_policy: {
    mode: 'hold_to_settlement' | 'model_guarded' | 'model_guarded_take_profit' | string
    model_probability_threshold?: number
    min_bid_over_model_edge?: number
    confirmations_required?: number
    min_hold_minutes?: number
    max_quote_age_seconds?: number
    take_profit_min_roi?: number
    take_profit_min_usd?: number
    take_profit_min_ticks?: number
    take_profit_min_hold_minutes?: number
  }
}

export interface StrategyProfileRevision extends StrategyProfileSnapshot {
  revision_id: string
  profile_key: string
  revision_no: number
  engine_version: string
  content_sha256: string
  parameters: StrategyProfileParameters
  active_scopes: string[]
  created_by?: string
  change_note?: string
  created_at?: string
}

export interface StrategyProfilesResponse {
  ok: boolean
  profiles: StrategyProfileRevision[]
  allowed_scopes: string[]
  live_trading: boolean
  live_execution_production_ready: boolean
}

export interface CityEvidenceModule {
  rows?: number
  signals?: number
  buckets?: number
  ready?: boolean
  chart?: string
  table?: string
  engine?: string
  formula?: string
  source?: string
  series?: string[]
  empty_state?: string
  strict_matching_required?: boolean
  summary?: CityEvidenceDiffStatsSummary
  probability_summary?: CityEvidenceProbabilitySummary
  market_summary?: CityEvidenceMarketBucketSummary
}

export interface CityEvidenceProbabilityBucket {
  bucket?: string
  probability?: number | null
  ask?: number | null
  bid?: number | null
  edge?: number | null
  market_id?: string
  signal_id?: number
  is_signal?: boolean
  actionable?: boolean
}

export interface CityEvidenceProbabilitySummary {
  signal_count?: number
  bucket_count?: number
  normalized_count?: number
  actionable_signal_count?: number
  highest_bucket?: string | null
  highest_probability?: number | null
  strict_matching_required?: boolean
  source?: string
  top_buckets?: CityEvidenceProbabilityBucket[]
}

export interface CityEvidenceMarketReason {
  reason: string
  count: number
}

export interface CityEvidenceMarketSignal {
  signal_id?: number
  market_id?: string
  event_url?: string | null
  bucket?: string
  price?: number | null
  bid?: number | null
  spread?: number | null
  edge?: number | null
  paper_allowed?: boolean
  live_allowed?: boolean
  reasons?: string[]
}

export interface CityEvidenceMarketBucketSummary {
  signal_count?: number
  bucket_count?: number
  matched_bucket_count?: number
  actionable_signal_count?: number
  paper_allowed_count?: number
  live_allowed_count?: number
  blocked_signal_count?: number
  open_tail_count?: number
  low_price_tail_count?: number
  missing_price_count?: number
  high_spread_count?: number
  stale_book_count?: number
  strict_matching_required?: boolean
  ready?: boolean
  reason_counts?: CityEvidenceMarketReason[]
  top_executable?: CityEvidenceMarketSignal[]
  top_blocked?: CityEvidenceMarketSignal[]
}

export interface CityEvidenceDiffStatsRow {
  timestamp?: string
  local_hour?: string
  observed?: number | null
  forecast?: number | null
  delta?: number | null
  source?: string
}

export interface CityEvidenceDiffStatsSummary {
  count?: number
  avg_delta?: number | null
  mae?: number | null
  pearson_r?: number | null
  metar_hours?: number
  forecast_hours?: number
  overlap_count?: number
  overlap_ratio?: number | null
  historical_metar_overlap_count?: number
  historical_metar_overlap_ratio?: number | null
  rows?: CityEvidenceDiffStatsRow[]
}

export interface CityEvidenceDate {
  target_date: string
  ready_modules: number
  module_count: number
  tabs: string[]
  modules: Record<string, CityEvidenceModule>
}

export interface CityEvidence {
  city_key: string
  city_name: string
  station_id?: string
  unit: string
  generated_from: string
  data_sources: string[]
  dates: CityEvidenceDate[]
  latest_date?: string | null
  latest_ready_modules?: number
}

export interface WeatherSignal {
  id?: number
  market_id: string
  city_key: string
  city_name: string
  target_date: string
  question?: string
  event_url?: string
  yes_token_id?: string
  bucket_label?: string
  threshold_f: number
  metric: string
  direction: string
  model_probability: number
  market_probability: number
  probability_edge?: number
  edge: number
  raw_model_probability?: number | null
  raw_edge?: number | null
  calibrated_probability?: number | null
  calibrated_edge?: number | null
  calibrated_sigma_f?: number | null
  calibration_bias_f?: number | null
  confidence: number
  suggested_size: number
  reasoning: string
  ensemble_mean: number
  ensemble_std: number
  ensemble_members: number
  actionable: boolean
  platform?: string
  status?: string
  limit_price?: number
  bid_price?: number
  spread?: number
  shares?: number
  sim_amount?: number | null
  manual_note?: string | null
  paper_position?: boolean
  fit_markets?: number
  fit_samples?: number
  fit_mae_f?: number
  fit_bias_f?: number
  fit_decayed_bias_f?: number
  quality_flags?: string[]
  truth?: TruthCityHealth | null
  distribution?: EventDistribution | null
  decision?: SignalDecision | null
  strategy_tags?: string[]
  strategy_score?: number
  strategy_notes?: string[]
  dispersion_ratio?: number | null
  live_allowed?: boolean
  live_risk_level?: 'eligible' | 'caution' | 'blocked'
  live_block_reasons?: string[]
  live_cautions?: string[]
  live_pre_strategy_allowed?: boolean
  near_lock?: {
    hours_left: number
    observed_temp: number
    model_best: number
    remaining_potential: number
  } | null
}

export interface BulkSimulateSkipExample {
  id?: number
  reason: string
  city?: string | null
  target_date?: string | null
  title?: string | null
  event_url?: string | null
}

export interface BulkSimulateResult {
  ok: boolean
  count: number
  spent: number
  remaining: number
  total_current: number
  skipped: number
  reason_counts: Record<string, number>
  examples: BulkSimulateSkipExample[]
}

export interface ModelDatasetAudit {
  audit_version: string
  generated_at: string
  status: 'ready' | 'blocked'
  required_samples: number
  summary: {
    event_days: number
    mature_event_days: number
    pending_settlement_samples: number
    training_eligible_samples: number
    baseline_ready_samples: number
    replay_ready_samples: number
    blocked_samples: number
    cities: number
    eligible_cities: number
    baseline_ready_cities: number
  }
  reason_counts: Record<string, number>
  training_reason_counts: Record<string, number>
  operational_counts: {
    unverified_contract_event_days: number
    auto_verified_unreviewed_contracts: number
    mature_auto_verified_unreviewed_contracts: number
    pending_settlement_samples: number
  }
  leakage_flags: Record<string, number>
  source_counts: Record<string, number>
  horizon_counts: Record<string, number>
  next_actions: Array<{
    key: string
    priority: number
    label: string
    count: number
    impact: string
    command: string
    apply_command?: string
    requires_operator: boolean
    targets: Array<Record<string, string>>
  }>
  cities: Array<{
    city: string
    city_name: string
    samples: number
    training_eligible: number
    baseline_ready: number
    replay_ready: number
    eligible_truth: number
    no_leak_forecast_runs: number
    warnings: Record<string, number>
    reasons: Record<string, number>
  }>
}

export interface ForecastArchiveManifest {
  manifest_version: string
  generated_at: string
  record_count: number
  by_city: Record<string, number>
  by_source: Record<string, number>
  records: Array<{
    city: string
    city_name?: string
    target_date?: string
    timezone?: string
    station_id?: string
    station_name?: string
    unit?: string
    source: string
    provider?: string
    model?: string
    model_version?: string
    archive_gap_reasons?: string[]
    no_leak_rule?: string
  }>
  sources: string[]
  schema_doc: string
  template_command: string
  import_dry_run_command: string
  import_apply_command: string
  audit_summary?: ModelDatasetAudit['summary']
  reason_counts?: Record<string, number>
}

export interface ForwardValidationMetric {
  n: number
  mean?: number | null
  std?: number | null
  ci95_low?: number | null
  ci95_high?: number | null
}

export interface ForwardValidationSummary {
  ok: boolean
  protocol: {
    protocol_id: string
    started_at: string
    ask_min: number
    ask_max: number
    edge_min: number
    target_n: number
    power_effect_clv: number
    expected_evaluation_date: string
  }
  progress: {
    samples: number
    enrolled_candidates?: number
    target_samples: number
    completion_percent: number
    expected_evaluation_date: string
  }
  clv: ForwardValidationMetric
  probability_score: {
    n: number
    model_brier?: number | null
    market_brier?: number | null
  }
  paper_pnl: {
    settled_orders: number
    open_orders: number
    realized_usd: number
    unrealized_usd: number
  }
  hypotheses: {
    'H-A': ForwardValidationMetric
    'H-B': ForwardValidationMetric
  }
  strata?: {
    paper_allowed?: {
      true?: {
        enrolled: number
        clv: ForwardValidationMetric
      }
      false?: {
        enrolled: number
        clv: ForwardValidationMetric
      }
    }
  }
  generated_at?: string
}

export interface DashboardData {
  stats: BotStats
  v3?: V3Summary
  data_readiness?: DataReadiness
  production_refresh?: ProductionRefreshResult | null
  model_dataset_audit?: ModelDatasetAudit
  truth_health?: TruthHealth
  btc_price: BtcPrice | null
  microstructure: Microstructure | null
  windows: BtcWindow[]
  active_signals: Signal[]
  recent_trades: Trade[]
  equity_curve: EquityPoint[]
  calibration: CalibrationSummary | null
  backtest?: BacktestSummary | null
  weather_signals: WeatherSignal[]
  weather_forecasts: WeatherForecast[]
  weather_city_series?: WeatherCitySeries[]
  city_statuses?: Record<string, CityStatusConfig>
  city_evidence?: CityEvidence[]
  recommendations?: DashboardRecommendations
  forward_validation?: ForwardValidationSummary
  scheduler_status?: SchedulerStatus
  events?: DashboardEvent[]
  fetch_log?: FetchLogRow[]
  _meta?: {
    cache?: string
    reason?: string
    generated_at?: string
  }
}

export type CityTradingStatus = 'fully_active' | 'paper_only' | 'monitor_only' | 'observation_only'

export interface CityStatusConfig {
  status?: CityTradingStatus | string
  rank?: number
  volume?: number
  settlement?: string
  reason?: string
  note?: string
}

export interface TruthDeltaAuditRow {
  id?: number
  audit_key?: string
  icao?: string
  city?: string
  date_local?: string
  wu_high_c?: number | null
  iem_high_c?: number | null
  hko_high_c?: number | null
  polymarket_resolved_bucket?: string | null
  delta_wu_minus_iem?: number | null
  resolved_at?: string | null
  notes?: string | null
}

export interface TruthDeltaAuditSummary {
  ok: boolean
  count: number
  city_filter?: string
  rows: TruthDeltaAuditRow[]
  by_city?: Array<{
    city?: string
    icao?: string
    count: number
    latest_date?: string | null
    delta_wu_minus_iem_values?: number[]
  }>
  histogram?: Array<{ bucket: string; count: number }>
}

export interface ModelRepriceEvent {
  id?: number
  event_key?: string
  city_key?: string
  target_date?: string
  market_id?: string
  bucket_key?: string
  triggered_at?: string
  model_source?: string
  previous_model_prob?: number | null
  model_prob?: number | null
  delta_prob?: number | null
  market_mid?: number | null
  edge?: number | null
  alpha_candidate?: boolean
}

export interface ModelRepriceEventSummary {
  ok: boolean
  count: number
  alpha_count: number
  city_filter?: string
  target_date_filter?: string
  rows: ModelRepriceEvent[]
}

export interface DashboardEvent {
  id?: number
  timestamp?: string
  type?: string
  message?: string
  data?: unknown
}

export interface FetchLogRow {
  index?: number
  time?: string
  source?: string
  stage?: string
  status?: 'OK' | 'WARN' | 'ERR' | 'INFO' | string
  duration?: number | string | null
  message?: string
  details?: string
  event_id?: number
  event_type?: string
  city?: string
  target_date?: string
}

export interface V3Summary {
  signals: number
  ai_reviews: number
  paper_orders: number
  live_orders: number
  live_open_orders: number
  risk_events: number
  notifications: number
  config?: {
    live_trading: boolean
    live_dry_run: boolean
    ai_review_enabled: boolean
    ai_required_for_live: boolean
    max_order_usd: number
    daily_max_usd: number
    max_open_positions: number
    feishu_configured: boolean
    minimax_configured: boolean
  }
}
