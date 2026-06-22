import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  bulkSimulateSignals,
  fetchDashboard,
  fetchTemperatureFit,
  notifyDailySummary,
  placeLiveOrder,
  resetSimulation,
  settleTradesApi,
  simulateTrade,
  startBot,
  stopBot,
  updateSignalStatus,
} from './api'
import { BacktestPanel } from './components/BacktestPanel'
import { CalibrationPanel } from './components/CalibrationPanel'
import { EdgeDistribution } from './components/EdgeDistribution'
import { EquityChart } from './components/EquityChart'
import { MicrostructurePanel } from './components/MicrostructurePanel'
import { SignalsTable } from './components/SignalsTable'
import { StatsCards } from './components/StatsCards'
import { Terminal } from './components/Terminal'
import { TradesTable } from './components/TradesTable'
import { TemperatureFitPage } from './components/TemperatureFitPage'
import { WeatherPanel } from './components/WeatherPanel'
import type { BotStats, BulkSimulateResult } from './types'

const GlobeView = lazy(() => import('./components/GlobeView').then(module => ({ default: module.GlobeView })))

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

function LiveClock() {
  const [time, setTime] = useState(new Date())

  useEffect(() => {
    const interval = window.setInterval(() => setTime(new Date()), 1000)
    return () => window.clearInterval(interval)
  }, [])

  return (
    <span className="text-xs tabular-nums text-neutral-400">
      {time.toLocaleTimeString('zh-CN', { hour12: false })}
    </span>
  )
}

