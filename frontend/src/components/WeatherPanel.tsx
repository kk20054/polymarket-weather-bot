import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { CloudSun, Database, ExternalLink, RefreshCw, Signal, ThermometerSun } from 'lucide-react'
import type { HistoricalWeatherPoint, WeatherCityPoint, WeatherCitySeries, WeatherForecast, WeatherSignal } from '../types'

interface Props {
  forecasts: WeatherForecast[]
  signals: WeatherSignal[]
  citySeries?: WeatherCitySeries[]
  selectedCity?: string
  onSelectedCity?: (cityKey: string) => void
  onBackfillHistory?: () => void
  backfilling?: boolean
  backfillResult?: {
    fetched: number
    errors: Array<{ city: string; error: string }>
  }
}

type EvidenceStatus = 'fresh' | 'stale' | 'missing'

function fmtTemp(value?: number | null, unit = 'F') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${Number(value).toFixed(1)}°${unit}`
}

function fmtPct(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${Number(value).toFixed(0)}%`
}

function shortDate(value?: string | null) {
  if (!value) return '--'
  try {
    const date = new Date(value.includes('T') ? value : `${value}T00:00:00`)
    return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  } catch {
    return value
  }
}

function shortTime(value?: string | null) {
  if (!value) return '--'
  try {
    return new Date(value).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch {
    return value
  }
}

function minutesSince(value?: string | null) {
  if (!value) return null
  const time = new Date(value).getTime()
  if (Number.isNaN(time)) return null
  return Math.max(0, (Date.now() - time) / 60000)
}

function freshnessLabel(value?: string | null) {
  const minutes = minutesSince(value)
  if (minutes === null) return '无数据'
  if (minutes < 60) return `${minutes.toFixed(0)} 分钟前`
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)} 小时前`
  return `${(minutes / 1440).toFixed(1)} 天前`
}

function evidenceStatus(value?: string | null, staleAfterMinutes = 180): EvidenceStatus {
  const minutes = minutesSince(value)
  if (minutes === null) return 'missing'
  return minutes <= staleAfterMinutes ? 'fresh' : 'stale'
}

function statusClass(status: EvidenceStatus) {
  if (status === 'fresh') return 'border-green-500/25 bg-green-500/5 text-green-200'
  if (status === 'stale') return 'border-amber-500/25 bg-amber-500/5 text-amber-200'
  return 'border-neutral-800 bg-neutral-950 text-neutral-500'
}

function latestBy<T>(items: T[], predicate: (item: T) => boolean, getter: (item: T) => string | undefined | null): T | undefined {
  return [...items]
    .filter(predicate)
    .sort((a, b) => String(getter(b) ?? '').localeCompare(String(getter(a) ?? '')))[0]
}

function uniqueCities(citySeries: WeatherCitySeries[], forecasts: WeatherForecast[]) {
  const rows = new Map<string, { key: string; name: string }>()
  for (const row of citySeries) rows.set(row.city_key, { key: row.city_key, name: row.city_name })
  for (const row of forecasts) rows.set(row.city_key, { key: row.city_key, name: row.city_name })
  return [...rows.values()].sort((a, b) => a.name.localeCompare(b.name))
}

function buildChartData(series?: WeatherCitySeries) {
  const byDate = new Map<string, any>()
  for (const point of series?.history_points ?? []) {
    const key = point.target_date
    byDate.set(key, {
      date: key,
      label: shortDate(key),
      actual_high: point.actual_high ?? null,
      humidity_mean: point.humidity_mean ?? null,
      historical_provider: point.provider,
      calibration_tier: point.calibration_tier,
    })
  }

  const latestForecastByDate = new Map<string, WeatherCityPoint>()
  for (const point of series?.forecast_points ?? series?.points ?? []) {
    if (!point.target_date) continue
    const existing = latestForecastByDate.get(point.target_date)
    if (!existing || String(point.timestamp) > String(existing.timestamp)) {
      latestForecastByDate.set(point.target_date, point)
    }
  }

  for (const [targetDate, point] of latestForecastByDate.entries()) {
    const row = byDate.get(targetDate) ?? { date: targetDate, label: shortDate(targetDate) }
    row.forecast_high = point.best ?? point.ensemble_mean ?? null
    row.metar = point.metar ?? null
    row.ecmwf = point.ecmwf ?? null
    row.hrrr = point.hrrr ?? null
    row.forecast_source = point.source
    row.forecast_timestamp = point.timestamp
    if (point.humidity !== null && point.humidity !== undefined) row.humidity_mean = point.humidity
    byDate.set(targetDate, row)
  }

  return [...byDate.values()]
    .sort((a, b) => String(a.date).localeCompare(String(b.date)))
    .slice(-60)
}

export function WeatherPanel({
  forecasts,
  signals,
  citySeries = [],
  selectedCity,
  onSelectedCity,
  onBackfillHistory,
  backfilling = false,
  backfillResult,
}: Props) {
  const cities = useMemo(() => uniqueCities(citySeries, forecasts), [citySeries, forecasts])
  const [internalSelected, setInternalSelected] = useState(cities[0]?.key ?? '')
  const selected = selectedCity ?? internalSelected
  const setSelected = (cityKey: string) => {
    setInternalSelected(cityKey)
    onSelectedCity?.(cityKey)
  }

  useEffect(() => {
    if (!selected && cities[0]?.key) setSelected(cities[0].key)
    if (selected && cities.length > 0 && !cities.some(city => city.key === selected)) {
      setSelected(cities[0].key)
    }
  }, [cities, selected])

  const series = citySeries.find(row => row.city_key === selected) ?? citySeries[0]
  const forecastFallback = forecasts.find(row => row.city_key === selected) ?? forecasts[0]
  const cityKey = series?.city_key ?? forecastFallback?.city_key ?? selected
  const unit = series?.unit ?? 'F'

  const citySignals = useMemo(() => signals.filter(signal => signal.city_key === cityKey), [signals, cityKey])
  const actionableSignals = citySignals.filter(signal => signal.actionable)
  const bestSignal = [...citySignals].sort((a, b) => Math.abs((b.probability_edge ?? b.edge ?? 0)) - Math.abs((a.probability_edge ?? a.edge ?? 0)))[0]
  const latestHistory = latestBy<HistoricalWeatherPoint>(
    series?.history_points ?? [],
    point => point.actual_high !== null && point.actual_high !== undefined,
    point => point.target_date
  )
  const latestForecast = latestBy<WeatherCityPoint>(
    series?.forecast_points ?? series?.points ?? [],
    point => point.best !== null && point.best !== undefined,
    point => point.timestamp
  )
  const latestMetar = latestBy<WeatherCityPoint>(
    series?.forecast_points ?? series?.points ?? [],
    point => point.metar !== null && point.metar !== undefined,
    point => point.timestamp
  )
  const chartData = useMemo(() => buildChartData(series), [series])
  const forecastStatus = evidenceStatus(latestForecast?.timestamp)
  const metarStatus = evidenceStatus(latestMetar?.timestamp, 45)
  const historyStatus = latestHistory ? 'fresh' : 'missing'
  const humidityAvailable = chartData.some(row => row.humidity_mean !== null && row.humidity_mean !== undefined)
  const truthTier = latestHistory?.calibration_tier === 'live_truth'
    ? '实盘 truth'
    : latestHistory?.calibration_tier === 'research_truth'
      ? '研究 truth'
      : 'truth 待补'

  if (forecasts.length === 0 && citySeries.length === 0 && signals.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-center text-[11px] leading-relaxed text-neutral-600">
        暂无天气快照。请先启动扫描器，或等待下一轮 forecast/orderbook 写入本地数据库。
      </div>
    )
  }

  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_auto_minmax(210px,1fr)_auto] gap-2 p-3 text-[11px] text-neutral-400">
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={cityKey}
          onChange={event => setSelected(event.target.value)}
          className="min-w-[180px] flex-1 border border-neutral-800 bg-black px-2 py-1 text-neutral-200 outline-none focus:border-cyan-500/50"
          aria-label="选择城市"
        >
          {cities.map(row => (
            <option key={row.key} value={row.key}>{row.name}</option>
          ))}
        </select>
        <div className="shrink-0 border border-neutral-800 px-2 py-1 text-[10px] text-neutral-400">
          站点 {series?.station_id || '未映射'}
        </div>
        <button
          onClick={onBackfillHistory}
          disabled={backfilling || !onBackfillHistory}
          className="inline-flex shrink-0 items-center gap-1 border border-cyan-500/30 px-2 py-1 text-[10px] text-cyan-300 hover:bg-cyan-500/10 disabled:opacity-40"
          title="补最近 30 天研究级历史天气；不会开启实盘，也不会解锁实盘闸门。"
        >
          <RefreshCw className={`h-3 w-3 ${backfilling ? 'animate-spin' : ''}`} />
          {backfilling ? '补历史中' : '补历史数据'}
        </button>
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(92px,1fr))] gap-2">
        <Metric icon={<ThermometerSun className="h-3.5 w-3.5" />} label="最新预测" value={fmtTemp(latestForecast?.best ?? forecastFallback?.mean_high, unit)} tone="green" sub={freshnessLabel(latestForecast?.timestamp)} />
        <Metric icon={<CloudSun className="h-3.5 w-3.5" />} label="METAR 实测" value={fmtTemp(latestMetar?.metar, unit)} tone="amber" sub={freshnessLabel(latestMetar?.timestamp)} />
        <Metric icon={<Database className="h-3.5 w-3.5" />} label="历史最高" value={fmtTemp(latestHistory?.actual_high, unit)} tone="cyan" sub={latestHistory?.provider || truthTier} />
        <Metric icon={<Signal className="h-3.5 w-3.5" />} label="可操作信号" value={`${actionableSignals.length}/${citySignals.length}`} tone={actionableSignals.length > 0 ? 'green' : 'neutral'} sub={bestSignal ? `${bestSignal.bucket_label || bestSignal.threshold_f} · ${(((bestSignal.probability_edge ?? bestSignal.edge) || 0) * 100).toFixed(1)}%` : '暂无'} />
      </div>

      <div className="flex min-h-0 flex-col border border-neutral-800 bg-black">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <EvidenceBadge label="预报" status={forecastStatus} detail={freshnessLabel(latestForecast?.timestamp)} />
            <EvidenceBadge label="METAR" status={metarStatus} detail={freshnessLabel(latestMetar?.timestamp)} />
            <EvidenceBadge label="历史观测" status={historyStatus} detail={latestHistory?.provider || '无数据'} />
            <EvidenceBadge label="湿度" status={humidityAvailable ? 'fresh' : 'missing'} detail={humidityAvailable ? '已采集' : '无数据'} />
          </div>
          <details className="text-[10px] text-neutral-500">
            <summary className="cursor-pointer select-none hover:text-neutral-300">读图方式</summary>
            <div className="mt-1 max-w-xl leading-relaxed text-neutral-500">
              蓝线是已保存的实际最高温，绿虚线是预测最高温，橙色柱是日均湿度，橙线是 METAR 观测。实盘校准只应使用高置信结算 truth；研究级历史只能辅助观察。
            </div>
          </details>
        </div>

        {chartData.length > 0 ? (
          <div
            className="min-h-0 flex-1 p-2"
            role="img"
            aria-label={`${series?.city_name ?? '当前城市'}天气证据图。蓝线表示历史实际最高温，绿虚线表示预测最高温，橙线表示 METAR 观测，浅橙柱表示湿度。`}
          >
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
                <CartesianGrid stroke="#1f1f1f" strokeDasharray="3 3" />
                <XAxis dataKey="label" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} minTickGap={14} />
                <YAxis yAxisId="temp" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} />
                <YAxis yAxisId="humidity" orientation="right" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} domain={[0, 100]} />
                <Tooltip
                  contentStyle={{ background: '#050505', border: '1px solid #262626', color: '#e5e5e5', fontSize: 11 }}
                  formatter={(value: any, name: any) => {
                    if (name === '日均湿度') return [fmtPct(Number(value)), name]
                    return [fmtTemp(Number(value), unit), name]
                  }}
                  labelFormatter={(_, payload) => payload?.[0]?.payload?.date ?? ''}
                />
                <Bar
                  yAxisId="humidity"
                  dataKey="humidity_mean"
                  name="日均湿度"
                  fill="#f59e0b"
                  fillOpacity={0.24}
                  maxBarSize={10}
                  radius={[1, 1, 0, 0]}
                />
                <Line yAxisId="temp" type="monotone" dataKey="actual_high" name="历史实际最高温" stroke="#38bdf8" dot={false} strokeWidth={2.3} connectNulls={false} />
                <Line yAxisId="temp" type="monotone" dataKey="forecast_high" name="预测最高温" stroke="#22c55e" strokeDasharray="7 4" dot={false} strokeWidth={2.3} connectNulls={false} />
                <Line yAxisId="temp" type="monotone" dataKey="metar" name="METAR 实测" stroke="#f97316" dot={false} strokeWidth={1.8} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="flex h-full min-h-[180px] items-center justify-center p-4 text-center text-neutral-600">
            该城市还没有历史或预测曲线。先补历史数据，再启动扫描器获取预测线。
          </div>
        )}
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-2">
        <div className="border border-neutral-800 p-2 leading-relaxed">
          <div className="mb-1 text-neutral-500">当前城市信号</div>
          <div className="text-neutral-300">
            {bestSignal ? `${bestSignal.bucket_label || bestSignal.threshold_f} · ${bestSignal.direction || 'YES'} · EV ${((bestSignal.edge ?? 0) * 100).toFixed(1)}%` : '暂无可排序信号'}
          </div>
          <div className="text-[10px] text-neutral-600">最新预测更新时间 {shortTime(latestForecast?.timestamp)}</div>
          {bestSignal?.event_url && (
            <a href={bestSignal.event_url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-[10px] text-cyan-300 hover:text-cyan-100">
              打开 Polymarket <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>

        <div className="border border-neutral-800 p-2 leading-relaxed">
          <div className="mb-1 text-neutral-500">数据明细</div>
          <div>历史点 {series?.history_count ?? series?.history_points?.length ?? 0} · 预测点 {series?.forecast_count ?? series?.forecast_points?.length ?? series?.points?.length ?? 0}</div>
          <div className="text-[10px] text-neutral-600">truth 层级：{truthTier}；站点：{series?.station_id || '未映射'}</div>
          {backfillResult && (
            <div className="mt-1 text-[10px] text-cyan-300">
              最近补历史：写入 {backfillResult.fetched} 条，错误 {backfillResult.errors.length} 个
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Metric({
  icon,
  label,
  value,
  sub,
  tone,
}: {
  icon: ReactNode
  label: string
  value: string
  sub?: string
  tone: 'neutral' | 'cyan' | 'green' | 'amber'
}) {
  const color = tone === 'cyan' ? 'text-cyan-300' : tone === 'green' ? 'text-green-300' : tone === 'amber' ? 'text-amber-300' : 'text-neutral-200'
  return (
    <div className="min-w-0 border border-neutral-800 px-2 py-1.5">
      <div className="mb-0.5 flex items-center gap-1 text-[9px] text-neutral-600">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      <div className={`truncate tabular-nums ${color}`}>{value}</div>
      {sub && <div className="truncate text-[9px] text-neutral-600" title={sub}>{sub}</div>}
    </div>
  )
}

function EvidenceBadge({ label, status, detail }: { label: string; status: EvidenceStatus; detail: string }) {
  return (
    <span className={`inline-flex items-center gap-1 border px-1.5 py-0.5 text-[9px] ${statusClass(status)}`} title={detail}>
      <span className={`h-1.5 w-1.5 rounded-full ${status === 'fresh' ? 'bg-green-300' : status === 'stale' ? 'bg-amber-300' : 'bg-neutral-600'}`} />
      {label}
    </span>
  )
}
