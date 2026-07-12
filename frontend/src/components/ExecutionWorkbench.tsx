import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ExternalLink, FlaskConical, Info, ListChecks, Play, Settings2, ShieldAlert, Square } from 'lucide-react'
import { executePaperOrders, fetchPaperOrders, fetchStrategyProfiles, runPaperValidationTick, startPaperValidation, stopPaperValidation } from '../api'
import type {
  PaperExecutionResult,
  PaperOrderRecord,
  PaperValidationStatus,
  SignalDecisionRecord,
  SignalDecisionSummary,
} from '../types'

interface Props {
  cityKey: string
  targetDate: string
  decisions?: SignalDecisionSummary | null
  validation?: PaperValidationStatus | null
  liveAvailable: boolean
  schedulerRunning: boolean
}

type QueueItem = {
  key: string
  strategy: string
  ladderGroupId?: string
  decisions: SignalDecisionRecord[]
}

const STRATEGY_OPTIONS = [
  { key: 'single_bucket_ev', label: '单桶最高温', help: '只买模型优势最大的单个温度桶，持有至结算。' },
  { key: 'ladder_grid', label: '相邻三桶阶梯', help: '中心桶与左右相邻桶作为原子组合，整组买入或整组跳过。' },
  { key: 'tail_buying', label: '低价尾部', help: '仅观察/买入价格较低且概率差足够大的尾部桶。' },
] as const

const REASON_LABELS: Record<string, string> = {
  paper_gate_not_passed: '模拟闸门未通过',
  insufficient_bias_samples: '历史校准样本不足',
  spread_too_wide: '买卖价差过大',
  settlement_unverified: '结算规则未核验',
  market_probability_missing: '盘口价格缺失',
  model_probability_missing: '模型概率缺失',
  bucket_not_strict_match: '市场温度桶未严格匹配',
  orderbook_stale: '盘口数据过期',
  order_min_size_missing: '最小下单份额缺失',
  tick_size_missing: '价格步长缺失',
  low_price_tail_bucket: '低价尾部桶风险',
  live_trading_disabled: '实盘已锁定',
}

function reasonText(reason?: string | null) {
  if (!reason) return '暂无'
  return REASON_LABELS[reason] ?? reason.split('_').join(' ')
}

function money(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return `$${Number(value).toFixed(2)}`
}

