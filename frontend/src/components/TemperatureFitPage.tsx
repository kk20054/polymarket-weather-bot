import { useMemo, useState } from 'react'
import { ArrowLeft, AlertTriangle } from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { TemperatureFitData, TemperatureFitGroup, TemperatureFitRecord } from '../types'

interface Props {
  data?: TemperatureFitData
  loading: boolean
  onBack: () => void
}

function f(value?: number | null) {
  if (value === undefined || value === null || Number.isNaN(value)) return '-'
  return `${value.toFixed(1)}F`
}

function nativeTemp(value?: number, unit?: string) {
  if (value === undefined || Number.isNaN(value)) return '-'
  return `${value.toFixed(1)}°${unit || ''}`
}

function biasLabel(value: number) {
  if (value > 0.5) return '偏热'
  if (value < -0.5) return '偏冷'
  return '中性'
}

function fitStatusLabel(status?: string) {
  switch (status) {
    case 'eligible': return '可交易'
    case 'watch': return '观察'
    case 'blocked': return '禁用'
    default: return '未知'
  }
}

function fitStatusClass(status?: string) {
  switch (status) {
    case 'eligible': return 'border-green-500/30 bg-green-500/10 text-green-400'
    case 'watch': return 'border-amber-500/30 bg-amber-500/10 text-amber-300'
    case 'blocked': return 'border-red-500/30 bg-red-500/10 text-red-400'
    default: return 'border-neutral-700 bg-neutral-900 text-neutral-500'
  }
}

function fitReasonLabel(reason: string) {
  switch (reason) {
    case 'fit_samples_too_low': return '样本过少'
    case 'fit_samples_low': return '样本偏少'
    case 'fit_independent_days_too_low': return '独立日过少'
    case 'fit_independent_days_low': return '独立日不足'
    case 'fit_markets_low': return '市场天数少'
    case 'fit_mae_block': return 'MAE过高'
    case 'fit_mae_watch': return 'MAE偏高'
    case 'fit_bias_block': return 'Bias过高'
    case 'fit_bias_watch': return 'Bias偏高'
    default: return reason
  }
}

function ErrorTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload as TemperatureFitRecord
  return (
    <div className="border border-neutral-800 bg-neutral-950 px-2 py-1.5 text-[10px] text-neutral-300">
      <div className="mb-1 text-neutral-100">{row.city_name} · {row.target_date}</div>
      <div>预测 {nativeTemp(row.forecast, row.unit)} / 实际 {nativeTemp(row.actual, row.unit)}</div>
      <div>误差 {f(row.error_f)} · {row.horizon || '-'} · {row.hours_left.toFixed(1)}h</div>
    </div>
  )
}

function CityTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload as TemperatureFitGroup
  return (
    <div className="border border-neutral-800 bg-neutral-950 px-2 py-1.5 text-[10px] text-neutral-300">
      <div className="mb-1 text-neutral-100">{row.city_name || row.source}</div>
      <div>MAE {f(row.mae_f)} / RMSE {f(row.rmse_f)}</div>
      <div>Bias {f(row.bias_f)} · 衰减 {f(row.decayed_bias_f)} · 样本 {row.samples}</div>
      <div>{fitStatusLabel(row.fit_status)} · score {((row.trade_score ?? 0) * 100).toFixed(0)}</div>
      {(row.fit_reasons ?? []).length > 0 && (
        <div className="text-amber-300">{(row.fit_reasons ?? []).map(fitReasonLabel).join('、')}</div>
      )}
    </div>
  )
}

