import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  BarChart3,
  CheckCircle2,
  FlaskConical,
  PauseCircle,
  PlayCircle,
  RefreshCw,
  ShieldAlert,
  Wallet,
} from 'lucide-react'
import {
  backfillWeatherHistory,
  bulkSimulateSignals,
  fetchDashboard,
  fetchTemperatureFit,
  placeLiveOrder,
  resetSimulation,
  settleTradesApi,
  startBot,
  stopBot,
  updateSignalStatus,
} from './api'
import { EquityChart } from './components/EquityChart'
import { SignalsTable } from './components/SignalsTable'
import { StatsCards } from './components/StatsCards'
import { TemperatureFitPage } from './components/TemperatureFitPage'
import { TradesTable } from './components/TradesTable'
import { TruthHealthPanel } from './components/TruthHealthPanel'
import { WeatherPanel } from './components/WeatherPanel'
import type { BotStats, BulkSimulateResult } from './types'

const GlobeView = lazy(() => import('./components/GlobeView').then(module => ({ default: module.GlobeView })))
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
  }
  return map[reason] ?? reason
}

function bulkReasonLabel(reason: string): string {
  if (reason.startsWith('risk_gate:')) return `风控拦截：${reasonLabel(reason.slice('risk_gate:'.length))}`
  if (reason.startsWith('paper_rejected:')) return `模拟拒单：${bulkReasonLabel(reason.slice('paper_rejected:'.length))}`
  const map: Record<string, string> = {
    already_paper_position: '已有模拟持仓',
    already_simulated: '已经模拟',
    already_bought: '已经标记实盘',
    already_skipped: '已经跳过',
    expired_signal: '信号过期',
    not_actionable: '不可操作',
    no_simulation_cash: '模拟现金不足',
    below_order_min_size: '低于最小订单',
    spread_above_max_slippage: 'spread 过宽',
    best_ask_above_limit: '卖一高于限价',
    quote_stale: '盘口过期',
    spread_cost_too_high: 'spread 成本过高',
    low_price_tail_unverified: '低价尾部未验证',
  }
  return map[reason] ?? reasonLabel(reason)
}

