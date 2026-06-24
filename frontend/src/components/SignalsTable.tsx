import { ChevronDown, Check, ExternalLink, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { Signal, WeatherSignal } from '../types'

interface Props {
  signals: Signal[]
  weatherSignals: WeatherSignal[]
  onSimulateTrade: (ticker: string) => void
  isSimulating: boolean
  onSignalStatus?: (signalId: number, status: string, amount?: number) => void
  onLiveOrder?: (signalId: number, amount?: number) => void
  liveModeAvailable?: boolean
  tradeMode?: 'paper' | 'live'
}

function pct(value?: number | null, signed = false) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  const n = Number(value) * 100
  return `${signed && n > 0 ? '+' : ''}${n.toFixed(1)}%`
}

function cents(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${(Number(value) * 100).toFixed(1)}c`
}

function money(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `$${Number(value).toFixed(2)}`
}

function statusText(signal: WeatherSignal) {
  if (signal.paper_position || signal.status === 'simulated') return '已模拟'
  if (signal.status === 'bought') return '已标记实盘'
  if (signal.status === 'skipped') return '已跳过'
  if (signal.actionable && signal.live_allowed) return '可模拟，实盘待确认'
  if (signal.actionable) return '建议先模拟'
  return '观察'
}

function statusClass(signal: WeatherSignal) {
  const label = statusText(signal)
  if (label.includes('已模拟')) return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300'
  if (label.includes('建议')) return 'border-green-500/30 bg-green-500/10 text-green-300'
  if (label.includes('实盘')) return 'border-blue-500/30 bg-blue-500/10 text-blue-300'
  if (label.includes('跳过')) return 'border-neutral-700 bg-neutral-900 text-neutral-500'
  return 'border-amber-500/30 bg-amber-500/10 text-amber-300'
}

function reasonLabel(reason: string) {
  const map: Record<string, string> = {
    strategy_not_ready: '策略尚未达到实盘标准',
    fit_missing: '缺少城市拟合样本',
    fit_independent_days_too_low: '独立结算日太少',
    fit_independent_days_low: '独立结算日不足',
    truth_missing: '缺少结算 truth',
    truth_observations_below_min: 'truth 样本不足',
    open_meteo_truth_fallback_present: '仍含 Open-Meteo fallback',
    legacy_truth_unknown: '存在旧版未知 truth',
    distribution_missing: '缺少整场分布',
    distribution_edge_negative: '归一化后无优势',
    spread_cost_too_high: 'spread 成本太高',
    spread_above_limit: 'spread 过宽',
    low_price_tail_unverified: '低价尾部未验证',
    price_below_min: '价格低于策略下限',
    price_above_max: '价格高于上限',
    expired_signal: '信号已过期',
    already_simulated: '已模拟',
    already_bought: '已买入/标记',
    already_skipped: '已跳过',
    city_bias_high: '该城市历史偏差较大',
    truth_independent_days_low: '高置信结算日不足',
    strategy_score_low: '综合策略评分不足',
    fit_sample_low: '拟合样本偏少',
  }
  return map[reason] ?? reason
}

function shortToken(token?: string) {
  if (!token) return ''
  return `${token.slice(0, 8)}...${token.slice(-6)}`
}

export function SignalsTable({
  weatherSignals,
  onSignalStatus,
  onLiveOrder,
  liveModeAvailable = false,
  tradeMode = 'paper',
}: Props) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [amounts, setAmounts] = useState<Record<string, string>>({})

  const sorted = useMemo(() => {
    return [...weatherSignals].sort((a, b) => {
      const aDone = a.paper_position || ['simulated', 'bought', 'skipped'].includes(a.status || '')
      const bDone = b.paper_position || ['simulated', 'bought', 'skipped'].includes(b.status || '')
      if (aDone !== bDone) return aDone ? 1 : -1
      if (a.actionable !== b.actionable) return a.actionable ? -1 : 1
      return Math.abs((b.edge ?? 0)) - Math.abs((a.edge ?? 0))
    })
  }, [weatherSignals])

  if (sorted.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1 p-6 text-center text-neutral-600">
        <div className="text-sm text-neutral-400">暂无天气信号</div>
        <div className="text-[11px]">启动扫描器后，符合日期和盘口条件的信号会出现在这里。</div>
      </div>
    )
  }

  return (
    <div className="divide-y divide-neutral-900">
      {sorted.map(signal => {
        const key = `${signal.id ?? signal.market_id}`
        const isExpanded = expanded === key
        const locked = signal.paper_position || ['simulated', 'bought', 'skipped'].includes(signal.status || '')
        const liveAllowed = Boolean(liveModeAvailable && signal.live_allowed)
        const amount = amounts[key] ?? String(signal.sim_amount ?? signal.suggested_size ?? '')
        const parsedAmount = Number(amount)
        const amountForSave = Number.isFinite(parsedAmount) ? parsedAmount : signal.suggested_size
        const liveReasons = signal.live_block_reasons ?? []
        const qualityFlags = signal.quality_flags ?? []
        const decisionReasons = signal.decision?.reasons ?? []
        const allReasons = [...new Set([...decisionReasons, ...liveReasons, ...qualityFlags])]

        return (
          <div key={key} className="bg-black text-[11px] hover:bg-neutral-950">
            <button
              type="button"
              onClick={() => setExpanded(isExpanded ? null : key)}
              className="grid w-full grid-cols-[18px_1fr_72px_84px] items-start gap-2 px-2 py-2 text-left"
            >
              <ChevronDown className={`mt-0.5 h-3.5 w-3.5 text-neutral-600 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
              <div className="min-w-0">
                <div className="break-words leading-snug text-neutral-200">{signal.question || signal.market_id}</div>
                <div className="mt-1 flex flex-wrap items-center gap-1">
                  <span className={`border px-1.5 py-0.5 text-[9px] ${statusClass(signal)}`}>{statusText(signal)}</span>
                  <span className="text-[9px] text-neutral-600">{signal.city_name} / {signal.target_date}</span>
                  <span className="text-[9px] text-neutral-600">YES {cents(signal.limit_price ?? signal.market_probability)}</span>
                </div>
              </div>
              <div className="text-right">
                <div className={signal.edge >= 0 ? 'tabular-nums text-green-400' : 'tabular-nums text-red-400'}>
                  {pct(signal.probability_edge ?? signal.edge, true)}
                </div>
                <div className="text-[9px] text-neutral-600">模型概率差</div>
              </div>
              <div className="text-right">
                <div className="tabular-nums text-blue-300">{money(signal.suggested_size)}</div>
                <div className="text-[9px] text-neutral-600">{tradeMode === 'paper' ? '模拟金额' : '实盘上限'}</div>
              </div>
            </button>

            {isExpanded && (
              <div className="space-y-3 border-t border-neutral-900 px-3 py-3 text-neutral-400">
                <div className="grid grid-cols-2 gap-2">
                  <div className="border border-neutral-800 p-2">
                    <div className="text-[9px] text-neutral-600">模型概率 / 市场价格</div>
                    <div className="tabular-nums text-neutral-100">{pct(signal.model_probability)} / {cents(signal.limit_price ?? signal.market_probability)}</div>
                    <div className="mt-1 text-[10px] text-neutral-600">
                      归一化概率 {pct(signal.distribution?.signal_probability)}，概率差 {pct(signal.probability_edge, true)}
                    </div>
                  </div>
                  <div className="border border-neutral-800 p-2">
                    <div className="text-[9px] text-neutral-600">盘口成本</div>
                    <div className="tabular-nums text-neutral-100">Bid {cents(signal.bid_price)} / Ask {cents(signal.limit_price)}</div>
                    <div className="mt-1 text-[10px] text-neutral-600">Spread {cents(signal.spread)}，买入后立即按 bid 估值会先显示浮亏。</div>
                  </div>
                </div>

                <div className="border border-neutral-800 p-2">
                  <div className="mb-1 text-[10px] text-neutral-500">操作</div>
                  <div className="grid grid-cols-2 gap-2">
                    <label className="col-span-2">
                      <span className="mb-1 block text-[9px] text-neutral-600">买入金额</span>
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={amount}
                        onChange={event => setAmounts(prev => ({ ...prev, [key]: event.target.value }))}
                        className="w-full px-2 py-1 text-right tabular-nums"
                        aria-label="模拟买入金额"
                      />
                    </label>
                    <button
                      onClick={() => signal.id && onSignalStatus?.(signal.id, 'simulated', amountForSave)}
                      disabled={locked || !signal.id}
                      className={`inline-flex min-h-9 items-center gap-1 border px-2 py-1 disabled:opacity-30 ${
                        tradeMode === 'paper'
                          ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200 hover:bg-cyan-500/15'
                          : 'border-neutral-700 text-neutral-400 hover:bg-neutral-900'
                      }`}
                    >
                      <Check className="h-3.5 w-3.5" />
                      模拟买入
                    </button>
                    <button
                      onClick={() => signal.id && onLiveOrder?.(signal.id, amountForSave)}
                      disabled={locked || !signal.id || !liveAllowed}
                      className={`min-h-9 border px-2 py-1 disabled:cursor-not-allowed disabled:opacity-30 ${
                        tradeMode === 'live'
                          ? 'border-blue-500/40 bg-blue-500/10 text-blue-200 hover:bg-blue-500/15'
                          : 'border-blue-500/20 text-blue-300 hover:bg-blue-500/10'
                      }`}
                      title={liveAllowed ? '执行后端实盘/dry-run 检查' : '当前实盘未开放或该信号未通过实盘门槛'}
                    >
                      {liveModeAvailable ? '实盘买入' : '实盘未开放'}
                    </button>
                    <button
                      onClick={() => signal.id && onSignalStatus?.(signal.id, 'skipped')}
                      disabled={locked || !signal.id}
                      className="inline-flex items-center gap-1 border border-red-500/30 px-2 py-1 text-red-300 hover:bg-red-500/10 disabled:opacity-30"
                    >
                      <X className="h-3.5 w-3.5" />
                      跳过
                    </button>
                    {signal.event_url && (
                      <a
                        href={signal.event_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex min-h-9 items-center justify-center gap-1 border border-neutral-700 px-2 py-1 text-neutral-300 hover:text-cyan-300"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                        Polymarket
                      </a>
                    )}
                  </div>
                </div>

                <div className="grid gap-2 md:grid-cols-2">
                  <div className="border border-neutral-800 p-2 leading-relaxed">
                    <div className="mb-1 text-[10px] text-neutral-500">为什么买/不买</div>
                    <p className="break-words">{signal.reasoning}</p>
                    {allReasons.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {allReasons.slice(0, 8).map(reason => (
                          <span key={reason} className="border border-amber-500/20 bg-amber-500/5 px-1.5 py-0.5 text-[9px] text-amber-200">
                            {reasonLabel(reason)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="border border-neutral-800 p-2 leading-relaxed">
                    <div className="mb-1 text-[10px] text-neutral-500">校准与 truth</div>
                    <div>独立样本 {signal.fit_markets ?? 0} 天 / 快照 {signal.fit_samples ?? 0}</div>
                    <div>MAE {signal.fit_mae_f?.toFixed?.(1) ?? '--'}F / Bias {signal.fit_bias_f?.toFixed?.(1) ?? '--'}F</div>
                    <div>truth {signal.truth?.latest_provider || '缺失'} / 站点 {signal.truth?.station_id || '未映射'}</div>
                    {signal.yes_token_id && <div className="mt-1 break-all text-neutral-600">YES token {shortToken(signal.yes_token_id)}</div>}
                  </div>
                </div>

                {signal.distribution?.top_model?.length ? (
                  <div className="border border-neutral-800 p-2">
                    <div className="mb-2 text-[10px] text-neutral-500">整场 bucket 分布 Top</div>
                    <div className="grid gap-1 sm:grid-cols-3">
                      {signal.distribution.top_model.slice(0, 6).map(item => (
                        <div
                          key={item.market_id}
                          className={`border px-2 py-1 ${item.is_signal ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200' : 'border-neutral-800 text-neutral-400'}`}
                        >
                          <div className="truncate">{item.bucket_low}-{item.bucket_high}</div>
                          <div className="tabular-nums text-[10px]">P {pct(item.probability)} / Ask {cents(item.ask)}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
