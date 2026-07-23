import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ExternalLink, FlaskConical, Info, ListChecks, Play, Settings2, ShieldAlert, Square } from 'lucide-react'
import { createStrategyProfile, executePaperOrders, fetchPaperOrders, fetchStrategyProfiles, runPaperValidationTick, startPaperValidation, stopPaperValidation } from '../api'
import { EquityChart } from './EquityChart'
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
  onOpenDeveloperSettings: () => void
  language?: 'zh' | 'en'
}

type QueueItem = {
  key: string
  strategy: string
  ladderGroupId?: string
  decisions: SignalDecisionRecord[]
}

type ExitMode = 'hold_to_settlement' | 'model_guarded' | 'model_guarded_take_profit'

const STRATEGY_OPTIONS = [
  { key: 'core_modal_v1', zh: '动态核心温度桶', en: 'Dynamic modal bucket', helpZh: '仅选择模型概率最高的前两个温度桶；未满 20 个无泄漏样本的模型不参与权重，成熟模型按近期误差动态分配权重。', helpEn: 'Only consider the top two model buckets; models with fewer than 20 leakage-free pairs receive zero weight, while mature models are weighted by recent accuracy.' },
  { key: 'single_bucket_ev', zh: '单桶最高温', en: 'Single bucket', helpZh: '仅当盘口与风控闸门全部通过时，模拟买入概率优势最大的单个温度桶并持有至结算。', helpEn: 'Only after all book and risk gates pass, paper-buy the single bucket with the strongest probability advantage and hold to settlement.' },
  { key: 'ladder_grid', zh: '相邻三桶阶梯', en: 'Three-bucket ladder', helpZh: '中心桶与左右相邻桶作为原子组合，整组买入或整组跳过。', helpEn: 'Treat the center bucket and its two neighbors as an atomic group.' },
  { key: 'tail_buying', zh: '低价尾部', en: 'Low-price tail', helpZh: '仅观察/买入价格较低且概率差足够大的尾部桶。', helpEn: 'Watch or buy low-price tail buckets only when the probability gap is large enough.' },
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
  paper_validation_run_not_active: '模拟账户未启动或已停止',
  strategy_revision_mismatch: '模拟账户与策略版本不一致',
  edge_below_min_after_reprice: '最新盘口下的概率优势已不足',
  spread_too_wide_after_reprice: '最新盘口价差过大',
  tail_ask_above_max_after_reprice: '尾部桶最新买价超过策略上限',
  orderbook_timestamp_missing_or_invalid: '最新盘口时间无效',
}

const REASON_LABELS_EN: Record<string, string> = {
  paper_gate_not_passed: 'Paper gate not passed',
  insufficient_bias_samples: 'Insufficient calibration samples',
  spread_too_wide: 'Bid/ask spread is too wide',
  settlement_unverified: 'Settlement rule is unverified',
  market_probability_missing: 'Market price is missing',
  model_probability_missing: 'Model probability is missing',
  bucket_not_strict_match: 'Temperature bucket is not a strict match',
  orderbook_stale: 'Order book is stale',
  order_min_size_missing: 'Minimum order size is missing',
  tick_size_missing: 'Tick size is missing',
  low_price_tail_bucket: 'Low-price tail bucket risk',
  live_trading_disabled: 'Live trading is locked',
  paper_validation_run_not_active: 'Paper account is not active',
  strategy_revision_mismatch: 'Paper account and strategy version differ',
  edge_below_min_after_reprice: 'Model edge is too small at the latest price',
  spread_too_wide_after_reprice: 'Latest spread is too wide',
  tail_ask_above_max_after_reprice: 'Latest tail ask exceeds the strategy cap',
  orderbook_timestamp_missing_or_invalid: 'Order-book timestamp is invalid',
}

function tx(language: 'zh' | 'en', zh: string, en: string) {
  return language === 'zh' ? zh : en
}

function normalizeExitMode(value?: string): ExitMode {
  if (value === 'model_guarded' || value === 'model_guarded_take_profit') return value
  return 'hold_to_settlement'
}

function reasonText(reason?: string | null, language: 'zh' | 'en' = 'zh') {
  if (!reason) return tx(language, '暂无', 'None')
  return (language === 'zh' ? REASON_LABELS[reason] : REASON_LABELS_EN[reason]) ?? reason.split('_').join(' ')
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

function probabilityPoints(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  const points = Number(value) * 100
  return `${points > 0 ? '+' : ''}${points.toFixed(1)}pp`
}

function cents(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return `${(Number(value) * 100).toFixed(1)}¢`
}

function formatTimestamp(value?: string | null, language: 'zh' | 'en' = 'zh') {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function quoteAge(value?: number | null, language: 'zh' | 'en' = 'zh') {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  const seconds = Math.max(0, Number(value))
  if (seconds < 60) return tx(language, `${Math.round(seconds)} 秒前`, `${Math.round(seconds)}s ago`)
  if (seconds < 3600) return tx(language, `${Math.round(seconds / 60)} 分钟前`, `${Math.round(seconds / 60)}m ago`)
  return tx(language, `${(seconds / 3600).toFixed(1)} 小时前`, `${(seconds / 3600).toFixed(1)}h ago`)
}

function cityLabel(value?: string) {
  return String(value || '--').split('-').map(part => part ? `${part[0].toUpperCase()}${part.slice(1)}` : part).join(' ')
}

function bucketLabel(decision: SignalDecisionRecord, language: 'zh' | 'en' = 'zh') {
  const unit = decision.model_distribution?.unit ?? ''
  const low = decision.bucket_lower
  const high = decision.bucket_upper
  if (decision.bucket_direction === 'or_below' || decision.bucket_direction === 'below' || low === null || low === undefined) {
    return tx(language, `${high ?? '--'}°${unit} 或以下`, `${high ?? '--'}°${unit} or below`)
  }
  if (decision.bucket_direction === 'or_above' || decision.bucket_direction === 'above' || high === null || high === undefined) {
    return tx(language, `${low ?? '--'}°${unit} 或以上`, `${low ?? '--'}°${unit} or above`)
  }
  if (decision.bucket_direction === 'exact' || low === high) return `${low}°${unit}`
  return `${low}–${high}°${unit}`
}

function strategyLabel(strategy: string, language: 'zh' | 'en' = 'zh') {
  if (strategy === 'core_modal_v1') return tx(language, '动态核心温度桶', 'Dynamic modal bucket')
  if (strategy === 'ladder_grid') return tx(language, '三桶阶梯', 'Three-bucket ladder')
  if (strategy === 'tail_buying') return tx(language, '尾部价值', 'Tail value')
  return tx(language, '单桶 EV', 'Single-bucket EV')
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
    const aAllowed = queueItemEligible(a)
    const bAllowed = queueItemEligible(b)
    if (aAllowed !== bAllowed) return aAllowed ? -1 : 1
    return Number(b.decisions[0]?.edge ?? -1) - Number(a.decisions[0]?.edge ?? -1)
  })
}

