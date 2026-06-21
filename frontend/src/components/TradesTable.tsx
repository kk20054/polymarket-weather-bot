import { formatDistanceToNow } from 'date-fns'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ExternalLink } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { useMemo, useState } from 'react'
import type { Trade } from '../types'
import { platformStyles } from '../utils'

interface Props {
  trades: Trade[]
}

type SortKey = 'timestamp' | 'size' | 'pnl' | 'result'
type SortDir = 'asc' | 'desc'

function money(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  return `${value >= 0 ? '+' : ''}$${value.toFixed(2)}`
}

function price(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  return `${(value * 100).toFixed(1)}c`
}

function titleFor(trade: Trade) {
  return String(trade.market_title || trade.event_slug || trade.market_ticker || trade.id)
}

function resultText(result: string) {
  if (result === 'pending') return '待定'
  if (result === 'win') return '盈利'
  if (result === 'loss') return '亏损'
  return result
}

export function TradesTable({ trades }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('timestamp')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const sortedTrades = useMemo(() => {
    return [...trades].sort((a, b) => {
      let aVal: number | string
      let bVal: number | string
      switch (sortKey) {
        case 'timestamp':
          aVal = new Date(a.timestamp).getTime()
          bVal = new Date(b.timestamp).getTime()
          break
        case 'size':
          aVal = a.size
          bVal = b.size
          break
        case 'pnl':
          aVal = a.pnl ?? a.unrealized_pnl ?? 0
          bVal = b.pnl ?? b.unrealized_pnl ?? 0
          break
        case 'result':
          aVal = a.result
          bVal = b.result
          break
        default:
          return 0
      }
      if (typeof aVal === 'string') {
        return sortDir === 'asc'
          ? aVal.localeCompare(bVal as string)
          : (bVal as string).localeCompare(aVal)
      }
      return sortDir === 'asc' ? aVal - (bVal as number) : (bVal as number) - aVal
    })
  }, [trades, sortKey, sortDir])

  const SortIcon = ({ column }: { column: SortKey }) => {
    if (sortKey !== column) return <ArrowUpDown className="h-2.5 w-2.5 text-neutral-600" />
    return sortDir === 'asc'
      ? <ArrowUp className="h-2.5 w-2.5 text-amber-500" />
      : <ArrowDown className="h-2.5 w-2.5 text-amber-500" />
  }

  if (trades.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-neutral-600">
        <p className="text-xs">暂无记录</p>
        <p className="mt-0.5 text-[10px]">模拟买入后会出现在这里</p>
      </div>
    )
  }

  return (
    <table className="w-full table-fixed">
      <thead className="sticky top-0 z-10 bg-[#0a0a0a]">
        <tr className="border-b border-neutral-800 text-left text-[10px] text-neutral-600">
          <th className="w-5 px-1.5 py-1.5"></th>
          <th
            className="w-12 cursor-pointer px-1.5 py-1.5 font-medium hover:text-neutral-400"
            onClick={() => handleSort('result')}
          >
            <div className="flex items-center gap-0.5">
              状态 <SortIcon column="result" />
            </div>
          </th>
          <th className="px-1.5 py-1.5 font-medium">市场</th>
          <th className="w-10 px-1.5 py-1.5 text-center font-medium">方向</th>
          <th
            className="w-16 cursor-pointer px-1.5 py-1.5 text-right font-medium hover:text-neutral-400"
            onClick={() => handleSort('size')}
          >
            <div className="flex items-center justify-end gap-0.5">
              金额 <SortIcon column="size" />
            </div>
          </th>
          <th
            className="w-16 cursor-pointer px-1.5 py-1.5 text-right font-medium hover:text-neutral-400"
            onClick={() => handleSort('pnl')}
          >
            <div className="flex items-center justify-end gap-0.5">
              盈亏 <SortIcon column="pnl" />
            </div>
          </th>
          <th
            className="w-16 cursor-pointer px-1.5 py-1.5 text-right font-medium hover:text-neutral-400"
            onClick={() => handleSort('timestamp')}
          >
            <div className="flex items-center justify-end gap-0.5">
              时间 <SortIcon column="timestamp" />
            </div>
          </th>
        </tr>
      </thead>
      <tbody>
        <AnimatePresence>
          {sortedTrades.map((trade, i) => {
            const tradeKey = String(trade.id)
            const isPending = trade.result === 'pending'
            const isWin = trade.result === 'win'
            const style = platformStyles[trade.platform?.toLowerCase()]
            const expanded = expandedId === tradeKey
            const tradeTitle = titleFor(trade)
            const displayPnl = trade.pnl ?? trade.unrealized_pnl ?? null

            return (
              <motion.tr
                key={tradeKey}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.02 }}
                className="cursor-pointer border-b border-neutral-800/50 align-top text-[11px] hover:bg-neutral-800/30"
                onClick={() => setExpandedId(expanded ? null : tradeKey)}
              >
                <td className="px-1.5 py-1">
                  <div className="flex items-center gap-1">
                    <ChevronDown className={`h-3 w-3 text-neutral-600 transition-transform ${expanded ? 'rotate-180' : ''}`} />
                    {style && <span className={`platform-badge ${style.badge}`}>{style.icon}</span>}
                  </div>
                </td>
                <td className="px-1.5 py-1">
                  <span className={`text-[9px] font-medium uppercase ${
                    isPending ? 'text-amber-500' : isWin ? 'text-green-500' : 'text-red-500'
                  }`}>
                    {resultText(trade.result)}
                  </span>
                </td>
                <td className="px-1.5 py-1">
                  <div className="flex items-center gap-1">
                    <span className="block truncate text-neutral-400" title={tradeTitle}>
                      {tradeTitle}
                    </span>
                    {trade.event_url && (
                      <a
                        href={trade.event_url}
                        target="_blank"
                        rel="noreferrer"
                        className="shrink-0 text-cyan-400 hover:text-cyan-300"
                        onClick={(event) => event.stopPropagation()}
                        title="打开 Polymarket"
                      >
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    )}
                  </div>
                  {expanded && (
                    <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 border-l border-neutral-800 pl-2 text-[10px] text-neutral-500">
                      <span>ID</span><span className="truncate text-neutral-400" title={tradeKey}>{tradeKey}</span>
                      <span>Polymarket</span>
                      <span className="truncate text-cyan-400">
                        {trade.event_url ? <a href={trade.event_url} target="_blank" rel="noreferrer" onClick={event => event.stopPropagation()}>打开市场</a> : '-'}
                      </span>
                      <span>买入价 ask</span><span className="tabular-nums text-neutral-300">{price(trade.entry_price)}</span>
                      <span>入场 bid</span><span className="tabular-nums text-neutral-300">{price(trade.bid_at_entry)}</span>
                      <span>当前估值 bid</span><span className="tabular-nums text-neutral-300">{price(trade.mark_price)}</span>
                      <span>spread</span><span className="tabular-nums text-neutral-300">{price(trade.spread)}</span>
                      <span>份额</span><span className="tabular-nums text-neutral-300">{trade.shares?.toFixed(2) ?? '-'}</span>
                      <span>数据源</span><span className="text-neutral-300">{trade.source || '-'}</span>
                      <span>未实现盈亏</span><span className={`tabular-nums ${Number(trade.unrealized_pnl ?? 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>{money(trade.unrealized_pnl)}</span>
                      <span>结算原因</span><span className="text-neutral-300">{trade.close_reason || (isPending ? '等待结算' : '-')}</span>
                    </div>
                  )}
                </td>
                <td className="px-1.5 py-1 text-center">
                  <span className="text-[10px] font-semibold uppercase text-cyan-400">
                    {trade.direction}
                  </span>
                </td>
                <td className="px-1.5 py-1 text-right tabular-nums text-neutral-300">
                  ${trade.size.toFixed(2)}
                </td>
                <td className="px-1.5 py-1 text-right">
                  {displayPnl !== null ? (
                    <span className={`font-semibold tabular-nums ${
                      displayPnl >= 0 ? 'text-green-500' : 'text-red-500'
                    }`}>
                      {money(displayPnl)}
                    </span>
                  ) : (
                    <span className="text-neutral-600">-</span>
                  )}
                </td>
                <td className="px-1.5 py-1 text-right text-[10px] tabular-nums text-neutral-600">
                  {formatDistanceToNow(new Date(trade.timestamp), { addSuffix: false })}
                </td>
              </motion.tr>
            )
          })}
        </AnimatePresence>
      </tbody>
    </table>
  )
}
