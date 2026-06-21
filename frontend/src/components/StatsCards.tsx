import { motion } from 'framer-motion'
import type { BotStats } from '../types'

interface Props {
  stats: BotStats
}

export function StatsCards({ stats }: Props) {
  const winRate = (stats.win_rate ?? 0) * 100
  const openTrades = stats.open_trades ?? 0
  const settledTrades = stats.settled_trades ?? 0
  const hasSettled = settledTrades > 0
  const returnPercent = stats.bankroll - stats.total_pnl > 0
    ? ((stats.total_pnl / (stats.bankroll - stats.total_pnl)) * 100)
    : 0

  return (
    <div className="flex items-center gap-3">
      <motion.div className="flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
        <span className="text-[10px] text-neutral-600 uppercase">资金</span>
        <span className="text-sm font-semibold tabular-nums text-neutral-100">
          ${stats.bankroll >= 1000 ? (stats.bankroll / 1000).toFixed(1) + 'K' : stats.bankroll.toFixed(2)}
        </span>
      </motion.div>

      <div className="w-px h-3 bg-neutral-800" />

      <motion.div className="flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.05 }}>
        <span className="text-[10px] text-neutral-600 uppercase">盈亏</span>
        <span className={`text-sm font-semibold tabular-nums ${stats.total_pnl >= 0 ? 'text-green-500 glow-green' : 'text-red-500 glow-red'}`}>
          {stats.total_pnl >= 0 ? '+' : '-'}${Math.abs(stats.total_pnl).toFixed(2)}
        </span>
        <span className={`text-[10px] tabular-nums ${returnPercent >= 0 ? 'text-green-500/60' : 'text-red-500/60'}`}>
          {returnPercent >= 0 ? '+' : ''}{returnPercent.toFixed(1)}%
        </span>
      </motion.div>

      <div className="w-px h-3 bg-neutral-800" />

      <motion.div className="flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }}>
        <span className="text-[10px] text-neutral-600 uppercase">胜率</span>
        <span className={`text-sm font-semibold tabular-nums ${!hasSettled ? 'text-neutral-400' : winRate >= 55 ? 'text-green-500' : winRate >= 45 ? 'text-yellow-500' : 'text-red-500'}`}>
          {hasSettled ? `${winRate.toFixed(0)}%` : '--'}
        </span>
        <span className="text-[10px] text-neutral-600 tabular-nums">
          {stats.winning_trades}/{settledTrades}
        </span>
      </motion.div>

      <div className="w-px h-3 bg-neutral-800" />

      <motion.div className="flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.15 }}>
        <span className="text-[10px] text-neutral-600 uppercase">持仓/结算</span>
        <span className="text-sm font-semibold tabular-nums text-neutral-100">{openTrades}/{settledTrades}</span>
        {stats.is_running && <div className="live-dot" />}
      </motion.div>
    </div>
  )
}
