import { useMemo, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { WeatherCitySeries, WeatherForecast, WeatherSignal } from '../types'

interface Props {
  forecasts: WeatherForecast[]
  signals: WeatherSignal[]
  citySeries?: WeatherCitySeries[]
  onBackfillHistory?: () => void
  backfilling?: boolean
  backfillResult?: {
    fetched: number
    errors: Array<{ city: string; error: string }>
  }
}

function fmt(value?: number | null, unit = 'F') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${Number(value).toFixed(1)}°${unit}`
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

function pct(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${Number(value).toFixed(0)}%`
}

export function WeatherPanel({
  forecasts,
  signals,
  citySeries = [],
  onBackfillHistory,
  backfilling = false,
  backfillResult,
}: Props) {
  const [selected, setSelected] = useState(citySeries[0]?.city_key ?? forecasts[0]?.city_key ?? '')
  const series = citySeries.find(row => row.city_key === selected) ?? citySeries[0]
  const forecastFallback = forecasts.find(row => row.city_key === selected) ?? forecasts[0]

  const citySignals = useMemo(() => {
    const key = series?.city_key ?? forecastFallback?.city_key
    return signals.filter(signal => signal.city_key === key)
  }, [signals, series?.city_key, forecastFallback?.city_key])

  const chartData = useMemo(() => {
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

    const latestForecastByDate = new Map<string, any>()
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
  }, [series])

  if (forecasts.length === 0 && citySeries.length === 0 && signals.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-center text-[11px] leading-relaxed text-neutral-600">
        暂无天气快照。请先启动扫描器，或等待下一轮 forecast_snapshots 写入本地数据。
      </div>
    )
  }

  const unit = series?.unit ?? 'F'
  const actionable = citySignals.filter(signal => signal.actionable)
  const bestSignal = [...citySignals].sort((a, b) => Math.abs((b.edge ?? 0)) - Math.abs((a.edge ?? 0)))[0]
  const humidityText = series?.humidity_status === 'available' ? '已采集' : '尚未采集'
  const latestHistory = [...(series?.history_points ?? [])].reverse().find(point => point.actual_high !== null && point.actual_high !== undefined)
  const latestForecast = [...(series?.forecast_points ?? series?.points ?? [])].reverse().find(point => point.best !== null && point.best !== undefined)

  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_auto_minmax(190px,1fr)_auto] gap-2 p-3 text-[11px] text-neutral-400">
      <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-2">
        <select
          value={series?.city_key ?? forecastFallback?.city_key ?? ''}
          onChange={event => setSelected(event.target.value)}
          className="min-w-0 border border-neutral-800 bg-black px-2 py-1 text-neutral-200"
          aria-label="选择城市"
        >
          {citySeries.map(row => (
            <option key={row.city_key} value={row.city_key}>{row.city_name}</option>
          ))}
          {citySeries.length === 0 && forecasts.map(row => (
            <option key={row.city_key} value={row.city_key}>{row.city_name}</option>
          ))}
        </select>
        <div className="border border-neutral-800 px-2 py-1 text-[10px] text-neutral-500">
          站点 {series?.station_id || '未映射'}
        </div>
        <button
          onClick={onBackfillHistory}
          disabled={backfilling || !onBackfillHistory}
          className="border border-cyan-500/30 px-2 py-1 text-[10px] text-cyan-300 hover:bg-cyan-500/10 disabled:opacity-40"
          title="补最近 30 天研究级历史天气。不会开启实盘，也不会解锁实盘门槛。"
        >
          {backfilling ? '补历史中...' : '补历史数据'}
        </button>
      </div>

      <div className="grid grid-cols-5 gap-2">
        <Metric label="历史点" value={`${series?.history_count ?? series?.history_points?.length ?? 0}`} tone="neutral" />
        <Metric label="预测点" value={`${series?.forecast_count ?? series?.forecast_points?.length ?? series?.points?.length ?? 0}`} tone="neutral" />
        <Metric label="最新实际" value={fmt(latestHistory?.actual_high, unit)} tone="cyan" />
        <Metric label="最新预测" value={fmt(latestForecast?.best ?? forecastFallback?.mean_high, unit)} tone="green" />
        <Metric label="湿度" value={humidityText} tone={humidityText === '已采集' ? 'green' : 'amber'} />
      </div>

      <div className="min-h-0 border border-neutral-800 bg-black p-2">
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="#1f1f1f" strokeDasharray="3 3" />
              <XAxis dataKey="label" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} minTickGap={14} />
              <YAxis yAxisId="temp" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} />
              <YAxis yAxisId="humidity" orientation="right" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} domain={[0, 100]} />
              <Tooltip
                contentStyle={{ background: '#050505', border: '1px solid #262626', color: '#e5e5e5', fontSize: 11 }}
                formatter={(value: any, name: any) => {
                  if (name === '湿度') return [pct(Number(value)), name]
                  return [fmt(Number(value), unit), name]
                }}
                labelFormatter={(_, payload) => payload?.[0]?.payload?.date ?? ''}
              />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Line yAxisId="temp" type="monotone" dataKey="actual_high" name="历史实际高温" stroke="#38bdf8" dot={false} strokeWidth={2.2} connectNulls={false} />
              <Line yAxisId="temp" type="monotone" dataKey="forecast_high" name="预测高温" stroke="#22c55e" dot={false} strokeWidth={2.2} connectNulls={false} />
              <Line yAxisId="humidity" type="monotone" dataKey="humidity_mean" name="湿度" stroke="#f59e0b" dot={false} strokeWidth={1.5} connectNulls={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center text-center text-neutral-600">
            该城市还没有历史或预测曲线。可以先点击“补历史数据”，再启动扫描器获取预测线。
          </div>
        )}
      </div>

      <div className="grid grid-cols-[1fr_1fr] gap-2">
        <div className="border border-neutral-800 p-2 leading-relaxed">
          <div className="mb-1 text-neutral-500">读图方式</div>
          <p className="text-[10px] text-neutral-600">
            蓝线是历史实际最高温，绿线是当前预测最高温。两条线用于判断模型是否长期偏热/偏冷；下单仍要看盘口和风控。
          </p>
          {backfillResult && (
            <p className="mt-1 text-[10px] text-cyan-300">
              最近补历史：写入 {backfillResult.fetched} 条，错误 {backfillResult.errors.length} 个。
            </p>
          )}
        </div>
        <div className="border border-neutral-800 p-2 leading-relaxed">
          <div className="mb-1 text-neutral-500">当前城市信号</div>
          <div>可操作 {actionable.length} 条；最强信号 {bestSignal ? `${bestSignal.bucket_label || bestSignal.threshold_f} / EV ${(bestSignal.edge * 100).toFixed(1)}%` : '暂无'}</div>
          <div className="text-[10px] text-neutral-600">最新预测更新时间 {shortTime(latestForecast?.timestamp)}</div>
        </div>
      </div>
    </div>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone: 'neutral' | 'cyan' | 'green' | 'amber' }) {
  const color = tone === 'cyan' ? 'text-cyan-300' : tone === 'green' ? 'text-green-300' : tone === 'amber' ? 'text-amber-300' : 'text-neutral-200'
  return (
    <div className="min-w-0 border border-neutral-800 px-2 py-1">
      <div className="truncate text-[9px] text-neutral-600">{label}</div>
      <div className={`truncate tabular-nums ${color}`}>{value}</div>
    </div>
  )
}