function percent(value?: number | null, signed = false) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  const number = Number(value) * 100
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(1)}%`
}

function cents(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return `${(Number(value) * 100).toFixed(1)}¢`
}

function bucketLabel(decision: SignalDecisionRecord) {
  const unit = decision.model_distribution?.unit ?? ''
  const low = decision.bucket_lower
  const high = decision.bucket_upper
  if (decision.bucket_direction === 'or_below' || decision.bucket_direction === 'below' || low === null || low === undefined) {
    return `${high ?? '--'}°${unit} 或以下`
  }
  if (decision.bucket_direction === 'or_above' || decision.bucket_direction === 'above' || high === null || high === undefined) {
    return `${low ?? '--'}°${unit} 或以上`
  }
  if (decision.bucket_direction === 'exact' || low === high) return `${low}°${unit}`
  return `${low}–${high}°${unit}`
}

function strategyLabel(strategy: string) {
  if (strategy === 'ladder_grid') return '三桶阶梯'
  if (strategy === 'tail_buying') return '尾部价值'
  return '单桶 EV'
}

function groupDecisions(rows: SignalDecisionRecord[]): QueueItem[] {
  const result: QueueItem[] = []
  const ladderGroups = new Map<string, SignalDecisionRecord[]>()
  for (const decision of rows) {
    const groupId = String(decision.ladder_group_id ?? '')
    if (groupId) {
      ladderGroups.set(groupId, [...(ladderGroups.get(groupId) ?? []), decision])
      continue
    }
    result.push({
      key: decision.decision_id,
      strategy: decision.strategy_name ?? 'single_bucket_ev',
      decisions: [decision],
    })
  }
  for (const [ladderGroupId, decisions] of ladderGroups) {
    result.push({ key: ladderGroupId, ladderGroupId, strategy: 'ladder_grid', decisions })
  }
  return result.sort((a, b) => {
    const aAllowed = a.decisions.every(row => row.paper_allowed && row.paper_decision === 'buy')
    const bAllowed = b.decisions.every(row => row.paper_allowed && row.paper_decision === 'buy')
    if (aAllowed !== bAllowed) return aAllowed ? -1 : 1
    return Number(b.decisions[0]?.edge ?? -1) - Number(a.decisions[0]?.edge ?? -1)
  })
}

function resultMessage(result?: PaperExecutionResult | null) {
  if (!result) return null
  if (result.status === 'duplicate') return '该策略已存在模拟订单，没有重复买入。'
  if (result.ok && result.dry_run) return `检查通过：${result.requested ?? result.results?.length ?? 1} 组策略可模拟成交。`
  if (result.ok) return `模拟完成：成交 ${result.executed ?? result.results?.length ?? 1} 组。`
  return `未执行：${reasonText(result.reason)}`
}

function DecisionRow({
  item,
  pending,
  onExecute,
}: {
  item: QueueItem
  pending: boolean
  onExecute: (decisionId: string, amount: number | undefined, dryRun: boolean) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const first = item.decisions[0]
  const eligible = item.decisions.length === (item.ladderGroupId ? 3 : 1)
    && item.decisions.every(row => row.paper_allowed && row.paper_decision === 'buy')
  const suggested = item.decisions.reduce((sum, row) => sum + Number(row.position_size_usd ?? 0), 0)
  const [amount, setAmount] = useState(suggested > 0 ? suggested.toFixed(2) : '2.00')
  const reasons = [...new Set(item.decisions.flatMap(row => row.gate_reasons ?? row.reasons ?? []))]
  const eventUrl = String(first.evidence_links?.event_url ?? '')

  return (
    <div className="border-b border-neutral-800/80">
      <button
        type="button"
        onClick={() => setExpanded(value => !value)}
        className="grid w-full grid-cols-[16px_1fr_62px] gap-2 px-3 py-2 text-left hover:bg-neutral-950/70"
      >
        <ChevronDown className={`mt-0.5 h-3.5 w-3.5 text-neutral-600 transition ${expanded ? 'rotate-180' : ''}`} />
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-1">
            <span className="text-[11px] font-medium text-neutral-100">{strategyLabel(item.strategy)}</span>
            <span className={`border px-1 py-0.5 text-[9px] ${eligible ? 'border-green-500/30 text-green-300' : 'border-amber-500/30 text-amber-300'}`}>
              {eligible ? '可模拟' : '观察'}
            </span>
          </span>
          <span className="mt-1 block truncate text-[10px] text-neutral-500">
            {item.ladderGroupId ? item.decisions.map(bucketLabel).join(' / ') : bucketLabel(first)}
          </span>
        </span>
        <span className="text-right">
          <span className={`block tabular-nums text-[11px] ${Number(first.edge ?? 0) > 0 ? 'text-green-400' : 'text-neutral-500'}`}>
            {percent(first.edge, true)}
          </span>
          <span className="block text-[9px] text-neutral-600">Edge</span>
        </span>
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-neutral-800 bg-neutral-950/50 px-3 py-3">
          <div className="space-y-1">
            {item.decisions.map(decision => (
              <div key={decision.decision_id} className="grid grid-cols-[1fr_54px_54px] gap-2 text-[10px]">
                <span className="truncate text-neutral-300">{bucketLabel(decision)}</span>
                <span className="text-right tabular-nums text-cyan-300">{percent(decision.model_probability)}</span>
                <span className="text-right tabular-nums text-neutral-400">{cents(decision.market_ask)}</span>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-3 gap-1 border-y border-neutral-800 py-2 text-[9px] text-neutral-500">
            <span>模型概率</span><span className="text-center">Ask</span><span className="text-right">Kelly 建议 {money(suggested)}</span>
          </div>
          {!eligible && (
            <div className="text-[10px] leading-relaxed text-amber-300">
              {reasonText(first.blocked_reason_primary ?? reasons[0])}
              {reasons.length > 1 && (
                <details className="mt-1 text-neutral-500">
                  <summary className="cursor-pointer">全部阻塞原因</summary>
                  <div className="mt-1">{reasons.map(reasonText).join('；')}</div>
                </details>
              )}
            </div>
          )}
          <label className="grid grid-cols-[1fr_88px] items-center gap-2 text-[10px] text-neutral-500">
            模拟金额{item.ladderGroupId ? '（整组）' : ''}
            <input
              type="number"
              min="0"
              step="0.01"
              value={amount}
              onChange={event => setAmount(event.target.value)}
              className="h-8 w-full border border-neutral-700 bg-black px-2 text-right tabular-nums text-neutral-200"
            />
          </label>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={!eligible || pending}
              onClick={() => onExecute(first.decision_id, Number(amount) || undefined, true)}
              className="min-h-9 border border-neutral-700 text-[10px] text-neutral-300 hover:bg-neutral-900 disabled:opacity-30"
            >
              检查成交条件
            </button>
            <button
              type="button"
              disabled={!eligible || pending}
              onClick={() => onExecute(first.decision_id, Number(amount) || undefined, false)}
              className="min-h-9 border border-cyan-500/40 bg-cyan-500/10 text-[10px] text-cyan-200 hover:bg-cyan-500/15 disabled:opacity-30"
            >
              模拟买入
            </button>
          </div>
          {eventUrl && (
            <a href={eventUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[10px] text-cyan-400">
              <ExternalLink className="h-3 w-3" /> 打开 Polymarket
            </a>
          )}
        </div>
      )}
    </div>
  )
}

function OrderRow({ order }: { order: PaperOrderRecord }) {
  const [expanded, setExpanded] = useState(false)
  const pnl = Number(order.realized_pnl ?? order.unrealized_pnl ?? 0)
  return (
    <div className="border-b border-neutral-800/80">
      <button type="button" onClick={() => setExpanded(value => !value)} className="grid w-full grid-cols-[16px_1fr_68px] gap-2 px-3 py-2 text-left hover:bg-neutral-950/70">
        <ChevronDown className={`mt-0.5 h-3.5 w-3.5 text-neutral-600 transition ${expanded ? 'rotate-180' : ''}`} />
        <span className="min-w-0">
          <span className="block truncate text-[11px] text-neutral-200">{strategyLabel(order.strategy_name ?? 'single_bucket_ev')} · {order.bucket_key || '温度桶'}</span>
          <span className="mt-0.5 block text-[9px] text-neutral-600">{order.fill_status ?? order.status} · {money(order.filled_amount ?? order.requested_amount)}</span>
        </span>
        <span className={`text-right text-[11px] tabular-nums ${pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{money(pnl)}</span>
      </button>
      {expanded && (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 border-t border-neutral-800 bg-neutral-950/50 px-3 py-3 text-[10px] text-neutral-500">
          <span>限价</span><span className="text-right text-neutral-300">{cents(order.limit_price)}</span>
          <span>平均成交</span><span className="text-right text-neutral-300">{cents(order.average_fill_price)}</span>
          <span>当前估值</span><span className="text-right text-neutral-300">{cents(order.mark_price)}</span>
          <span>成交份额</span><span className="text-right text-neutral-300">{Number(order.filled_shares ?? 0).toFixed(2)}</span>
          <span>生命周期</span><span className="text-right text-neutral-300">{order.lifecycle_status ?? '--'}</span>
          {order.failure_reason && <><span>失败原因</span><span className="text-right text-amber-300">{reasonText(order.failure_reason)}</span></>}
          {order.event_url && (
            <a href={order.event_url} target="_blank" rel="noreferrer" className="col-span-2 mt-1 inline-flex items-center gap-1 text-cyan-400">
              <ExternalLink className="h-3 w-3" /> 对应市场
            </a>
          )}
        </div>
      )}
    </div>
  )
}