export function TemperatureFitPage({ data, loading, onBack }: Props) {
  const [selectedCity, setSelectedCity] = useState('all')
  const fit = data
  const cities = fit?.cities ?? []
  const records = fit?.records ?? []
  const visibleRecords = useMemo(() => {
    return selectedCity === 'all' ? records : records.filter(row => row.city_key === selectedCity)
  }, [records, selectedCity])
  const cityByKey = useMemo(() => {
    return new Map(cities.map(city => [city.city_key, city]))
  }, [cities])
  const worstCities = cities.slice(0, 10)
  const sourceRows = fit?.sources ?? []
  const summary = fit?.summary ?? { markets: 0, samples: 0, mae_f: 0, bias_f: 0, rmse_f: 0 }
  const readiness = fit?.readiness_counts ?? { eligible: 0, watch: 0, blocked: 0 }
  const strategySummary = fit?.strategy_summary
  const sampleWeak = (summary?.markets ?? 0) < 30

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-black text-neutral-200">
      <header className="flex shrink-0 items-center gap-3 border-b border-neutral-800 px-3 py-2">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-1 border border-neutral-700 bg-neutral-900 px-2 py-1 text-[10px] text-neutral-300 hover:border-neutral-500"
        >
          <ArrowLeft className="h-3 w-3" />
          返回看板
        </button>
        <div>
          <h1 className="text-sm font-bold tracking-wide text-neutral-100">城市温度拟合复盘</h1>
          <div className="text-[10px] text-neutral-600">用实际最高温校验当前天气模型，先验证预测，再谈下注。</div>
        </div>
        <div className="flex-1" />
        {sampleWeak && (
          <div className="flex items-center gap-1 border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-300">
            <AlertTriangle className="h-3 w-3" />
            样本不足，暂不建议实盘放大
          </div>
        )}
      </header>

      {loading || !fit ? (
        <div className="flex flex-1 items-center justify-center text-[11px] text-neutral-600">正在加载拟合数据...</div>
      ) : (
        <main className="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)] overflow-hidden">
          <aside className="min-h-0 overflow-y-auto border-r border-neutral-800 p-3 text-[10px]">
            <div className="mb-3 grid grid-cols-2 gap-2">
              <div className="border border-neutral-800 p-2">
                <div className="text-neutral-600">市场数</div>
                <div className="tabular-nums text-lg font-bold text-neutral-100">{summary.markets}</div>
              </div>
              <div className="border border-neutral-800 p-2">
                <div className="text-neutral-600">快照数</div>
                <div className="tabular-nums text-lg font-bold text-neutral-100">{summary.samples}</div>
              </div>
              <div className="border border-neutral-800 p-2">
                <div className="text-neutral-600">MAE</div>
                <div className="tabular-nums text-lg font-bold text-cyan-300">{f(summary.mae_f)}</div>
              </div>
              <div className="border border-neutral-800 p-2">
                <div className="text-neutral-600">Bias</div>
                <div className={`tabular-nums text-lg font-bold ${Math.abs(summary.bias_f) <= 0.5 ? 'text-green-400' : 'text-amber-300'}`}>
                  {f(summary.bias_f)}
                </div>
                <div className="text-[9px] text-neutral-600">衰减 {f(summary.decayed_bias_f)}</div>
              </div>
              <div className="border border-neutral-800 p-2">
                <div className="text-neutral-600">MOS MAE</div>
                <div className="tabular-nums text-lg font-bold text-violet-300">{f(summary.mos_mae_f)}</div>
                <div className={`${(summary.mos_improvement_f ?? 0) > 0 ? 'text-green-400' : 'text-neutral-600'} text-[9px] tabular-nums`}>
                  改善 {f(summary.mos_improvement_f)}
                </div>
              </div>
              <div className="border border-neutral-800 p-2">
                <div className="text-neutral-600">MOS 公式</div>
                <div className="tabular-nums text-sm font-bold text-neutral-200">
                  {summary.mos_slope !== null && summary.mos_slope !== undefined ? summary.mos_slope.toFixed(2) : '-'}x
                </div>
                <div className="text-[9px] text-neutral-600">截距 {f(summary.mos_intercept_f)}</div>
              </div>
            </div>

            <div className="mb-3 border border-neutral-800 p-2">
              <div className="mb-1 text-neutral-600">交易可用分层</div>
              <div className="grid grid-cols-3 gap-1 text-center">
                <div className="border border-green-500/20 bg-green-500/5 px-1 py-1">
                  <div className="tabular-nums text-sm font-bold text-green-400">{readiness.eligible}</div>
                  <div className="text-[9px] text-neutral-600">可交易</div>
                </div>
                <div className="border border-amber-500/20 bg-amber-500/5 px-1 py-1">
                  <div className="tabular-nums text-sm font-bold text-amber-300">{readiness.watch}</div>
                  <div className="text-[9px] text-neutral-600">观察</div>
                </div>
                <div className="border border-red-500/20 bg-red-500/5 px-1 py-1">
                  <div className="tabular-nums text-sm font-bold text-red-400">{readiness.blocked}</div>
                  <div className="text-[9px] text-neutral-600">禁用</div>
                </div>
              </div>
              <div className="mt-1 text-[9px] leading-relaxed text-neutral-600">
                自动实盘只应考虑“可交易”城市；观察/禁用城市最多进入模拟池。
              </div>
            </div>

            <label className="mb-1 block text-neutral-600">城市筛选</label>
            <select
              value={selectedCity}
              onChange={event => setSelectedCity(event.target.value)}
              className="mb-3 w-full border border-neutral-800 bg-black px-2 py-1 text-neutral-300"
            >
              <option value="all">全部城市</option>
              {cities.map(city => (
                <option key={city.city_key} value={city.city_key}>{city.city_name}</option>
              ))}
            </select>

            <div className="mb-2 text-neutral-600">数据源误差</div>
            <div className="space-y-1">
              {sourceRows.map(source => (
                <div key={source.source} className="border border-neutral-800 px-2 py-1">
                  <div className="flex items-center justify-between">
                    <span className="text-neutral-300">{source.source}</span>
                    <span className={`border px-1 py-0.5 text-[8px] ${fitStatusClass(source.fit_status)}`}>
                      {fitStatusLabel(source.fit_status)}
                    </span>
                  </div>
                  <div className="text-[9px] text-neutral-600">
                    MAE {f(source.mae_f)} · Bias {f(source.bias_f)} · 衰减 {f(source.decayed_bias_f)} · score {((source.trade_score ?? 0) * 100).toFixed(0)}
                  </div>
                  {source.mos_mae_f !== null && source.mos_mae_f !== undefined && (
                    <div className="text-[9px] text-violet-300/80">
                      MOS MAE {f(source.mos_mae_f)} · 改善 {f(source.mos_improvement_f)}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {strategySummary && (
              <div className="mt-3 border-t border-neutral-800 pt-2">
                <div className="mb-2 text-neutral-600">策略回测切片</div>
                <div className="space-y-1">
                  <div className="border border-neutral-800 px-2 py-1">
                    <div className="flex items-center justify-between">
                      <span className="text-neutral-300">NEAR-LOCK / METAR</span>
                      <span className="tabular-nums text-cyan-300">{f(strategySummary.near_lock.mae_f)}</span>
                    </div>
                    <div className="text-[9px] text-neutral-600">
                      {strategySummary.near_lock.samples} 点 · Bias {f(strategySummary.near_lock.bias_f)}
                    </div>
                  </div>
                  <div className="border border-neutral-800 px-2 py-1">
                    <div className="flex items-center justify-between">
                      <span className="text-neutral-300">离散度不足</span>
                      <span className="tabular-nums text-amber-300">{(strategySummary.dispersion.underdispersed_rate * 100).toFixed(0)}%</span>
                    </div>
                    <div className="text-[9px] text-neutral-600">
                      {strategySummary.dispersion.underdispersed_cases}/{strategySummary.dispersion.samples} 个 D+1/D+2 样本超过 1.5x std
                    </div>
                  </div>
                </div>
              </div>
            )}

            <div className="mt-3 space-y-1 border-t border-neutral-800 pt-2 text-[9px] leading-relaxed text-neutral-600">
              {fit.notes.map(note => <p key={note}>{note}</p>)}
            </div>
          </aside>

          <section className="grid min-h-0 grid-rows-[48%_28%_24%] overflow-hidden">
            <div className="min-h-0 border-b border-neutral-800 p-3">
              <div className="mb-2 flex items-center justify-between">
                <div>
                  <div className="text-[11px] font-semibold text-neutral-200">预测 vs 实际</div>
                  <div className="text-[9px] text-neutral-600">越靠近斜线越好；偏离越大，模型越不适合自动下注。</div>
                </div>
                <div className="text-[10px] text-neutral-500">当前显示 {visibleRecords.length} 个预测快照</div>
              </div>
              <ResponsiveContainer width="100%" height="85%">
                <ScatterChart margin={{ top: 12, right: 20, bottom: 4, left: 0 }}>
                  <CartesianGrid stroke="#1f1f1f" strokeDasharray="3 3" />
                  <XAxis dataKey="actual_f" type="number" name="实际" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} domain={['dataMin - 3', 'dataMax + 3']} />
                  <YAxis dataKey="forecast_f" type="number" name="预测" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} domain={['dataMin - 3', 'dataMax + 3']} />
                  <Tooltip content={<ErrorTooltip />} />
                  <ReferenceLine segment={[{ x: 20, y: 20 }, { x: 115, y: 115 }]} stroke="#525252" strokeDasharray="4 4" />
                  <Scatter data={visibleRecords} fill="#06b6d4" fillOpacity={0.8} />
                </ScatterChart>
              </ResponsiveContainer>
            </div>

            <div className="min-h-0 border-b border-neutral-800 p-3">
              <div className="mb-2">
                <div className="text-[11px] font-semibold text-neutral-200">城市误差排行</div>
                <div className="text-[9px] text-neutral-600">优先排查 MAE 高、Bias 极端的城市；这些地方不应直接进入自动实盘。</div>
              </div>
              <ResponsiveContainer width="100%" height="78%">
                <BarChart data={worstCities} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 12 }}>
                  <CartesianGrid stroke="#1f1f1f" horizontal={false} />
                  <XAxis type="number" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} />
                  <YAxis dataKey="city_name" type="category" width={92} stroke="#737373" fontSize={10} tickLine={false} axisLine={false} />
                  <Tooltip content={<CityTooltip />} />
                  <Bar dataKey="mae_f" name="MAE">
                    {worstCities.map(city => (
                      <Cell
                        key={city.city_key}
                        fill={city.fit_status === 'eligible' ? '#22c55e' : city.fit_status === 'watch' ? '#f59e0b' : '#ef4444'}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className="min-h-0 overflow-y-auto p-3">
              <table className="w-full table-fixed text-[10px]">
                <thead className="sticky top-0 bg-black text-left text-neutral-600">
                  <tr className="border-b border-neutral-800">
                    <th className="w-28 py-1">城市</th>
                    <th className="w-20 py-1">日期</th>
                    <th className="w-16 py-1 text-right">预测</th>
                    <th className="w-16 py-1 text-right">实际</th>
                    <th className="w-16 py-1 text-right">误差</th>
                    <th className="w-20 py-1 text-right">窗口</th>
                    <th className="w-16 py-1 text-right">分层</th>
                    <th className="py-1 text-right">判断</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRecords.slice(-80).reverse().map(row => {
                    const cityFit = cityByKey.get(row.city_key)
                    return (
                      <tr key={`${row.city_key}-${row.target_date}-${row.timestamp}-${row.hours_left}`} className="border-b border-neutral-900 text-neutral-400">
                        <td className="truncate py-1">{row.city_name}</td>
                        <td className="py-1 tabular-nums text-neutral-500">{row.target_date}</td>
                        <td className="py-1 text-right tabular-nums">{nativeTemp(row.forecast, row.unit)}</td>
                        <td className="py-1 text-right tabular-nums">{nativeTemp(row.actual, row.unit)}</td>
                        <td className={`py-1 text-right tabular-nums ${Math.abs(row.error_f) <= 2 ? 'text-green-400' : Math.abs(row.error_f) <= 4 ? 'text-amber-300' : 'text-red-400'}`}>
                          {f(row.error_f)}
                        </td>
                        <td className="py-1 text-right tabular-nums text-neutral-500">{row.horizon || '-'} / {row.hours_left.toFixed(1)}h</td>
                        <td className="py-1 text-right">
                          <span className={`border px-1 py-0.5 text-[8px] ${fitStatusClass(cityFit?.fit_status)}`}>
                            {fitStatusLabel(cityFit?.fit_status)}
                          </span>
                        </td>
                        <td className="py-1 text-right text-neutral-500">{biasLabel(row.error_f)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </main>
      )}
    </div>
  )
}
