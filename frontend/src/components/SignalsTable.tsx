import { AnimatePresence, motion } from 'framer-motion'
import { ArrowDown, ArrowUp, ArrowUpDown, Check, ChevronDown, ExternalLink, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { Signal, WeatherSignal } from '../types'
import { platformStyles } from '../utils'

interface Props {
  signals: Signal[]
  weatherSignals: WeatherSignal[]
  onSimulateTrade: (ticker: string) => void
  isSimulating: boolean
  onSignalStatus?: (signalId: number, status: string, amount?: number) => void
  onLiveOrder?: (signalId: number, amount?: number) => void
}

type SortKey = 'edge' | 'model_probability' | 'suggested_size' | 'strategy_score'
type SortDir = 'asc' | 'desc'

interface UnifiedSignal {
  key: string
  id?: number
  ticker: string
  title: string
  platform: string
  direction: string
  edge: number
  probabilityEdge?: number
  modelProb: number
  marketProb: number
  confidence: number
  suggestedSize: number
  reasoning: string
  actionable: boolean
  eventUrl?: string
  status?: string
  limitPrice?: number
  bidPrice?: number
  spread?: number
  shares?: number
  token?: string
  simAmount?: number | null
  paperPosition?: boolean
  fitSamples?: number
  fitMaeF?: number
  fitBiasF?: number
  qualityFlags?: string[]
  strategyTags?: string[]
  strategyScore?: number
  strategyNotes?: string[]
  dispersionRatio?: number | null
  liveAllowed?: boolean
  liveRiskLevel?: 'eligible' | 'caution' | 'blocked'
  liveBlockReasons?: string[]
  liveCautions?: string[]
  nearLock?: {
    hours_left: number
    observed_temp: number
    model_best: number
    remaining_potential: number
  } | null
}

function PlatformBadge({ platform }: { platform: string }) {
  const style = platformStyles[platform.toLowerCase()]
  if (!style) return null
  return <span className={`platform-badge ${style.badge}`}>{style.icon}</span>
}

function WeatherBadge() {
  return <span className="border border-cyan-500/20 bg-cyan-500/10 px-1 py-0.5 text-[8px] font-bold text-cyan-400">天气</span>
}

function EdgeBar({ edge }: { edge: number }) {
  const absEdge = Math.abs(edge) * 100
  const width = Math.min(100, absEdge * 2)
  const color = edge > 0.08 ? '#22c55e' : edge > 0 ? '#22c55e80' : '#dc2626'
  return (
    <div className="edge-bar">
      <div className="edge-fill" style={{ width: `${width}%`, backgroundColor: color }} />
    </div>
  )
}

function shortToken(token?: string) {
  if (!token) return ''
  return `${token.slice(0, 6)}...${token.slice(-4)}`
}

function statusLabel(status?: string) {
  switch (status) {
    case 'simulated': return '已模拟'
    case 'bought': return '已标记实盘'
    case 'paper_open': return '纸面持仓'
    case 'skipped': return '已跳过'
    case 'signal': return '新信号'
    default: return status || '观察'
  }
}

function flagLabel(flag: string) {
  switch (flag) {
    case 'fit_sample_low': return '拟合样本少'
    case 'city_mae_high': return '城市误差高'
    case 'city_bias_high': return '城市偏差高'
    case 'fit_missing': return '缺少拟合样本'
    default: return flag
  }
}

function strategyLabel(tag: string) {
  switch (tag) {
    case 'near_lock_watch': return '临近结算观察'
    case 'near_lock_strong': return '临近结算强信号'
    case 'near_lock_missing_metar': return '缺少 METAR'
    case 'dispersion_underpricing_watch': return '离散度不足'
    case 'cheap_tail_candidate': return '低价尾部'
    case 'fit_risk': return '拟合风险'
    case 'bias_risk': return '偏差风险'
    case 'standard_ev': return '普通 EV'
    default: return tag
  }
}

function gateLabel(reason: string) {
  switch (reason) {
    case 'fit_missing': return '缺少城市拟合'
    case 'city_mae_high': return '城市 MAE 过高'
    case 'city_bias_high': return '城市 Bias 过高'
    case 'fit_sample_low': return '拟合样本偏少'
    case 'near_lock_missing_metar': return 'D+0 缺少 METAR'
    case 'strategy_score_low': return '策略分过低'
    case 'price_below_min': return '价格低于最小阈值'
    case 'price_above_max': return '价格高于上限'
    case 'spread_missing': return '缺少 spread'
    case 'spread_above_limit': return 'spread 过宽'
    case 'expired_signal': return '信号已过期'
    case 'strategy_not_ready': return '策略回放未达实盘标准'
    case 'already_simulated': return '已模拟'
    case 'already_bought': return '已买入'
    case 'already_skipped': return '已跳过'
    default: return reason
  }
}

function liveGateText(sig: UnifiedSignal) {
  if (sig.liveAllowed) {
    return sig.liveRiskLevel === 'caution' ? '可实盘，需谨慎' : '可实盘'
  }
  return '禁止实盘'
}

export function SignalsTable({
  signals: _signals,
  weatherSignals,
  onSimulateTrade: _onSimulateTrade,
  isSimulating: _isSimulating,
  onSignalStatus,
  onLiveOrder,
}: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('edge')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const [simAmounts, setSimAmounts] = useState<Record<string, string>>({})

  const unified: UnifiedSignal[] = useMemo(() => {
    return weatherSignals.map(signal => ({
      key: `wx-${signal.id || signal.market_id}`,
      id: signal.id,
      ticker: signal.market_id,
      title: signal.question || `${signal.city_name} ${signal.bucket_label || `${signal.threshold_f}F`}`,
      platform: signal.platform || 'polymarket',
      direction: signal.direction,
      edge: signal.edge,
      probabilityEdge: signal.probability_edge,
      modelProb: signal.model_probability,
      marketProb: signal.market_probability,
      confidence: signal.confidence,
      suggestedSize: signal.suggested_size,
      reasoning: signal.reasoning,
      actionable: signal.actionable,
      eventUrl: signal.event_url,
      status: signal.status,
      limitPrice: signal.limit_price,
      bidPrice: signal.bid_price,
      spread: signal.spread,
      shares: signal.shares,
      token: signal.yes_token_id,
      simAmount: signal.sim_amount,
      paperPosition: signal.paper_position,
      fitSamples: signal.fit_samples,
      fitMaeF: signal.fit_mae_f,
      fitBiasF: signal.fit_bias_f,
      qualityFlags: signal.quality_flags,
      strategyTags: signal.strategy_tags,
      strategyScore: signal.strategy_score,
      strategyNotes: signal.strategy_notes,
      dispersionRatio: signal.dispersion_ratio,
      liveAllowed: signal.live_allowed,
      liveRiskLevel: signal.live_risk_level,
      liveBlockReasons: signal.live_block_reasons,
      liveCautions: signal.live_cautions,
      nearLock: signal.near_lock,
    }))
  }, [weatherSignals])

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const sorted = useMemo(() => {
    return [...unified].sort((a, b) => {
      if (a.actionable !== b.actionable) return a.actionable ? -1 : 1
      let aVal: number
      let bVal: number
      switch (sortKey) {
        case 'edge':
          aVal = Math.abs(a.edge); bVal = Math.abs(b.edge); break
        case 'model_probability':
          aVal = a.modelProb; bVal = b.modelProb; break
        case 'strategy_score':
          aVal = a.strategyScore ?? 0; bVal = b.strategyScore ?? 0; break
        case 'suggested_size':
          aVal = a.suggestedSize; bVal = b.suggestedSize; break
        default:
          return 0
      }
      return sortDir === 'asc' ? aVal - bVal : bVal - aVal
    })
  }, [unified, sortKey, sortDir])

  const SortIcon = ({ column }: { column: SortKey }) => {
    if (sortKey !== column) return <ArrowUpDown className="h-2.5 w-2.5 text-neutral-600" />
    return sortDir === 'asc'
      ? <ArrowUp className="h-2.5 w-2.5 text-amber-500" />
      : <ArrowDown className="h-2.5 w-2.5 text-amber-500" />
  }

  if (unified.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-neutral-600">
        <p className="text-xs">暂无信号</p>
        <p className="mt-0.5 text-[10px] text-neutral-700">运行 WeatherBot 后等待下一轮扫描</p>
      </div>
    )
  }

  return (
    <table className="w-full table-fixed">
      <thead className="sticky top-0 z-10 bg-[#0a0a0a]">
        <tr className="border-b border-neutral-800 text-left text-[10px] text-neutral-600">
          <th className="w-12 px-1.5 py-1.5 font-medium"></th>
          <th className="px-1.5 py-1.5 font-medium">信号</th>
          <th className="w-16 cursor-pointer px-1.5 py-1.5 text-right font-medium hover:text-neutral-400" onClick={() => handleSort('edge')}>
            <div className="flex items-center justify-end gap-0.5">
              EV <SortIcon column="edge" />
            </div>
          </th>
          <th className="w-16 cursor-pointer px-1.5 py-1.5 text-right font-medium hover:text-neutral-400" onClick={() => handleSort('strategy_score')}>
            <div className="flex items-center justify-end gap-0.5">
              风控 <SortIcon column="strategy_score" />
            </div>
          </th>
          <th className="w-20 cursor-pointer px-1.5 py-1.5 text-right font-medium hover:text-neutral-400" onClick={() => handleSort('suggested_size')}>
            <div className="flex items-center justify-end gap-0.5">
              模拟 <SortIcon column="suggested_size" />
            </div>
          </th>
        </tr>
      </thead>
      <tbody>
        <AnimatePresence>
          {sorted.map((sig, index) => {
            const isExpanded = expandedKey === sig.key
            const status = sig.status || (sig.actionable ? 'signal' : 'watch')
            const locked = ['simulated', 'bought', 'skipped'].includes(status)
            const liveBlocked = sig.liveAllowed === false
            const amountValue = simAmounts[sig.key] ?? String(sig.simAmount ?? sig.suggestedSize ?? '')
            const parsedAmount = Number(amountValue)
            const amountForSave = Number.isFinite(parsedAmount) ? parsedAmount : sig.suggestedSize
            const flags = sig.qualityFlags ?? []
            const liveReasons = sig.liveBlockReasons ?? []
            const liveCautions = sig.liveCautions ?? []

            return (
              <motion.tr
                key={sig.key}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.02 }}
                className={`cursor-pointer border-b border-neutral-800/50 text-[11px] hover:bg-neutral-800/30 ${sig.actionable ? '' : 'opacity-55'}`}
                onClick={() => setExpandedKey(isExpanded ? null : sig.key)}
              >
                <td className="px-1.5 py-1 align-top">
                  <div className="flex items-center gap-1">
                    <ChevronDown className={`h-3 w-3 text-neutral-600 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                    <PlatformBadge platform={sig.platform} />
                    <WeatherBadge />
                  </div>
                </td>
                <td className="px-1.5 py-1 align-top">
                  <span className="block break-words leading-snug text-neutral-300" title={sig.title}>
                    {sig.title}
                  </span>
                  <span className="mt-0.5 block text-[9px] text-neutral-600">
                    {statusLabel(status)} · {sig.direction.toUpperCase()} · 限价 {sig.limitPrice ? `${(sig.limitPrice * 100).toFixed(1)}c` : `${(sig.marketProb * 100).toFixed(1)}c`}
                    {sig.paperPosition ? ' · 已有纸面仓位' : ''}
                  </span>
                  {sig.token && <span className="block text-[9px] text-neutral-700">{shortToken(sig.token)}</span>}
                  {isExpanded && (
                    <div className="mt-1 space-y-1 text-[10px] leading-relaxed text-neutral-500">
                      <div>{sig.reasoning}</div>
                      <div>
                        模型P {(sig.modelProb * 100).toFixed(1)}% / 市场P {(sig.marketProb * 100).toFixed(1)}% / 概率差 {sig.probabilityEdge !== undefined ? `${sig.probabilityEdge > 0 ? '+' : ''}${(sig.probabilityEdge * 100).toFixed(1)}%` : '--'} / EV {sig.edge > 0 ? '+' : ''}{(sig.edge * 100).toFixed(1)}%
                      </div>
                      {sig.bidPrice !== undefined && sig.spread !== undefined && (
                        <div>Bid/Ask {((sig.bidPrice || 0) * 100).toFixed(1)}c / {((sig.limitPrice || 0) * 100).toFixed(1)}c · spread {(sig.spread * 100).toFixed(1)}c</div>
                      )}
                      <div className="border-l border-neutral-800 pl-2">
                        拟合质量：样本 {sig.fitSamples ?? 0} / MAE {sig.fitMaeF !== undefined ? `${sig.fitMaeF.toFixed(1)}F` : '--'} / Bias {sig.fitBiasF !== undefined ? `${sig.fitBiasF.toFixed(1)}F` : '--'}
                        {flags.length ? ` / 提示 ${flags.map(flagLabel).join('、')}` : ' / 暂无硬风险提示'}
                      </div>
                      <div className="border-l border-cyan-500/30 pl-2">
                        <div>
                          策略诊断：分数 {sig.strategyScore !== undefined ? sig.strategyScore.toFixed(2) : '--'} / {(sig.strategyTags ?? []).map(strategyLabel).join('、') || '普通 EV'}
                          {sig.dispersionRatio ? ` / 离散度比 ${sig.dispersionRatio.toFixed(2)}` : ''}
                        </div>
                        {sig.nearLock && (
                          <div>
                            NEAR-LOCK：剩余 {sig.nearLock.hours_left.toFixed(1)}h / METAR {sig.nearLock.observed_temp.toFixed(1)} / 模型 {sig.nearLock.model_best.toFixed(1)} / 剩余潜力 {sig.nearLock.remaining_potential.toFixed(1)}
                          </div>
                        )}
                        {(sig.strategyNotes ?? []).map(note => (
                          <div key={note} className="text-neutral-600">{note}</div>
                        ))}
                      </div>
                      <div className={`border-l pl-2 ${liveBlocked ? 'border-red-500/40 text-red-300' : 'border-green-500/30 text-green-300'}`}>
                        实盘门槛：{liveGateText(sig)}
                        {liveReasons.length ? ` / 拦截：${liveReasons.map(gateLabel).join('、')}` : ''}
                        {liveCautions.length ? ` / 提醒：${liveCautions.map(gateLabel).join('、')}` : ''}
                      </div>
                      <div>模拟买入只写入本地记录，不会向 Polymarket 下单；实盘按钮会再次经过后端风控。</div>
                    </div>
                  )}
                </td>
                <td className="px-1.5 py-1 text-right align-top">
                  <span className={`font-semibold tabular-nums ${sig.edge > 0 ? 'text-green-500' : sig.edge < 0 ? 'text-red-500' : 'text-neutral-600'}`}>
                    {sig.edge === 0 ? '-' : `${sig.edge > 0 ? '+' : ''}${(sig.edge * 100).toFixed(1)}%`}
                  </span>
                  <EdgeBar edge={sig.edge} />
                </td>
                <td className="px-1.5 py-1 text-right align-top">
                  <span className={`text-[10px] font-semibold ${
                    liveBlocked ? 'text-red-400' : sig.liveRiskLevel === 'caution' ? 'text-amber-400' : 'text-green-400'
                  }`}>
                    {liveGateText(sig)}
                  </span>
                  <div className="text-[9px] text-neutral-600">
                    {sig.strategyScore !== undefined ? sig.strategyScore.toFixed(2) : '--'}
                  </div>
                </td>
                <td className="px-1.5 py-1 text-right align-top tabular-nums text-blue-400">
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={amountValue}
                    onClick={event => event.stopPropagation()}
                    onChange={event => setSimAmounts(prev => ({ ...prev, [sig.key]: event.target.value }))}
                    className="w-14 border border-neutral-800 bg-black px-1 py-0.5 text-right text-[10px] text-blue-300"
                  />
                  {sig.shares ? <div className="text-[9px] text-neutral-600">{sig.shares.toFixed(2)} sh</div> : null}
                  <div className="mt-1 flex items-center justify-end gap-1">
                    {sig.eventUrl && (
                      <a
                        href={sig.eventUrl}
                        target="_blank"
                        rel="noreferrer"
                        onClick={event => event.stopPropagation()}
                        className="border border-neutral-700 p-0.5 text-neutral-400 hover:text-cyan-400"
                        title="打开 Polymarket"
                      >
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    )}
                    {sig.id && onSignalStatus && (
                      <>
                        <button
                          onClick={event => { event.stopPropagation(); onSignalStatus(sig.id!, 'simulated', amountForSave) }}
                          disabled={locked}
                          className="border border-green-500/30 p-0.5 text-green-400 hover:bg-green-500/10 disabled:opacity-30"
                          title="模拟买入"
                        >
                          <Check className="h-3 w-3" />
                        </button>
                        <button
                          onClick={event => { event.stopPropagation(); onLiveOrder ? onLiveOrder(sig.id!, amountForSave) : onSignalStatus(sig.id!, 'bought', amountForSave) }}
                          disabled={locked || liveBlocked}
                          className="border border-blue-500/30 px-1 py-0.5 text-[9px] text-blue-400 hover:bg-blue-500/10 disabled:opacity-30"
                          title={liveBlocked ? `实盘被拦截：${liveReasons.map(gateLabel).join('、')}` : '执行实盘下单或 dry-run'}
                        >
                          $
                        </button>
                        <button
                          onClick={event => { event.stopPropagation(); onSignalStatus(sig.id!, 'skipped') }}
                          disabled={locked}
                          className="border border-red-500/30 p-0.5 text-red-400 hover:bg-red-500/10 disabled:opacity-30"
                          title="跳过"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </motion.tr>
            )
          })}
        </AnimatePresence>
      </tbody>
    </table>
  )
}
