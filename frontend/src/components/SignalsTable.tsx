import { ArrowUpDown, ArrowUp, ArrowDown, ExternalLink, Check, X } from 'lucide-react'
import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { Signal, WeatherSignal } from '../types'
import { platformStyles } from '../utils'

interface Props {
  signals: Signal[]
  weatherSignals: WeatherSignal[]
  onSimulateTrade: (ticker: string) => void
  isSimulating: boolean
  onSignalStatus?: (signalId: number, status: string, amount?: number) => void
}

type SortKey = 'edge' | 'model_probability' | 'suggested_size'
type SortDir = 'asc' | 'desc'

interface UnifiedSignal {
  key: string
  id?: number
  ticker: string
  title: string
  platform: string
  category: 'BTC' | 'WX'
  direction: string
  edge: number
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
}

function PlatformBadge({ platform }: { platform: string }) {
  const style = platformStyles[platform.toLowerCase()]
  if (!style) return null
  return <span className={`platform-badge ${style.badge}`}>{style.icon}</span>
}

function CategoryBadge({ category }: { category: 'BTC' | 'WX' }) {
  return category === 'BTC'
    ? <span className="text-[8px] font-bold px-1 py-0.5 bg-amber-500/10 text-amber-500 border border-amber-500/20">BTC</span>
    : <span className="text-[8px] font-bold px-1 py-0.5 bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">WX</span>
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
    case 'simulated': return '模拟'
    case 'bought': return '实盘'
    case 'paper_open': return '纸面持仓'
    case 'skipped': return '跳过'
    case 'signal': return '信号'
    default: return status || '观察'
  }
}