export function ExecutionWorkbench({ cityKey, targetDate, decisions, validation, liveAvailable, schedulerRunning }: Props) {
  const queryClient = useQueryClient()
  const [view, setView] = useState<'queue' | 'orders'>('queue')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [bankroll, setBankroll] = useState('40')
  const [maxPerTrade, setMaxPerTrade] = useState('2')
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>(['single_bucket_ev'])
  const [lastResult, setLastResult] = useState<PaperExecutionResult | null>(null)
  const validationActive = validation?.status === 'active'
  const profilesQuery = useQuery({
    queryKey: ['strategy-profiles'],
    queryFn: fetchStrategyProfiles,
    staleTime: 30000,
  })
  const activePaperProfile = profilesQuery.data?.profiles.find(profile => profile.active_scopes.includes('paper_default'))
  const selectedRevisionId = validation?.strategy_revision_id ?? activePaperProfile?.revision_id ?? ''
  useEffect(() => {
    if (!validationActive) return
    setBankroll(String(validation?.bankroll_usd ?? 40))
    setMaxPerTrade(String(validation?.max_per_trade_usd ?? 2))
    if (validation?.strategies?.length) setSelectedStrategies(validation.strategies)
  }, [validationActive, validation?.bankroll_usd, validation?.max_per_trade_usd, validation?.strategies])
  const queue = useMemo(() => {
    const rows = decisions?.decisions ?? []
    const latestIssuedAt = rows.reduce(
      (latest, row) => String(row.issued_at ?? '') > latest ? String(row.issued_at ?? '') : latest,
      '',
    )
    const latestRows = latestIssuedAt ? rows.filter(row => row.issued_at === latestIssuedAt) : rows
    const revisionRows = selectedRevisionId
      ? latestRows.filter(row => row.strategy_revision_id === selectedRevisionId)
      : []
    return groupDecisions(revisionRows.filter(row => selectedStrategies.includes(row.strategy_name ?? 'single_bucket_ev')))
  }, [decisions, selectedRevisionId, selectedStrategies])
  const eligibleCount = queue.filter(item => item.decisions.length === (item.ladderGroupId ? 3 : 1)
    && item.decisions.every(row => row.paper_allowed && row.paper_decision === 'buy')).length
  const ordersQuery = useQuery({
    queryKey: ['paper-orders', cityKey, targetDate],
    queryFn: () => fetchPaperOrders(cityKey, targetDate, 100),
    enabled: Boolean(cityKey && targetDate),
    refetchInterval: 30000,
  })
  const executeMutation = useMutation({
    mutationFn: (payload: { decisionId?: string; amount?: number; dryRun: boolean }) => executePaperOrders({
      decisionId: payload.decisionId,
      city: payload.decisionId ? undefined : cityKey,
      targetDate: payload.decisionId ? undefined : targetDate,
      amount: payload.amount,
      limit: 100,
      dryRun: payload.dryRun,
      strategies: selectedStrategies,
    }),
    onSuccess: result => {
      setLastResult(result)
      queryClient.invalidateQueries({ queryKey: ['paper-orders', cityKey, targetDate] })
      queryClient.invalidateQueries({ queryKey: ['paper-validation-status'] })
    },
    onError: error => setLastResult({ ok: false, reason: error instanceof Error ? error.message : 'request_failed' }),
  })
  const validationMutation = useMutation({
    mutationFn: async (action: 'start' | 'stop') => {
      if (action === 'stop') return stopPaperValidation()
      const started = await startPaperValidation({
        bankroll_usd: Math.max(1, Number(bankroll) || 40),
        max_per_trade_usd: Math.max(0.1, Number(maxPerTrade) || 2),
        duration_days: 14,
        daily_max_usd: Math.max(1, Math.min(Number(bankroll) || 40, 10)),
        max_open_positions: 5,
        max_orders_per_day: 5,
        decision_max_age_minutes: 30,
        strategies: selectedStrategies,
        strategy_revision_id: selectedRevisionId,
      })
      await runPaperValidationTick()
      return started
    },
    onSuccess: () => {
      setLastResult(null)
      queryClient.invalidateQueries({ queryKey: ['paper-validation-status'] })
      queryClient.invalidateQueries({ queryKey: ['paper-orders'] })
    },
    onError: error => setLastResult({ ok: false, reason: error instanceof Error ? error.message : 'paper_validation_request_failed' }),
  })
  const summary = ordersQuery.data
  const toggleStrategy = (strategy: string) => {
    if (validationActive) return
    setSelectedStrategies(current => current.includes(strategy)
      ? (current.length > 1 ? current.filter(item => item !== strategy) : current)
      : [...current, strategy])
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b border-neutral-800 bg-black/95 px-3 py-2">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="text-sm font-medium text-neutral-100">模拟交易台</div>
            <div className="mt-0.5 text-[10px] text-neutral-600">Kelly 分配 → 盘口成交 → Polymarket 结算</div>
          </div>
          <span className={`border px-1.5 py-0.5 text-[9px] ${liveAvailable ? 'border-green-500/30 text-green-300' : 'border-amber-500/30 text-amber-300'}`}>
            {liveAvailable ? '实盘待验收' : '实盘锁定'}
          </span>
        </div>
        <div className="mt-1 flex items-center justify-between gap-2 text-[9px] text-neutral-600">
          <span title={selectedRevisionId || '未加载'}>策略版本 {selectedRevisionId ? selectedRevisionId.slice(0, 12) : '--'}</span>
          <a href="/developer" className="text-cyan-500 hover:text-cyan-300">开发者模式</a>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-1 text-[10px]">
          <div className="border border-neutral-800 p-2"><div className="text-neutral-600">可模拟策略</div><div className="mt-1 tabular-nums text-neutral-200">{eligibleCount} / {queue.length}</div></div>
          <div className="border border-neutral-800 p-2"><div className="text-neutral-600">订单 / 已结算</div><div className="mt-1 tabular-nums text-neutral-200">{summary?.count ?? 0} / {summary?.resolved_orders ?? 0}</div></div>
          <div className="border border-neutral-800 p-2"><div className="text-neutral-600">浮动盈亏</div><div className={`mt-1 tabular-nums ${Number(summary?.unrealized_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>{money(summary?.unrealized_pnl)}</div></div>
          <div className="border border-neutral-800 p-2"><div className="text-neutral-600">本金 / 可用</div><div className="mt-1 tabular-nums text-neutral-200">{money(validation?.bankroll_usd ?? Number(bankroll))} / {money(validation?.cash_available_usd ?? Number(bankroll))}</div></div>
        </div>
        <button type="button" onClick={() => setSettingsOpen(value => !value)} className="mt-2 inline-flex min-h-8 w-full items-center justify-between border border-neutral-800 px-2 text-[10px] text-neutral-400 hover:bg-neutral-950">
          <span className="inline-flex items-center gap-1"><Settings2 className="h-3.5 w-3.5" /> 自动模拟设置</span>
          <ChevronDown className={`h-3.5 w-3.5 transition ${settingsOpen ? 'rotate-180' : ''}`} />
        </button>
        {settingsOpen && (
          <div className="mt-2 space-y-2 border border-neutral-800 bg-neutral-950/60 p-2">
            <div className="grid grid-cols-2 gap-2">
              <label className="text-[9px] text-neutral-500">模拟本金（USD）<input disabled={validationActive} type="number" min="1" step="1" value={bankroll} onChange={event => setBankroll(event.target.value)} className="mt-1 h-8 w-full border border-neutral-700 bg-black px-2 text-right text-[11px] text-neutral-200 disabled:opacity-60" /></label>
              <label className="text-[9px] text-neutral-500">单笔上限（USD）<input disabled={validationActive} type="number" min="0.1" step="0.1" value={maxPerTrade} onChange={event => setMaxPerTrade(event.target.value)} className="mt-1 h-8 w-full border border-neutral-700 bg-black px-2 text-right text-[11px] text-neutral-200 disabled:opacity-60" /></label>
            </div>
            <fieldset disabled={validationActive} className="space-y-1">
              <legend className="mb-1 text-[9px] text-neutral-500">入场策略（可组合）</legend>
              {STRATEGY_OPTIONS.map(option => (
                <label key={option.key} title={option.help} className="flex min-h-7 items-center gap-2 border border-neutral-800 px-2 text-[10px] text-neutral-300">
                  <input type="checkbox" checked={selectedStrategies.includes(option.key)} onChange={() => toggleStrategy(option.key)} />
                  <span>{option.label}</span><Info className="ml-auto h-3 w-3 text-neutral-600" />
                </label>
              ))}
            </fieldset>
            <label className="block text-[9px] text-neutral-500">退出方式
              <select className="mt-1 h-8 w-full border border-neutral-700 bg-black px-2 text-[10px] text-neutral-200" value="hold_to_settlement" disabled>
                <option value="hold_to_settlement">持有至 Polymarket 结算（当前可用）</option>
              </select>
            </label>
            <div className="text-[9px] leading-relaxed text-neutral-600">信息差止盈需要可靠的历史盘口回放和 SELL 成交模拟，尚未通过验证，因此不伪装成可用选项。</div>
            {!schedulerRunning && <div className="border border-amber-500/20 bg-amber-500/5 px-2 py-1.5 text-[9px] text-amber-300">请先启动顶部调度器；自动模拟和结算由后端定时任务驱动。</div>}
            <button
              type="button"
              disabled={validationMutation.isPending || (!validationActive && (!schedulerRunning || selectedStrategies.length === 0))}
              onClick={() => validationMutation.mutate(validationActive ? 'stop' : 'start')}
              className={`inline-flex min-h-9 w-full items-center justify-center gap-1 border text-[10px] disabled:opacity-30 ${validationActive ? 'border-red-500/30 text-red-300 hover:bg-red-500/10' : 'border-green-500/30 bg-green-500/10 text-green-200 hover:bg-green-500/15'}`}
            >
              {validationActive ? <><Square className="h-3 w-3" /> 停止自动模拟</> : <><Play className="h-3.5 w-3.5" /> 一键模拟</>}
            </button>
          </div>
        )}
      </div>

      <div className="grid shrink-0 grid-cols-2 border-b border-neutral-800" role="tablist" aria-label="模拟策略与订单">
        <button type="button" role="tab" aria-selected={view === 'queue'} onClick={() => setView('queue')} className={`min-h-10 border-r border-neutral-800 px-3 text-left text-[11px] ${view === 'queue' ? 'bg-cyan-500/10 text-cyan-200' : 'text-neutral-500'}`}>
          <span className="inline-flex items-center gap-1"><ListChecks className="h-3.5 w-3.5" /> 策略队列</span>
        </button>
        <button type="button" role="tab" aria-selected={view === 'orders'} onClick={() => setView('orders')} className={`min-h-10 px-3 text-left text-[11px] ${view === 'orders' ? 'bg-amber-500/10 text-amber-200' : 'text-neutral-500'}`}>
          <span className="inline-flex items-center gap-1"><FlaskConical className="h-3.5 w-3.5" /> 模拟订单</span>
        </button>
      </div>

      {lastResult && (
        <div className={`shrink-0 border-b px-3 py-2 text-[10px] ${lastResult.ok ? 'border-green-500/20 bg-green-500/5 text-green-300' : 'border-amber-500/20 bg-amber-500/5 text-amber-300'}`}>
          {resultMessage(lastResult)}
        </div>
      )}

      {view === 'queue' ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between border-b border-neutral-800 px-3 py-2">
            <span className="text-[10px] text-neutral-500">{cityKey} · {targetDate}</span>
            <button
              type="button"
              disabled={eligibleCount === 0 || executeMutation.isPending}
              onClick={() => executeMutation.mutate({ dryRun: false })}
              className="border border-cyan-500/30 px-2 py-1 text-[10px] text-cyan-200 hover:bg-cyan-500/10 disabled:opacity-30"
            >
              模拟当前可用策略
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {queue.length ? queue.map(item => (
              <DecisionRow
                key={item.key}
                item={item}
                pending={executeMutation.isPending}
                onExecute={(decisionId, amount, dryRun) => executeMutation.mutate({ decisionId, amount, dryRun })}
              />
            )) : (
              <div className="px-3 py-8 text-center text-[11px] text-neutral-600">当前城市和日期暂无策略决策</div>
            )}
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {ordersQuery.isLoading ? (
            <div className="px-3 py-8 text-center text-[11px] text-neutral-600">读取模拟订单…</div>
          ) : summary?.orders?.length ? summary.orders.map(order => <OrderRow key={order.id} order={order} />) : (
            <div className="px-3 py-8 text-center text-[11px] text-neutral-600">暂无模拟订单</div>
          )}
        </div>
      )}

      {!liveAvailable && (
        <div className="shrink-0 border-t border-neutral-800 px-3 py-2 text-[10px] text-neutral-600">
          <span className="inline-flex items-center gap-1"><ShieldAlert className="h-3 w-3 text-amber-400" /> 所有操作仅写入本地模拟订单，不会提交实盘。</span>
        </div>
      )}
    </div>
  )
}
