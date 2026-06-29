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
  ensemble_mean?: number | null
  ensemble_std?: number | null
  humidity?: number | null
  source?: string
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
  unit: string
  latest_best?: number | null
  latest_metar?: number | null
  latest_source?: string | null
  latest_timestamp?: string | null
  humidity_status?: 'available' | 'not_collected' | string
  history_count?: number
  forecast_count?: number
  history_points?: HistoricalWeatherPoint[]
  forecast_points?: WeatherCityPoint[]
  points: WeatherCityPoint[]
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
  events?: DashboardEvent[]
  _meta?: {
    cache?: string
    reason?: string
    generated_at?: string
  }
}

export interface DashboardEvent {
  id?: number
  timestamp?: string
  type?: string
  message?: string
  data?: unknown
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
