import { BarChart3, ShieldCheck } from 'lucide-react'
import type { BacktestRiskSlice, BacktestSummary } from '../types'

interface Props {
  backtest?: BacktestSummary | null
  onOpenFit?: () => void
}

function pct(value: number) {
  return `${(value * 100).toFixed(0)}%`
}

function money(value: number) {
  return `${value >= 0 ? '+' : '-'}$${Math.abs(value).toFixed(2)}`
}

function sliceName(name: string) {
  switch (name) {
    case 'gate_allowed': return '规则允许'
    case 'gate_blocked': return '规则拦截'
    case '<3c': return '<3c'
    case '3-10c': return '3-10c'
    case '10-25c': return '10-25c'
    case '25-45c': return '25-45c'
    case '>45c': return '>45c'
    case 'fit_missing': return '缺拟合'
    case 'sample_low': return '样本少'
    case 'mae_high': return 'MAE 高'
    case 'bias_high': return 'Bias 高'
    case 'fit_ok': return '拟合可用'
    case 'city_mae_high': return '城市 MAE 高'
    case 'city_bias_high': return '城市 Bias 高'
    case 'price_below_min': return '价格低于 3c'
    case 'price_above_max': return '价格高于 45c'
    case 'spread_above_limit': return 'spread 过宽'
    case 'spread_missing': return '缺 spread'
    case 'all_completed': return '全部仓位'
    case 'current_gate_allowed': return '当前允许组'
    case 'price_3_45c': return '3-45c'
    case 'price_10_45c': return '10-45c'
    case 'price_10_25c': return '10-25c'
    case 'not_fit_missing': return '排除缺拟合'
    case 'gate_allowed_10_45c': return '允许组 + 10-45c'
    case 'gate_allowed_fit_ok': return '允许组 + 拟合可用'
    case 'gate_allowed_fit_ok_10_45c': return '允许组 + 拟合可用 + 10-45c'
    case 'avoid_d0_metar_gap': return '排除临近背离'
    case 'gate_avoid_d0_metar_gap_10_45c': return '允许组 + 排除背离'
    case 'near_lock_metar_aligned': return 'Near-lock 对齐'
    case 'bias_adjusted_in_bucket': return '校正后仍落桶'
    case 'gate_bias_adjusted_10_45c': return '允许组 + 校正落桶'
    case 'cheap_underdispersed_tail': return '低价波动尾部'
    default: return name
  }
}

function warnLabel(warning: string) {
  switch (warning) {
    case 'settled_sample_missing': return '无结算样本'
    case 'sample_low': return '样本少'
    case 'pnl_negative': return '亏损'
    case 'roi_negative': return 'ROI 负'
    case 'win_rate_low': return '胜率低'
    default: return warning
  }
}

