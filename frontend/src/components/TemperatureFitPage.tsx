import { useMemo, useState } from 'react'
import { AlertTriangle, ArrowLeft } from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
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
  if (value === undefined || value === null || Number.isNaN(Number(value))) return '--'
  return `${Number(value).toFixed(1)}F`
}

function temp(value?: number | null, unit = '') {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return '--'
  return `${Number(value).toFixed(1)}°${unit}`
}

function statusLabel(status?: string) {
  if (status === 'eligible') return '可用于实盘校准'
  if (status === 'watch') return '观察'
  if (status === 'blocked') return '禁止实盘'
  return '未知'
}

function reasonLabel(reason: string) {
  const map: Record<string, string> = {
    truth_not_high_confidence: 'truth 置信度不足',
    fit_samples_too_low: '样本太少',
    fit_samples_low: '样本偏少',
    fit_independent_days_too_low: '独立结算日太少',
    fit_independent_days_low: '独立结算日不足',
    fit_mae_block: 'MAE 过高',
    fit_mae_watch: 'MAE 偏高',
    fit_bias_block: 'Bias 过高',
    fit_bias_watch: 'Bias 偏高',
    research_history_not_live_settlement_truth: '研究级历史天气，不是实盘结算 truth',
  }
  return map[reason] ?? reason
}

function ScatterTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload as TemperatureFitRecord
  return (
    <div className="border border-neutral-800 bg-neutral-950 px-2 py-1.5 text-[11px] text-neutral-300">
      <div className="mb-1 text-neutral-100">{row.city_name} / {row.target_date}</div>
      <div>预测 {temp(row.forecast, row.unit)}，实际 {temp(row.actual, row.unit)}</div>
      <div>误差 {f(row.error_f)}，来源 {row.best_source || row.source}</div>
      <div>truth {row.actual_provider || 'unknown'}，{row.calibration_eligible ? '实盘校准' : '研究拟合'}</div>
    </div>
  )
}

function CityTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload as TemperatureFitGroup
  return (
    <div className="border border-neutral-800 bg-neutral-950 px-2 py-1.5 text-[11px] text-neutral-300">
      <div className="mb-1 text-neutral-100">{row.city_name}</div>
      <div>MAE {f(row.mae_f)}，Bias {f(row.bias_f)}，样本 {row.samples}</div>
      <div>{statusLabel(row.fit_status)}</div>
    </div>
  )
}

