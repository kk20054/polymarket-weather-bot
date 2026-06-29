import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  BarChart3,
  CheckCircle2,
  FlaskConical,
  ListChecks,
  PauseCircle,
  RefreshCw,
  ShieldAlert,
  Wallet,
} from 'lucide-react'
import {
  backfillWeatherHistory,
  fetchDashboard,
  fetchForecastArchiveManifest,
  fetchProductionValidation,
  fetchSettlementContracts,
  fetchTemperatureFit,
  placeLiveOrder,
  resetSimulation,
  runProductionRefresh,
  setAutoSimulation,
  settleTradesApi,
  stopBot,
  updateSignalStatus,
  verifySettlementContract,
  verifySettlementContractsBulk,
} from './api'
import { DataReadinessPanel } from './components/DataReadinessPanel'
import { EquityChart } from './components/EquityChart'
import { ModelDatasetPanel } from './components/ModelDatasetPanel'
import { SignalsTable } from './components/SignalsTable'
import { StatsCards } from './components/StatsCards'
import { TemperatureFitPage } from './components/TemperatureFitPage'
import { TradesTable } from './components/TradesTable'
import { TruthHealthPanel } from './components/TruthHealthPanel'
import { WeatherPanel } from './components/WeatherPanel'
import type { AutoSimulationStatus, BotStats, DataReadiness, ProductionValidationReport } from './types'

type TradeMode = 'paper' | 'live'

const EMPTY_STATS: BotStats = {
  is_running: false,
  last_run: null,
  total_trades: 0,
  open_trades: 0,
  settled_trades: 0,
  total_pnl: 0,
  bankroll: 40,
  winning_trades: 0,
  win_rate: 0,
  simulation_started_at: null,
}

function money(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${Number(value) >= 0 ? '' : '-'}$${Math.abs(Number(value)).toFixed(2)}`
}

function timeText(value?: string | null) {
  if (!value) return '暂无'
  try {
    return new Date(value).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch {
    return value
  }
}

function dataAge(minutes?: number | null) {
  if (minutes === null || minutes === undefined) return '暂无'
  if (minutes < 60) return `${minutes.toFixed(0)} 分钟前`
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)} 小时前`
  return `${(minutes / 1440).toFixed(1)} 天前`
}

function reasonLabel(reason: string) {
  const map: Record<string, string> = {
    truth_observations_below_min: '高置信 truth 样本不足',
    open_meteo_truth_fallback_present: '仍有 Open-Meteo fallback',
    legacy_truth_unknown: '存在旧版未知 truth',
    settled_sample_missing: '已结算样本不足',
    sample_low: '回放样本不足',
    pnl_negative: '允许组仍亏损',
    roi_negative: 'ROI 为负',
    win_rate_low: '胜率偏低',
    strategy_not_ready: '策略尚未达标',
    resolved_sample_below_30: '已结算样本 < 30',
    allowed_sample_below_20: '允许组样本 < 20',
    allowed_group_pnl_negative: '允许组 PnL 为负',
    allowed_group_roi_negative: '允许组 ROI 为负',
    allowed_win_rate_low: '允许组胜率偏低',
    settlement_rule_not_manually_verified: '结算规则未核验',
    settlement_contracts_missing: '事件级合同缺失',
    timezone_mismatch: '规则时区不一致',
    independent_truth_days_below_min: '独立 truth 日不足',
    all_orderbooks_stale: '盘口快照已过期',
    fresh_clob_depth_missing: 'CLOB 深度缺失',
    fresh_clob_depth_below_min: 'CLOB 深度不足',
    forecast_city_coverage_incomplete: '预测城市覆盖不足',
  }
  return map[reason] ?? reason
}

function cityPageSlug(city: { key: string; station?: string }) {
  return `${city.key}${city.station ? `-${city.station.toLowerCase()}` : ''}`
}

function cityKeyFromParam(value: string | null) {
  if (!value) return ''
  return value.split('-').slice(0, -1).join('-') || value
}

function ReadinessBanner({ stats, readiness }: { stats: BotStats; readiness?: DataReadiness | null }) {
  const ready = Boolean(stats.strategy_live_ready)
  const reasons = stats.strategy_readiness_reasons ?? []
  const phase = readiness?.production_phase

  return (
    <div className={`border px-3 py-2 ${ready ? 'border-green-500/30 bg-green-500/10' : 'border-amber-500/30 bg-amber-500/10'}`}>
      <div className="flex items-center gap-2">
        {ready ? <CheckCircle2 className="h-4 w-4 text-green-300" /> : <ShieldAlert className="h-4 w-4 text-amber-300" />}
        <div className="min-w-0">
          <div className="text-sm font-medium text-neutral-100">
            {phase ? `${phase.label}：${phase.name}` : ready ? '实盘门槛已通过，但仍建议从 $1-$2 canary 开始' : '当前只允许模拟观察，实盘按钮已锁定'}
          </div>
          <div className="truncate text-[10px] text-neutral-500">
            {ready ? '实盘门槛已通过，但仍建议从 $1-$2 canary 开始。' : phase?.operator_action ?? '当前只允许模拟观察，实盘按钮已锁定。'}
          </div>
        </div>
      </div>
      {!ready && (
        <div className="mt-2 flex flex-wrap gap-1">
          {reasons.length ? reasons.slice(0, 8).map(reason => (
            <span key={reason} className="border border-amber-500/20 bg-black/30 px-1.5 py-0.5 text-[10px] text-amber-100">
              {reasonLabel(reason)}
            </span>
          )) : (
            <span className="text-[11px] text-neutral-500">等待更多模拟和结算样本。</span>
          )}
        </div>
      )}
    </div>
  )
}