function SliceRow({ row, showRoi = false }: { row: BacktestRiskSlice; showRoi?: boolean }) {
  return (
    <div className="grid grid-cols-[1fr_36px_48px_52px] items-center gap-1">
      <span className="truncate text-neutral-400" title={row.name}>{sliceName(row.name)}</span>
      <span className="text-right tabular-nums text-neutral-500">{row.resolved}/{row.count}</span>
      <span className={`text-right tabular-nums ${row.win_rate >= 0.55 ? 'text-green-500' : row.resolved ? 'text-red-500' : 'text-neutral-600'}`}>
        {showRoi ? pct(row.roi) : row.resolved ? pct(row.win_rate) : '--'}
      </span>
      <span className={`text-right tabular-nums ${row.pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
        {money(row.pnl)}
      </span>
    </div>
  )
}

function PolicyCandidateRow({ row }: { row: BacktestRiskSlice }) {
  const warnings = row.warnings ?? []
  return (
    <div className="space-y-0.5 border-b border-neutral-900 pb-1 last:border-b-0">
      <div className="grid grid-cols-[1fr_38px_48px_52px] items-center gap-1">
        <span className="truncate text-neutral-300" title={row.description || row.name}>{sliceName(row.name)}</span>
        <span className="text-right tabular-nums text-neutral-500">{row.resolved}/{row.count}</span>
        <span className={`text-right tabular-nums ${row.roi >= 0 ? 'text-green-500' : 'text-red-500'}`}>{pct(row.roi)}</span>
        <span className={`text-right tabular-nums ${row.pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>{money(row.pnl)}</span>
      </div>
      <div className="flex items-center justify-between gap-2 text-[9px] text-neutral-600">
        <span className="truncate">{row.description}</span>
        {warnings.length > 0 && <span className="shrink-0 text-amber-500/80">{warnings.map(warnLabel).join('、')}</span>}
      </div>
    </div>
  )
}

export function BacktestPanel({ backtest, onOpenFit }: Props) {
  if (!backtest || backtest.total_positions === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-3 text-center text-[10px] text-neutral-600">
        <div>暂无可复盘样本</div>
        <button
          onClick={onOpenFit}
          className="inline-flex items-center gap-1 border border-cyan-500/30 px-2 py-1 text-cyan-400 hover:bg-cyan-500/10"
        >
          <BarChart3 className="h-3 w-3" />
          温度拟合分析
        </button>
      </div>
    )
  }

  const sampleWeak = backtest.resolved_positions < 30
  const topSources = backtest.sources.slice(0, 3)
  const gateRows = (backtest.risk_slices ?? []).filter(row => row.kind === 'gate')
  const priceRows = (backtest.risk_slices ?? []).filter(row => row.kind === 'price_bucket')
  const fitRows = (backtest.risk_slices ?? []).filter(row => row.kind === 'fit_band')
  const blockerRows = (backtest.block_reasons ?? []).slice(0, 4)
  const policyRows = (backtest.policy_candidates ?? []).slice(0, 5)

  return (
    <div className="h-full space-y-2 overflow-y-auto p-2 text-[10px] text-neutral-500">
      <button
        onClick={onOpenFit}
        className="flex w-full items-center justify-center gap-1 border border-cyan-500/30 px-2 py-1 text-cyan-400 hover:bg-cyan-500/10"
      >
        <BarChart3 className="h-3 w-3" />
        查看城市温度拟合
      </button>

      <div className="grid grid-cols-3 gap-1">
        <div className="border border-neutral-800 px-1 py-1">
          <div className="text-neutral-600">样本</div>
          <div className="tabular-nums text-sm font-bold text-neutral-200">
            {backtest.resolved_positions}/{backtest.total_positions}
          </div>
          <div className="text-[9px] text-neutral-600">已结算 / 总仓位</div>
        </div>
        <div className="border border-neutral-800 px-1 py-1">
          <div className="text-neutral-600">胜率</div>
          <div className={`tabular-nums text-sm font-bold ${backtest.win_rate >= 0.55 ? 'text-green-500' : 'text-red-500'}`}>
            {pct(backtest.win_rate)}
          </div>
          <div className="text-[9px] text-neutral-600">{backtest.wins}/{backtest.resolved_positions}</div>
        </div>
        <div className="border border-neutral-800 px-1 py-1">
          <div className="text-neutral-600">PnL</div>
          <div className={`tabular-nums text-sm font-bold ${backtest.total_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
            {money(backtest.total_pnl)}
          </div>
          <div className="text-[9px] text-neutral-600">已完成仓位</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-1">
        <div>
          <div className="text-neutral-600">结算率</div>
          <div className="tabular-nums text-neutral-300">{pct(backtest.settlement_rate)}</div>
        </div>
        <div>
          <div className="text-neutral-600">Brier</div>
          <div className="tabular-nums text-neutral-300">{backtest.brier_score.toFixed(3)}</div>
        </div>
        <div>
          <div className="text-neutral-600">实际 EV</div>
          <div className={`${backtest.avg_actual_return >= 0 ? 'text-green-500' : 'text-red-500'} tabular-nums`}>
            {pct(backtest.avg_actual_return)}
          </div>
        </div>
      </div>

      <div className="space-y-1 border-t border-neutral-800 pt-1">
        <div className="flex items-center gap-1 text-neutral-600">
          <ShieldCheck className="h-3 w-3" />
          风控回放
        </div>
        {gateRows.length === 0 ? (
          <div className="text-neutral-700">暂无风控切片</div>
        ) : gateRows.map(row => <SliceRow key={`${row.kind}-${row.name}`} row={row} />)}
      </div>

      {blockerRows.length > 0 && (
        <div className="space-y-1 border-t border-neutral-800 pt-1">
          <div className="text-neutral-600">主要拦截原因</div>
          {blockerRows.map(row => <SliceRow key={`${row.kind}-${row.name}`} row={row} />)}
        </div>
      )}

      <div className="space-y-1 border-t border-neutral-800 pt-1">
        <div className="text-neutral-600">规则候选</div>
        {policyRows.length === 0 ? (
          <div className="text-neutral-700">暂无候选规则</div>
        ) : policyRows.map(row => <PolicyCandidateRow key={row.name} row={row} />)}
      </div>

      <div className="space-y-1 border-t border-neutral-800 pt-1">
        <div className="text-neutral-600">价格桶表现</div>
        {priceRows.slice(0, 5).map(row => <SliceRow key={`${row.kind}-${row.name}`} row={row} showRoi />)}
      </div>

      <div className="space-y-1 border-t border-neutral-800 pt-1">
        <div className="text-neutral-600">拟合质量表现</div>
        {fitRows.slice(0, 5).map(row => <SliceRow key={`${row.kind}-${row.name}`} row={row} />)}
      </div>

      <div className="space-y-1 border-t border-neutral-800 pt-1">
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

      <div className="border-t border-neutral-800 pt-1 text-[9px] leading-relaxed text-neutral-600">
        {sampleWeak ? '样本仍少，先观察，不建议接实盘。' : '样本量开始可用于初步评估。'}
        {' '}风控回放用于判断当前规则是否挡掉历史亏损仓位；温度拟合页用于判断天气模型本身是否偏。
      </div>
    </div>
  )
}
