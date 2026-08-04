import axios from 'axios'
import type { ApiSettingsResponse, ApiSettingTestResult, AutoSimulationStatus, BucketProbabilitySummary, BulkContractVerificationResult, BulkSimulateResult, DashboardData, Signal, Trade, BotStats, BtcPrice, BtcWindow, WeatherForecast, WeatherSignal, TemperatureFitData, SettlementContractList, ForecastArchiveManifest, ProductionRefreshResult, ProductionValidationReport, ProductionActionRequest, ProductionActionRunResult, MarketBucketSummary, SignalDecisionSummary, DailyMaxPredictionSummary, SchedulerStatus, SourceHealthMatrix, PaperValidationStatus, PaperValidationStartOptions, PaperExecutionSummary, PaperExecutionResult, WeatherCitySeries, TruthDeltaAuditSummary, ModelRepriceEventSummary, HourlyConsensusSummary, ForecastRevisionHistory, StrategyProfileParameters, StrategyProfileRevision, StrategyProfilesResponse } from './types'

const API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')

const api = axios.create({
  baseURL: `${API_BASE}/api`,
})

export type ApiAccessMode = 'local' | 'live' | 'snapshot' | 'unknown'

export type ApiAccessState = {
  mode: ApiAccessMode
  snapshotAt: string | null
  writable: boolean
  observedAt: string
}

const isLocalBrowser = () => typeof window !== 'undefined'
  && ['127.0.0.1', 'localhost'].includes(window.location.hostname)

let apiAccessState: ApiAccessState = {
  mode: isLocalBrowser() ? 'local' : 'unknown',
  snapshotAt: null,
  writable: isLocalBrowser(),
  observedAt: new Date().toISOString(),
}

const apiAccessListeners = new Set<(state: ApiAccessState) => void>()

function responseHeader(headers: unknown, name: string) {
  if (!headers || typeof headers !== 'object') return ''
  const record = headers as Record<string, unknown> & { get?: (key: string) => unknown }
  return String(record[name] ?? record.get?.(name) ?? '')
}

function recordApiAccess(headers: unknown) {
  const reportedMode = responseHeader(headers, 'x-weatherbot-data-mode')
  if (reportedMode !== 'live' && reportedMode !== 'snapshot') return
  const next: ApiAccessState = {
    mode: reportedMode,
    snapshotAt: responseHeader(headers, 'x-weatherbot-snapshot-at') || null,
    writable: reportedMode === 'live' && responseHeader(headers, 'x-weatherbot-write-enabled') === 'true',
    observedAt: new Date().toISOString(),
  }
  apiAccessState = next
  apiAccessListeners.forEach(listener => listener(next))
}

api.interceptors.response.use(
  response => {
    recordApiAccess(response.headers)
    return response
  },
  error => {
    recordApiAccess(error?.response?.headers)
    return Promise.reject(error)
  },
)

export function getApiAccessState() {
  return apiAccessState
}

export function subscribeApiAccessState(listener: (state: ApiAccessState) => void) {
  apiAccessListeners.add(listener)
  return () => {
    apiAccessListeners.delete(listener)
  }
}