function decisionBookIsStale(row: SignalDecisionRecord) {
  return (row.cautions ?? []).includes('stale_book') || Number(row.book_age_seconds ?? 0) > 300
}

function decisionMeetsOrderMinimum(row: SignalDecisionRecord) {
  const ask = Number(row.market_ask ?? 0)
  const minimumShares = Number(row.order_min_size ?? 0)
  const suggestedAmount = Number(row.position_size_usd ?? 0)
  return ask > 0 && minimumShares > 0 && suggestedAmount / ask + 1e-9 >= minimumShares
}

function queueItemEligible(item: QueueItem) {
  return !item.decisions.some(decisionBookIsStale)
    && item.decisions.length === (item.ladderGroupId ? 3 : 1)
    && item.decisions.every(row => row.paper_allowed && row.paper_decision === 'buy' && decisionMeetsOrderMinimum(row))
}

function resultMessage(result?: PaperExecutionResult | null, language: 'zh' | 'en' = 'zh') {
  if (!result) return null
  if (result.status === 'duplicate') return tx(language, '该策略已存在模拟订单，没有重复买入。', 'A paper order already exists for this strategy; no duplicate was placed.')
  if (result.status === 'capacity_reached') return tx(language, '模拟账户已达到今日额度或持仓上限。', 'The paper account reached its daily or position limit.')
  if (result.status === 'no_executable_candidates') return tx(language, `当前盘口不再满足策略：${reasonText(result.reason, language)}`, `The latest book no longer meets the strategy: ${reasonText(result.reason, language)}`)
  if (result.status === 'no_fresh_candidates') return tx(language, '当前批次没有可执行的新策略。', 'No fresh executable strategies in this batch.')
  if (result.ok && result.dry_run) return tx(language, `检查通过：${result.requested ?? result.results?.length ?? 1} 组策略可模拟成交。`, `Check passed: ${result.requested ?? result.results?.length ?? 1} strategy groups can be filled in paper mode.`)
  if (result.ok && result.status === 'dry_run') return tx(language, `检查通过：${result.executed ?? 0} 组策略符合当前账户与盘口约束。`, `Check passed: ${result.executed ?? 0} strategy groups meet account and book constraints.`)
  if (result.ok) return tx(language, `模拟完成：成交 ${result.executed ?? result.results?.length ?? 1} 组。`, `Paper execution complete: ${result.executed ?? result.results?.length ?? 1} groups filled.`)
  return tx(language, `未执行：${reasonText(result.reason, language)}`, `Not executed: ${reasonText(result.reason, language)}`)
}

