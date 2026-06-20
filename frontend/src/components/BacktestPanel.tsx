import type { BacktestSummary } from '../types'

interface Props {
  backtest?: BacktestSummary | null
}

function pct(value: number) {
  return `${(value * 100).toFixed(0)}%`
}

function money(value: number) {
  return `${value >= 0 ? '+' : '-'}$${Math.abs(value).toFixed(2)}`
}

export function BacktestPanel({ backtest }: Props) {
  if (!backtest || backtest.total_positions === 0) {
    return (
      <div className="h-full flex items-center justify-center text-neutral-600 text-[10px]">
        暂无可复盘样本
      </div>
    )
  }

  const sampleWeak = backtest.resolved_positions < 30
  const topSources = backtest.sources.slice(0, 3)

  return (
    <div className="h-full overflow-y-auto p-2 text-[10px] text-neutral-500 space-y-2">
      <div className="grid grid-cols-3 gap-1">
        <div className="border border-neutral-800 px-1 py-1">
          <div className="text-neutral-600">样本</div>
          <div className="text-neutral-200 text-sm font-bold tabular-nums">
            {backtest.resolved_positions}/{backtest.total_positions}
          </div>
          <div className="text-[9px] text-neutral-600">已结算/总仓位</div>
        </div>
        <div className="border border-neutral-800 px-1 py-1">
          <div className="text-neutral-600">胜率</div>
          <div className={`text-sm font-bold tabular-nums ${backtest.win_rate >= 0.55 ? 'text-green-500' : 'text-red-500'}`}>
            {pct(backtest.win_rate)}
          </div>
          <div className="text-[9px] text-neutral-600">{backtest.wins}/{backtest.resolved_positions}</div>
        </div>
        <div className="border border-neutral-800 px-1 py-1">
          <div className="text-neutral-600">PnL</div>
          <div className={`text-sm font-bold tabular-nums ${backtest.total_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
            {money(backtest.total_pnl)}
          </div>
          <div className="text-[9px] text-neutral-600">已完成仓位</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-1">
        <div>
          <div className="text-neutral-600">结算率</div>
          <div className="text-neutral-300 tabular-nums">{pct(backtest.settlement_rate)}</div>
        </div>
        <div>
          <div className="text-neutral-600">Brier</div>
          <div className="text-neutral-300 tabular-nums">{backtest.brier_score.toFixed(3)}</div>
        </div>
        <div>
          <div className="text-neutral-600">实际EV</div>
          <div className={`${backtest.avg_actual_return >= 0 ? 'text-green-500' : 'text-red-500'} tabular-nums`}>
            {pct(backtest.avg_actual_return)}
          </div>
        </div>
      </div>

      <div className="border-t border-neutral-800 pt-1 space-y-1">
        <div className="text-neutral-600">来源表现</div>
        {topSources.length === 0 ? (
          <div className="text-neutral-700">暂无来源样本</div>
        ) : topSources.map(source => (
          <div key={source.source} className="flex items-center justify-between gap-2">
            <span className="text-neutral-400">{source.source}</span>
            <span className="tabular-nums">{source.resolved}/{source.count}</span>
            <span className={source.pnl >= 0 ? 'text-green-500' : 'text-red-500'}>{money(source.pnl)}</span>
          </div>
        ))}
      </div>

      <div className="border-t border-neutral-800 pt-1 leading-relaxed text-[9px] text-neutral-600">
        {sampleWeak ? '样本还少，先观察，不建议接实盘。' : '样本量开始可用于评估。'}
        {' '}P 是模型胜率，EV收益是赔率折算后的期望回报；参考项目的 edge 更接近这里的“概率差”。
      </div>
    </div>
  )
}
