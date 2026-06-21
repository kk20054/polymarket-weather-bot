import { useState, useEffect, Suspense, lazy, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { bulkSimulateSignals, fetchDashboard, notifyDailySummary, placeLiveOrder, resetSimulation, settleTradesApi, simulateTrade, startBot, stopBot, updateSignalStatus } from './api'
import { StatsCards } from './components/StatsCards'
import { SignalsTable } from './components/SignalsTable'
import { TradesTable } from './components/TradesTable'
import { EquityChart } from './components/EquityChart'
import { Terminal } from './components/Terminal'
import { MicrostructurePanel } from './components/MicrostructurePanel'
import { CalibrationPanel } from './components/CalibrationPanel'
import { WeatherPanel } from './components/WeatherPanel'
import { EdgeDistribution } from './components/EdgeDistribution'
import { BacktestPanel } from './components/BacktestPanel'

const GlobeView = lazy(() => import('./components/GlobeView').then(m => ({ default: m.GlobeView })))

function LiveClock() {
  const [time, setTime] = useState(new Date())
  useEffect(() => {
    const interval = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(interval)
  }, [])
  return (
    <span className="text-xs tabular-nums text-neutral-400">
      {time.toLocaleTimeString('en-US', { hour12: false })}
    </span>
  )
}

function RefreshBar({ interval }: { interval: number }) {
  const [progress, setProgress] = useState(100)

  useEffect(() => {
    setProgress(100)
    const step = 100 / (interval / 1000)
    const timer = setInterval(() => {
      setProgress(p => Math.max(0, p - step))
    }, 1000)
    return () => clearInterval(timer)
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
    <div className="h-full p-2 text-[10px] text-neutral-500 space-y-2 overflow-y-auto">
      <div className="flex items-center justify-between">
        <span className="uppercase tracking-wider">当前总权益</span>
        <span className="text-neutral-200 tabular-nums">${bankroll.toFixed(2)}</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="uppercase tracking-wider">现金/持仓</span>
        <span className="text-neutral-300 tabular-nums">${cashBalance.toFixed(2)} / ${reservedCapital.toFixed(2)}</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="uppercase tracking-wider">本轮开始</span>
        <span className="text-neutral-300 tabular-nums">{formatDateTime(startedAt)}</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="uppercase tracking-wider">持仓/结算</span>
        <span className="text-neutral-300 tabular-nums">{openTrades}/{settledTrades}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          min="0"
          step="1"
          value={value}
          onChange={(e) => onValue(e.target.value)}
          className="w-full px-2 py-1 text-[11px] tabular-nums"
          placeholder="输入本金"
        />
        <button
          onClick={onReset}
          disabled={disabled}
          className="px-2 py-1 border border-green-500/30 text-green-400 hover:bg-green-500/10 disabled:opacity-40 whitespace-nowrap"
        >
          应用
        </button>
        <button
          onClick={onSettle}
          disabled={settling || openTrades === 0}
          className="px-2 py-1 border border-amber-500/30 text-amber-400 hover:bg-amber-500/10 disabled:opacity-40 whitespace-nowrap"
        >
          检查结算
        </button>
      </div>
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={clearMarks}
          onChange={(e) => onClearMarks(e.target.checked)}
          className="w-3 h-3 p-0"
        />
        <span>同时清除“模拟/跳过/实盘”标记</span>
      </label>
      <div className="border-t border-neutral-800 pt-2 leading-relaxed space-y-1">
        <p>应用本金：重置本轮模拟起点，旧交易不会再混入当前统计。</p>
        <p>一键模拟：把当前可操作信号批量标记为模拟。</p>
        <p>单条按钮：外链、模拟、实盘标记、跳过都会保留在每条信号右侧。</p>
        <p>启动扫描：运行本地 WeatherBot，日志会进入系统日志区。</p>
      </div>
    </div>
  )
}

function App() {
  const queryClient = useQueryClient()
  const [rightWidth, setRightWidth] = useState(420)
  const [simBalance, setSimBalance] = useState('40')
  const [clearMarks, setClearMarks] = useState(false)
  const dragRef = useRef(false)
  const balanceInitRef = useRef(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['dashboard'],
    queryFn: fetchDashboard,
    refetchInterval: 10000,
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
    mutationFn: ({ signalId, status, amount }: { signalId: number; status: string; amount?: number }) => updateSignalStatus(signalId, status, amount),
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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  })

  const resetSimulationMutation = useMutation({
    mutationFn: ({ balance, clear }: { balance: number; clear: boolean }) => resetSimulation(balance, clear),
    onSuccess: (result) => {
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
      if (!dragRef.current) return
      const next = Math.max(320, Math.min(640, window.innerWidth - event.clientX))
      setRightWidth(next)
    }
    const onUp = () => {
      dragRef.current = false
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

  const activeSignals = data?.active_signals ?? []
  const recentTrades = data?.recent_trades ?? []
  const btcPrice = data?.btc_price
  const micro = data?.microstructure
  const weatherSignals = data?.weather_signals ?? []
  const weatherForecasts = data?.weather_forecasts ?? []

  const stats = data?.stats ?? {
    is_running: false,
    last_run: null,
    total_trades: 0,
    open_trades: 0,
    settled_trades: 0,
    total_pnl: 0,
    bankroll: 10000,
    winning_trades: 0,
    win_rate: 0,
    simulation_started_at: null
  }
  const equityCurve = data?.equity_curve ?? []
  const calibration = data?.calibration ?? null
  const backtest = data?.backtest ?? null

  useEffect(() => {
    if (!balanceInitRef.current && data?.stats?.bankroll !== undefined) {
      setSimBalance(String(data.stats.bankroll.toFixed(0)))
      balanceInitRef.current = true
    }
  }, [data?.stats?.bankroll])

  const actionableCount = activeSignals.filter(s => s.actionable).length + weatherSignals.filter(s => s.actionable).length

  if (isLoading) {
    return (
      <div className="h-screen bg-black flex items-center justify-center">
        <div className="text-center">
          <div className="relative w-10 h-10 mx-auto mb-4">
            <div className="absolute inset-0 border-2 border-neutral-800 rounded-full" />
            <div className="absolute inset-0 border-2 border-transparent border-t-green-500 rounded-full animate-spin" />
          </div>
          <div className="text-[10px] text-neutral-500 uppercase tracking-widest font-mono">正在连接看板</div>
        </div>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="h-screen bg-black flex items-center justify-center">
        <div className="text-center">
          <div className="text-red-500 text-xs uppercase mb-2 tracking-wider">后端连接失败</div>
          <button
            onClick={() => refetch()}
            className="px-3 py-1.5 bg-neutral-900 border border-neutral-700 text-neutral-300 text-xs uppercase tracking-wider"
          >
            重试
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen bg-black text-neutral-200 flex flex-col overflow-hidden">
      {/* ===== HEADER ===== */}
      <motion.header
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="shrink-0 border-b border-neutral-800 px-3 py-1.5 flex items-center gap-4 relative"
      >
        <div className="scan-line" />

        <div className="flex items-center gap-2 shrink-0">
          <h1 className="text-xs font-bold text-neutral-100 uppercase tracking-widest whitespace-nowrap font-mono">
            天气交易终端
          </h1>
          <span className={`px-1.5 py-0.5 text-[9px] font-bold uppercase ${
            stats.is_running
              ? 'bg-green-500/10 text-green-500 border border-green-500/20'
              : 'bg-neutral-800 text-neutral-500 border border-neutral-700'
          }`}>
            {stats.is_running ? '扫描中' : '扫描停止'}
          </span>
          <span className="px-1.5 py-0.5 text-[9px] font-bold uppercase bg-amber-500/10 text-amber-400 border border-amber-500/20">
            模拟模式
          </span>
          <span className={`px-1.5 py-0.5 text-[9px] font-bold uppercase ${
            (stats.data_age_minutes ?? 99999) <= 90
              ? 'bg-green-500/10 text-green-500 border border-green-500/20'
              : 'bg-red-500/10 text-red-400 border border-red-500/20'
          }`}>
            数据 {formatDataAge(stats.data_age_minutes)}
          </span>
        </div>

        {btcPrice && (
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-sm font-bold tabular-nums text-neutral-100">
              ${btcPrice.price.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </span>
            <span className={`text-[10px] tabular-nums ${btcPrice.change_24h >= 0 ? 'text-green-500' : 'text-red-500'}`}>
              {btcPrice.change_24h >= 0 ? '+' : ''}{btcPrice.change_24h.toFixed(2)}%
            </span>
          </div>
        )}

        <div className="flex-1" />

        <StatsCards stats={stats} />

        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => refetch()}
            className="px-2.5 py-1 bg-neutral-900 border border-neutral-700 hover:border-neutral-600 text-neutral-300 text-[10px] uppercase tracking-wider transition-colors disabled:opacity-50 whitespace-nowrap"
          >
            刷新
          </button>
          <button
            onClick={() => bulkSimulateMutation.mutate()}
            disabled={bulkSimulateMutation.isPending || weatherSignals.filter(s => s.actionable).length === 0}
            className="px-2.5 py-1 bg-green-500/10 border border-green-500/30 hover:border-green-500/60 text-green-400 text-[10px] uppercase tracking-wider transition-colors disabled:opacity-40 whitespace-nowrap"
            title="只做本地模拟记录，不会真实下单"
          >
            {bulkSimulateMutation.isPending ? '模拟中...' : '一键模拟'}
          </button>
          <button
            onClick={() => notifyDailyMutation.mutate()}
            disabled={notifyDailyMutation.isPending}
            className="px-2.5 py-1 bg-blue-500/10 border border-blue-500/30 hover:border-blue-500/60 text-blue-400 text-[10px] uppercase tracking-wider transition-colors disabled:opacity-40 whitespace-nowrap"
            title="Send daily Feishu summary; without webhook it records locally only"
          >
            {notifyDailyMutation.isPending ? 'Sending' : 'Daily'}
          </button>
          <LiveClock />
        </div>
      </motion.header>

      {/* ===== MAIN GRID ===== */}
      <div
        className="flex-1 min-h-0 grid grid-rows-[1fr] gap-0"
        style={{ gridTemplateColumns: `300px minmax(420px, 1fr) 6px ${rightWidth}px` }}
      >

        {/* ===== LEFT COLUMN ===== */}
        <div className="flex flex-col border-r border-neutral-800 min-h-0 overflow-hidden">
          {/* Microstructure */}
          {micro && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="shrink-0 border-b border-neutral-800 px-2 py-2"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] text-neutral-500 uppercase tracking-wider">盘口结构</span>
                <span className="text-[9px] text-neutral-600 tabular-nums">{micro.source}</span>
              </div>
              <MicrostructurePanel micro={micro} />
            </motion.div>
          )}

          {/* Equity chart */}
          <div className="border-b border-neutral-800" style={{ height: '28%', minHeight: '120px' }}>
            <div className="px-2 py-1 border-b border-neutral-800 flex items-center justify-between shrink-0">
              <span className="text-[10px] text-neutral-500 uppercase tracking-wider">资金曲线</span>
              <span className={`text-[10px] tabular-nums ${stats.total_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                {stats.total_pnl >= 0 ? '+' : ''}${stats.total_pnl.toFixed(0)}
              </span>
            </div>
            <div className="h-[calc(100%-24px)] p-1">
              <EquityChart data={equityCurve} initialBankroll={stats.bankroll - stats.total_pnl} />
            </div>
          </div>

          {/* Calibration */}
          {calibration && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="border-b border-neutral-800 px-2 py-2 overflow-hidden"
              style={{ height: '22%', minHeight: '120px' }}
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[10px] text-neutral-500 uppercase tracking-wider">校准</span>
                <span className="text-[9px] text-neutral-600 tabular-nums">{calibration.total_with_outcome} 已结算</span>
              </div>
              <CalibrationPanel calibration={calibration} />
            </motion.div>
          )}

          {/* Terminal fills remaining */}
          <div className="flex-1 min-h-0">
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

        {/* ===== CENTER COLUMN ===== */}
        <div className="flex flex-col min-h-0">
          {/* Globe - top 60% */}
          <div className="relative" style={{ height: '58%' }}>
            <div className="absolute inset-0">
              <Suspense fallback={
                <div className="w-full h-full flex items-center justify-center bg-black">
                  <span className="text-[10px] text-neutral-600 uppercase tracking-wider">加载地球视图...</span>
                </div>
              }>
                <GlobeView forecasts={weatherForecasts} signals={weatherSignals} />
              </Suspense>
            </div>
            {/* Globe overlay: actionable count */}
            <div className="absolute top-2 left-2 z-10">
              <div className="px-2 py-1 bg-black/80 border border-neutral-800 text-[10px]">
                <span className="text-neutral-500 uppercase tracking-wider mr-2">市场</span>
                <span className="text-amber-500 tabular-nums">{actionableCount} 个当前信号</span>
              </div>
            </div>
          </div>

          {/* Bottom panels */}
          <div className="flex-1 min-h-0 grid grid-cols-4 border-t border-neutral-800">
            {/* Edge Distribution */}
            <div className="border-r border-neutral-800 flex flex-col min-h-0">
              <div className="px-2 py-1 border-b border-neutral-800 shrink-0">
                <span className="text-[10px] text-neutral-500 uppercase tracking-wider">EV 分布</span>
              </div>
              <div className="flex-1 min-h-0 p-1">
                <EdgeDistribution btcSignals={activeSignals} weatherSignals={weatherSignals} />
              </div>
            </div>

            {/* Simulation controls */}
            <div className="border-r border-neutral-800 flex flex-col min-h-0">
              <div className="px-2 py-1 border-b border-neutral-800 shrink-0">
                <span className="text-[10px] text-neutral-500 uppercase tracking-wider">模拟账户</span>
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

            {/* Historical backtest/replay */}
            <div className="border-r border-neutral-800 flex flex-col min-h-0">
              <div className="px-2 py-1 border-b border-neutral-800 shrink-0">
                <span className="text-[10px] text-neutral-500 uppercase tracking-wider">历史复盘</span>
              </div>
              <div className="flex-1 min-h-0">
                <BacktestPanel backtest={backtest} />
              </div>
            </div>

            {/* Weather Forecasts */}
            <div className="flex flex-col min-h-0">
              <div className="px-2 py-1 border-b border-neutral-800 flex items-center justify-between shrink-0">
                <span className="text-[10px] text-neutral-500 uppercase tracking-wider">天气</span>
                <span className="px-1 py-0.5 text-[8px] font-bold uppercase bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">天气</span>
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto">
                <WeatherPanel forecasts={weatherForecasts} signals={weatherSignals} />
              </div>
            </div>
          </div>
        </div>

        <div
          className="bg-neutral-900/60 hover:bg-green-500/30 cursor-col-resize border-l border-r border-neutral-800"
          onMouseDown={() => {
            dragRef.current = true
            document.body.style.cursor = 'col-resize'
            document.body.style.userSelect = 'none'
          }}
          title="拖拽调整右侧栏宽度"
        />

        {/* ===== RIGHT COLUMN ===== */}
        <div className="flex flex-col min-h-0 overflow-hidden">
          {/* Signals - top portion */}
          <div className="flex flex-col min-h-0" style={{ height: '50%' }}>
            <div className="px-2 py-1 border-b border-neutral-800 flex items-center justify-between shrink-0">
              <span className="text-[10px] text-neutral-500 uppercase tracking-wider">信号</span>
              <div className="flex items-center gap-2">
                {(stats.expired_signal_count ?? 0) > 0 && (
                  <span className="text-[10px] text-neutral-600 tabular-nums">{stats.expired_signal_count} 已过期隐藏</span>
                )}
                {activeSignals.length > 0 && (
                  <span className="text-[10px] text-amber-400 tabular-nums">{activeSignals.length} BTC</span>
                )}
                {weatherSignals.length > 0 && (
                  <span className="text-[10px] text-cyan-400 tabular-nums">{weatherSignals.length} 天气</span>
                )}
              </div>
            </div>
            <div className="flex-1 overflow-y-auto min-h-0">
              <SignalsTable
                signals={activeSignals}
                weatherSignals={weatherSignals}
                onSimulateTrade={(ticker) => tradeMutation.mutate(ticker)}
                isSimulating={tradeMutation.isPending}
                onSignalStatus={(signalId, status, amount) => signalStatusMutation.mutate({ signalId, status, amount })}
                onLiveOrder={(signalId, amount) => liveOrderMutation.mutate({ signalId, amount })}
              />
            </div>
          </div>

          {/* Trades */}
          <div className="flex flex-col min-h-0 border-t border-neutral-800" style={{ height: '50%' }}>
            <div className="px-2 py-1 border-b border-neutral-800 flex items-center justify-between shrink-0">
              <span className="text-[10px] text-neutral-500 uppercase tracking-wider">模拟/交易记录</span>
              <span className="text-[10px] text-neutral-600 tabular-nums">{recentTrades.length}</span>
            </div>
            <div className="flex-1 overflow-y-auto min-h-0">
              <TradesTable trades={recentTrades} />
            </div>
          </div>
        </div>
      </div>

      {/* ===== FOOTER ===== */}
      <footer className="shrink-0 border-t border-neutral-800 px-3 py-0.5 flex items-center justify-between">
        <span className="text-[10px] text-neutral-700 font-mono">
          Open-Meteo | METAR | Polymarket 天气区间
        </span>
        <div className="flex items-center gap-3">
          <RefreshBar interval={10000} />
          <span className="text-[10px] text-neutral-700 font-mono">WeatherBot 信号引擎 + Kalshi 看板框架</span>
          <div className="flex items-center gap-1">
            <div className="w-1.5 h-1.5 rounded-full bg-green-500" />
            <span className="text-[10px] text-neutral-600 font-mono">已连接</span>
          </div>
        </div>
      </footer>
    </div>
  )
}

export default App