function DecisionRow({
  item,
  pending,
  accountActive,
  onExecute,
  language,
}: {
  item: QueueItem
  pending: boolean
  accountActive: boolean
  onExecute: (decisionId: string, dryRun: boolean) => void
  language: 'zh' | 'en'
}) {
  const [expanded, setExpanded] = useState(false)
  const first = item.decisions[0]
  const staleBook = item.decisions.some(decisionBookIsStale)
  const eligible = queueItemEligible(item)
  const suggested = item.decisions.reduce((sum, row) => sum + Number(row.position_size_usd ?? 0), 0)
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
            <span className="text-[11px] font-medium text-neutral-100">{strategyLabel(item.strategy, language)}</span>
            <span className={`border px-1 py-0.5 text-[9px] ${eligible ? 'border-green-500/30 text-green-300' : 'border-amber-500/30 text-amber-300'}`}>
              {eligible ? tx(language, '可模拟', 'Eligible') : tx(language, '观察', 'Watch')}
            </span>
          </span>
          <span className="mt-1 block truncate text-[10px] text-neutral-500">
            {item.ladderGroupId ? item.decisions.map(row => bucketLabel(row, language)).join(' / ') : bucketLabel(first, language)}
          </span>
        </span>
        <span className="text-right">
          <span
            className={`block tabular-nums text-[11px] ${eligible && Number(first.edge ?? 0) > 0 ? 'text-green-400' : 'text-neutral-500'}`}
            title={tx(language, '模型概率减去当前 YES 卖一价；正数还必须通过盘口、深度、最小订单和风控复核。', 'Model probability minus current YES best ask; a positive value must still pass quote, depth, minimum-size, and risk checks.')}
          >
            {staleBook ? '--' : probabilityPoints(first.edge)}
          </span>
          {staleBook && <span className="block text-[9px] text-neutral-600">{tx(language, '盘口过期', 'Stale book')}</span>}
        </span>
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-neutral-800 bg-neutral-950/50 px-3 py-3">
          <div className="space-y-1">
            {item.decisions.map(decision => (
              <div key={decision.decision_id} className="grid grid-cols-[1fr_54px_54px] gap-2 text-[10px]">
                <span className="truncate text-neutral-300">{bucketLabel(decision, language)}</span>
                <span className="text-right tabular-nums text-cyan-300">{percent(decision.model_probability)}</span>
                <span className="text-right tabular-nums text-neutral-400">{cents(decision.market_ask)}</span>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-3 gap-1 border-y border-neutral-800 py-2 text-[9px] text-neutral-500">
            <span>{tx(language, '模型概率', 'Model probability')}</span><span className="text-center">Ask</span><span className="text-right">{tx(language, 'Kelly 建议', 'Kelly size')} {money(suggested)}</span>
          </div>
          {!eligible && (
            <div className="text-[10px] leading-relaxed text-amber-300">
              {reasonText(first.blocked_reason_primary ?? reasons[0], language)}
              {reasons.length > 1 && (
                <details className="mt-1 text-neutral-500">
                  <summary className="cursor-pointer">{tx(language, '全部阻塞原因', 'All gate reasons')}</summary>
                  <div className="mt-1">{reasons.map(reason => reasonText(reason, language)).join(language === 'zh' ? '；' : '; ')}</div>
                </details>
              )}
            </div>
          )}
          <div className="flex items-center justify-between border border-neutral-800 px-2 py-2 text-[10px] text-neutral-500">
            <span>{tx(language, '账户将按 Kelly 与风控自动分配', 'The account allocates using Kelly and risk limits')}</span>
            <span className="tabular-nums text-neutral-300">{tx(language, '建议', 'Suggested')} {money(suggested)}</span>
          </div>
          {!accountActive && <div className="text-[10px] text-amber-300">{tx(language, '请先启动上方模拟账户，再检查或执行该策略。', 'Start the paper account before checking or executing this strategy.')}</div>}
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={!eligible || !accountActive || pending}
              onClick={() => onExecute(first.decision_id, true)}
              className="min-h-9 border border-neutral-700 text-[10px] text-neutral-300 hover:bg-neutral-900 disabled:opacity-30"
            >
              {tx(language, '检查成交条件', 'Check fill conditions')}
            </button>
            <button
              type="button"
              disabled={!eligible || !accountActive || pending}
              onClick={() => onExecute(first.decision_id, false)}
              className="min-h-9 border border-cyan-500/40 bg-cyan-500/10 text-[10px] text-cyan-200 hover:bg-cyan-500/15 disabled:opacity-30"
            >
              {tx(language, '模拟买入', 'Paper buy')}
            </button>
          </div>
          {eventUrl && (
            <a href={eventUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[10px] text-cyan-400">
              <ExternalLink className="h-3 w-3" /> {tx(language, '打开 Polymarket', 'Open Polymarket')}
            </a>
          )}
        </div>
      )}
    </div>
  )
}

function OrderRow({ order, language }: { order: PaperOrderRecord; language: 'zh' | 'en' }) {
  const [expanded, setExpanded] = useState(false)
  const settled = order.pnl_kind === 'realized' || order.lifecycle_status === 'settled'
  const exited = order.pnl_kind === 'realized_exit' || order.lifecycle_status === 'exited'
  const closed = settled || exited
  const pnl = Number(order.pnl_value ?? (closed ? order.realized_pnl : order.unrealized_pnl) ?? 0)
  const pnlPositive = pnl >= 0
  const bucket = order.bucket_label || order.bucket_key || tx(language, '温度桶', 'Temperature bucket')
  const statusLabel = closed
    ? (exited ? tx(language, '已保护退出', 'Guarded exit') : tx(language, '已结算', 'Settled'))
    : order.lifecycle_status === 'open'
      ? tx(language, '持仓中', 'Open')
      : tx(language, '未成交', 'Not filled')
  const pnlLabel = closed
    ? (pnlPositive ? tx(language, '盈利', 'Profit') : tx(language, '亏损', 'Loss'))
    : (pnlPositive ? tx(language, '浮盈', 'Unrealized gain') : tx(language, '浮亏', 'Unrealized loss'))
  const sizing = order.sizing_snapshot ?? {}
  const exitDetails = order.exit_details ?? {}
  const exitTrigger = exitDetails.trigger === 'observed_bucket_breach'
    ? tx(language, '实测最高温已越过温度桶', 'Observed high moved beyond the bucket')
    : exitDetails.trigger === 'model_probability_invalidated'
      ? tx(language, '模型概率连续失效', 'Model probability invalidated repeatedly')
      : tx(language, '模型保护规则', 'Model guard rule')
  return (
    <div data-testid="paper-order-row" className="border-b border-neutral-800/80">
      <button type="button" onClick={() => setExpanded(value => !value)} className="grid w-full grid-cols-[16px_minmax(0,1fr)_86px] gap-2 px-3 py-2.5 text-left hover:bg-neutral-950/70">
        <ChevronDown className={`mt-0.5 h-3.5 w-3.5 text-neutral-600 transition ${expanded ? 'rotate-180' : ''}`} />
        <span className="min-w-0">
          <span className="block truncate text-[9px] text-cyan-500">{cityLabel(order.city_key)} · {order.target_date || '--'}</span>
          <span className="block truncate text-[11px] font-medium text-neutral-200">{bucket} · {strategyLabel(order.strategy_name ?? 'single_bucket_ev', language)}</span>
          <span className="mt-0.5 block truncate text-[9px] text-neutral-500">
            {statusLabel} · {tx(language, '买入', 'Bought')} {formatTimestamp(order.opened_at, language)} · {money(order.entry_value ?? order.filled_amount)}
          </span>
        </span>
        <span className="text-right">
          <span className={`block text-[9px] ${pnlPositive ? 'text-green-500' : 'text-red-500'}`}>{pnlLabel}</span>
          <span className={`block text-[12px] font-medium tabular-nums ${pnlPositive ? 'text-green-400' : 'text-red-400'}`}>{money(pnl)}</span>
          <span className="block text-[9px] tabular-nums text-neutral-600">{percent(order.pnl_pct, true)}</span>
        </span>
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-neutral-800 bg-neutral-950/50 px-3 py-3 text-[10px]">
          <section className="border border-neutral-800">
            <div className="border-b border-neutral-800 px-2 py-1.5 text-[9px] font-medium text-neutral-400">{tx(language, '买入成交', 'Entry fill')}</div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 p-2 text-neutral-500">
              <span>{tx(language, '操作', 'Action')}</span><span className="text-right font-medium text-cyan-300">BUY YES</span>
              <span>{tx(language, '成交价 / 限价', 'Fill / limit')}</span><span className="text-right tabular-nums text-neutral-300">{cents(order.entry_price ?? order.average_fill_price)} / {cents(order.limit_price)}</span>
              <span>{tx(language, '份额 / 金额', 'Shares / cost')}</span><span className="text-right tabular-nums text-neutral-300">{Number(order.filled_shares ?? 0).toFixed(2)} / {money(order.entry_value ?? order.filled_amount)}</span>
              <span>{tx(language, '买入时间', 'Bought at')}</span><span className="text-right tabular-nums text-neutral-300">{formatTimestamp(order.opened_at, language)}</span>
            </div>
          </section>

          <section className="border border-neutral-800">
            <div className="flex items-center justify-between border-b border-neutral-800 px-2 py-1.5 text-[9px]">
              <span className="font-medium text-neutral-400">
                {settled
                  ? tx(language, '结算结果', 'Settlement')
                  : exited
                    ? tx(language, '保护退出', 'Guarded exit')
                    : tx(language, '当前估值', 'Current mark')}
              </span>
              {!closed && <span className={order.quote_is_stale ? 'text-amber-400' : 'text-neutral-600'}>{quoteAge(order.mark_age_seconds, language)}</span>}
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 p-2 text-neutral-500">
              <span>{settled
                ? tx(language, '结算价', 'Settlement price')
                : exited
                  ? tx(language, '模拟卖出价（买一）', 'Paper sell price (bid)')
                  : tx(language, '参考卖出价（买一）', 'Reference sell price (bid)')}</span>
              <span className="text-right tabular-nums text-neutral-300">{cents(closed ? order.exit_price : order.mark_price)}</span>
              <span>{exited ? tx(language, '卖出回款', 'Sale proceeds') : tx(language, '持仓市值', 'Position value')}</span>
              <span className="text-right tabular-nums text-neutral-300">{money(exited ? exitDetails.proceeds : order.mark_value)}</span>
              <span>{pnlLabel}</span><span className={`text-right font-medium tabular-nums ${pnlPositive ? 'text-green-400' : 'text-red-400'}`}>{money(pnl)} · {percent(order.pnl_pct, true)}</span>
              <span>{settled ? tx(language, '结算时间', 'Settled at') : tx(language, '卖出时间', 'Sold at')}</span>
              <span className="text-right tabular-nums text-neutral-300">{closed ? formatTimestamp(order.exit_time ?? order.closed_at, language) : tx(language, '未卖出', 'Not sold')}</span>
              {exited && <>
                <span>{tx(language, '触发原因', 'Exit trigger')}</span><span className="text-right text-amber-200">{exitTrigger}</span>
                {exitDetails.model_probability != null && <><span>{tx(language, '退出时模型概率', 'Model probability at exit')}</span><span className="text-right tabular-nums text-neutral-300">{percent(exitDetails.model_probability)}</span></>}
                {exitDetails.observed_high != null && <><span>{tx(language, '退出时已观测最高温', 'Observed high at exit')}</span><span className="text-right tabular-nums text-neutral-300">{Number(exitDetails.observed_high).toFixed(1)}°</span></>}
              </>}
            </div>
          </section>

          <section className="border border-neutral-800">
            <div className="border-b border-neutral-800 px-2 py-1.5 text-[9px] font-medium text-neutral-400">{tx(language, '信号与仓位', 'Signal and sizing')}</div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 p-2 text-neutral-500">
              <span>{tx(language, '模型概率 / 入场盘口', 'Model / entry market')}</span><span className="text-right tabular-nums text-neutral-300">{percent(order.model_probability)} / {percent(order.market_probability)}</span>
              <span>{tx(language, '入场优势', 'Entry edge')}</span><span className="text-right tabular-nums text-neutral-300">{probabilityPoints(order.edge)}</span>
              <span>{tx(language, 'Kelly 比例 / 分配金额', 'Kelly / allocated')}</span><span className="text-right tabular-nums text-neutral-300">{percent(Number(sizing.kelly_fraction ?? 0))} / {money(Number(sizing.final_position_size_usd ?? order.entry_value ?? 0))}</span>
            </div>
          </section>

          <div className="border border-amber-500/20 bg-amber-500/5 px-2 py-2 text-[9px] leading-relaxed text-amber-200">
            {settled
              ? tx(language, '该订单已按 Polymarket 官方结果结算。', 'This order was settled from the official Polymarket outcome.')
              : exited
                ? tx(language, '该订单已按模拟盘保护规则，以当时可成交的 YES 买一价卖出；盈亏已经实现。', 'This paper order exited under the guard rule at the executable YES best bid; PnL is realized.')
                : order.exit_policy === 'model_guarded_take_profit'
                  ? tx(language, '退出规则：达到可成交止盈门槛时按最新买一价卖出；否则继续使用实况穿桶与模型失效保护。页面中间价浮盈不会触发退出。', 'Exit rule: sell at the executable best bid after the take-profit threshold is met; otherwise retain observed-breach and model-invalidation protection. Mid-price gains never trigger an exit.')
                : order.exit_policy === 'model_guarded'
                  ? tx(language, '退出规则：若实测最高温使温度桶不可能命中，立即按可成交买一价退出；若只是模型转弱，需连续两次确认且盘口价格合理。价格下跌本身不会触发。', 'Exit rule: an impossible bucket exits at an executable best bid; a model-only exit needs two confirmations and a rational quote. Price decline alone never triggers it.')
                  : tx(language, '退出规则：持有至 Polymarket 官方结算；当前没有盘中强制止损或自动卖出。', 'Exit rule: hold to official Polymarket settlement; no intraday forced stop or automatic sell is active.')}
          </div>
          {order.failure_reason && <div className="text-amber-300">{tx(language, '失败原因', 'Failure reason')}: {reasonText(order.failure_reason, language)}</div>}
          {order.event_url && (
            <a href={order.event_url} target="_blank" rel="noreferrer" className="inline-flex min-h-8 w-full items-center justify-center gap-1 border border-cyan-500/30 text-cyan-300 hover:bg-cyan-500/10">
              <ExternalLink className="h-3 w-3" /> {tx(language, '打开对应 Polymarket 市场', 'Open corresponding Polymarket market')}
            </a>
          )}
        </div>
      )}
    </div>
  )
}

