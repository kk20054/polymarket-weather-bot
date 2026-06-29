import axios from 'axios'
import type { AutoSimulationStatus, BulkContractVerificationResult, BulkSimulateResult, DashboardData, Signal, Trade, BotStats, BtcPrice, BtcWindow, WeatherForecast, WeatherSignal, TemperatureFitData, SettlementContractList, ForecastArchiveManifest, ProductionRefreshResult, ProductionValidationReport } from './types'

const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8765'

const api = axios.create({
  baseURL: `${API_BASE}/api`,
})

export async function fetchDashboard(): Promise<DashboardData> {
  const { data } = await api.get<DashboardData>('/dashboard')
  return data
}

export async function fetchSignals(): Promise<Signal[]> {
  const { data } = await api.get<Signal[]>('/signals')
  return data
}

export async function fetchBtcPrice(): Promise<BtcPrice | null> {
  const { data } = await api.get<BtcPrice | null>('/btc/price')
  return data
}

export async function fetchBtcWindows(): Promise<BtcWindow[]> {
  const { data } = await api.get<BtcWindow[]>('/btc/windows')
  return data
}

export async function fetchTrades(): Promise<Trade[]> {
  const { data } = await api.get<Trade[]>('/trades')
  return data
}

export async function fetchStats(): Promise<BotStats> {
  const { data } = await api.get<BotStats>('/stats')
  return data
}

export async function runScan(): Promise<{ total_signals: number; actionable_signals: number }> {
  const { data } = await api.post('/run-scan')
  return data
}

export async function simulateTrade(ticker: string): Promise<{ trade_id: number; size: number }> {
  const { data } = await api.post('/simulate-trade', null, {
    params: { signal_ticker: ticker }
  })
  return data
}

export async function bulkSimulateSignals(): Promise<BulkSimulateResult> {
  const { data } = await api.post('/signals/bulk-simulate')
  return data
}

export async function setAutoSimulation(enabled: boolean, intervalSeconds = 300): Promise<AutoSimulationStatus & { ok: boolean }> {
  const { data } = await api.post('/simulation/auto', {
    enabled,
    interval_seconds: intervalSeconds,
  })
  return data
}

export async function resetSimulation(balance: number, clearMarks = false): Promise<{ ok: boolean; balance: number; simulation_started_at?: string; cleared_positions?: number }> {
  const { data } = await api.post('/simulation/reset', { balance, clear_marks: clearMarks })
  return data
}

export async function updateSignalStatus(signalId: number, status: string, amount?: number): Promise<{ ok: boolean }> {
  const payload: { status: string; amount?: number } = { status }
  if (amount !== undefined && Number.isFinite(amount)) payload.amount = amount
  const { data } = await api.post(`/signals/${signalId}/status`, payload)
  return data
}

export async function placeLiveOrder(signalId: number, amount?: number): Promise<{ ok: boolean; status: string; reason?: string | null }> {
  const payload: { signal_id: number; amount?: number } = { signal_id: signalId }
  if (amount !== undefined && Number.isFinite(amount)) payload.amount = amount
  const { data } = await api.post('/v3/live-order', payload)
  return data
}

export async function notifyDailySummary(): Promise<{ ok: boolean; sent: boolean }> {
  const { data } = await api.post('/v3/notify-daily')
  return data
}

export async function startBot(): Promise<{ status: string; is_running: boolean }> {
  const { data } = await api.post('/bot/start')
  return data
}

export async function stopBot(): Promise<{ status: string; is_running: boolean }> {
  const { data } = await api.post('/bot/stop')
  return data
}

export async function settleTradesApi(): Promise<{ ok: boolean; checked: number; settled_count: number; pending_count: number; errors?: unknown[] }> {
  const { data } = await api.post('/settle-trades')
  return data
}

export async function fetchTemperatureFit(): Promise<TemperatureFitData> {
  const { data } = await api.get<TemperatureFitData>('/temperature-fit')
  return data
}

export async function fetchForecastArchiveManifest(): Promise<ForecastArchiveManifest> {
  const { data } = await api.get<ForecastArchiveManifest>('/forecast-archive/manifest', {
    params: { limit: 80 },
  })
  return data
}

export async function fetchProductionValidation(): Promise<ProductionValidationReport> {
  const { data } = await api.get<ProductionValidationReport>('/production-validation')
  return data
}

export async function fetchSettlementContracts(status = 'unverified', limit = 12): Promise<SettlementContractList> {
  const { data } = await api.get<SettlementContractList>('/contracts', {
    params: { status, limit },
  })
  return data
}

export async function verifySettlementContract(contractId: string, verified = true, note = 'dashboard manual review'): Promise<{ ok: boolean }> {
  const { data } = await api.post(`/contracts/${encodeURIComponent(contractId)}/verification`, {
    verified,
    reviewer: 'dashboard',
    note,
  })
  return data
}

export async function verifySettlementContractsBulk(contractIds: string[], apply = true, matureOnly = false, note = 'dashboard visible batch review'): Promise<BulkContractVerificationResult> {
  const { data } = await api.post<BulkContractVerificationResult>('/contracts/bulk-verification', {
    contract_ids: contractIds,
    limit: contractIds.length,
    reviewer: 'dashboard',
    note,
    mature_only: matureOnly,
    apply,
  })
  return data
}

export async function runProductionRefresh(options: {
  cities?: string[]
  days?: number
  limit?: number
  skipSignalScan?: boolean
} = {}): Promise<ProductionRefreshResult> {
  const { data } = await api.post<ProductionRefreshResult>('/production-refresh', {
    cities: options.cities ?? [],
    days: options.days ?? 1,
    limit: options.limit ?? 20,
    skip_signal_scan: options.skipSignalScan ?? true,
  })
  return data
}

export async function backfillWeatherHistory(days = 30): Promise<{
  ok: boolean
  days: number
  cities: number
  fetched: number
  cached_cities: number
  errors: Array<{ city: string; error: string }>
}> {
  const { data } = await api.post('/weather/backfill-history', { days })
  return data
}

export async function resetBot(): Promise<{ status: string; trades_deleted: number; new_bankroll: number }> {
  const { data } = await api.post('/bot/reset')
  return data
}

export async function fetchWeatherForecasts(): Promise<WeatherForecast[]> {
  const { data } = await api.get<WeatherForecast[]>('/weather/forecasts')
  return data
}

export async function fetchWeatherSignals(): Promise<WeatherSignal[]> {
  const { data } = await api.get<WeatherSignal[]>('/weather/signals')
  return data
}