function ProductionValidationPanel({
  report,
  loading,
}: {
  report?: ProductionValidationReport | null
  loading?: boolean
}) {
  const status = report?.status ?? (loading ? 'loading' : 'missing')
  const readyForCanary = Boolean(report?.live_allowed)
  const score = report ? Math.round(Number(report.score ?? 0) * 100) : 0
  const layers = report?.layers ?? []
  const actions = report?.next_actions ?? []

  return (
    <div className="border border-neutral-800 bg-black p-3">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <ListChecks className="h-4 w-4 shrink-0 text-cyan-300" />
          <div className="min-w-0">
            <div className="text-sm font-medium text-neutral-100">生产验证</div>
            <div className="truncate text-[10px] text-neutral-500">
              {report ? `更新 ${timeText(report.generated_at)}` : '等待后端验证报告'}
            </div>
          </div>
        </div>
        <span className={`shrink-0 border px-2 py-1 text-[10px] ${readyForCanary ? 'border-green-500/30 bg-green-500/10 text-green-200' : 'border-amber-500/30 bg-amber-500/10 text-amber-200'}`}>
          {readyForCanary ? '可 Canary' : status === 'loading' ? '读取中' : '阻塞'}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 text-[10px]">
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-500">评分</div>
          <div className="mt-1 tabular-nums text-neutral-100">{report ? `${score}%` : '--'}</div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-500">层级</div>
          <div className="mt-1 tabular-nums text-neutral-100">
            {report ? `${report.ready_layers}/${report.total_layers}` : '--'}
          </div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-500">实盘</div>
          <div className={`mt-1 ${readyForCanary ? 'text-green-300' : 'text-amber-300'}`}>
            {readyForCanary ? '放行' : '锁定'}
          </div>
        </div>
      </div>

      <div className="mt-3 space-y-1">
        {layers.length ? layers.map(layer => {
          const blockedText = layer.blockers?.[0] ? reasonLabel(layer.blockers[0]) : '已通过'
          return (
            <div key={layer.key} className="flex items-center justify-between gap-2 border border-neutral-800 px-2 py-1.5 text-[10px]">
              <div className="min-w-0">
                <div className="truncate text-neutral-200">{layer.label}</div>
                <div className="truncate text-neutral-500" title={(layer.blockers ?? []).join(', ')}>
                  {blockedText}
                </div>
              </div>
              <span className={`shrink-0 tabular-nums ${layer.ready ? 'text-green-300' : 'text-amber-300'}`}>
                {layer.ready ? 'ready' : 'blocked'}
              </span>
            </div>
          )
        }) : (
          <div className="border border-neutral-800 px-2 py-2 text-[10px] text-neutral-500">暂无生产验证层级。</div>
        )}
      </div>

      <details className="mt-3 text-[10px] text-neutral-500">
        <summary className="cursor-pointer select-none hover:text-neutral-300">下一步动作</summary>
        <div className="mt-2 space-y-1">
          {actions.length ? actions.slice(0, 5).map((action, index) => (
            <div key={`${action.key ?? action.label ?? 'action'}-${index}`} className="border border-neutral-800 px-2 py-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-neutral-300">{action.label ?? action.key ?? '待处理'}</span>
                {typeof action.count === 'number' && <span className="tabular-nums text-neutral-500">{action.count}</span>}
              </div>
              {action.command && <code className="mt-1 block break-all text-[9px] text-neutral-600">{action.command}</code>}
            </div>
          )) : (
            <div className="text-neutral-500">暂无新增动作。</div>
          )}
        </div>
      </details>
    </div>
  )
}

function TradeModeSwitch({
  mode,
  liveAvailable,
  onMode,
}: {
  mode: TradeMode
  liveAvailable: boolean
  onMode: (mode: TradeMode) => void
}) {
  const locked = !liveAvailable

  return (
    <div className="border border-neutral-800 bg-black p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-neutral-100">交易模式</div>
        </div>
        <span
          className={`shrink-0 border px-2 py-1 text-[10px] ${
            mode === 'paper'
              ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200'
              : 'border-blue-500/30 bg-blue-500/10 text-blue-200'
          }`}
          aria-live="polite"
        >
          {mode === 'paper' ? '模拟盘' : '实盘检查'}
        </span>
      </div>

      <div className="grid grid-cols-2 border border-neutral-800" role="group" aria-label="选择交易模式">
        <button
          type="button"
          onClick={() => onMode('paper')}
          aria-pressed={mode === 'paper'}
          className={`inline-flex min-h-10 items-center justify-center gap-2 px-3 py-2 text-xs ${
            mode === 'paper' ? 'bg-cyan-500/15 text-cyan-200' : 'text-neutral-500 hover:bg-neutral-900'
          }`}
        >
          <FlaskConical className="h-4 w-4" />
          模拟
        </button>
        <button
          type="button"
          onClick={() => liveAvailable && onMode('live')}
          disabled={!liveAvailable}
          aria-pressed={mode === 'live'}
          aria-describedby={locked ? 'live-mode-unavailable' : undefined}
          className={`inline-flex min-h-10 items-center justify-center gap-2 border-l border-neutral-800 px-3 py-2 text-xs ${
            mode === 'live'
              ? 'bg-blue-500/15 text-blue-200'
              : liveAvailable
                ? 'text-neutral-400 hover:bg-neutral-900'
                : 'cursor-not-allowed text-neutral-700'
          }`}
        >
          <Wallet className="h-4 w-4" />
          实盘
        </button>
      </div>

      <div className="mt-2 flex items-center justify-between gap-2 text-[10px]">
        <span className={`border px-1.5 py-0.5 ${locked ? 'border-amber-500/30 text-amber-300' : 'border-green-500/30 text-green-300'}`}>
          {locked ? '实盘锁定' : '可用 canary'}
        </span>
        <details className="min-w-0 text-right text-neutral-500">
          <summary className="cursor-pointer select-none hover:text-neutral-300">执行说明</summary>
          <p id="live-mode-unavailable" className="mt-1 max-w-[260px] text-left leading-relaxed text-neutral-500">
            {locked
              ? '策略闸门或实盘配置未通过，买入只会写入模拟账户。'
              : '实盘会先执行 canary 风控、盘口、tick 和 orderMinSize 检查。'}
          </p>
        </details>
      </div>
    </div>
  )
}