export function ExecutionWorkbench({ cityKey, targetDate, decisions, validation, liveAvailable, schedulerRunning, onOpenDeveloperSettings, language = 'zh' }: Props) {
  const queryClient = useQueryClient()
  const [view, setView] = useState<'queue' | 'orders'>('queue')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [bankroll, setBankroll] = useState('40')
  const [maxPerTrade, setMaxPerTrade] = useState('2')
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>(['core_modal_v1'])
  const [exitMode, setExitMode] = useState<ExitMode>('hold_to_settlement')
  const [lastResult, setLastResult] = useState<PaperExecutionResult | null>(null)
  const validationActive = validation?.status === 'active'
  const profilesQuery = useQuery({
    queryKey: ['strategy-profiles'],
    queryFn: fetchStrategyProfiles,
    staleTime: 30000,
  })
  const activePaperProfile = profilesQuery.data?.profiles.find(profile => profile.active_scopes.includes('paper_default'))
  const selectedRevisionId = validation?.strategy_revision_id ?? activePaperProfile?.revision_id ?? ''
  const selectedRevisionRows = useMemo(
    () => selectedRevisionId
      ? (decisions?.decisions ?? []).filter(row => row.strategy_revision_id === selectedRevisionId)
      : [],
    [decisions, selectedRevisionId],
  )
  const latestDecisionIssuedAt = useMemo(
    () => selectedRevisionRows.reduce(
      (latest, row) => String(row.issued_at ?? '') > latest ? String(row.issued_at ?? '') : latest,
      '',
    ),
    [selectedRevisionRows],
  )
  useEffect(() => {
    if (validationActive) {
      setBankroll(String(validation?.bankroll_usd ?? 40))
      setMaxPerTrade(String(validation?.max_per_trade_usd ?? 2))
      if (validation?.strategies?.length) setSelectedStrategies(validation.strategies)
      setExitMode(normalizeExitMode(validation?.strategy_profile_snapshot?.parameters?.exit_policy?.mode))
      return
    }
    setExitMode(normalizeExitMode(activePaperProfile?.parameters?.exit_policy?.mode))
    const configuredStrategies = Object.entries(activePaperProfile?.parameters?.strategies ?? {})
      .filter(([, settings]) => settings.enabled === true)
      .map(([name]) => name)
      .filter(name => STRATEGY_OPTIONS.some(option => option.key === name))
    if (configuredStrategies.length) setSelectedStrategies(configuredStrategies)
  }, [activePaperProfile?.revision_id, activePaperProfile?.parameters?.exit_policy?.mode, validationActive, validation?.bankroll_usd, validation?.max_per_trade_usd, validation?.strategies, validation?.strategy_profile_snapshot?.parameters?.exit_policy?.mode])
  const queue = useMemo(() => {
    const latestRows = latestDecisionIssuedAt
      ? selectedRevisionRows.filter(row => row.issued_at === latestDecisionIssuedAt)
      : selectedRevisionRows
    return groupDecisions(latestRows.filter(row => selectedStrategies.includes(row.strategy_name ?? 'single_bucket_ev')))
  }, [latestDecisionIssuedAt, selectedRevisionRows, selectedStrategies])
  const eligibleCount = queue.filter(queueItemEligible).length
  const activeCohortRunId = validation?.run_id ?? ''
  const ordersQuery = useQuery({
    queryKey: ['paper-orders', activeCohortRunId || cityKey, activeCohortRunId ? 'all-cities' : targetDate],
    queryFn: () => activeCohortRunId
      ? fetchPaperOrders('', '', 100, activeCohortRunId)
      : fetchPaperOrders(cityKey, targetDate, 100),
    enabled: Boolean(activeCohortRunId || (cityKey && targetDate)),
    refetchInterval: 30000,
  })
  const executeMutation = useMutation({
    mutationFn: (payload: { decisionId?: string; dryRun: boolean }) => executePaperOrders({
      decisionId: payload.decisionId,
      city: payload.decisionId ? undefined : cityKey,
      targetDate: payload.decisionId ? undefined : targetDate,
      limit: 100,
      dryRun: payload.dryRun,
      strategies: selectedStrategies,
      strategyRevisionId: selectedRevisionId,
      decisionBatchIssuedAt: latestDecisionIssuedAt,
      cohortRunId: validation?.run_id,
    }),
    onSuccess: result => {
      setLastResult(result)
      queryClient.invalidateQueries({ queryKey: ['paper-orders'] })
      queryClient.invalidateQueries({ queryKey: ['paper-validation-status'] })
    },
    onError: error => setLastResult({ ok: false, reason: error instanceof Error ? error.message : 'request_failed' }),
  })
  const validationMutation = useMutation({
    mutationFn: async (action: 'start' | 'stop') => {
      if (action === 'stop') return stopPaperValidation()
      let revisionId = selectedRevisionId
      const enabledProfileStrategies = Object.entries(activePaperProfile?.parameters?.strategies ?? {})
        .filter(([, settings]) => settings.enabled === true)
        .map(([name]) => name)
        .sort()
      const strategySelectionChanged = enabledProfileStrategies.join('|') !== [...selectedStrategies].sort().join('|')
      if (activePaperProfile && (
        activePaperProfile.parameters.exit_policy.mode !== exitMode
        || strategySelectionChanged
      )) {
        const strategySettings = Object.fromEntries(
          Object.entries(activePaperProfile.parameters.strategies).map(([name, settings]) => [
            name,
            { ...settings, enabled: selectedStrategies.includes(name) },
          ]),
        )
        const revision = await createStrategyProfile({
          profile_key: activePaperProfile.profile_key,
          parameters: {
            ...activePaperProfile.parameters,
            strategies: strategySettings,
            exit_policy: {
              ...activePaperProfile.parameters.exit_policy,
              mode: exitMode,
            },
          },
          change_note: `Paper strategy ${selectedStrategies[0]} with exit mode ${exitMode}`,
          activate_scopes: ['signal_generation', 'paper_default'],
          confirm: true,
        })
        revisionId = revision.revision_id
      }
      const started = await startPaperValidation({
        bankroll_usd: Math.max(1, Number(bankroll) || 40),
        max_per_trade_usd: Math.max(0.1, Number(maxPerTrade) || 2),
        duration_days: 14,
        daily_max_usd: Math.max(1, Math.min(Number(bankroll) || 40, 10)),
        max_open_positions: 5,
        max_orders_per_day: 5,
        decision_max_age_minutes: 30,
        strategies: selectedStrategies,
        strategy_revision_id: revisionId,
      })
      if (!started.ok || started.status !== 'active' || !started.run_id) return started
      await runPaperValidationTick({ runId: started.run_id })
      return started
    },
    onSuccess: () => {
      setLastResult(null)
      queryClient.invalidateQueries({ queryKey: ['paper-validation-status'] })
      queryClient.invalidateQueries({ queryKey: ['paper-orders'] })
      queryClient.invalidateQueries({ queryKey: ['strategy-profiles'] })
    },
    onError: error => setLastResult({ ok: false, reason: error instanceof Error ? error.message : 'paper_validation_request_failed' }),
  })
  const summary = ordersQuery.data
  const toggleStrategy = (strategy: string) => {
    if (validationActive) return
    setSelectedStrategies([strategy])
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b border-neutral-800 bg-black/95 px-3 py-2">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="text-sm font-medium text-neutral-100">{tx(language, '模拟交易台', 'Paper trading')}</div>
            <div className="mt-0.5 text-[10px] text-neutral-600">{tx(language, 'Kelly 分配 → 盘口成交 → Polymarket 结算', 'Kelly sizing → order-book fill → Polymarket settlement')}</div>
          </div>
          <span className={`border px-1.5 py-0.5 text-[9px] ${liveAvailable ? 'border-green-500/30 text-green-300' : 'border-amber-500/30 text-amber-300'}`}>
            {liveAvailable ? tx(language, '实盘待验收', 'Live pending review') : tx(language, '实盘锁定', 'Live locked')}
          </span>
        </div>
        <div className="mt-1 flex items-center justify-between gap-2 text-[9px] text-neutral-600">
          <span title={selectedRevisionId || tx(language, '未加载', 'Not loaded')}>{tx(language, '策略版本', 'Strategy version')} {selectedRevisionId ? tx(language, '已加载', 'loaded') : '--'}</span>
          <button type="button" onClick={onOpenDeveloperSettings} className="inline-flex items-center gap-1 text-cyan-500 hover:text-cyan-300">
            <Settings2 className="h-3 w-3" /> {tx(language, '设置', 'Settings')}
          </button>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-1 text-[10px]">
          <div className="border border-neutral-800 p-2"><div className="text-neutral-600">{tx(language, '账户权益', 'Account equity')}</div><div className="mt-1 text-sm tabular-nums text-neutral-100">{money(summary?.equity ?? validation?.bankroll_usd ?? Number(bankroll))}</div></div>
          <div className="border border-neutral-800 p-2"><div className="text-neutral-600">{tx(language, '累计盈亏', 'Total PnL')}</div><div className={`mt-1 text-sm tabular-nums ${Number(summary?.total_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>{money(summary?.total_pnl ?? 0)}</div></div>
          <div className="border border-neutral-800 p-2"><div className="text-neutral-600">{tx(language, '现金 / 持仓市值', 'Cash / positions')}</div><div className="mt-1 tabular-nums text-neutral-200">{money(summary?.cash_available ?? validation?.cash_available_usd ?? Number(bankroll))} / {money(summary?.position_value ?? 0)}</div></div>
          <div className="border border-neutral-800 p-2"><div className="text-neutral-600">{tx(language, '持仓 / 已结算 / 已保护退出', 'Open / settled / guarded')}</div><div className="mt-1 tabular-nums text-neutral-200">{summary?.open_orders ?? 0} / {summary?.resolved_orders ?? 0} / {summary?.exited_orders ?? 0}</div></div>
        </div>
        <button type="button" onClick={() => setSettingsOpen(value => !value)} className="mt-2 inline-flex min-h-8 w-full items-center justify-between border border-neutral-800 px-2 text-[10px] text-neutral-400 hover:bg-neutral-950">
          <span className="inline-flex items-center gap-1"><Settings2 className="h-3.5 w-3.5" /> {tx(language, '自动模拟设置', 'Paper automation settings')}</span>
          <ChevronDown className={`h-3.5 w-3.5 transition ${settingsOpen ? 'rotate-180' : ''}`} />
        </button>
        {settingsOpen && (
          <div className="mt-2 space-y-2 border border-neutral-800 bg-neutral-950/60 p-2">
            <div className="grid grid-cols-2 gap-2">
              <label className="text-[9px] text-neutral-500">{tx(language, '模拟本金（USD）', 'Paper bankroll (USD)')}<input disabled={validationActive} type="number" min="1" step="1" value={bankroll} onChange={event => setBankroll(event.target.value)} className="mt-1 h-8 w-full border border-neutral-700 bg-black px-2 text-right text-[11px] text-neutral-200 disabled:opacity-60" /></label>
              <label className="text-[9px] text-neutral-500">{tx(language, '单笔上限（USD）', 'Max per trade (USD)')}<input disabled={validationActive} type="number" min="0.1" step="0.1" value={maxPerTrade} onChange={event => setMaxPerTrade(event.target.value)} className="mt-1 h-8 w-full border border-neutral-700 bg-black px-2 text-right text-[11px] text-neutral-200 disabled:opacity-60" /></label>
            </div>
            <fieldset disabled={validationActive} className="space-y-1">
              <legend className="mb-1 text-[9px] text-neutral-500">{tx(language, '入场策略（单选，避免重复敞口）', 'Entry strategy (single choice to avoid duplicate exposure)')}</legend>
              {STRATEGY_OPTIONS.map(option => (
                <label key={option.key} title={tx(language, option.helpZh, option.helpEn)} className="flex min-h-7 items-center gap-2 border border-neutral-800 px-2 text-[10px] text-neutral-300">
                  <input type="radio" name="paper-entry-strategy" checked={selectedStrategies.includes(option.key)} onChange={() => toggleStrategy(option.key)} />
                  <span>{tx(language, option.zh, option.en)}</span><Info className="ml-auto h-3 w-3 text-neutral-600" />
                </label>
              ))}
            </fieldset>
            <label className="block text-[9px] text-neutral-500">{tx(language, '退出方式', 'Exit method')}
              <select
                className="mt-1 h-8 w-full border border-neutral-700 bg-black px-2 text-[10px] text-neutral-200 disabled:opacity-60"
                value={exitMode}
                disabled={validationActive}
                onChange={event => setExitMode(event.target.value as ExitMode)}
              >
                <option value="hold_to_settlement">{tx(language, '持有至 Polymarket 结算（当前可用）', 'Hold to Polymarket settlement (available)')}</option>
                <option value="model_guarded">{tx(language, '模型保护退出（模拟盘）', 'Model-guarded exit (paper)')}</option>
                <option value="model_guarded_take_profit">{tx(language, '盈利止盈 + 模型保护（模拟盘）', 'Take profit + model guard (paper)')}</option>
              </select>
            </label>
            <div className="text-[9px] leading-relaxed text-neutral-600">
              {exitMode === 'model_guarded_take_profit'
                ? tx(language, '仅按新鲜盘口的可成交买一价计算：持仓至少 15 分钟，利润同时达到 5%、$0.05 且至少高于入场一档 tick，并有足够深度承接全部份额时止盈；实况穿桶和模型失效保护仍生效。只作用于下一批模拟。', 'Use only a fresh executable best bid: after 15 minutes, take profit when gain reaches 5%, $0.05, and at least one tick above entry, with enough depth for every share. Observed-breach and model guards remain active. Applies to the next paper cohort only.')
                : exitMode === 'model_guarded'
                ? tx(language, '实测最高温穿过本桶时立即模拟卖出；仅模型转弱时，需连续两次低于 8%，且买一价不低于模型公允价。新选择只对下一批模拟生效。', 'Exit immediately when the observed high makes the bucket impossible. A model-only exit needs two readings below 8% and a bid no worse than model fair value. This applies only to the next paper cohort.')
                : tx(language, '持有至官方结算，不因短期价差或盘中价格波动自动卖出。', 'Hold to official settlement; short-term spread and price noise never trigger an automatic sell.')}
            </div>
            {!schedulerRunning && <div className="border border-amber-500/20 bg-amber-500/5 px-2 py-1.5 text-[9px] text-amber-300">{tx(language, '请先启动顶部调度器；自动模拟和结算由后端定时任务驱动。', 'Start the scheduler first; paper execution and settlement are driven by backend jobs.')}</div>}
            <button
              type="button"
              disabled={validationMutation.isPending || (!validationActive && (!schedulerRunning || selectedStrategies.length === 0))}
              onClick={() => validationMutation.mutate(validationActive ? 'stop' : 'start')}
              className={`inline-flex min-h-9 w-full items-center justify-center gap-1 border text-[10px] disabled:opacity-30 ${validationActive ? 'border-red-500/30 text-red-300 hover:bg-red-500/10' : 'border-green-500/30 bg-green-500/10 text-green-200 hover:bg-green-500/15'}`}
            >
              {validationActive ? <><Square className="h-3 w-3" /> {tx(language, '停止自动模拟', 'Stop paper automation')}</> : <><Play className="h-3.5 w-3.5" /> {tx(language, '一键模拟', 'Start paper automation')}</>}
            </button>
          </div>
        )}
      </div>

      <div className="grid shrink-0 grid-cols-2 border-b border-neutral-800" role="tablist" aria-label={tx(language, '模拟策略与订单', 'Paper strategies and orders')}>
        <button type="button" role="tab" aria-selected={view === 'queue'} onClick={() => setView('queue')} className={`min-h-10 border-r border-neutral-800 px-3 text-left text-[11px] ${view === 'queue' ? 'bg-cyan-500/10 text-cyan-200' : 'text-neutral-500'}`}>
          <span className="inline-flex items-center gap-1"><ListChecks className="h-3.5 w-3.5" /> {tx(language, '策略队列', 'Strategy queue')}</span>
        </button>
        <button type="button" role="tab" aria-selected={view === 'orders'} onClick={() => setView('orders')} className={`min-h-10 px-3 text-left text-[11px] ${view === 'orders' ? 'bg-amber-500/10 text-amber-200' : 'text-neutral-500'}`}>
          <span className="inline-flex items-center gap-1"><FlaskConical className="h-3.5 w-3.5" /> {tx(language, '模拟订单', 'Paper orders')}</span>
        </button>
      </div>

      {lastResult && (
        <div className={`shrink-0 border-b px-3 py-2 text-[10px] ${lastResult.ok ? 'border-green-500/20 bg-green-500/5 text-green-300' : 'border-amber-500/20 bg-amber-500/5 text-amber-300'}`}>
          {resultMessage(lastResult, language)}
        </div>
      )}

      {view === 'queue' ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between border-b border-neutral-800 px-3 py-2">
            <span className="text-[10px] text-neutral-500">{cityKey} · {targetDate}</span>
            <button
              type="button"
              disabled={!validationActive || eligibleCount === 0 || executeMutation.isPending}
              onClick={() => executeMutation.mutate({ dryRun: false })}
              className="border border-cyan-500/30 px-2 py-1 text-[10px] text-cyan-200 hover:bg-cyan-500/10 disabled:opacity-30"
            >
              {tx(language, '模拟当前可用策略', 'Execute eligible strategies')}
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {queue.length ? queue.map(item => (
              <DecisionRow
                key={item.key}
                item={item}
                pending={executeMutation.isPending}
                accountActive={validationActive}
                onExecute={(decisionId, dryRun) => executeMutation.mutate({ decisionId, dryRun })}
                language={language}
              />
            )) : (
              <div className="px-3 py-8 text-center text-[11px] text-neutral-600">{tx(language, '当前城市和日期暂无策略决策', 'No strategy decisions for this city and date')}</div>
            )}
          </div>
        </div>
      ) : (
        <div data-testid="paper-order-list" className="min-h-0 flex-1 overflow-y-auto">
          {ordersQuery.isLoading ? (
            <div className="px-3 py-8 text-center text-[11px] text-neutral-600">{tx(language, '读取模拟订单…', 'Loading paper orders…')}</div>
          ) : summary?.orders?.length ? <>
            <section className="border-b border-neutral-800 px-3 py-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div>
                  <div className="text-[10px] font-medium text-neutral-300">{tx(language, '资金曲线', 'Equity curve')}</div>
                  <div className="text-[9px] text-neutral-600">{tx(language, '持仓按最新 YES 买一价估值', 'Open positions marked at latest YES best bid')}</div>
                </div>
                <div className={`text-right text-[11px] tabular-nums ${Number(summary.total_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {money(summary.total_pnl ?? 0)}
                </div>
              </div>
              <div className="h-36">
                <EquityChart data={summary.equity_curve ?? []} initialBankroll={Number(summary.starting_bankroll ?? validation?.bankroll_usd ?? 0)} language={language} />
              </div>
            </section>
            {summary.orders.map(order => <OrderRow key={order.id} order={order} language={language} />)}
          </> : (
            <div className="px-3 py-8 text-center text-[11px] text-neutral-600">{tx(language, '暂无模拟订单', 'No paper orders')}</div>
          )}
        </div>
      )}

      {!liveAvailable && (
        <div className="shrink-0 border-t border-neutral-800 px-3 py-2 text-[10px] text-neutral-600">
          <span className="inline-flex items-center gap-1"><ShieldAlert className="h-3 w-3 text-amber-400" /> {tx(language, '所有操作仅写入本地模拟订单，不会提交实盘。', 'All actions write local paper orders only; no live orders are submitted.')}</span>
        </div>
      )}
    </div>
  )
}