export function TemperatureFitPage({ data, loading, onBack }: Props) {
  const [selectedCity, setSelectedCity] = useState('all')
  const fit = data
  const records = fit?.records ?? []
  const cities = fit?.cities ?? []
  const summary = fit?.summary
  const visibleRecords = useMemo(() => {
    return selectedCity === 'all' ? records : records.filter(row => row.city_key === selectedCity)
  }, [records, selectedCity])
  const worstCities = [...cities].sort((a, b) => (b.mae_f ?? 0) - (a.mae_f ?? 0)).slice(0, 12)
  const providerCounts = Object.entries(summary?.provider_counts ?? {})
  const tierCounts = Object.entries(summary?.tier_counts ?? {})
  const ineligibleCounts = Object.entries(summary?.ineligible_counts ?? {})

  return (
    <div className="flex min-h-screen flex-col bg-black text-neutral-200 xl:h-screen xl:overflow-hidden">
      <header className="flex shrink-0 items-start gap-3 border-b border-neutral-800 px-3 py-2 sm:items-center">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-1 border border-neutral-700 bg-neutral-900 px-2 py-1 text-[11px] text-neutral-300 hover:border-neutral-500"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          返回看板
        </button>
        <div>
          <h1 className="text-sm font-semibold text-neutral-100">温度拟合与结算源分析</h1>
          <div className="text-[11px] text-neutral-600">用已结算天气日检查模型偏差；低置信 truth 只用于观察，不应直接放进实盘校准。</div>
        </div>
      </header>

      {loading || !fit ? (
        <div className="flex flex-1 items-center justify-center text-neutral-600">正在加载拟合数据...</div>
      ) : records.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-6 text-center">
          <div className="max-w-xl border border-neutral-800 p-5">
            <AlertTriangle className="mx-auto mb-3 h-8 w-8 text-amber-400" />
            <div className="mb-2 text-neutral-100">目前没有可展示的拟合样本</div>
            <p className="text-sm leading-relaxed text-neutral-500">
              这通常表示本地还没有 actual_temp，也没有历史天气缓存。可以先回首页点击“补历史数据”，
              然后这里会出现研究级预测 vs 实际误差；实盘校准仍需要更高置信的站点 truth。
            </p>
          </div>
        </div>
      ) : (
        <main className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto xl:grid-cols-[320px_minmax(0,1fr)] xl:overflow-hidden">
          <aside className="border-b border-neutral-800 p-3 text-[11px] xl:min-h-0 xl:overflow-y-auto xl:border-b-0 xl:border-r">
            <div className="mb-3 grid grid-cols-2 gap-2">
              <div className="border border-neutral-800 p-2">
                <div className="text-neutral-600">独立天气日</div>
                <div className="tabular-nums text-xl font-semibold text-neutral-100">{summary?.markets ?? 0}</div>
              </div>
              <div className="border border-neutral-800 p-2">
                <div className="text-neutral-600">实盘校准天气日</div>
                <div className="tabular-nums text-xl font-semibold text-green-300">{summary?.eligible_markets ?? 0}</div>
              </div>
              <div className="border border-neutral-800 p-2">
                <div className="text-neutral-600">MAE</div>
                <div className="tabular-nums text-xl font-semibold text-cyan-300">{f(summary?.mae_f)}</div>
              </div>
              <div className="border border-neutral-800 p-2">
                <div className="text-neutral-600">Bias</div>
                <div className="tabular-nums text-xl font-semibold text-amber-300">{f(summary?.bias_f)}</div>
              </div>
            </div>

            <div className="mb-3 border border-neutral-800 p-2">
              <div className="mb-2 text-neutral-500">城市实盘可用性</div>
              <div className="grid grid-cols-3 gap-1 text-center">
                <div className="border border-green-500/20 bg-green-500/5 p-1">
                  <div className="tabular-nums text-green-300">{fit.readiness_counts?.eligible ?? 0}</div>
                  <div className="text-[9px] text-neutral-600">可用</div>
                </div>
                <div className="border border-amber-500/20 bg-amber-500/5 p-1">
                  <div className="tabular-nums text-amber-300">{fit.readiness_counts?.watch ?? 0}</div>
                  <div className="text-[9px] text-neutral-600">观察</div>
                </div>
                <div className="border border-red-500/20 bg-red-500/5 p-1">
                  <div className="tabular-nums text-red-300">{fit.readiness_counts?.blocked ?? 0}</div>
                  <div className="text-[9px] text-neutral-600">禁止</div>
                </div>
              </div>
            </div>

            <label className="mb-1 block text-neutral-600">城市筛选</label>
            <select
              value={selectedCity}
              onChange={event => setSelectedCity(event.target.value)}
              className="mb-3 w-full border border-neutral-800 bg-black px-2 py-1 text-neutral-300"
            >
              <option value="all">全部城市</option>
              {cities.map(city => <option key={city.city_key} value={city.city_key}>{city.city_name}</option>)}
            </select>

            <div className="mb-3 border border-neutral-800 p-2">
              <div className="mb-1 text-neutral-500">样本分层</div>
              {tierCounts.length ? tierCounts.map(([tier, count]) => (
                <div key={tier} className="flex items-center justify-between border-b border-neutral-900 py-1 last:border-b-0">
                  <span>{tier === 'live_truth' ? '实盘结算校准' : tier === 'research_truth' ? '研究级历史拟合' : tier}</span>
                  <span className="tabular-nums text-neutral-300">{count}</span>
                </div>
              )) : <div className="text-neutral-600">暂无样本分层</div>}
              <p className="mt-2 text-[10px] leading-relaxed text-neutral-600">
                研究级历史样本用于快速发现偏差和调算法；只有实盘结算校准样本可用于解锁自动实盘。
              </p>
              <p className="mt-1 text-[10px] leading-relaxed text-neutral-600">
                页面按城市/日期去重，当前展示 {summary?.observed_samples ?? records.length} 个独立天气日；
                后台仍保留 {summary?.snapshot_samples ?? records.length} 个原始扫描快照供审计。
              </p>
            </div>

            <div className="mb-3 border border-neutral-800 p-2">
              <div className="mb-1 text-neutral-500">truth 来源</div>
              {providerCounts.length ? providerCounts.map(([provider, count]) => (
                <div key={provider} className="flex items-center justify-between border-b border-neutral-900 py-1 last:border-b-0">
                  <span>{provider}</span>
                  <span className="tabular-nums text-neutral-300">{count}</span>
                </div>
              )) : <div className="text-neutral-600">暂无来源统计</div>}
            </div>

            {ineligibleCounts.length > 0 && (
              <div className="mb-3 border border-amber-500/20 bg-amber-500/5 p-2">
                <div className="mb-1 text-amber-200">不能用于实盘校准的原因</div>
                {ineligibleCounts.map(([reason, count]) => (
                  <div key={reason} className="flex items-center justify-between py-0.5">
                    <span>{reasonLabel(reason)}</span>
                    <span className="tabular-nums">{count}</span>
                  </div>
                ))}
              </div>
            )}

            <div className="space-y-1 text-[10px] leading-relaxed text-neutral-600">
              {fit.notes.map(note => <p key={note}>{note}</p>)}
            </div>
          </aside>

          <section className="grid min-h-0 grid-rows-[360px_300px_460px] xl:grid-rows-[46%_26%_28%] xl:overflow-hidden">
            <div className="min-h-0 border-b border-neutral-800 p-3">
              <div className="mb-2 flex items-center justify-between">
                <div>
                  <div className="text-sm text-neutral-100">预测 vs 实际</div>
                  <div className="text-[11px] text-neutral-600">越靠近斜线越好；偏离越大，越不适合自动实盘。</div>
                </div>
                <div className="text-[11px] text-neutral-500">显示 {visibleRecords.length} 个独立天气日</div>
              </div>
              <ResponsiveContainer width="100%" height="84%">
                <ScatterChart margin={{ top: 12, right: 20, bottom: 4, left: 0 }}>
                  <CartesianGrid stroke="#1f1f1f" strokeDasharray="3 3" />
                  <XAxis dataKey="actual_f" type="number" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} domain={['dataMin - 3', 'dataMax + 3']} />
                  <YAxis dataKey="forecast_f" type="number" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} domain={['dataMin - 3', 'dataMax + 3']} />
                  <Tooltip content={<ScatterTooltip />} />
                  <Scatter data={visibleRecords} fill="#38bdf8" fillOpacity={0.8} />
                </ScatterChart>
              </ResponsiveContainer>
            </div>

            <div className="min-h-0 border-b border-neutral-800 p-3">
              <div className="mb-2 text-sm text-neutral-100">城市误差排行</div>
              <ResponsiveContainer width="100%" height="82%">
                <BarChart data={worstCities} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 24 }}>
                  <CartesianGrid stroke="#1f1f1f" horizontal={false} />
                  <XAxis type="number" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} />
                  <YAxis dataKey="city_name" type="category" width={96} stroke="#737373" fontSize={10} tickLine={false} axisLine={false} />
                  <Tooltip content={<CityTooltip />} />
                  <Bar dataKey="mae_f" fill="#f59e0b" />
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className="min-h-0 overflow-y-auto p-3">
              <table className="w-full table-fixed text-[11px]">
                <thead className="sticky top-0 bg-black text-left text-neutral-600">
                  <tr className="border-b border-neutral-800">
                    <th className="w-28 py-1">城市</th>
                    <th className="w-20 py-1">日期</th>
                    <th className="w-16 py-1 text-right">预测</th>
                    <th className="w-16 py-1 text-right">实际</th>
                    <th className="w-16 py-1 text-right">误差</th>
                    <th className="w-24 py-1 text-right">truth</th>
                    <th className="w-24 py-1 text-right">样本层级</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRecords.slice(-120).reverse().map(row => (
                    <tr key={`${row.city_key}-${row.target_date}-${row.timestamp}-${row.hours_left}`} className="border-b border-neutral-900 text-neutral-400">
                      <td className="truncate py-1">{row.city_name}</td>
                      <td className="py-1 tabular-nums text-neutral-500">{row.target_date}</td>
                      <td className="py-1 text-right tabular-nums">{temp(row.forecast, row.unit)}</td>
                      <td className="py-1 text-right tabular-nums">{temp(row.actual, row.unit)}</td>
                      <td className={`py-1 text-right tabular-nums ${Math.abs(row.error_f) <= 2 ? 'text-green-300' : Math.abs(row.error_f) <= 4 ? 'text-amber-300' : 'text-red-300'}`}>
                        {f(row.error_f)}
                      </td>
                      <td className="truncate py-1 text-right text-neutral-500">{row.actual_provider || 'unknown'}</td>
                      <td className="py-1 text-right">
                        <span
                          className={`border px-1 py-0.5 text-[9px] ${
                            row.calibration_eligible ? 'border-green-500/30 text-green-300' : 'border-amber-500/30 text-amber-300'
                          }`}
                          title={row.reason_if_ineligible || ''}
                        >
                          {row.calibration_eligible ? '实盘校准' : '研究拟合'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </main>
      )}
    </div>
  )
}