function SimulationCard({
  stats,
  value,
  clearMarks,
  autoSimulation,
  onValue,
  onClearMarks,
  onReset,
  onSettle,
  onToggleAuto,
  resetting,
  settling,
  autoPending,
}: {
  stats: BotStats
  value: string
  clearMarks: boolean
  autoSimulation: AutoSimulationStatus
  onValue: (value: string) => void
  onClearMarks: (value: boolean) => void
  onReset: () => void
  onSettle: () => void
  onToggleAuto: () => void
  resetting: boolean
  settling: boolean
  autoPending: boolean
}) {
  const autoRunning = autoSimulation.enabled
  const lastResult = autoSimulation.last_result

  return (
    <div className="border border-neutral-800 bg-black p-3">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Wallet className="h-4 w-4 text-cyan-300" />
          <div>
            <div className="text-sm font-medium text-neutral-100">模拟账户</div>
          </div>
        </div>
        <span className={`shrink-0 border px-1.5 py-0.5 text-[9px] ${
          autoRunning ? 'border-green-500/30 text-green-300' : 'border-neutral-700 text-neutral-500'
        }`}>
          {autoRunning ? '运行中' : '已停止'}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">权益</div>
          <div className="tabular-nums text-lg text-neutral-100">{money(stats.bankroll)}</div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">未实现</div>
          <div className={`tabular-nums text-lg ${(stats.unrealized_pnl ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'}`}>
            {money(stats.unrealized_pnl ?? 0)}
          </div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">现金 / 占用</div>
          <div className="tabular-nums text-neutral-200">{money(stats.cash_balance ?? stats.bankroll)} / {money(stats.reserved_capital ?? 0)}</div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">持仓 / 结算</div>
          <div className="tabular-nums text-neutral-200">{stats.open_trades ?? 0} / {stats.settled_trades ?? 0}</div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-[1fr_auto] gap-2">
        <label className="grid min-w-0 grid-cols-[auto_1fr] items-center border border-neutral-800 bg-neutral-950/70 px-2 py-1 text-[10px] text-neutral-500">
          本金
          <input
            type="number"
            min="0"
            step="1"
            value={value}
            onChange={event => onValue(event.target.value)}
            className="min-w-0 border-0 bg-transparent p-0 text-right text-xs tabular-nums text-neutral-100 focus:outline-none"
            aria-label="设置模拟本金"
          />
        </label>
        <button
          onClick={onReset}
          disabled={resetting}
          className="border border-cyan-500/30 px-2 py-1 text-[11px] text-cyan-300 hover:bg-cyan-500/10 disabled:opacity-40"
        >
          应用
        </button>
      </div>

      <label className="mt-2 flex items-center gap-2 text-[10px] text-neutral-500">
        <input
          type="checkbox"
          checked={clearMarks}
          onChange={event => onClearMarks(event.target.checked)}
          className="h-3 w-3 p-0"
        />
        重置时清除模拟标记
      </label>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <button
          onClick={onToggleAuto}
          disabled={autoPending}
          className={`border px-2 py-1.5 disabled:opacity-40 ${
            autoRunning
              ? 'border-red-500/30 bg-red-500/5 text-red-300 hover:bg-red-500/10'
              : 'border-green-500/30 bg-green-500/10 text-green-300 hover:border-green-500/60'
          }`}
        >
          {autoPending ? '更新中...' : autoRunning ? '停止模拟' : '一键模拟'}
        </button>
        <button
          onClick={onSettle}
          disabled={settling || (stats.open_trades ?? 0) === 0}
          className="border border-amber-500/30 px-2 py-1.5 text-amber-300 hover:bg-amber-500/10 disabled:opacity-40"
        >
          检查结算
        </button>
      </div>

      {(lastResult || autoSimulation.last_error) && (
        <div className="mt-3 border border-neutral-800 p-2 text-[10px] leading-relaxed text-neutral-400">
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="text-neutral-200">最近检查</span>
            <span className="tabular-nums text-neutral-500">{timeText(autoSimulation.last_run)}</span>
          </div>
          {lastResult && (
            <div>
              买入 {lastResult.count}，跳过 {lastResult.skipped}，花费 {money(lastResult.spent)}，剩余 {money(lastResult.remaining)}
              {lastResult.orderbooks_refreshed !== undefined && (
                <span title={`盘口刷新失败 ${lastResult.orderbook_refresh_failed ?? 0} 个`}>
                  {' '}· 盘口 {lastResult.orderbooks_refreshed}
                </span>
              )}
            </div>
          )}
          {autoSimulation.last_error && <div className="text-red-300">{autoSimulation.last_error}</div>}
        </div>
      )}

      <details className="mt-3 text-[10px] text-neutral-600">
        <summary className="cursor-pointer select-none hover:text-neutral-300">估值口径</summary>
        <p className="mt-1 leading-relaxed">
          新买入会按卖一成交、按买一估值，spread 先进入未实现亏损；这只是执行成本，不代表最终结算已经错。
        </p>
      </details>
    </div>
  )
}

