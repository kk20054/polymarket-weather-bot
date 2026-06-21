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
  simulation_started_at?: string | null
  scanner_status?: string
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
  brier_score: number
  buckets: BacktestBucket[]
  sources: BacktestSource[]
  notes: string[]
}

export interface TemperatureFitRecord {
  city_key: string
  city_name: string
  target_date: string
  unit: string
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
  rmse_f: number
  latest_date?: string
  latest_forecast?: number
  latest_actual?: number
}

export interface TemperatureFitSummary {
  markets: number
  samples: number
  mae_f: number
  bias_f: number
  rmse_f: number
}

export interface TemperatureFitData {
  summary: TemperatureFitSummary
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
  fit_samples?: number
  fit_mae_f?: number
  fit_bias_f?: number
  quality_flags?: string[]
  strategy_tags?: string[]
  strategy_score?: number
  strategy_notes?: string[]
  dispersion_ratio?: number | null
  near_lock?: {
    hours_left: number
    observed_temp: number
    model_best: number
    remaining_potential: number
  } | null
}

export interface DashboardData {
  stats: BotStats
  v3?: V3Summary
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
