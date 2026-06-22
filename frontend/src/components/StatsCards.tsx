import { motion } from 'framer-motion'
import type { BotStats } from '../types'

interface Props {
  stats: BotStats
}

function readinessReasonLabel(reason?: string) {
  switch (reason) {
    case 'resolved_sample_below_30': return '已结算样本不足'
    case 'allowed_sample_below_20': return '允许组样本不足'
    case 'allowed_group_pnl_negative': return '允许组仍亏损'
    case 'allowed_group_roi_negative': return '允许组 ROI 为负'
    case 'allowed_win_rate_low': return '允许组胜率低'
    case 'allowed_not_outperforming_blocked': return '允许组未跑赢拦截组'
    case 'brier_too_high': return 'Brier 偏高'
    default: return reason || ''
  }
}

export function StatsCards({ stats }: Props) {
  const winRate = (stats.win_rate ?? 0) * 100
  const openTrades = stats.open_trades ?? 0
  const settledTrades = stats.settled_trades ?? 0
  const hasSettled = settledTrades > 0
  const starting = stats.bankroll - stats.total_pnl
  const returnPercent = starting > 0 ? (stats.total_pnl / starting) * 100 : 0
  const realized = stats.realized_pnl ?? stats.total_pnl
  const unrealized = stats.unrealized_pnl ?? 0
  const readiness = stats.strategy_readiness_status ?? 'watch'
  const liveReady = Boolean(stats.strategy_live_ready)
  const readinessText = liveReady ? '可实盘' : readiness === 'blocked' ? '禁止' : '观察'
  const reasons = stats.strategy_readiness_reasons ?? []
  const readinessReason = (
    reasons.find(reason => reason === 'allowed_group_pnl_negative')
    || reasons.find(reason => reason === 'allowed_group_roi_negative')
    || reasons.find(reason => reason === 'allowed_not_outperforming_blocked')
    || reasons[0]
  )
  const readinessReasonText = readinessReasonLabel(readinessReason)
  const readinessClass = liveReady
    ? 'border-green-500/30 bg-green-500/10 text-green-400'
    : readiness === 'blocked'
      ? 'border-red-500/30 bg-red-500/10 text-red-400'
      : 'border-amber-500/30 bg-amber-500/10 text-amber-400'

  return (
    <div className="flex items-center gap-3">
      <motion.div className="flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
        <span className="text-[10px] text-neutral-600 uppercase">权益</span>
        <span className="text-sm font-semibold tabular-nums text-neutral-100">
          ${stats.bankroll >= 1000 ? (stats.bankroll / 1000).toFixed(1) + 'K' : stats.bankroll.toFixed(2)}
        </span>
      </motion.div>

      <div className="w-px h-3 bg-neutral-800" />

      <motion.div className="flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.05 }}>
        <span className="text-[10px] text-neutral-600 uppercase">总盈亏</span>
        <span className={`text-sm font-semibold tabular-nums ${stats.total_pnl >= 0 ? 'text-green-500 glow-green' : 'text-red-500 glow-red'}`}>
          {stats.total_pnl >= 0 ? '+' : '-'}${Math.abs(stats.total_pnl).toFixed(2)}
        </span>
        <span className={`text-[10px] tabular-nums ${returnPercent >= 0 ? 'text-green-500/60' : 'text-red-500/60'}`}>
          {returnPercent >= 0 ? '+' : ''}{returnPercent.toFixed(1)}%
        </span>
      </motion.div>

      <div className="w-px h-3 bg-neutral-800" />

      <motion.div className="flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }}>
        <span className="text-[10px] text-neutral-600 uppercase">已/未实现</span>
        <span className={`text-sm font-semibold tabular-nums ${realized >= 0 ? 'text-green-500' : 'text-red-500'}`}>
          {realized >= 0 ? '+' : '-'}${Math.abs(realized).toFixed(2)}
        </span>
        <span className={`text-[10px] tabular-nums ${unrealized >= 0 ? 'text-green-500/70' : 'text-red-500/70'}`}>
          {unrealized >= 0 ? '+' : '-'}${Math.abs(unrealized).toFixed(2)}
        </span>
      </motion.div>

      <div className="w-px h-3 bg-neutral-800" />

      <motion.div className="flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.15 }}>
        <span className="text-[10px] text-neutral-600 uppercase">现金/占用</span>
        <span className="text-sm font-semibold tabular-nums text-neutral-100">
          ${(stats.cash_balance ?? stats.bankroll).toFixed(2)}
        </span>
        <span className="text-[10px] tabular-nums text-neutral-600">
          / ${(stats.reserved_capital ?? 0).toFixed(2)}
        </span>
      </motion.div>

      <div className="w-px h-3 bg-neutral-800" />

      <motion.div className="flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }}>
        <span className="text-[10px] text-neutral-600 uppercase">胜率</span>
        <span className={`text-sm font-semibold tabular-nums ${!hasSettled ? 'text-neutral-400' : winRate >= 55 ? 'text-green-500' : winRate >= 45 ? 'text-yellow-500' : 'text-red-500'}`}>
          {hasSettled ? `${winRate.toFixed(0)}%` : '--'}
        </span>
        <span className="text-[10px] text-neutral-600 tabular-nums">
          {stats.winning_trades}/{settledTrades}
        </span>
      </motion.div>

      <div className="w-px h-3 bg-neutral-800" />

      <motion.div className="flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.25 }}>
        <span className="text-[10px] text-neutral-600 uppercase">自动实盘</span>
        <span
          className={`border px-1.5 py-0.5 text-[9px] font-bold tabular-nums ${readinessClass}`}
          title={reasons.map(readinessReasonLabel).join('、') || '策略回放满足实盘要求'}
        >
          {readinessText}
        </span>
        <span className="max-w-24 truncate text-[9px] text-neutral-600" title={readinessReasonText}>
          {readinessReasonText || `${stats.strategy_allowed_resolved ?? 0} 样本`}
        </span>
      </motion.div>

      <div className="w-px h-3 bg-neutral-800" />

      <motion.div className="flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.3 }}>
        <span className="text-[10px] text-neutral-600 uppercase">持仓/实盘候选</span>
        <span className="text-sm font-semibold tabular-nums text-neutral-100">{openTrades}/{stats.live_candidate_count ?? 0}</span>
        {stats.is_running && <div className="live-dot" />}
      </motion.div>
    </div>
  )
}