function App() {
  const queryClient = useQueryClient()
  const [view, setView] = useState<'dashboard' | 'temperature-fit'>('dashboard')
  const [tradeMode, setTradeMode] = useState<TradeMode>('paper')
  const [activityView, setActivityView] = useState<'signals' | 'trades'>('signals')
  const [selectedCity, setSelectedCity] = useState(() => {
    if (typeof window === 'undefined') return ''
    return cityKeyFromParam(new URLSearchParams(window.location.search).get('city'))
  })
  const [selectedDate, setSelectedDate] = useState(() => {
    if (typeof window === 'undefined') return ''
    return new URLSearchParams(window.location.search).get('date') ?? ''
  })
  const [simBalance, setSimBalance] = useState('40')
  const [clearMarks, setClearMarks] = useState(false)
  const [contractStatus, setContractStatus] = useState('mature-auto')
  const [citySearch, setCitySearch] = useState('')
  const balanceInitRef = useRef(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['dashboard'],
    queryFn: fetchDashboard,
    refetchInterval: 10000,
    retry: 1,
  })

  const temperatureFitQuery = useQuery({
    queryKey: ['temperature-fit'],
    queryFn: fetchTemperatureFit,
    enabled: view === 'temperature-fit',
    refetchInterval: view === 'temperature-fit' ? 30000 : false,
  })

  const contractsQuery = useQuery({
    queryKey: ['settlement-contracts', contractStatus],
    queryFn: () => fetchSettlementContracts(contractStatus, 12),
    refetchInterval: 120000,
  })

  const forecastArchiveManifestQuery = useQuery({
    queryKey: ['forecast-archive-manifest'],
    queryFn: fetchForecastArchiveManifest,
    refetchInterval: 120000,
  })

  const productionValidationQuery = useQuery({
    queryKey: ['production-validation'],
    queryFn: fetchProductionValidation,
    refetchInterval: 120000,
  })

  const stopMutation = useMutation({
    mutationFn: stopBot,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  })

  const signalStatusMutation = useMutation({
    mutationFn: ({ signalId, status, amount }: { signalId: number; status: string; amount?: number }) =>
      updateSignalStatus(signalId, status, amount),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const liveOrderMutation = useMutation({
    mutationFn: ({ signalId, amount }: { signalId: number; amount?: number }) => placeLiveOrder(signalId, amount),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const autoSimulationMutation = useMutation({
    mutationFn: (enabled: boolean) => setAutoSimulation(enabled, 300),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const verifyContractMutation = useMutation({
    mutationFn: ({ contractId, note }: { contractId: string; note: string }) =>
      verifySettlementContract(contractId, true, note || 'dashboard manual review'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settlement-contracts'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const bulkVerifyContractMutation = useMutation({
    mutationFn: ({ contractIds, note }: { contractIds: string[]; note: string }) =>
      verifySettlementContractsBulk(contractIds, true, true, note || 'dashboard visible batch review'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settlement-contracts'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const productionRefreshMutation = useMutation({
    mutationFn: () => runProductionRefresh({ days: 1, limit: 20, skipSignalScan: true }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settlement-contracts'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const resetSimulationMutation = useMutation({
    mutationFn: ({ balance, clear }: { balance: number; clear: boolean }) => resetSimulation(balance, clear),
    onSuccess: result => {
      setSimBalance(String(result.balance))
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const settleMutation = useMutation({
    mutationFn: settleTradesApi,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const historyBackfillMutation = useMutation({
    mutationFn: () => backfillWeatherHistory(30),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const stats = data?.stats ?? EMPTY_STATS
  const signals = data?.weather_signals ?? []
  const forecasts = data?.weather_forecasts ?? []
  const citySeries = data?.weather_city_series ?? []
  const events = data?.events ?? []
  const trades = data?.recent_trades ?? []
  const equityCurve = data?.equity_curve ?? []
  const truthHealth = data?.truth_health ?? null
  const dataReadiness = data?.data_readiness ?? null
  const productionRefresh = productionRefreshMutation.data ?? data?.production_refresh ?? null
  const productionValidation = productionValidationQuery.data ?? null
  const modelDatasetAudit = data?.model_dataset_audit ?? null
  const forecastArchiveManifest = forecastArchiveManifestQuery.data ?? null
  const actionable = signals.filter(signal => signal.actionable).length
  const liveAvailable = Boolean(stats.strategy_live_ready && data?.v3?.config?.live_trading)
  const needsManualRefresh = data?._meta?.reason === 'manual_refresh_required'
  const autoSimulation = stats.auto_simulation ?? {
    enabled: false,
    interval_seconds: 300,
    last_run: null,
    last_result: null,
    last_error: null,
  }
  const cityOptions = useMemo(() => {
    const rows = new Map<string, {
      key: string
      name: string
      station?: string
      unit: string
      latest?: number | null
      latestMetar?: number | null
      forecastCount: number
      historyCount: number
      humidityStatus?: string
      signals: number
      actionable: number
    }>()

    for (const row of citySeries) {
      rows.set(row.city_key, {
        key: row.city_key,
        name: row.city_name,
        station: row.station_id,
        unit: row.unit || 'F',
        latest: row.latest_best ?? null,
        latestMetar: row.latest_metar ?? null,
        forecastCount: row.forecast_count ?? row.forecast_points?.length ?? row.points?.length ?? 0,
        historyCount: row.history_count ?? row.history_points?.length ?? 0,
        humidityStatus: row.humidity_status,
        signals: 0,
        actionable: 0,
      })
    }

    for (const row of forecasts) {
      if (!rows.has(row.city_key)) {
        rows.set(row.city_key, {
          key: row.city_key,
          name: row.city_name,
          unit: 'F',
          latest: row.mean_high,
          latestMetar: null,
          forecastCount: 1,
          historyCount: 0,
          humidityStatus: 'not_collected',
          signals: 0,
          actionable: 0,
        })
      }
    }

    for (const signal of signals) {
      const row = rows.get(signal.city_key) ?? {
        key: signal.city_key,
        name: signal.city_name,
        unit: 'F',
        latest: null,
        latestMetar: null,
        forecastCount: 0,
        historyCount: 0,
        humidityStatus: 'not_collected',
        signals: 0,
        actionable: 0,
      }
      row.signals += 1
      if (signal.actionable) row.actionable += 1
      rows.set(signal.city_key, row)
    }

    return [...rows.values()].sort((a, b) => {
      if (b.actionable !== a.actionable) return b.actionable - a.actionable
      if (b.signals !== a.signals) return b.signals - a.signals
      return a.name.localeCompare(b.name)
    })
  }, [citySeries, forecasts, signals])

  useEffect(() => {
    if (!balanceInitRef.current && data?.stats?.bankroll !== undefined) {
      setSimBalance(String(Math.round(data.stats.bankroll)))
      balanceInitRef.current = true
    }
  }, [data?.stats?.bankroll])

  useEffect(() => {
    if (!liveAvailable && tradeMode === 'live') {
      setTradeMode('paper')
    }
  }, [liveAvailable, tradeMode])

  useEffect(() => {
    if (!selectedCity && cityOptions[0]?.key) {
      setSelectedCity(cityOptions[0].key)
    } else if (selectedCity && cityOptions.length > 0 && !cityOptions.some(city => city.key === selectedCity)) {
      setSelectedCity(cityOptions[0].key)
    }
  }, [cityOptions, selectedCity])

  const selectedCityMeta = cityOptions.find(city => city.key === selectedCity)
  const recommendedCity = cityOptions.find(city => city.actionable > 0)
  const citySummaryCard = recommendedCity ?? selectedCityMeta ?? cityOptions[0]
  const actionableCityCount = cityOptions.filter(city => city.actionable > 0).length
  const selectedEvidenceCount = (selectedCityMeta?.forecastCount ?? 0)
    + (selectedCityMeta?.historyCount ?? 0)
    + (selectedCityMeta?.latestMetar !== null && selectedCityMeta?.latestMetar !== undefined ? 1 : 0)
    + (selectedCityMeta?.humidityStatus === 'available' ? 1 : 0)
  const selectedEvidenceReady = selectedEvidenceCount > 0
  const filteredCityOptions = cityOptions.filter(city => {
    const query = citySearch.trim().toLowerCase()
    if (!query) return true
    return `${city.name} ${city.station ?? ''} ${city.key}`.toLowerCase().includes(query)
  })
  const cityHref = (city: { key: string; station?: string }) => {
    const params = new URLSearchParams()
    params.set('city', cityPageSlug(city))
    if (selectedDate) params.set('date', selectedDate)
    return `?${params.toString()}`
  }

  useEffect(() => {
    if (!selectedCityMeta || typeof window === 'undefined') return
    const params = new URLSearchParams(window.location.search)
    const nextCity = cityPageSlug(selectedCityMeta)
    if (params.get('city') === nextCity) return
    params.set('city', nextCity)
    const nextUrl = `${window.location.pathname}?${params.toString()}`
    window.history.replaceState(null, '', nextUrl)
  }, [selectedCityMeta])

  useEffect(() => {
    if (!selectedDate || typeof window === 'undefined') return
    const params = new URLSearchParams(window.location.search)
    if (params.get('date') === selectedDate) return
    params.set('date', selectedDate)
    const nextUrl = `${window.location.pathname}?${params.toString()}`
    window.history.replaceState(null, '', nextUrl)
  }, [selectedDate])

  if (view === 'temperature-fit') {
    return (
      <TemperatureFitPage
        data={temperatureFitQuery.data}
        loading={temperatureFitQuery.isLoading}
        onBack={() => setView('dashboard')}
      />
    )
  }

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-black text-neutral-300">
        <div className="text-center">
          <div className="mx-auto mb-4 h-9 w-9 animate-spin rounded-full border-2 border-neutral-800 border-t-green-400" />
          <div className="text-xs text-neutral-500">正在连接本地看板 API...</div>
        </div>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="flex h-screen items-center justify-center bg-black text-neutral-300">
        <div className="max-w-md border border-red-500/30 bg-red-500/5 p-5 text-center">
          <div className="mb-2 text-sm text-red-300">后端未连接</div>
          <p className="mb-4 text-[12px] leading-relaxed text-neutral-500">
            请确认 dashboard_server 正在运行于 http://127.0.0.1:8765，然后刷新页面。
          </p>
          <button onClick={() => refetch()} className="border border-neutral-700 px-3 py-1.5 text-xs text-neutral-200">
            重试
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen flex-col bg-black text-neutral-200 xl:h-screen xl:overflow-hidden">
      <header className="flex shrink-0 flex-wrap items-start gap-2 border-b border-neutral-800 px-3 py-2">
        <div className="min-w-0 flex-1 basis-[130px]">
          <h1 className="text-sm font-semibold tracking-wide text-neutral-100">WeatherBot</h1>
          <div className="text-[11px] text-neutral-600">城市最高温交易工作台</div>
        </div>
        <div className="order-last flex min-w-0 basis-full flex-nowrap items-center gap-1.5 overflow-x-auto text-[10px] xl:overflow-visible">
          <span className="shrink-0 border border-neutral-800 px-2 py-1 text-neutral-400">数据 {dataAge(stats.data_age_minutes)}</span>
          <span className={`shrink-0 border px-2 py-1 ${stats.is_running ? 'border-green-500/30 text-green-300' : 'border-neutral-800 text-neutral-500'}`}>
            {stats.is_running ? '旧扫描运行中' : '手动刷新'}
          </span>
          <span className={`shrink-0 border px-2 py-1 ${autoSimulation.enabled ? 'border-cyan-500/30 text-cyan-300' : 'border-neutral-800 text-neutral-500'}`}>
            {autoSimulation.enabled ? '一键模拟运行中' : '一键模拟关闭'}
          </span>
          <span className={`shrink-0 border px-2 py-1 ${liveAvailable ? 'border-green-500/30 text-green-300' : 'border-amber-500/30 text-amber-300'}`}>
            {liveAvailable ? '实盘可用' : '实盘锁定'}
          </span>
        </div>
        <button
          onClick={() => productionRefreshMutation.mutate()}
          disabled={productionRefreshMutation.isPending}
          className="inline-flex items-center gap-1 whitespace-nowrap border border-green-500/30 px-2 py-1.5 text-[11px] text-green-300 hover:bg-green-500/10 disabled:opacity-40"
          title="受控刷新：同步合约、预测快照和 CLOB 盘口；默认不启动旧版无限信号扫描。"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${productionRefreshMutation.isPending ? 'animate-spin' : ''}`} />
          {productionRefreshMutation.isPending ? '抓取中' : '手动抓取'}
        </button>
        {stats.is_running && (
          <button
            onClick={() => stopMutation.mutate()}
            disabled={stopMutation.isPending}
            className="inline-flex items-center gap-1 whitespace-nowrap border border-red-500/30 px-2 py-1.5 text-[11px] text-red-300 hover:bg-red-500/10 disabled:opacity-40"
            title="停止旧版 weatherbet.py 循环扫描。v3 数据刷新不依赖这个进程。"
          >
            <PauseCircle className="h-3.5 w-3.5" />
            停止旧扫描
          </button>
        )}
        <button
          onClick={() => refetch()}
          className="inline-flex items-center gap-1 whitespace-nowrap border border-neutral-700 px-2 py-1.5 text-[11px] text-neutral-300 hover:bg-neutral-900"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          刷新
        </button>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto xl:grid-cols-[260px_minmax(560px,1fr)_340px] xl:overflow-hidden">
        <aside className="order-2 border-b border-neutral-800 bg-neutral-950/40 xl:order-1 xl:min-h-0 xl:overflow-y-auto xl:border-b-0 xl:border-r">
          {citySummaryCard && (
            <a
              href={cityHref(citySummaryCard)}
              onClick={event => {
                event.preventDefault()
                setSelectedCity(citySummaryCard.key)
              }}
              className={`m-3 block border p-3 text-left transition ${
                recommendedCity
                  ? 'border-emerald-500/30 bg-emerald-500/10 hover:bg-emerald-500/15'
                  : 'border-neutral-800 bg-black/35 hover:border-neutral-700'
              }`}
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <div className={`text-xs font-medium ${recommendedCity ? 'text-emerald-100' : 'text-neutral-300'}`}>
                  {recommendedCity ? '推荐关注' : needsManualRefresh ? '等待抓取' : '当前城市'}
                </div>
                <div className={`text-[10px] ${recommendedCity ? 'text-emerald-300' : 'text-neutral-500'}`}>
                  {citySummaryCard.actionable}/{citySummaryCard.signals} 信号
                </div>
              </div>
              <div className="truncate text-sm text-neutral-100">{citySummaryCard.name}</div>
              <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-neutral-500">
                <span>{citySummaryCard.station || 'station 未映射'}</span>
                <span className="tabular-nums text-neutral-200">
                  {citySummaryCard.latest === null || citySummaryCard.latest === undefined ? '--' : `${Number(citySummaryCard.latest).toFixed(1)}°${citySummaryCard.unit}`}
                </span>
              </div>
            </a>
          )}

          <div className="border-t border-neutral-800 p-3">
            <div className="mb-2 flex items-center justify-between">
              <div>
                <div className="text-sm font-medium text-neutral-100">城市索引</div>
                <div className="text-[10px] text-neutral-600">
                  {actionableCityCount > 0 ? `${actionableCityCount} 个城市有可执行信号` : '无信号时按城市浏览证据'}
                </div>
              </div>
              <span className="border border-neutral-800 px-1.5 py-0.5 text-[10px] text-neutral-500">{cityOptions.length}</span>
            </div>
            <input
              value={citySearch}
              onChange={event => setCitySearch(event.target.value)}
              placeholder="搜索城市或机场"
              className="mb-2 w-full border-neutral-800 bg-black px-2 py-1.5 text-[11px]"
              aria-label="搜索城市或机场"
            />
            <div className="space-y-1">
              {cityOptions.length === 0 && (
                <div className="border border-neutral-800 bg-black/40 p-3 text-[11px] leading-relaxed text-neutral-500">
                  暂无城市快照。点击顶部“手动抓取”后，这里会按城市列出预报、站点和信号数量。
                </div>
              )}
              {cityOptions.length > 0 && filteredCityOptions.length === 0 && (
                <div className="border border-neutral-800 bg-black/40 p-3 text-[11px] text-neutral-500">
                  没有匹配的城市。
                </div>
              )}
              {filteredCityOptions.map(city => (
                <a
                  key={city.key}
                  href={cityHref(city)}
                  onClick={event => {
                    event.preventDefault()
                    setSelectedCity(city.key)
                  }}
                  className={`block min-h-[54px] w-full border px-2 py-2 text-left transition ${
                    selectedCity === city.key
                      ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-100'
                      : 'border-neutral-800 bg-black/40 text-neutral-300 hover:border-neutral-700'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium leading-tight">{city.name}</div>
                      <div className="mt-1 truncate text-[10px] leading-tight text-neutral-600">{city.station || 'station 未映射'}</div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="tabular-nums text-[11px] leading-tight text-neutral-200">
                        {city.latest === null || city.latest === undefined ? '--' : `${Number(city.latest).toFixed(1)}°${city.unit}`}
                      </div>
                      <div className={`mt-1 text-[10px] leading-tight ${city.actionable > 0 ? 'text-green-300' : 'text-neutral-600'}`}>
                        {city.actionable}/{city.signals}
                      </div>
                    </div>
                  </div>
                  <div className="mt-2 flex items-center gap-1 text-[9px]">
                    <span
                      className={`border px-1 py-0.5 ${
                        city.forecastCount > 0
                          ? 'border-cyan-500/25 bg-cyan-500/5 text-cyan-200'
                          : 'border-neutral-800 text-neutral-600'
                      }`}
                      title={`预报快照 ${city.forecastCount}`}
                    >
                      F {city.forecastCount}
                    </span>
                    <span
                      className={`border px-1 py-0.5 ${
                        city.latestMetar !== null && city.latestMetar !== undefined
                          ? 'border-amber-500/25 bg-amber-500/5 text-amber-200'
                          : 'border-neutral-800 text-neutral-600'
                      }`}
                      title={city.latestMetar !== null && city.latestMetar !== undefined ? `METAR ${Number(city.latestMetar).toFixed(1)}°${city.unit}` : '暂无 METAR'}
                    >
                      M {city.latestMetar !== null && city.latestMetar !== undefined ? Number(city.latestMetar).toFixed(0) : '--'}
                    </span>
                    <span
                      className={`border px-1 py-0.5 ${
                        city.historyCount > 0
                          ? 'border-emerald-500/25 bg-emerald-500/5 text-emerald-200'
                          : 'border-neutral-800 text-neutral-600'
                      }`}
                      title={`历史观测 ${city.historyCount}`}
                    >
                      H {city.historyCount}
                    </span>
                    {city.humidityStatus === 'available' && (
                      <span className="border border-blue-500/20 bg-blue-500/5 px-1 py-0.5 text-blue-200" title="湿度数据可用">
                        RH
                      </span>
                    )}
                  </div>
                </a>
              ))}
            </div>
          </div>

        </aside>

        <section className="order-1 min-h-[720px] overflow-hidden xl:order-2 xl:flex xl:min-h-0 xl:flex-col">
          <div className="z-20 shrink-0 flex flex-wrap items-center justify-between gap-2 border-b border-neutral-800 bg-black/95 px-3 py-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-neutral-100">
                {selectedCityMeta?.name ?? '城市天气证据'} · {selectedCityMeta?.station || 'station 未映射'}
              </div>
              <div className="mt-0.5 flex flex-wrap gap-1.5 text-[10px]">
                <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">{selectedDate || '日期待定'}</span>
                <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">
                  {selectedCityMeta?.latest === null || selectedCityMeta?.latest === undefined ? '预测 --' : `预测 ${Number(selectedCityMeta.latest).toFixed(1)}°${selectedCityMeta.unit}`}
                </span>
                <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">
                  {selectedCityMeta?.latestMetar === null || selectedCityMeta?.latestMetar === undefined ? 'METAR --' : `METAR ${Number(selectedCityMeta.latestMetar).toFixed(1)}°${selectedCityMeta.unit}`}
                </span>
                <span className={`border px-1.5 py-0.5 ${selectedEvidenceReady ? 'border-cyan-500/30 text-cyan-200' : 'border-neutral-800 text-neutral-500'}`}>
                  证据 F{selectedCityMeta?.forecastCount ?? 0} / H{selectedCityMeta?.historyCount ?? 0}
                </span>
                <span className={`border px-1.5 py-0.5 ${actionable > 0 ? 'border-green-500/30 text-green-300' : 'border-neutral-800 text-neutral-500'}`}>
                  信号 {actionable}/{signals.length}
                </span>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5 text-[10px]">
              {needsManualRefresh && (
                <span className="border border-cyan-500/30 bg-cyan-500/10 px-1.5 py-0.5 text-cyan-200">
                  等待手动抓取
                </span>
              )}
              <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-400">数据 {dataAge(stats.data_age_minutes)}</span>
            </div>
          </div>

          <div className="min-h-[720px] overflow-y-auto xl:min-h-0 xl:flex-1">
            <WeatherPanel
              forecasts={forecasts}
              signals={signals}
              citySeries={citySeries}
              events={events}
              productionRefresh={productionRefresh}
              selectedCity={selectedCity}
              onSelectedCity={setSelectedCity}
              selectedDate={selectedDate}
              onSelectedDate={setSelectedDate}
              onBackfillHistory={() => historyBackfillMutation.mutate()}
              backfilling={historyBackfillMutation.isPending}
              backfillResult={historyBackfillMutation.data}
            />
          </div>
        </section>

        <aside className="order-3 flex h-[900px] min-h-0 flex-col border-t border-neutral-800 xl:h-auto xl:border-l xl:border-t-0">
          <div className="space-y-3 border-b border-neutral-800 p-3">
            <TradeModeSwitch mode={tradeMode} liveAvailable={liveAvailable} onMode={setTradeMode} />
            <SimulationCard
              stats={stats}
              value={simBalance}
              clearMarks={clearMarks}
              autoSimulation={autoSimulation}
              onValue={setSimBalance}
              onClearMarks={setClearMarks}
              onReset={() => {
                const parsed = Number(simBalance)
                if (Number.isFinite(parsed) && parsed >= 0) {
                  resetSimulationMutation.mutate({ balance: parsed, clear: clearMarks })
                }
              }}
              onSettle={() => settleMutation.mutate()}
              onToggleAuto={() => autoSimulationMutation.mutate(!autoSimulation.enabled)}
              resetting={resetSimulationMutation.isPending}
              settling={settleMutation.isPending}
              autoPending={autoSimulationMutation.isPending}
            />
          </div>
          <div className="grid grid-cols-2 border-b border-neutral-800" role="tablist" aria-label="行动与交易记录">
            <button
              type="button"
              role="tab"
              aria-selected={activityView === 'signals'}
              onClick={() => setActivityView('signals')}
              className={`min-h-11 border-r border-neutral-800 px-3 text-left ${
                activityView === 'signals' ? 'bg-cyan-500/10 text-cyan-200' : 'text-neutral-500 hover:bg-neutral-950'
              }`}
            >
              <div className="text-xs">信号队列</div>
              <div className="text-[9px]">{signals.length} 条</div>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activityView === 'trades'}
              onClick={() => setActivityView('trades')}
              className={`min-h-11 px-3 text-left ${
                activityView === 'trades' ? 'bg-amber-500/10 text-amber-200' : 'text-neutral-500 hover:bg-neutral-950'
              }`}
            >
              <div className="text-xs">模拟 / 交易记录</div>
              <div className="text-[9px]">{trades.length} 条</div>
            </button>
          </div>

          {activityView === 'signals' ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="border-b border-neutral-800 px-3 py-2">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-xs text-neutral-200">信号行动</div>
                    <div className="text-[10px] tabular-nums text-neutral-500">{actionable} 可执行 / {signals.length} 总信号</div>
                  </div>
                  <details className="text-right text-[10px] text-neutral-500">
                    <summary className="cursor-pointer select-none hover:text-neutral-300">详情</summary>
                    <div className="mt-1 max-w-[240px] text-left leading-relaxed">
                      展开单条信号可看盘口、风控原因和 Polymarket 链接。
                    </div>
                  </details>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <SignalsTable
                  signals={[]}
                  weatherSignals={signals}
                  onSimulateTrade={() => undefined}
                  isSimulating={signalStatusMutation.isPending}
                  onSignalStatus={(signalId, status, amount) => signalStatusMutation.mutate({ signalId, status, amount })}
                  onLiveOrder={(signalId, amount) => liveOrderMutation.mutate({ signalId, amount })}
                  liveModeAvailable={liveAvailable}
                  tradeMode={tradeMode}
                />
              </div>
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="border-b border-neutral-800 px-3 py-2">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-xs text-neutral-200">模拟 / 交易</div>
                    <div className="text-[10px] tabular-nums text-neutral-500">{stats.open_trades ?? 0} 持仓 / {stats.settled_trades ?? 0} 结算</div>
                  </div>
                  <details className="text-right text-[10px] text-neutral-500">
                    <summary className="cursor-pointer select-none hover:text-neutral-300">口径</summary>
                    <div className="mt-1 max-w-[240px] text-left leading-relaxed">
                      未结算持仓按当前 bid 估值，会包含买卖价差造成的即时浮亏。
                    </div>
                  </details>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <TradesTable trades={trades} />
              </div>
            </div>
          )}

          <details className="shrink-0 border-t border-neutral-800 bg-black">
            <summary className="cursor-pointer select-none px-3 py-2 text-xs text-neutral-300 hover:bg-neutral-950">
              系统、复盘与风控
            </summary>
            <div className="max-h-[48vh] space-y-3 overflow-y-auto border-t border-neutral-800 p-3">
              <div className="grid grid-cols-2 gap-2 text-[11px]">
                <StatusTile label="扫描器" value={stats.is_running ? '运行中' : '已停止'} active={stats.is_running} icon={<Activity className="h-3.5 w-3.5" />} />
                <StatusTile label="数据年龄" value={dataAge(stats.data_age_minutes)} />
                <StatusTile label="当前信号" value={`${actionable} / ${signals.length}`} tone={actionable > 0 ? 'green' : 'neutral'} />
                <StatusTile label="实盘状态" value={liveAvailable ? '可用' : '锁定'} tone={liveAvailable ? 'green' : 'amber'} />
              </div>

              <ProductionValidationPanel
                report={productionValidation}
                loading={productionValidationQuery.isLoading}
              />

              <div className="overflow-x-auto border border-neutral-800 bg-black p-2">
                <StatsCards stats={stats} />
              </div>

              <ReadinessBanner stats={stats} readiness={dataReadiness} />

              <div className="border border-neutral-800 bg-black">
                <div className="flex items-center justify-between border-b border-neutral-800 px-3 py-2">
                  <div className="text-sm text-neutral-100">资金曲线</div>
                  <div className={`tabular-nums text-[11px] ${(stats.total_pnl ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'}`}>
                    {money(stats.total_pnl)}
                  </div>
                </div>
                <div className="h-[150px] p-2">
                  <EquityChart data={equityCurve} initialBankroll={stats.bankroll - stats.total_pnl} />
                </div>
              </div>

              <div className="border border-neutral-800 bg-black p-3">
                <div className="mb-2 flex items-center gap-2">
                  <BarChart3 className="h-4 w-4 text-cyan-300" />
                  <div className="text-sm text-neutral-100">温度拟合与数据审计</div>
                </div>
                <button
                  onClick={() => setView('temperature-fit')}
                  className="w-full border border-cyan-500/30 px-2 py-1.5 text-cyan-300 hover:bg-cyan-500/10"
                >
                  打开温度拟合
                </button>
              </div>

              <DataReadinessPanel
                readiness={dataReadiness}
                contracts={contractsQuery.data}
                contractStatus={contractStatus}
                onContractStatus={setContractStatus}
                verifyingContractId={verifyContractMutation.variables?.contractId}
                bulkVerifying={bulkVerifyContractMutation.isPending}
                productionRefresh={productionRefresh}
                productionRefreshing={productionRefreshMutation.isPending}
                onProductionRefresh={() => productionRefreshMutation.mutate()}
                onVerifyContract={(contractId, note) => verifyContractMutation.mutate({ contractId, note })}
                onVerifyVisibleContracts={(contractIds, note) => bulkVerifyContractMutation.mutate({ contractIds, note })}
              />

              <ModelDatasetPanel audit={modelDatasetAudit} archiveManifest={forecastArchiveManifest} />

              <div className="border border-neutral-800 bg-black p-3">
                <div className="mb-2 text-sm text-neutral-100">结算源健康</div>
                <TruthHealthPanel truth={truthHealth} />
              </div>
            </div>
          </details>
        </aside>
      </main>
    </div>
  )
}

function StatusTile({
  label,
  value,
  active = false,
  tone = 'neutral',
  icon,
}: {
  label: string
  value: string
  active?: boolean
  tone?: 'neutral' | 'green' | 'amber'
  icon?: ReactNode
}) {
  const valueClass = tone === 'green' || active ? 'text-green-300' : tone === 'amber' ? 'text-amber-300' : 'text-neutral-200'
  return (
    <div className={`border p-2 ${active ? 'border-green-500/30 bg-green-500/10' : 'border-neutral-800'}`}>
      <div className="mb-1 flex items-center gap-1 text-neutral-500">
        {icon}
        {label}
      </div>
      <div className={valueClass}>{value}</div>
    </div>
  )
}

export default App