function ReadinessBanner({ stats }: { stats: BotStats }) {
  const ready = Boolean(stats.strategy_live_ready)
  const reasons = stats.strategy_readiness_reasons ?? []
  return (
    <div className={`border px-3 py-2 ${ready ? 'border-green-500/30 bg-green-500/10' : 'border-amber-500/30 bg-amber-500/10'}`}>
      <div className="flex items-center gap-2">
        {ready ? <CheckCircle2 className="h-4 w-4 text-green-300" /> : <ShieldAlert className="h-4 w-4 text-amber-300" />}
        <div className="text-sm font-medium text-neutral-100">
          {ready ? '实盘门槛已通过，但仍建议从 $1-$2 canary 开始' : '当前只允许模拟观察，实盘按钮已锁定'}
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

function TradeModeSwitch({
  mode,
  liveAvailable,
  onMode,
}: {
  mode: TradeMode
  liveAvailable: boolean
  onMode: (mode: TradeMode) => void
}) {
  return (
    <div className="border border-neutral-800 bg-black p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-neutral-100">交易模式</div>
          <div className="text-[11px] text-neutral-500">
            {mode === 'paper' ? '当前所有买入操作只写入模拟账户。' : '当前操作会进入实盘下单检查。'}
          </div>
        </div>
        <span
          className={`shrink-0 border px-2 py-1 text-[10px] ${
            mode === 'paper'
              ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200'
              : 'border-blue-500/30 bg-blue-500/10 text-blue-200'
          }`}
          aria-live="polite"
        >
          {mode === 'paper' ? '模拟盘' : '实盘'}
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
          aria-describedby={!liveAvailable ? 'live-mode-unavailable' : undefined}
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
      {!liveAvailable && (
        <p id="live-mode-unavailable" className="mt-2 text-[10px] leading-relaxed text-amber-300">
          实盘尚未连接或策略门槛未通过，因此目前只能使用模拟盘。
        </p>
      )}
    </div>
  )
}

function SimulationCard({
  stats,
  value,
  clearMarks,
  lastBulk,
  onValue,
  onClearMarks,
  onReset,
  onSettle,
  onBulk,
  resetting,
  settling,
  bulkPending,
}: {
  stats: BotStats
  value: string
  clearMarks: boolean
  lastBulk: BulkSimulateResult | null
  onValue: (value: string) => void
  onClearMarks: (value: boolean) => void
  onReset: () => void
  onSettle: () => void
  onBulk: () => void
  resetting: boolean
  settling: boolean
  bulkPending: boolean
}) {
  return (
    <div className="border border-neutral-800 bg-black p-3">
      <div className="mb-3 flex items-center gap-2">
        <Wallet className="h-4 w-4 text-cyan-300" />
        <div>
          <div className="text-sm font-medium text-neutral-100">模拟账户</div>
          <div className="text-[11px] text-neutral-500">设置本金、批量模拟当前可操作信号、检查已有持仓结算。</div>
        </div>
      </div>

      <div className="mb-3 flex items-center justify-between gap-3 border border-amber-500/20 bg-amber-500/5 px-2 py-2">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 shrink-0 rounded-full bg-amber-300" aria-hidden="true" />
          <div>
            <div className="text-[11px] font-medium text-amber-200">自动模拟未开启</div>
            <div className="text-[10px] text-neutral-500">新信号不会自动买入，需要点击下方按钮。</div>
          </div>
        </div>
        <span className="shrink-0 border border-neutral-700 px-1.5 py-0.5 text-[9px] text-neutral-400">手动触发</span>
      </div>

      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">当前权益</div>
          <div className="tabular-nums text-lg text-neutral-100">{money(stats.bankroll)}</div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">未实现盈亏</div>
          <div className={`tabular-nums text-lg ${(stats.unrealized_pnl ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'}`}>
            {money(stats.unrealized_pnl ?? 0)}
          </div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">现金 / 占用</div>
          <div className="tabular-nums text-neutral-200">{money(stats.cash_balance ?? stats.bankroll)} / {money(stats.reserved_capital ?? 0)}</div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">持仓 / 已结算</div>
          <div className="tabular-nums text-neutral-200">{stats.open_trades ?? 0} / {stats.settled_trades ?? 0}</div>
        </div>
      </div>

      <div className="mt-3 flex gap-2">
        <input
          type="number"
          min="0"
          step="1"
          value={value}
          onChange={event => onValue(event.target.value)}
          className="min-w-0 flex-1 px-2 py-1 text-right tabular-nums"
          aria-label="设置模拟本金"
        />
        <button
          onClick={onReset}
          disabled={resetting}
          className="border border-cyan-500/30 px-2 py-1 text-cyan-300 hover:bg-cyan-500/10 disabled:opacity-40"
        >
          应用本金
        </button>
      </div>

      <label className="mt-2 flex items-center gap-2 text-[11px] text-neutral-500">
        <input
          type="checkbox"
          checked={clearMarks}
          onChange={event => onClearMarks(event.target.checked)}
          className="h-3 w-3 p-0"
        />
        重置时清除模拟/跳过/实盘标记
      </label>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <button
          onClick={onBulk}
          disabled={bulkPending}
          className="border border-green-500/30 bg-green-500/10 px-2 py-1.5 text-green-300 hover:border-green-500/60 disabled:opacity-40"
        >
          {bulkPending ? '模拟中...' : '一键模拟当前信号'}
        </button>
        <button
          onClick={onSettle}
          disabled={settling || (stats.open_trades ?? 0) === 0}
          className="border border-amber-500/30 px-2 py-1.5 text-amber-300 hover:bg-amber-500/10 disabled:opacity-40"
        >
          检查结算
        </button>
      </div>

      {lastBulk && (
        <div className="mt-3 border border-neutral-800 p-2 text-[11px] leading-relaxed text-neutral-400">
          <div className="mb-1 text-neutral-200">最近一次一键模拟</div>
          <div>买入 {lastBulk.count} 笔，跳过 {lastBulk.skipped}/{lastBulk.total_current}，花费 {money(lastBulk.spent)}，剩余 {money(lastBulk.remaining)}</div>
          {Object.entries(lastBulk.reason_counts).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {Object.entries(lastBulk.reason_counts).slice(0, 6).map(([reason, count]) => (
                <span key={reason} className="border border-neutral-800 bg-neutral-950 px-1.5 py-0.5 text-[10px] text-neutral-500">
                  {bulkReasonLabel(reason)} × {count}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <p className="mt-3 text-[11px] leading-relaxed text-neutral-600">
        新买入立刻显示浮亏，多数是因为按卖一成交、按买一估值，spread 会先被计入未实现亏损；这不等于最终判断已经错。
      </p>
    </div>
  )
}

function App() {
  const queryClient = useQueryClient()
  const [view, setView] = useState<'dashboard' | 'temperature-fit'>('dashboard')
  const [tradeMode, setTradeMode] = useState<TradeMode>('paper')
  const [simBalance, setSimBalance] = useState('40')
  const [clearMarks, setClearMarks] = useState(false)
  const [lastBulkResult, setLastBulkResult] = useState<BulkSimulateResult | null>(null)
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

  const startMutation = useMutation({
    mutationFn: startBot,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  })
  const stopMutation = useMutation({
    mutationFn: stopBot,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  })
  const signalStatusMutation = useMutation({
    mutationFn: ({ signalId, status, amount }: { signalId: number; status: string; amount?: number }) =>
      updateSignalStatus(signalId, status, amount),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  })
  const liveOrderMutation = useMutation({
    mutationFn: ({ signalId, amount }: { signalId: number; amount?: number }) => placeLiveOrder(signalId, amount),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  })
  const bulkSimulateMutation = useMutation({
    mutationFn: bulkSimulateSignals,
    onSuccess: result => {
      setLastBulkResult(result)
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
  const resetSimulationMutation = useMutation({
    mutationFn: ({ balance, clear }: { balance: number; clear: boolean }) => resetSimulation(balance, clear),
    onSuccess: result => {
      setSimBalance(String(result.balance))
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
  const settleMutation = useMutation({
    mutationFn: settleTradesApi,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  })
  const historyBackfillMutation = useMutation({
    mutationFn: () => backfillWeatherHistory(30),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  })

  const stats = data?.stats ?? EMPTY_STATS
  const signals = data?.weather_signals ?? []
  const forecasts = data?.weather_forecasts ?? []
  const citySeries = data?.weather_city_series ?? []
  const trades = data?.recent_trades ?? []
  const equityCurve = data?.equity_curve ?? []
  const truthHealth = data?.truth_health ?? null
  const actionable = signals.filter(signal => signal.actionable).length
  const liveAvailable = Boolean(stats.strategy_live_ready && data?.v3?.config?.live_trading)

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
            请确认 dashboard_server 正在运行在 http://127.0.0.1:8765，然后刷新页面。
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
      <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-neutral-800 px-3 py-2">
        <div className="min-w-[190px] shrink-0">
          <h1 className="text-sm font-semibold tracking-wide text-neutral-100">WeatherBot 生产化看板</h1>
          <div className="text-[11px] text-neutral-600">当前是模拟优先模式；实盘未达标前不会自动下单。</div>
        </div>
        <div className="order-3 min-w-0 basis-full overflow-x-auto xl:order-none xl:basis-auto xl:flex-1">
          <StatsCards stats={stats} />
        </div>
        <button
          onClick={() => refetch()}
          className="inline-flex items-center gap-1 border border-neutral-700 px-2 py-1 text-[11px] text-neutral-300 hover:bg-neutral-900"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          刷新
        </button>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto xl:grid-cols-[320px_minmax(500px,1fr)_390px] xl:overflow-hidden">
        <aside className="order-3 space-y-3 border-t border-neutral-800 bg-neutral-950/40 p-3 xl:order-1 xl:min-h-0 xl:overflow-y-auto xl:border-r xl:border-t-0">
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div className={`border p-2 ${stats.is_running ? 'border-green-500/30 bg-green-500/10' : 'border-neutral-800'}`}>
              <div className="mb-1 flex items-center gap-1 text-neutral-500">
                <Activity className="h-3.5 w-3.5" />
                扫描器
              </div>
              <div className={stats.is_running ? 'text-green-300' : 'text-neutral-300'}>{stats.is_running ? '运行中' : '已停止'}</div>
            </div>
            <div className="border border-neutral-800 p-2">
              <div className="text-neutral-500">数据年龄</div>
              <div className="text-neutral-200">{dataAge(stats.data_age_minutes)}</div>
            </div>
            <div className="border border-neutral-800 p-2">
              <div className="text-neutral-500">当前信号</div>
              <div className="tabular-nums text-green-300">{actionable} / {signals.length}</div>
            </div>
            <div className="border border-neutral-800 p-2">
              <div className="text-neutral-500">实盘状态</div>
              <div className={liveAvailable ? 'text-green-300' : 'text-amber-300'}>{liveAvailable ? '可用' : '锁定'}</div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => startMutation.mutate()}
              disabled={stats.is_running || startMutation.isPending}
              className="inline-flex items-center justify-center gap-1 border border-green-500/30 px-2 py-1.5 text-green-300 hover:bg-green-500/10 disabled:opacity-40"
            >
              <PlayCircle className="h-4 w-4" />
              启动扫描
            </button>
            <button
              onClick={() => stopMutation.mutate()}
              disabled={!stats.is_running || stopMutation.isPending}
              className="inline-flex items-center justify-center gap-1 border border-red-500/30 px-2 py-1.5 text-red-300 hover:bg-red-500/10 disabled:opacity-40"
            >
              <PauseCircle className="h-4 w-4" />
              停止扫描
            </button>
          </div>

          <TradeModeSwitch mode={tradeMode} liveAvailable={liveAvailable} onMode={setTradeMode} />

          <ReadinessBanner stats={stats} />

          <div>
            <SimulationCard
              stats={stats}
              value={simBalance}
              clearMarks={clearMarks}
              lastBulk={lastBulkResult}
              onValue={setSimBalance}
              onClearMarks={setClearMarks}
              onReset={() => {
                const parsed = Number(simBalance)
                if (Number.isFinite(parsed) && parsed >= 0) {
                  resetSimulationMutation.mutate({ balance: parsed, clear: clearMarks })
                }
              }}
              onSettle={() => settleMutation.mutate()}
              onBulk={() => bulkSimulateMutation.mutate()}
              resetting={resetSimulationMutation.isPending}
              settling={settleMutation.isPending}
              bulkPending={bulkSimulateMutation.isPending}
            />
          </div>

          <div className="border border-neutral-800 bg-black p-3">
            <div className="mb-2 flex items-center gap-2">
              <BarChart3 className="h-4 w-4 text-cyan-300" />
              <div>
                <div className="text-sm text-neutral-100">分析中心</div>
                <div className="text-[11px] text-neutral-600">查看温度拟合、truth 覆盖、城市偏差。</div>
              </div>
            </div>
            <button
              onClick={() => setView('temperature-fit')}
              className="w-full border border-cyan-500/30 px-2 py-1.5 text-cyan-300 hover:bg-cyan-500/10"
            >
              打开温度拟合
            </button>
          </div>

          <div className="border border-neutral-800 bg-black p-3">
            <div className="mb-2 text-sm text-neutral-100">结算源健康</div>
            <TruthHealthPanel truth={truthHealth} />
          </div>
        </aside>

        <section className="order-1 min-h-0 overflow-y-auto xl:order-2">
          <div className="relative h-[300px] min-h-[300px] border-b border-neutral-800 2xl:h-[340px]">
            <Suspense fallback={<div className="flex h-full items-center justify-center text-neutral-600">加载地球视图...</div>}>
              <GlobeView forecasts={forecasts} signals={signals} />
            </Suspense>
            <div className="absolute left-3 top-3 border border-neutral-800 bg-black/80 px-2 py-1 text-[11px]">
              <span className="text-neutral-500">可操作信号 </span>
              <span className="tabular-nums text-green-300">{actionable}</span>
              <span className="ml-2 text-neutral-500">最新扫描 </span>
              <span className="tabular-nums text-neutral-300">{timeText(stats.last_run)}</span>
            </div>
          </div>

          <div className="h-[520px] min-h-[520px] border-b border-neutral-800 xl:h-[390px] xl:min-h-[390px] 2xl:h-[430px] 2xl:min-h-[430px]">
            <div className="flex flex-col items-start gap-0.5 border-b border-neutral-800 px-3 py-1.5 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-sm text-neutral-100">机场天气趋势</div>
              <div className="text-[11px] text-neutral-600">温度来自 forecast 快照；湿度当前仅在数据源提供时显示。</div>
            </div>
            <WeatherPanel
              forecasts={forecasts}
              signals={signals}
              citySeries={citySeries}
              onBackfillHistory={() => historyBackfillMutation.mutate()}
              backfilling={historyBackfillMutation.isPending}
              backfillResult={historyBackfillMutation.data}
            />
          </div>

          <div className="h-[160px] min-h-[160px]">
            <div className="flex items-center justify-between border-b border-neutral-800 px-3 py-1.5">
              <div className="text-sm text-neutral-100">资金曲线</div>
              <div className={`tabular-nums text-[11px] ${(stats.total_pnl ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'}`}>
                {money(stats.total_pnl)}
              </div>
            </div>
            <div className="h-[118px] p-2">
              <EquityChart data={equityCurve} initialBankroll={stats.bankroll - stats.total_pnl} />
            </div>
          </div>
        </section>

        <aside className="order-2 flex h-[760px] min-h-0 flex-col border-t border-neutral-800 xl:order-3 xl:h-auto xl:border-l xl:border-t-0">
          <div className="flex min-h-0 flex-[1.15] flex-col">
            <div className="flex items-center justify-between border-b border-neutral-800 px-3 py-1.5">
              <div>
                <div className="text-sm text-neutral-100">信号行动队列</div>
                <div className="text-[11px] text-neutral-600">点击一行展开，查看下单链接、盘口和拦截原因。</div>
              </div>
              <div className="text-[11px] text-neutral-500">{signals.length} 条</div>
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

          <div className="flex min-h-0 flex-1 flex-col border-t border-neutral-800">
            <div className="flex items-center justify-between border-b border-neutral-800 px-3 py-1.5">
              <div>
                <div className="text-sm text-neutral-100">模拟 / 交易记录</div>
                <div className="text-[11px] text-neutral-600">未结算持仓按当前 bid 估值，因此会体现 spread 浮亏。</div>
              </div>
              <div className="text-[11px] text-neutral-500">{trades.length} 条</div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <TradesTable trades={trades} />
            </div>
          </div>
        </aside>
      </main>
    </div>
  )
}

export default App