function RefreshBar({ interval }: { interval: number }) {
  const [progress, setProgress] = useState(100)

  useEffect(() => {
    setProgress(100)
    const step = 100 / (interval / 1000)
    const timer = window.setInterval(() => {
      setProgress(value => Math.max(0, value - step))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [interval])

  return (
    <div className="refresh-bar w-16">
      <div className="refresh-fill" style={{ width: `${progress}%` }} />
    </div>
  )
}

function formatDataAge(minutes?: number | null) {
  if (minutes === null || minutes === undefined) return '暂无数据'
  if (minutes < 60) return `${minutes.toFixed(0)} 分钟前`
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)} 小时前`
  return `${(minutes / 1440).toFixed(1)} 天前`
}

function bulkReasonLabel(reason: string): string {
  if (reason.startsWith('risk_gate:')) return `风控拦截：${reason.slice('risk_gate:'.length)}`
  if (reason.startsWith('paper_rejected:')) return `盘口拒单：${bulkReasonLabel(reason.slice('paper_rejected:'.length))}`
  switch (reason) {
    case 'already_paper_position': return '已有纸面仓位'
    case 'already_simulated': return '已模拟'
    case 'already_bought': return '已实盘标记'
    case 'already_skipped': return '已跳过'
    case 'expired_signal': return '已过期'
    case 'not_actionable': return '不可行动'
    case 'calibrated_ev_nonpositive': return '校准EV非正'
    case 'risk_gate_blocked': return '风控拦截'
    case 'fit_independent_days_too_low': return '独立结算日过少'
    case 'fit_independent_days_low': return '独立结算日不足'
    case 'low_price_tail_unverified': return '低价尾部未验证'
    case 'spread_cost_too_high': return 'spread 成本过高'
    case 'no_requested_amount': return '无模拟金额'
    case 'no_simulation_cash': return '余额不足'
    case 'position_write_failed': return '持仓写入失败'
    case 'below_order_min_size': return '低于最小订单'
    case 'spread_above_max_slippage': return 'spread 太宽'
    case 'best_ask_above_limit': return '卖一高于限价'
    case 'orderbook_disabled': return '盘口未开启'
    case 'invalid_tick_size': return '价格不符合 tick'
    case 'quote_stale': return '盘口过期'
    default: return reason
  }
}

function formatDateTime(value?: string | null) {
  if (!value) return '尚未重置'
  try {
    return new Date(value).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch {
    return '时间未知'
  }
}

function SimulationPanel({
  bankroll,
  cashBalance,
  reservedCapital,
  startedAt,
  openTrades,
  settledTrades,
  value,
  clearMarks,
  onValue,
  onClearMarks,
  onReset,
  onSettle,
  disabled,
  settling,
}: {
  bankroll: number
  cashBalance: number
  reservedCapital: number
  startedAt?: string | null
  openTrades: number
  settledTrades: number
  value: string
  clearMarks: boolean
  onValue: (value: string) => void
  onClearMarks: (value: boolean) => void
  onReset: () => void
  onSettle: () => void
  disabled: boolean
  settling: boolean
}) {
  return (
    <div className="h-full space-y-2 overflow-y-auto p-2 text-[10px] text-neutral-500">
      <div className="flex items-center justify-between">
        <span className="uppercase tracking-wider">当前权益</span>
        <span className="tabular-nums text-neutral-200">${bankroll.toFixed(2)}</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="uppercase tracking-wider">现金 / 持仓成本</span>
        <span className="tabular-nums text-neutral-300">${cashBalance.toFixed(2)} / ${reservedCapital.toFixed(2)}</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="uppercase tracking-wider">本轮开始</span>
        <span className="tabular-nums text-neutral-300">{formatDateTime(startedAt)}</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="uppercase tracking-wider">持仓 / 已结算</span>
        <span className="tabular-nums text-neutral-300">{openTrades}/{settledTrades}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          min="0"
          step="1"
          value={value}
          onChange={event => onValue(event.target.value)}
          className="w-full px-2 py-1 text-[11px] tabular-nums"
          placeholder="输入模拟本金"
        />
        <button
          onClick={onReset}
          disabled={disabled}
          className="whitespace-nowrap border border-green-500/30 px-2 py-1 text-green-400 hover:bg-green-500/10 disabled:opacity-40"
        >
          应用
        </button>
        <button
          onClick={onSettle}
          disabled={settling || openTrades === 0}
          className="whitespace-nowrap border border-amber-500/30 px-2 py-1 text-amber-400 hover:bg-amber-500/10 disabled:opacity-40"
        >
          检查结算
        </button>
      </div>
      <label className="flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          checked={clearMarks}
          onChange={event => onClearMarks(event.target.checked)}
          className="h-3 w-3 p-0"
        />
        <span>同时清除模拟、跳过、实盘等标记</span>
      </label>
      <div className="space-y-1 border-t border-neutral-800 pt-2 leading-relaxed">
        <p>当前权益现在只按已结算盈亏计算；未结算持仓显示为占用成本，不再当成利润。</p>
        <p>一键模拟会按当前可操作天气信号批量写入本地模拟仓位，不会真实下单。</p>
        <p>单条信号仍支持模拟、标记实盘、跳过和打开 Polymarket 链接。</p>
      </div>
    </div>
  )
}

function App() {
  const queryClient = useQueryClient()
  const [leftWidth, setLeftWidth] = useState(300)
  const [rightWidth, setRightWidth] = useState(420)
  const [simBalance, setSimBalance] = useState('40')
  const [clearMarks, setClearMarks] = useState(false)
  const [view, setView] = useState<'dashboard' | 'temperature-fit'>('dashboard')
  const [lastBulkResult, setLastBulkResult] = useState<BulkSimulateResult | null>(null)
  const leftDragRef = useRef(false)
  const rightDragRef = useRef(false)
  const balanceInitRef = useRef(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['dashboard'],
    queryFn: fetchDashboard,
    refetchInterval: 10000,
  })

  const temperatureFitQuery = useQuery({
    queryKey: ['temperature-fit'],
    queryFn: fetchTemperatureFit,
    enabled: view === 'temperature-fit',
    refetchInterval: view === 'temperature-fit' ? 30000 : false,
  })

  const tradeMutation = useMutation({
    mutationFn: simulateTrade,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
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

  const notifyDailyMutation = useMutation({
    mutationFn: notifyDailySummary,
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

  useEffect(() => {
    const onMove = (event: MouseEvent) => {
      if (leftDragRef.current) {
        setLeftWidth(Math.max(240, Math.min(520, event.clientX)))
      }
      if (rightDragRef.current) {
        setRightWidth(Math.max(320, Math.min(640, window.innerWidth - event.clientX)))
      }
    }
    const onUp = () => {
      leftDragRef.current = false
      rightDragRef.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [])

  const stats = data?.stats ?? EMPTY_STATS
  const recentTrades = data?.recent_trades ?? []
  const micro = data?.microstructure
  const weatherSignals = data?.weather_signals ?? []
  const weatherForecasts = data?.weather_forecasts ?? []
  const equityCurve = data?.equity_curve ?? []
  const calibration = data?.calibration ?? null
  const backtest = data?.backtest ?? null
  const actionableCount = weatherSignals.filter(signal => signal.actionable).length

  useEffect(() => {
    if (!balanceInitRef.current && data?.stats?.bankroll !== undefined) {
      setSimBalance(String(data.stats.bankroll.toFixed(0)))
      balanceInitRef.current = true
    }
  }, [data?.stats?.bankroll])

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-black">
        <div className="text-center">
          <div className="relative mx-auto mb-4 h-10 w-10">
            <div className="absolute inset-0 rounded-full border-2 border-neutral-800" />
            <div className="absolute inset-0 animate-spin rounded-full border-2 border-transparent border-t-green-500" />
          </div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-neutral-500">正在连接看板</div>
        </div>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="flex h-screen items-center justify-center bg-black">
        <div className="text-center">
          <div className="mb-2 text-xs uppercase tracking-wider text-red-500">后端连接失败</div>
          <button
            onClick={() => refetch()}
            className="border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-xs uppercase tracking-wider text-neutral-300"
          >
            重试
          </button>
        </div>
      </div>
    )
  }

  if (view === 'temperature-fit') {
    return (
      <TemperatureFitPage
        data={temperatureFitQuery.data}
        loading={temperatureFitQuery.isLoading}
        onBack={() => setView('dashboard')}
      />
    )
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-black text-neutral-200">
      <motion.header
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="relative flex shrink-0 items-center gap-4 border-b border-neutral-800 px-3 py-1.5"
      >
        <div className="scan-line" />
        <div className="flex shrink-0 items-center gap-2">
          <h1 className="whitespace-nowrap font-mono text-xs font-bold uppercase tracking-widest text-neutral-100">
            天气交易终端
          </h1>
          <span className={`px-1.5 py-0.5 text-[9px] font-bold uppercase ${
            stats.is_running
              ? 'border border-green-500/20 bg-green-500/10 text-green-500'
              : 'border border-neutral-700 bg-neutral-800 text-neutral-500'
          }`}>
            {stats.is_running ? '扫描中' : '扫描停止'}
          </span>
          <span className="border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-bold uppercase text-amber-400">
            模拟模式
          </span>
          <span className={`px-1.5 py-0.5 text-[9px] font-bold uppercase ${
            (stats.data_age_minutes ?? 99999) <= 90
              ? 'border border-green-500/20 bg-green-500/10 text-green-500'
              : 'border border-red-500/20 bg-red-500/10 text-red-400'
          }`}>
            数据 {formatDataAge(stats.data_age_minutes)}
          </span>
        </div>

        <div className="flex-1" />
        <StatsCards stats={stats} />

        <div className="flex shrink-0 items-center gap-2">
          <button
            onClick={() => refetch()}
            className="whitespace-nowrap border border-neutral-700 bg-neutral-900 px-2.5 py-1 text-[10px] uppercase tracking-wider text-neutral-300 transition-colors hover:border-neutral-600 disabled:opacity-50"
          >
            刷新
          </button>
          <button
            onClick={() => bulkSimulateMutation.mutate()}
            disabled={bulkSimulateMutation.isPending}
            className="whitespace-nowrap border border-green-500/30 bg-green-500/10 px-2.5 py-1 text-[10px] uppercase tracking-wider text-green-400 transition-colors hover:border-green-500/60 disabled:opacity-40"
            title="只写入本地模拟记录，不会真实下单"
          >
            {bulkSimulateMutation.isPending ? '模拟中...' : '一键模拟'}
          </button>
          <button
            onClick={() => notifyDailyMutation.mutate()}
            disabled={notifyDailyMutation.isPending}
            className="whitespace-nowrap border border-blue-500/30 bg-blue-500/10 px-2.5 py-1 text-[10px] uppercase tracking-wider text-blue-400 transition-colors hover:border-blue-500/60 disabled:opacity-40"
            title="发送或记录日度摘要"
          >
            日报
          </button>
          <LiveClock />
        </div>
      </motion.header>

      {lastBulkResult && (
        <div className="shrink-0 border-b border-neutral-800 bg-neutral-950 px-3 py-1 text-[10px] text-neutral-400">
          <div className="flex items-center gap-3 overflow-x-auto">
            <span className="whitespace-nowrap text-neutral-300">一键模拟结果</span>
            <span className="whitespace-nowrap tabular-nums text-green-400">买入 {lastBulkResult.count}</span>
            <span className="whitespace-nowrap tabular-nums text-amber-400">跳过 {lastBulkResult.skipped}/{lastBulkResult.total_current}</span>
            <span className="whitespace-nowrap tabular-nums text-blue-300">
              用额 ${lastBulkResult.spent.toFixed(2)} / 剩余 ${lastBulkResult.remaining.toFixed(2)}
            </span>
            {Object.entries(lastBulkResult.reason_counts).slice(0, 5).map(([reason, value]) => (
              <span key={reason} className="whitespace-nowrap border border-neutral-800 bg-black px-1.5 py-0.5 text-neutral-500">
                {bulkReasonLabel(reason)} × {value}
              </span>
            ))}
          </div>
          {lastBulkResult.examples.length > 0 && (
            <div className="mt-1 flex gap-2 overflow-x-auto text-[9px] text-neutral-600">
              {lastBulkResult.examples.slice(0, 4).map(example => (
                <a
                  key={`${example.id}-${example.reason}`}
                  href={example.event_url || undefined}
                  target="_blank"
                  rel="noreferrer"
                  className="max-w-[260px] shrink-0 truncate border border-neutral-900 bg-black px-1.5 py-0.5 hover:text-cyan-400"
                  title={`${example.title || example.city || '信号'}：${bulkReasonLabel(example.reason)}`}
                >
                  {bulkReasonLabel(example.reason)} · {example.city || example.title || `#${example.id}`}
                </a>
              ))}
            </div>
          )}
        </div>
      )}

      <div
        className="grid min-h-0 flex-1 grid-rows-[1fr] gap-0"
        style={{ gridTemplateColumns: `${leftWidth}px 6px minmax(420px, 1fr) 6px ${rightWidth}px` }}
      >
        <div className="flex min-h-0 flex-col overflow-hidden border-r border-neutral-800">
          {micro && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="shrink-0 border-b border-neutral-800 px-2 py-2">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[10px] uppercase tracking-wider text-neutral-500">盘口结构</span>
                <span className="tabular-nums text-[9px] text-neutral-600">{micro.source}</span>
              </div>
              <MicrostructurePanel micro={micro} />
            </motion.div>
          )}

          <div className="border-b border-neutral-800" style={{ height: '28%', minHeight: '120px' }}>
            <div className="flex shrink-0 items-center justify-between border-b border-neutral-800 px-2 py-1">
              <span className="text-[10px] uppercase tracking-wider text-neutral-500">资金曲线</span>
              <span className={`tabular-nums text-[10px] ${stats.total_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                {stats.total_pnl >= 0 ? '+' : ''}${stats.total_pnl.toFixed(2)}
              </span>
            </div>
            <div className="h-[calc(100%-24px)] p-1">
              <EquityChart data={equityCurve} initialBankroll={stats.bankroll - stats.total_pnl} />
            </div>
          </div>

          {calibration && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="overflow-hidden border-b border-neutral-800 px-2 py-2"
              style={{ height: '22%', minHeight: '120px' }}
            >
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-[10px] uppercase tracking-wider text-neutral-500">校准</span>
                <span className="tabular-nums text-[9px] text-neutral-600">{calibration.total_with_outcome} 已结算</span>
              </div>
              <CalibrationPanel calibration={calibration} backtest={backtest} />
            </motion.div>
          )}

          <div className="min-h-0 flex-1">
            <Terminal
              isRunning={stats.is_running}
              lastRun={stats.last_run}
              stats={{ total_trades: stats.total_trades, total_pnl: stats.total_pnl }}
              onStart={() => startMutation.mutate()}
              onStop={() => stopMutation.mutate()}
              onScan={() => refetch()}
            />
          </div>
        </div>

        <div
          className="cursor-col-resize border-l border-r border-neutral-800 bg-neutral-900/60 hover:bg-green-500/30"
          onMouseDown={() => {
            leftDragRef.current = true
            document.body.style.cursor = 'col-resize'
            document.body.style.userSelect = 'none'
          }}
          title="拖拽调整左侧栏宽度"
        />

        <div className="flex min-h-0 flex-col">
          <div className="relative" style={{ height: '58%' }}>
            <div className="absolute inset-0">
              <Suspense fallback={
                <div className="flex h-full w-full items-center justify-center bg-black">
                  <span className="text-[10px] uppercase tracking-wider text-neutral-600">加载地球视图...</span>
                </div>
              }>
                <GlobeView forecasts={weatherForecasts} signals={weatherSignals} />
              </Suspense>
            </div>
            <div className="absolute left-2 top-2 z-10">
              <div className="border border-neutral-800 bg-black/80 px-2 py-1 text-[10px]">
                <span className="mr-2 uppercase tracking-wider text-neutral-500">市场</span>
                <span className="tabular-nums text-amber-500">{actionableCount} 个当前信号</span>
              </div>
            </div>
          </div>

          <div className="grid min-h-0 flex-1 grid-cols-4 border-t border-neutral-800">
            <div className="flex min-h-0 flex-col border-r border-neutral-800">
              <div className="shrink-0 border-b border-neutral-800 px-2 py-1">
                <span className="text-[10px] uppercase tracking-wider text-neutral-500">EV 分布</span>
              </div>
              <div className="min-h-0 flex-1 p-1">
                <EdgeDistribution weatherSignals={weatherSignals} />
              </div>
            </div>

            <div className="flex min-h-0 flex-col border-r border-neutral-800">
              <div className="shrink-0 border-b border-neutral-800 px-2 py-1">
                <span className="text-[10px] uppercase tracking-wider text-neutral-500">模拟账户</span>
              </div>
              <SimulationPanel
                bankroll={stats.bankroll}
                cashBalance={stats.cash_balance ?? stats.bankroll}
                reservedCapital={stats.reserved_capital ?? 0}
                startedAt={stats.simulation_started_at}
                openTrades={stats.open_trades ?? 0}
                settledTrades={stats.settled_trades ?? 0}
                value={simBalance}
                clearMarks={clearMarks}
                onValue={setSimBalance}
                onClearMarks={setClearMarks}
                onReset={() => {
                  const parsed = Number(simBalance)
                  if (Number.isFinite(parsed) && parsed >= 0) {
                    resetSimulationMutation.mutate({ balance: parsed, clear: clearMarks })
                  }
                }}
                onSettle={() => settleMutation.mutate()}
                disabled={resetSimulationMutation.isPending}
                settling={settleMutation.isPending}
              />
            </div>

            <div className="flex min-h-0 flex-col border-r border-neutral-800">
              <div className="shrink-0 border-b border-neutral-800 px-2 py-1">
                <span className="text-[10px] uppercase tracking-wider text-neutral-500">历史复盘</span>
              </div>
              <div className="min-h-0 flex-1">
                <BacktestPanel backtest={backtest} onOpenFit={() => setView('temperature-fit')} />
              </div>
            </div>

            <div className="flex min-h-0 flex-col">
              <div className="flex shrink-0 items-center justify-between border-b border-neutral-800 px-2 py-1">
                <span className="text-[10px] uppercase tracking-wider text-neutral-500">天气</span>
                <span className="border border-cyan-500/20 bg-cyan-500/10 px-1 py-0.5 text-[8px] font-bold uppercase text-cyan-400">WX</span>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <WeatherPanel forecasts={weatherForecasts} signals={weatherSignals} />
              </div>
            </div>
          </div>
        </div>

        <div
          className="cursor-col-resize border-l border-r border-neutral-800 bg-neutral-900/60 hover:bg-green-500/30"
          onMouseDown={() => {
            rightDragRef.current = true
            document.body.style.cursor = 'col-resize'
            document.body.style.userSelect = 'none'
          }}
          title="拖拽调整右侧栏宽度"
        />

        <div className="flex min-h-0 flex-col overflow-hidden">
          <div className="flex min-h-0 flex-col" style={{ height: '50%' }}>
            <div className="flex shrink-0 items-center justify-between border-b border-neutral-800 px-2 py-1">
              <span className="text-[10px] uppercase tracking-wider text-neutral-500">信号</span>
              <div className="flex items-center gap-2">
                {(stats.expired_signal_count ?? 0) > 0 && (
                  <span className="tabular-nums text-[10px] text-neutral-600">{stats.expired_signal_count} 过期隐藏</span>
                )}
                {weatherSignals.length > 0 && (
                  <span className="tabular-nums text-[10px] text-cyan-400">{weatherSignals.length} 天气</span>
                )}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <SignalsTable
                signals={[]}
                weatherSignals={weatherSignals}
                onSimulateTrade={ticker => tradeMutation.mutate(ticker)}
                isSimulating={tradeMutation.isPending}
                onSignalStatus={(signalId, status, amount) => signalStatusMutation.mutate({ signalId, status, amount })}
                onLiveOrder={(signalId, amount) => liveOrderMutation.mutate({ signalId, amount })}
              />
            </div>
          </div>

          <div className="flex min-h-0 flex-col border-t border-neutral-800" style={{ height: '50%' }}>
            <div className="flex shrink-0 items-center justify-between border-b border-neutral-800 px-2 py-1">
              <span className="text-[10px] uppercase tracking-wider text-neutral-500">模拟 / 交易记录</span>
              <span className="tabular-nums text-[10px] text-neutral-600">{recentTrades.length}</span>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <TradesTable trades={recentTrades} />
            </div>
          </div>
        </div>
      </div>

      <footer className="flex shrink-0 items-center justify-between border-t border-neutral-800 px-3 py-0.5">
        <span className="font-mono text-[10px] text-neutral-700">Open-Meteo | METAR | Polymarket 天气区间</span>
        <div className="flex items-center gap-3">
          <RefreshBar interval={10000} />
          <span className="font-mono text-[10px] text-neutral-700">WeatherBot 信号引擎 + Kalshi 看板框架</span>
          <div className="flex items-center gap-1">
            <div className="h-1.5 w-1.5 rounded-full bg-green-500" />
            <span className="font-mono text-[10px] text-neutral-600">已连接</span>
          </div>
        </div>
      </footer>
    </div>
  )
}

export default App
