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
import type { DashboardEvent, HistoricalWeatherPoint, WeatherCityPoint, WeatherCitySeries, WeatherForecast, WeatherSignal } from '../types'

interface Props {
  forecasts: WeatherForecast[]
  signals: WeatherSignal[]
  citySeries?: WeatherCitySeries[]
  events?: DashboardEvent[]
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
type WeatherTab = 'overview' | 'forecast' | 'metar' | 'history' | 'bias' | 'logs' | 'signals'

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

function longDate(value?: string | null) {
  if (!value) return '--'
  try {
    const date = new Date(value.includes('T') ? value : `${value}T00:00:00`)
    return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
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
  events = [],
  selectedCity,
  onSelectedCity,
  onBackfillHistory,
  backfilling = false,
  backfillResult,
}: Props) {
  const cities = useMemo(() => uniqueCities(citySeries, forecasts), [citySeries, forecasts])
  const [internalSelected, setInternalSelected] = useState(cities[0]?.key ?? '')
  const [activeTab, setActiveTab] = useState<WeatherTab>('overview')
  const [selectedDate, setSelectedDate] = useState(() => {
    if (typeof window === 'undefined') return ''
    return new URLSearchParams(window.location.search).get('date') ?? ''
  })
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
  const availableDates = useMemo(() => {
    return [...new Set(chartData.map(row => String(row.date)).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b))
  }, [chartData])
  const forecastStatus = evidenceStatus(latestForecast?.timestamp)
  const metarStatus = evidenceStatus(latestMetar?.timestamp, 45)
  const historyStatus = latestHistory ? 'fresh' : 'missing'
  const humidityAvailable = chartData.some(row => row.humidity_mean !== null && row.humidity_mean !== undefined)
  const truthTier = latestHistory?.calibration_tier === 'live_truth'
    ? '实盘 truth'
    : latestHistory?.calibration_tier === 'research_truth'
      ? '研究 truth'
      : 'truth 待补'

  useEffect(() => {
    const fallbackDate = availableDates[availableDates.length - 1] ?? forecastFallback?.target_date ?? latestForecast?.target_date ?? ''
    if (!selectedDate && fallbackDate) {
      setSelectedDate(fallbackDate)
    } else if (selectedDate && availableDates.length > 0 && !availableDates.includes(selectedDate)) {
      setSelectedDate(fallbackDate)
    }
  }, [availableDates, forecastFallback?.target_date, latestForecast?.target_date, selectedDate])

  const selectedDateIndex = availableDates.indexOf(selectedDate)
  const selectedDateRow = chartData.find(row => row.date === selectedDate) ?? chartData[chartData.length - 1]
  const forecastRows = [...(series?.forecast_points ?? series?.points ?? [])]
    .filter(point => !selectedDate || point.target_date === selectedDate)
    .sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)))
    .slice(0, 18)
  const metarRows = forecastRows
    .filter(point => point.metar !== null && point.metar !== undefined)
    .slice(0, 18)
  const historyRows = [...(series?.history_points ?? [])]
    .sort((a, b) => String(b.target_date).localeCompare(String(a.target_date)))
    .slice(0, 18)
  const eventRows = events
    .filter(event => {
      const text = `${event.message ?? ''} ${JSON.stringify(event.data ?? {})}`.toLowerCase()
      return !cityKey || text.includes(cityKey.toLowerCase()) || text.includes(String(series?.city_name ?? '').toLowerCase()) || /scan|forecast|orderbook|truth|refresh|scanner|weather/i.test(text)
    })
    .slice(0, 18)

  useEffect(() => {
    if (!selectedDate || typeof window === 'undefined') return
    const params = new URLSearchParams(window.location.search)
    if (params.get('date') === selectedDate) return
    params.set('date', selectedDate)
    window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}`)
  }, [selectedDate])

  if (forecasts.length === 0 && citySeries.length === 0 && signals.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-center text-[11px] leading-relaxed text-neutral-600">
        暂无天气快照。请先启动扫描器，或等待下一轮 forecast/orderbook 写入本地数据库。
      </div>
    )
  }

  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_auto_auto_minmax(210px,1fr)_auto] gap-2 p-3 text-[11px] text-neutral-400">
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
        <div className="inline-flex shrink-0 items-center border border-neutral-800">
          <button
            type="button"
            onClick={() => selectedDateIndex > 0 && setSelectedDate(availableDates[selectedDateIndex - 1])}
            disabled={selectedDateIndex <= 0}
            className="px-2 py-1 text-[10px] text-neutral-400 hover:bg-neutral-900 disabled:opacity-30"
          >
            前一天
          </button>
          <div className="border-x border-neutral-800 px-2 py-1 text-[10px] tabular-nums text-neutral-200">
            {longDate(selectedDate)}
          </div>
          <button
            type="button"
            onClick={() => selectedDateIndex >= 0 && selectedDateIndex < availableDates.length - 1 && setSelectedDate(availableDates[selectedDateIndex + 1])}
            disabled={selectedDateIndex < 0 || selectedDateIndex >= availableDates.length - 1}
            className="px-2 py-1 text-[10px] text-neutral-400 hover:bg-neutral-900 disabled:opacity-30"
          >
            后一天
          </button>
        </div>
        {bestSignal?.event_url && (
          <a href={bestSignal.event_url} target="_blank" rel="noreferrer" className="inline-flex shrink-0 items-center gap-1 border border-cyan-500/30 px-2 py-1 text-[10px] text-cyan-300 hover:bg-cyan-500/10">
            Polymarket <ExternalLink className="h-3 w-3" />
          </a>
        )}
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

      <div className="flex flex-wrap items-center justify-between gap-2 border border-neutral-800 bg-black px-2 py-1.5">
        <div className="flex flex-wrap gap-1" role="tablist" aria-label="天气证据标签">
          <TabButton active={activeTab === 'overview'} onClick={() => setActiveTab('overview')}>总览</TabButton>
          <TabButton active={activeTab === 'forecast'} onClick={() => setActiveTab('forecast')}>预报</TabButton>
          <TabButton active={activeTab === 'metar'} onClick={() => setActiveTab('metar')}>METAR</TabButton>
          <TabButton active={activeTab === 'history'} onClick={() => setActiveTab('history')}>历史观测</TabButton>
          <TabButton active={activeTab === 'bias'} onClick={() => setActiveTab('bias')}>偏差统计</TabButton>
          <TabButton active={activeTab === 'logs'} onClick={() => setActiveTab('logs')}>抓取日志</TabButton>
          <TabButton active={activeTab === 'signals'} onClick={() => setActiveTab('signals')}>市场信号</TabButton>
        </div>
        <div className="flex flex-wrap gap-1 text-[10px] text-neutral-500">
          <span className="border border-neutral-800 px-1.5 py-0.5">预报 {forecastRows.length}</span>
          <span className="border border-neutral-800 px-1.5 py-0.5">METAR {metarRows.length}</span>
          <span className="border border-neutral-800 px-1.5 py-0.5">历史 {historyRows.length}</span>
          <span className="border border-neutral-800 px-1.5 py-0.5">信号 {citySignals.length}</span>
          <span className="border border-neutral-800 px-1.5 py-0.5">日志 {eventRows.length}</span>
        </div>
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

        {activeTab === 'overview' ? (
          chartData.length > 0 ? (
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
          )
        ) : activeTab === 'forecast' ? (
          <EvidenceTable
            empty="该日期暂无预报快照"
            columns={['更新时间', '目标日', '最佳', 'ECMWF', 'HRRR', 'METAR', '湿度', '来源']}
            rows={forecastRows.map(point => [
              shortTime(point.timestamp),
              longDate(point.target_date),
              fmtTemp(point.best ?? point.ensemble_mean, unit),
              fmtTemp(point.ecmwf, unit),
              fmtTemp(point.hrrr, unit),
              fmtTemp(point.metar, unit),
              fmtPct(point.humidity),
              point.source || '--',
            ])}
          />
        ) : activeTab === 'metar' ? (
          <EvidenceTable
            empty="该日期暂无 METAR 快照"
            columns={['更新时间', '目标日', 'METAR', '当前 best', 'ECMWF', 'HRRR', '湿度', 'source']}
            rows={metarRows.map(point => [
              shortTime(point.timestamp),
              longDate(point.target_date),
              fmtTemp(point.metar, unit),
              fmtTemp(point.best ?? point.ensemble_mean, unit),
              fmtTemp(point.ecmwf, unit),
              fmtTemp(point.hrrr, unit),
              fmtPct(point.humidity),
              point.source || '--',
            ])}
          />
        ) : activeTab === 'history' ? (
          <EvidenceTable
            empty="暂无历史观测"
            columns={['日期', '实际最高', '湿度', 'provider', 'truth 层级', '站点']}
            rows={historyRows.map(point => [
              longDate(point.target_date),
              fmtTemp(point.actual_high, point.unit || unit),
              fmtPct(point.humidity_mean),
              point.provider || '--',
              point.calibration_tier || '--',
              point.station_id || series?.station_id || '--',
            ])}
          />
        ) : activeTab === 'bias' ? (
          <div className="grid min-h-0 flex-1 grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2 overflow-y-auto p-3">
            <MetricCard label="选中日期" value={longDate(selectedDate)} sub={`实际 ${fmtTemp(selectedDateRow?.actual_high, unit)} · 预测 ${fmtTemp(selectedDateRow?.forecast_high, unit)}`} />
            <MetricCard label="预测误差" value={selectedDateRow?.actual_high !== null && selectedDateRow?.actual_high !== undefined && selectedDateRow?.forecast_high !== null && selectedDateRow?.forecast_high !== undefined ? fmtTemp(Number(selectedDateRow.actual_high) - Number(selectedDateRow.forecast_high), unit) : '--'} sub="实际最高 - 预测最高" />
            <MetricCard label="历史样本" value={`${series?.history_count ?? historyRows.length}`} sub={latestHistory?.provider || truthTier} />
            <MetricCard label="预报样本" value={`${series?.forecast_count ?? forecastRows.length}`} sub={latestForecast?.source || 'forecast'} />
            <MetricCard label="信号数量" value={`${citySignals.length}`} sub={`可操作 ${actionableSignals.length}`} />
            <MetricCard label="数据状态" value={forecastStatus === 'fresh' ? '新鲜' : forecastStatus === 'stale' ? '过期' : '缺失'} sub={`METAR ${metarStatus} · 历史 ${historyStatus}`} />
          </div>
        ) : activeTab === 'logs' ? (
          <EvidenceTable
            empty="暂无抓取或扫描日志"
            columns={['时间', '类型', '消息']}
            rows={eventRows.map(event => [
              shortTime(event.timestamp),
              event.type || '--',
              event.message || '--',
            ])}
          />
        ) : (
          <EvidenceTable
            empty="该城市暂无市场信号"
            columns={['日期', '温度桶', '价格', '模型概率', 'edge', '状态', '链接']}
            rows={citySignals.slice(0, 18).map(signal => [
              longDate(signal.target_date),
              signal.bucket_label || String(signal.threshold_f),
              signal.limit_price !== undefined && signal.limit_price !== null ? `$${Number(signal.limit_price).toFixed(3)}` : '--',
              `${((signal.calibrated_probability ?? signal.model_probability ?? 0) * 100).toFixed(1)}%`,
              `${((signal.probability_edge ?? signal.edge ?? 0) * 100).toFixed(1)}%`,
              signal.actionable ? 'BUY' : signal.status || 'watch',
              signal.event_url ? 'Polymarket' : '--',
            ])}
            links={citySignals.slice(0, 18).map(signal => signal.event_url)}
          />
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

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`border px-2 py-1 text-[10px] transition ${
        active
          ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200'
          : 'border-neutral-800 text-neutral-500 hover:border-neutral-700 hover:text-neutral-300'
      }`}
    >
      {children}
    </button>
  )
}

function EvidenceTable({
  columns,
  rows,
  empty,
  links,
}: {
  columns: string[]
  rows: string[][]
  empty: string
  links?: Array<string | undefined>
}) {
  if (rows.length === 0) {
    return <div className="flex min-h-0 flex-1 items-center justify-center p-4 text-neutral-600">{empty}</div>
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="min-w-full border-collapse text-left text-[10px]">
        <thead className="sticky top-0 bg-black text-neutral-500">
          <tr>
            {columns.map(column => (
              <th key={column} className="border-b border-neutral-800 px-2 py-1 font-medium">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${rowIndex}-${row.join('|')}`} className="border-b border-neutral-900 hover:bg-neutral-950">
              {row.map((cell, cellIndex) => {
                const href = cellIndex === row.length - 1 ? links?.[rowIndex] : undefined
                return (
                  <td key={`${rowIndex}-${cellIndex}`} className="whitespace-nowrap px-2 py-1 text-neutral-300">
                    {href ? (
                      <a href={href} target="_blank" rel="noreferrer" className="text-cyan-300 hover:text-cyan-100">
                        {cell}
                      </a>
                    ) : cell}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MetricCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="border border-neutral-800 bg-neutral-950/40 p-3">
      <div className="text-[10px] text-neutral-500">{label}</div>
      <div className="mt-1 text-lg tabular-nums text-neutral-100">{value}</div>
      {sub && <div className="mt-1 truncate text-[10px] text-neutral-600" title={sub}>{sub}</div>}
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