export function SignalsTable({ signals, weatherSignals, onSimulateTrade, isSimulating, onSignalStatus }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('edge')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const [simAmounts, setSimAmounts] = useState<Record<string, string>>({})

  const unified: UnifiedSignal[] = useMemo(() => {
    const btc: UnifiedSignal[] = signals.map(s => ({
      key: `btc-${s.market_ticker}`,
      ticker: s.market_ticker,
      title: (s.event_slug || s.market_ticker).replace('btc-updown-5m-', ''),
      platform: s.platform || 'polymarket',
      category: 'BTC',
      direction: s.direction,
      edge: s.edge,
      modelProb: s.model_probability,
      marketProb: s.market_probability,
      confidence: s.confidence,
      suggestedSize: s.suggested_size,
      reasoning: s.reasoning,
      actionable: s.actionable,
    }))

    const wx: UnifiedSignal[] = weatherSignals.map(s => ({
      key: `wx-${s.id || s.market_id}`,
      id: s.id,
      ticker: s.market_id,
      title: s.question || `${s.city_name} ${s.bucket_label || `${s.threshold_f}F`}`,
      platform: s.platform || 'polymarket',
      category: 'WX',
      direction: s.direction,
      edge: s.edge,
      modelProb: s.model_probability,
      marketProb: s.market_probability,
      confidence: s.confidence,
      suggestedSize: s.suggested_size,
      reasoning: s.reasoning,
      actionable: s.actionable,
      eventUrl: s.event_url,
      status: s.status,
      limitPrice: s.limit_price,
      bidPrice: s.bid_price,
      spread: s.spread,
      shares: s.shares,
      token: s.yes_token_id,
      simAmount: s.sim_amount,
    }))

    return [...wx, ...btc]
  }, [signals, weatherSignals])

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
      let aVal: number, bVal: number
      switch (sortKey) {
        case 'edge':
          aVal = Math.abs(a.edge); bVal = Math.abs(b.edge); break
        case 'model_probability':
          aVal = a.modelProb; bVal = b.modelProb; break
        case 'suggested_size':
          aVal = a.suggestedSize; bVal = b.suggestedSize; break
        default: return 0
      }
      return sortDir === 'asc' ? aVal - bVal : bVal - aVal
    })
  }, [unified, sortKey, sortDir])

  const SortIcon = ({ column }: { column: SortKey }) => {
    if (sortKey !== column) return <ArrowUpDown className="w-2.5 h-2.5 text-neutral-600" />
    return sortDir === 'asc'
      ? <ArrowUp className="w-2.5 h-2.5 text-amber-500" />
      : <ArrowDown className="w-2.5 h-2.5 text-amber-500" />
  }

  if (unified.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-neutral-600">
        <p className="text-xs">暂无信号</p>
        <p className="text-[10px] mt-0.5 text-neutral-700">运行 WeatherBot 后等待下一轮扫描</p>
      </div>
    )
  }

  return (
    <table className="w-full table-fixed">
      <thead className="sticky top-0 bg-[#0a0a0a] z-10">
        <tr className="text-neutral-600 text-left text-[10px] border-b border-neutral-800">
          <th className="py-1.5 px-1.5 font-medium w-10"></th>
          <th className="py-1.5 px-1.5 font-medium">信号</th>
          <th className="py-1.5 px-1.5 font-medium text-right cursor-pointer hover:text-neutral-400" onClick={() => handleSort('edge')}>
            <div className="flex items-center justify-end gap-0.5">
              EV <SortIcon column="edge" />
            </div>
          </th>
          <th className="py-1.5 px-1.5 font-medium text-right cursor-pointer hover:text-neutral-400" onClick={() => handleSort('suggested_size')}>
            <div className="flex items-center justify-end gap-0.5">
              模拟 <SortIcon column="suggested_size" />
            </div>
          </th>
        </tr>
      </thead>
      <tbody>
        <AnimatePresence>
          {sorted.map((sig, i) => {
            const isExpanded = expandedKey === sig.key
            const status = sig.status || (sig.actionable ? 'signal' : 'watch')
            const amountValue = simAmounts[sig.key] ?? String(sig.simAmount ?? sig.suggestedSize ?? '')
            const parsedAmount = Number(amountValue)
            const amountForSave = Number.isFinite(parsedAmount) ? parsedAmount : sig.suggestedSize

            return (
              <motion.tr
                key={sig.key}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.02 }}
                className={`border-b border-neutral-800/50 hover:bg-neutral-800/30 text-[11px] cursor-pointer ${
                  sig.actionable ? '' : 'opacity-50'
                }`}
                onClick={() => setExpandedKey(isExpanded ? null : sig.key)}
              >
                <td className="py-1 px-1.5 align-top">
                  <div className="flex items-center gap-1">
                    <PlatformBadge platform={sig.platform} />
                    <CategoryBadge category={sig.category} />
                  </div>
                </td>
                <td className="py-1 px-1.5 align-top">
                  <span className="text-neutral-300 block leading-snug break-words" title={sig.title}>
                    {sig.title}
                  </span>
                  <span className="text-[9px] text-neutral-600 block mt-0.5">
                    {statusLabel(status)} · {sig.direction.toUpperCase()} · 限价 {sig.limitPrice ? `${(sig.limitPrice * 100).toFixed(0)}c` : `${(sig.marketProb * 100).toFixed(0)}c`}
                  </span>
                  {sig.token && <span className="text-[9px] text-neutral-700 block">{shortToken(sig.token)}</span>}
                  {isExpanded && (
                    <div className="mt-1 text-[10px] text-neutral-500 leading-relaxed">
                      {sig.reasoning}
                      {sig.bidPrice !== undefined && sig.spread !== undefined && (
                        <div>Bid/Ask {Math.round((sig.bidPrice || 0) * 100)}c / {Math.round((sig.limitPrice || 0) * 100)}c · spread {(sig.spread * 100).toFixed(1)}c</div>
                      )}
                      <div>模拟买入只写入本地记录，不会向 Polymarket 下单。</div>
                    </div>
                  )}
                </td>
                <td className="py-1 px-1.5 text-right align-top">
                  <span className={`font-semibold tabular-nums ${sig.edge > 0 ? 'text-green-500' : sig.edge < 0 ? 'text-red-500' : 'text-neutral-600'}`}>
                    {sig.edge === 0 ? '-' : `${Math.abs(sig.edge * 100).toFixed(1)}%`}
                  </span>
                  <EdgeBar edge={sig.edge} />
                </td>
                <td className="py-1 px-1.5 text-right text-blue-400 tabular-nums align-top">
                  {sig.category === 'WX' ? (
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={amountValue}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => setSimAmounts(prev => ({ ...prev, [sig.key]: e.target.value }))}
                      className="w-16 px-1 py-0.5 text-[10px] text-right tabular-nums bg-black border-neutral-800 text-blue-300"
                      title="模拟金额，单位美元"
                    />
                  ) : (
                    sig.suggestedSize > 0 ? `$${sig.suggestedSize.toFixed(2)}` : '-'
                  )}
                  {sig.shares ? <div className="text-[9px] text-neutral-600">{sig.shares.toFixed(2)} sh</div> : null}
                  <div className="flex justify-end gap-1 mt-1">
                    {sig.eventUrl && (
                      <a
                        href={sig.eventUrl}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="px-1.5 py-0.5 text-[8px] font-medium uppercase bg-blue-500/10 text-blue-400 border border-blue-500/20 hover:bg-blue-500/20"
                        title="打开 Polymarket"
                      >
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    )}
                    {sig.category === 'WX' && sig.id && sig.actionable && onSignalStatus && (
                      <>
                        <button
                          onClick={(e) => { e.stopPropagation(); onSignalStatus(sig.id!, 'simulated', amountForSave) }}
                          className="px-1.5 py-0.5 text-[8px] font-medium uppercase bg-green-500/10 text-green-400 border border-green-500/20 hover:bg-green-500/20"
                          title="模拟买入"
                        >
                          <Check className="w-3 h-3" />
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); onSignalStatus(sig.id!, 'bought', amountForSave) }}
                          className="px-1.5 py-0.5 text-[8px] font-medium uppercase bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 hover:bg-cyan-500/20"
                          title="标记已实盘买入"
                        >
                          $
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); onSignalStatus(sig.id!, 'skipped') }}
                          className="px-1.5 py-0.5 text-[8px] font-medium uppercase bg-amber-500/10 text-amber-400 border border-amber-500/20 hover:bg-amber-500/20"
                          title="跳过"
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </>
                    )}
                    {sig.actionable && sig.category === 'BTC' && (
                      <button
                        onClick={(e) => { e.stopPropagation(); onSimulateTrade(sig.ticker) }}
                        disabled={isSimulating}
                        className="px-1.5 py-0.5 text-[8px] font-medium uppercase bg-amber-500/10 text-amber-400 border border-amber-500/20 hover:bg-amber-500/20 disabled:opacity-50"
                      >
                        交易
                      </button>
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