export async function fetchDashboard(city = ''): Promise<DashboardData> {
  const { data } = await api.get<DashboardData>('/dashboard', { params: city ? { city } : undefined })
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

export async function fetchProductionRefreshStatus(): Promise<ProductionRefreshResult> {
  const { data } = await api.get<ProductionRefreshResult>('/production-refresh/status')
  return data
}

export async function fetchSchedulerStatus(): Promise<SchedulerStatus> {
  const { data } = await api.get<SchedulerStatus>('/scheduler/status')
  return data
}

export async function fetchSourceHealth(): Promise<SourceHealthMatrix> {
  const { data } = await api.get<SourceHealthMatrix>('/source-health')
  return data
}

export async function fetchApiSettings(): Promise<ApiSettingsResponse> {
  const { data } = await api.get<ApiSettingsResponse>('/developer/api-settings')
  return data
}

export async function updateApiSetting(providerKey: string, value = '', clear = false): Promise<ApiSettingsResponse['providers'][number]> {
  const { data } = await api.put<{ ok: boolean; provider: ApiSettingsResponse['providers'][number] }>(
    `/developer/api-settings/${encodeURIComponent(providerKey)}`,
    { value, clear, confirm: true },
  )
  return data.provider
}

export async function testApiSetting(providerKey: string, value = '', allowSideEffect = false): Promise<ApiSettingTestResult> {
  const { data } = await api.post<ApiSettingTestResult>(
    `/developer/api-settings/${encodeURIComponent(providerKey)}/test`,
    { value, confirm: true, allow_side_effect: allowSideEffect },
  )
  return data
}

export async function fetchPaperValidationStatus(): Promise<PaperValidationStatus> {
  const { data } = await api.get<PaperValidationStatus>('/paper-validation/status')
  return data
}

export async function startPaperValidation(options: PaperValidationStartOptions): Promise<PaperValidationStatus & { reason?: string }> {
  const { data } = await api.post('/paper-validation/start', options)
  return data
}

export async function stopPaperValidation(): Promise<PaperValidationStatus & { reason?: string }> {
  const { data } = await api.post('/paper-validation/stop')
  return data
}

export async function runPaperValidationTick(options: {
  runId?: string
  decisionId?: string
  city?: string
  targetDate?: string
  strategies?: string[]
  strategyRevisionId?: string
  decisionBatchIssuedAt?: string
  apply?: boolean
} = {}): Promise<PaperExecutionResult> {
  const { data } = await api.post<PaperExecutionResult>('/paper-validation/tick', {
    run_id: options.runId ?? '',
    decision_id: options.decisionId ?? '',
    city: options.city ?? '',
    target_date: options.targetDate ?? '',
    strategies: options.strategies,
    strategy_revision_id: options.strategyRevisionId ?? '',
    decision_batch_issued_at: options.decisionBatchIssuedAt ?? '',
    apply: options.apply ?? true,
  })
  return data
}

export async function fetchStrategyProfiles(): Promise<StrategyProfilesResponse> {
  const { data } = await api.get<StrategyProfilesResponse>('/developer/strategy-profiles')
  return data
}

export async function createStrategyProfile(payload: {
  profile_key: string
  parameters: StrategyProfileParameters
  change_note?: string
  activate_scopes?: string[]
  confirm: boolean
}): Promise<StrategyProfileRevision> {
  const { data } = await api.post<StrategyProfileRevision>('/developer/strategy-profiles', payload)
  return data
}

export async function activateStrategyProfile(
  revisionId: string,
  scope: string,
  reason = '',
): Promise<StrategyProfileRevision> {
  const { data } = await api.post<StrategyProfileRevision>(
    `/developer/strategy-profiles/${encodeURIComponent(revisionId)}/activate`,
    { scope, reason, confirm: true },
  )
  return data
}

export async function startScheduler(): Promise<SchedulerStatus> {
  const { data } = await api.post<SchedulerStatus>('/scheduler/start')
  return data
}

export async function stopScheduler(): Promise<SchedulerStatus> {
  const { data } = await api.post<SchedulerStatus>('/scheduler/stop')
  return data
}

export async function setStationEnabled(cityKey: string, enabled: boolean, tier?: number): Promise<{ ok: boolean; city_key: string; enabled: boolean; tier: number; station?: WeatherCitySeries }> {
  const payload: { enabled: boolean; tier?: number } = { enabled }
  if (tier !== undefined) payload.tier = tier
  const { data } = await api.post(`/stations/${encodeURIComponent(cityKey)}/enabled`, payload)
  return data
}

export async function fetchMarketBuckets(city: string, targetDate: string, limit = 80): Promise<MarketBucketSummary> {
  const { data } = await api.get<MarketBucketSummary>('/market-buckets', {
    params: { city, target_date: targetDate, limit },
  })
  return data
}

export async function fetchSignalDecisions(city: string, targetDate: string, limit = 120): Promise<SignalDecisionSummary> {
  const { data } = await api.get<SignalDecisionSummary>('/signal-decisions', {
    params: { city, target_date: targetDate, limit },
  })
  return data
}

export async function fetchPaperOrders(city: string, targetDate: string, limit = 100, cohortRunId = ''): Promise<PaperExecutionSummary> {
  const { data } = await api.get<PaperExecutionSummary>('/paper-orders', {
    params: { city, target_date: targetDate, cohort_run_id: cohortRunId, limit },
  })
  return data
}

export async function executePaperOrders(options: {
  decisionId?: string
  city?: string
  targetDate?: string
  amount?: number
  strategies?: string[]
  strategyRevisionId?: string
  decisionBatchIssuedAt?: string
  cohortRunId?: string
  limit?: number
  dryRun?: boolean
}): Promise<PaperExecutionResult> {
  const { data } = await api.post<PaperExecutionResult>('/paper-orders/execute', {
    decision_id: options.decisionId ?? '',
    city: options.city ?? '',
    target_date: options.targetDate ?? '',
    amount: options.amount,
    strategies: options.strategies,
    strategy_revision_id: options.strategyRevisionId ?? '',
    decision_batch_issued_at: options.decisionBatchIssuedAt ?? '',
    cohort_run_id: options.cohortRunId ?? '',
    limit: options.limit ?? 20,
    dry_run: options.dryRun ?? true,
  })
  return data
}

export async function fetchDailyMaxPredictions(city: string, targetDate: string): Promise<DailyMaxPredictionSummary> {
  const { data } = await api.get<DailyMaxPredictionSummary>('/daily-max-predictions', {
    params: { city, target_date: targetDate },
  })
  return data
}

export async function fetchBucketProbabilities(city: string, targetDate: string): Promise<BucketProbabilitySummary> {
  const { data } = await api.get<BucketProbabilitySummary>('/bucket-probabilities', {
    params: { city, target_date: targetDate },
  })
  return data
}

export async function fetchHourlyConsensus(city: string, targetDate: string): Promise<HourlyConsensusSummary> {
  const { data } = await api.get<HourlyConsensusSummary>('/hourly-consensus', {
    params: { city, target_date: targetDate },
  })
  return data
}

export async function fetchForecastHistory(city: string, targetDate: string, localHour: string): Promise<ForecastRevisionHistory> {
  const { data } = await api.get<ForecastRevisionHistory>('/forecast-history', {
    params: { city, target_date: targetDate, hour: localHour },
  })
  return data
}

export async function fetchTruthDeltaAudit(city = '', limit = 500): Promise<TruthDeltaAuditSummary> {
  const { data } = await api.get<TruthDeltaAuditSummary>('/truth-delta-audit', {
    params: { city, limit },
  })
  return data
}

export async function fetchModelRepriceEvents(city = '', targetDate = '', alphaOnly = false, limit = 200): Promise<ModelRepriceEventSummary> {
  const { data } = await api.get<ModelRepriceEventSummary>('/model-reprice-events', {
    params: { city, target_date: targetDate, alpha_only: alphaOnly, limit },
  })
  return data
}

export async function runProductionAction(options: ProductionActionRequest): Promise<ProductionActionRunResult> {
  const { data } = await api.post<ProductionActionRunResult>('/production-actions/run', {
    action_key: options.actionKey,
    apply: options.apply ?? false,
    operator_confirmed: options.operatorConfirmed ?? false,
    cities: options.cities ?? [],
    days: options.days ?? 1,
    limit: options.limit ?? 20,
    start_date: options.startDate ?? '',
    end_date: options.endDate ?? '',
    skip_signal_scan: options.skipSignalScan ?? true,
    note: options.note ?? '',
    archive_path: options.archivePath ?? '',
  })
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
  startDate?: string
  endDate?: string
  skipSignalScan?: boolean
} = {}): Promise<ProductionRefreshResult> {
  const { data } = await api.post<ProductionRefreshResult>('/production-refresh', {
    cities: options.cities ?? [],
    days: options.days ?? 1,
    limit: options.limit ?? 20,
    start_date: options.startDate ?? '',
    end_date: options.endDate ?? '',
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
