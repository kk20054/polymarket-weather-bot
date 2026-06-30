import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { CloudSun, Database, ExternalLink, RefreshCw, Signal, ThermometerSun } from 'lucide-react'
import type { DashboardEvent, DistributionItem, HistoricalWeatherPoint, ProductionRefreshResult, WeatherCityPoint, WeatherCitySeries, WeatherForecast, WeatherSignal } from '../types'

interface Props {
  forecasts: WeatherForecast[]
  signals: WeatherSignal[]
  citySeries?: WeatherCitySeries[]
  events?: DashboardEvent[]
  productionRefresh?: ProductionRefreshResult | null
  selectedCity?: string
  onSelectedCity?: (cityKey: string) => void
  selectedDate?: string
  onSelectedDate?: (date: string) => void
  onRefreshWeather?: () => void
  weatherRefreshing?: boolean
  onBackfillHistory?: () => void
  backfilling?: boolean
  backfillResult?: {
    fetched: number
    errors: Array<{ city: string; error: string }>
  }
}

type EvidenceStatus = 'fresh' | 'stale' | 'missing'

type WeatherChartRow = {
  date: string
  label: string
  actual_high?: number | null
  humidity_mean?: number | null
  historical_provider?: string
  calibration_tier?: string
  forecast_high?: number | null
  metar?: number | null
  ecmwf?: number | null
  hrrr?: number | null
  forecast_source?: string
  forecast_timestamp?: string
}

type HourlyWeatherRow = {
  id: string
  timestamp: string
  target_date: string
  label: string
  forecast?: number | null
  metar?: number | null
  ecmwf?: number | null
  hrrr?: number | null
  humidity?: number | null
  gap?: number | null
  source?: string
  horizon?: string
  member_count?: number
  archive?: boolean
}

type SourceSampleTone = 'green' | 'amber' | 'red' | 'cyan' | 'neutral'

type SourceSample = {
  label: string
  value: string
  meta?: string
  tone?: SourceSampleTone
}

type EvidenceCardTone = SourceSampleTone

type EvidenceCardItem = {
  id: string
  eyebrow: string
  title: string
  value: string
  meta?: string
  tone?: EvidenceCardTone
  badges?: Array<{ label: string; tone?: EvidenceCardTone }>
  details?: Array<{ label: string; value: string; wide?: boolean }>
}

type WeatherWorkbenchTab = 'forecast' | 'metar' | 'historical' | 'diff' | 'fetch'

const WORKBENCH_TABS: Array<{ id: WeatherWorkbenchTab; label: string; note: string }> = [
  { id: 'forecast', label: 'Forecast', note: 'Hourly Temperature + DEB + Forecast Data' },
  { id: 'metar', label: 'METAR', note: 'Station observations' },
  { id: 'historical', label: 'Historical', note: 'Settlement-truth history' },
  { id: 'diff', label: 'Diff Stats', note: 'Observed - Forecast' },
  { id: 'fetch', label: 'Fetch Log', note: 'Last 100 events' },
]

function fmtTemp(value?: number | null, unit = 'F') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${Number(value).toFixed(1)}°${unit}`
}

function fmtPct(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${Number(value).toFixed(0)}%`
}

function fmtProb(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${(Number(value) * 100).toFixed(1)}%`
}

function fmtPrice(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${(Number(value) * 100).toFixed(1)}¢`
}

function fmtSignedPct(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  const pct = Number(value) * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
}

function fmtSignedTemp(value?: number | null, unit = 'F') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  const temp = Number(value)
  return `${temp >= 0 ? '+' : ''}${temp.toFixed(1)}°${unit}`
}

function mean(values: number[]) {
  const valid = values.filter(value => Number.isFinite(value))
  if (valid.length === 0) return null
  return valid.reduce((total, value) => total + value, 0) / valid.length
}

function pearsonR(xValues: number[], yValues: number[]) {
  const pairs = xValues
    .map((x, index) => [x, yValues[index]] as const)
    .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y))
  if (pairs.length < 2) return null
  const xs = pairs.map(([x]) => x)
  const ys = pairs.map(([, y]) => y)
  const xMean = mean(xs)
  const yMean = mean(ys)
  if (xMean === null || yMean === null) return null
  let numerator = 0
  let xDenominator = 0
  let yDenominator = 0
  for (const [x, y] of pairs) {
    const dx = x - xMean
    const dy = y - yMean
    numerator += dx * dy
    xDenominator += dx * dx
    yDenominator += dy * dy
  }
  const denominator = Math.sqrt(xDenominator * yDenominator)
  return denominator === 0 ? null : numerator / denominator
}

function fmtPearson(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `r ${Number(value).toFixed(2)}`
}

function errorTone(absError: number) {
  if (absError <= 1.5) return 'green'
  if (absError <= 3) return 'amber'
  return 'red'
}

function compactData(value: unknown, max = 180) {
  if (value === null || value === undefined) return ''
  try {
    const raw = typeof value === 'string' ? value : JSON.stringify(value)
    return raw.length > max ? `${raw.slice(0, max)}...` : raw
  } catch {
    return String(value)
  }
}

function eventTone(event: DashboardEvent) {
  const text = `${event.type ?? ''} ${event.message ?? ''} ${compactData(event.data, 120)}`.toLowerCase()
  if (/error|fail|forbidden|timeout|exception|err/.test(text)) return 'red'
  if (/buy|signal|order|clob|market|盘口/.test(text)) return 'cyan'
  if (/truth|history|settle|actual|observ/.test(text)) return 'amber'
  if (/forecast|weather|metar|refresh|scan/.test(text)) return 'green'
  return 'neutral'
}

function eventStage(event: DashboardEvent) {
  const text = `${event.type ?? ''} ${event.message ?? ''} ${compactData(event.data, 120)}`.toLowerCase()
  if (/orderbook|clob|market|盘口/.test(text)) return '盘口'
  if (/signal|buy|trade|order/.test(text)) return '信号'
  if (/truth|history|settle|actual|observ/.test(text)) return '观测'
  if (/forecast|weather|metar/.test(text)) return '天气'
  if (/refresh|scan|scanner/.test(text)) return '刷新'
  return event.type || '事件'
}

function fmtBucket(item: DistributionItem, unit: string) {
  if (item.bucket_low <= -900) return `${fmtTemp(item.bucket_high, unit)} 或以下`
  if (item.bucket_high >= 900) return `${fmtTemp(item.bucket_low, unit)} 或以上`
  return `${fmtTemp(item.bucket_low, unit)} - ${fmtTemp(item.bucket_high, unit)}`
}

function fmtBucketLabel(raw?: string | null, fallback?: number | null, unit = 'F') {
  const fallbackNative =
    fallback === null || fallback === undefined || Number.isNaN(Number(fallback))
      ? null
      : unit === 'C'
        ? (Number(fallback) - 32) * 5 / 9
        : Number(fallback)
  if (!raw) return fmtTemp(fallbackNative, unit)
  const normalized = String(raw).trim()
  const match = normalized.match(/^\s*(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?\s*-\s*(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?\s*$/i)
  if (!match) return normalized.replace(/掳/g, '°')
  const low = Number(match[1])
  const high = Number(match[3])
  const labelUnit = (match[4] || match[2] || unit).toUpperCase()
  if (low <= -900) return `${fmtTemp(high, labelUnit)} 或以下`
  if (high >= 900) return `${fmtTemp(low, labelUnit)} 或以上`
  return `${fmtTemp(low, labelUnit)} - ${fmtTemp(high, labelUnit)}`
}

function signalBucketLabel(signal: WeatherSignal | undefined, unit = 'F') {
  if (!signal) return '--'
  return fmtBucketLabel(signal.bucket_label, signal.threshold_f, unit)
}

function isOpenTailBucket(signal?: WeatherSignal) {
  if (!signal?.bucket_label) return false
  return /(?:^|-)999(?:\.0+)?[CF]?$/i.test(signal.bucket_label) || /^-999(?:\.0+)?-/i.test(signal.bucket_label)
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

function shortHour(value?: string | null) {
  if (!value) return '--'
  try {
    return new Date(value).toLocaleTimeString('zh-CN', {
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

function localDateString(date = new Date()) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function elapsedLabel(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return ''
  if (value < 1000) return `${Math.round(Number(value))}ms`
  return `${(Number(value) / 1000).toFixed(1)}s`
}

function refreshStage(productionRefresh: ProductionRefreshResult | null | undefined, names: string[]) {
  return (productionRefresh?.stages ?? []).find(stage => names.includes(stage.name))
}

function refreshStageLabel(stage?: { ok?: boolean; elapsed_ms?: number | null; error?: string | null }) {
  if (!stage) return '未运行'
  return [stage.ok ? 'ok' : 'err', elapsedLabel(stage.elapsed_ms), stage.error].filter(Boolean).join(' · ')
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

function buildChartData(series?: WeatherCitySeries): WeatherChartRow[] {
  const byDate = new Map<string, WeatherChartRow>()
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

function buildHourlyRows(series?: WeatherCitySeries, selectedDate?: string): HourlyWeatherRow[] {
  const rows = new Map<string, HourlyWeatherRow>()
  const sourcePoints = (series?.hourly_points?.length ? series.hourly_points : (series?.forecast_points ?? series?.points ?? []))
  for (const point of sourcePoints) {
    if (selectedDate && point.target_date !== selectedDate) continue
    if (!point.timestamp) continue
    const forecast = point.best ?? point.ensemble_mean ?? null
    const metar = point.metar ?? null
    const gap = forecast !== null && forecast !== undefined && metar !== null && metar !== undefined
      ? Number(metar) - Number(forecast)
      : null
    const key = `${point.timestamp}:${point.target_date}`
    rows.set(key, {
      id: key,
      timestamp: point.timestamp,
      target_date: point.target_date,
      label: shortHour(point.timestamp),
      forecast,
      metar,
      ecmwf: point.ecmwf ?? null,
      hrrr: point.hrrr ?? null,
      humidity: point.humidity ?? null,
      gap,
      source: point.source || '--',
      horizon: point.horizon || '--',
      member_count: point.member_count,
      archive: point.archive,
    })
  }
  return [...rows.values()]
    .sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)))
    .slice(-48)
}

export function WeatherPanel({
  forecasts,
  signals,
  citySeries = [],
  events = [],
  productionRefresh,
  selectedCity,
  onSelectedCity,
  selectedDate: controlledSelectedDate,
  onSelectedDate,
  onRefreshWeather,
  weatherRefreshing = false,
  onBackfillHistory,
  backfilling = false,
  backfillResult,
}: Props) {
  const cities = useMemo(() => uniqueCities(citySeries, forecasts), [citySeries, forecasts])
  const [internalSelected, setInternalSelected] = useState(cities[0]?.key ?? '')
  const [internalSelectedDate, setInternalSelectedDate] = useState(() => {
    if (typeof window === 'undefined') return ''
    return new URLSearchParams(window.location.search).get('date') ?? ''
  })
  const [activeWorkbenchTab, setActiveWorkbenchTab] = useState<WeatherWorkbenchTab>('forecast')
  const selected = selectedCity ?? internalSelected
  const setSelected = (cityKey: string) => {
    setInternalSelected(cityKey)
    onSelectedCity?.(cityKey)
  }
  const selectedDate = controlledSelectedDate ?? internalSelectedDate
  const setSelectedDate = (date: string) => {
    setInternalSelectedDate(date)
    onSelectedDate?.(date)
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
  const todayDate = localDateString()

  const citySignals = useMemo(() => signals.filter(signal => signal.city_key === cityKey), [signals, cityKey])
  const actionableSignals = citySignals.filter(signal => signal.actionable)
  const selectedDateSignals = citySignals.filter(signal => !selectedDate || signal.target_date === selectedDate)
  const bestSignal = [...(selectedDateSignals.length > 0 ? selectedDateSignals : citySignals)]
    .sort((a, b) => {
      const actionDelta = Number(Boolean(b.actionable)) - Number(Boolean(a.actionable))
      if (actionDelta !== 0) return actionDelta
      return Math.abs((b.probability_edge ?? b.edge ?? 0)) - Math.abs((a.probability_edge ?? a.edge ?? 0))
    })[0]
  const distributionSignal = useMemo(() => {
    const dated = citySignals.filter(signal => !selectedDate || signal.target_date === selectedDate)
    const withDistribution = dated.filter(signal => (signal.distribution?.items?.length ?? 0) > 0)
    const candidates = withDistribution.length > 0 ? withDistribution : citySignals.filter(signal => (signal.distribution?.items?.length ?? 0) > 0)
    return [...candidates].sort((a, b) => {
      const actionDelta = Number(Boolean(b.actionable)) - Number(Boolean(a.actionable))
      if (actionDelta !== 0) return actionDelta
      return Math.abs((b.probability_edge ?? b.edge ?? 0)) - Math.abs((a.probability_edge ?? a.edge ?? 0))
    })[0]
  }, [citySignals, selectedDate])
  const distributionChartItems = useMemo(() => {
    const items = [...(distributionSignal?.distribution?.items ?? [])]
    return items
      .sort((a, b) => {
        const lowDelta = Number(a.bucket_low ?? 0) - Number(b.bucket_low ?? 0)
        if (lowDelta !== 0) return lowDelta
        return Number(a.bucket_high ?? 0) - Number(b.bucket_high ?? 0)
      })
      .slice(0, 18)
  }, [distributionSignal])
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
  const hourlyRows = useMemo(() => buildHourlyRows(series, selectedDate), [series, selectedDate])
  const availableDates = useMemo(() => {
    return [...new Set(chartData.map(row => String(row.date)).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b))
  }, [chartData])
  const forecastStatus = evidenceStatus(latestForecast?.timestamp)
  const metarStatus = evidenceStatus(latestMetar?.timestamp, 45)
  const historyStatus = latestHistory ? 'fresh' : 'missing'
  const humidityAvailable = chartData.some(row => row.humidity_mean !== null && row.humidity_mean !== undefined)
  const forecastRefreshStage = refreshStage(productionRefresh, ['forecast_backfill'])
  const signalRefreshStage = refreshStage(productionRefresh, ['signal_scan', 'signal_migration'])
  const orderbookRefreshStage = refreshStage(productionRefresh, ['orderbook_backfill'])
  const latestHistoryFetch = latestHistory?.fetched_at
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
  const decisionLabel = bestSignal?.actionable ? 'BUY YES' : bestSignal ? '观察' : '等待信号'
  const decisionTone = bestSignal?.actionable ? 'green' : bestSignal ? 'amber' : 'neutral'
  const decisionReason = bestSignal?.decision?.reasons?.[0] ?? bestSignal?.status ?? (bestSignal ? '未通过可执行闸门' : '手动抓取后生成')
  const selectedForecast = selectedDateRow?.forecast_high ?? latestForecast?.best ?? latestForecast?.ensemble_mean ?? forecastFallback?.mean_high
  const selectedMetar = selectedDateRow?.metar ?? latestMetar?.metar
  const metarGap = selectedForecast !== null && selectedForecast !== undefined && selectedMetar !== null && selectedMetar !== undefined
    ? Number(selectedForecast) - Number(selectedMetar)
    : null
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
  const forecastSamples: SourceSample[] = forecastRows.slice(0, 4).map(point => ({
    label: `${shortDate(point.target_date)} · ${shortTime(point.timestamp)}`,
    value: fmtTemp(point.best ?? point.ensemble_mean, unit),
    meta: `ECMWF ${fmtTemp(point.ecmwf, unit)} · HRRR ${fmtTemp(point.hrrr, unit)} · METAR ${fmtTemp(point.metar, unit)} · ${point.source || '--'}`,
    tone: point.target_date === selectedDate ? 'green' : 'neutral',
  }))
  const metarSamples: SourceSample[] = metarRows.slice(0, 4).map(point => ({
    label: `${shortDate(point.target_date)} · ${shortTime(point.timestamp)}`,
    value: fmtTemp(point.metar, unit),
    meta: `best ${fmtTemp(point.best ?? point.ensemble_mean, unit)} · humidity ${fmtPct(point.humidity)} · ${point.source || '--'}`,
    tone: point.target_date === selectedDate ? 'amber' : 'neutral',
  }))
  const historySamples: SourceSample[] = historyRows.slice(0, 4).map(point => ({
    label: longDate(point.target_date),
    value: fmtTemp(point.actual_high, point.unit || unit),
    meta: `${point.provider || '--'} · ${point.calibration_tier || '--'} · ${point.station_id || series?.station_id || '--'}`,
    tone: point.calibration_tier === 'live_truth' ? 'green' : point.calibration_tier === 'research_truth' ? 'amber' : 'neutral',
  }))
  const marketSamples: SourceSample[] = (selectedDateSignals.length > 0 ? selectedDateSignals : citySignals).slice(0, 4).map(signal => {
    const edge = signal.probability_edge ?? signal.edge
    const price = signal.limit_price ?? signal.market_probability
    return {
      label: `${shortDate(signal.target_date)} · ${signalBucketLabel(signal, unit)}`,
      value: signal.actionable ? 'BUY YES' : signal.status || '观察',
      meta: `price ${fmtPrice(price)} · edge ${fmtSignedPct(edge)} · ${signal.decision?.reasons?.[0] || signal.manual_note || signal.reasoning || '--'}`,
      tone: signal.actionable ? 'green' : 'neutral',
    }
  })
  const forecastCards: EvidenceCardItem[] = forecastRows.map((point, index) => ({
    id: `forecast-${point.timestamp}-${point.target_date}-${index}`,
    eyebrow: shortTime(point.timestamp),
    title: longDate(point.target_date),
    value: fmtTemp(point.best ?? point.ensemble_mean, unit),
    meta: point.source || 'forecast',
    tone: point.target_date === selectedDate ? 'green' : 'neutral',
    badges: [
      { label: `ECMWF ${fmtTemp(point.ecmwf, unit)}`, tone: 'cyan' },
      { label: `HRRR ${fmtTemp(point.hrrr, unit)}`, tone: 'green' },
      { label: `METAR ${fmtTemp(point.metar, unit)}`, tone: 'amber' },
      { label: `湿度 ${fmtPct(point.humidity)}`, tone: 'neutral' },
    ],
    details: [
      { label: '更新时间', value: shortTime(point.timestamp) },
      { label: '目标日期', value: longDate(point.target_date) },
      { label: 'best / ensemble', value: `${fmtTemp(point.best, unit)} / ${fmtTemp(point.ensemble_mean, unit)}` },
      { label: 'ensemble std', value: point.ensemble_std === null || point.ensemble_std === undefined ? '--' : point.ensemble_std.toFixed(2) },
      { label: 'ECMWF', value: fmtTemp(point.ecmwf, unit) },
      { label: 'HRRR', value: fmtTemp(point.hrrr, unit) },
      { label: 'METAR', value: fmtTemp(point.metar, unit) },
      { label: '湿度', value: fmtPct(point.humidity) },
      { label: 'horizon', value: point.horizon || '--' },
      { label: '来源', value: point.source || '--', wide: true },
    ],
  }))
  const metarCards: EvidenceCardItem[] = metarRows.map((point, index) => {
    const forecastValue = point.best ?? point.ensemble_mean
    const gap = forecastValue !== null && forecastValue !== undefined && point.metar !== null && point.metar !== undefined
      ? Number(forecastValue) - Number(point.metar)
      : null
    return {
      id: `metar-${point.timestamp}-${point.target_date}-${index}`,
      eyebrow: shortTime(point.timestamp),
      title: longDate(point.target_date),
      value: fmtTemp(point.metar, unit),
      meta: `预测差 ${fmtSignedTemp(gap, unit)}`,
      tone: gap === null ? 'neutral' : Math.abs(gap) <= 1.5 ? 'green' : Math.abs(gap) <= 3 ? 'amber' : 'red',
      badges: [
        { label: `best ${fmtTemp(forecastValue, unit)}`, tone: 'cyan' },
        { label: `差值 ${fmtSignedTemp(gap, unit)}`, tone: gap === null ? 'neutral' : Math.abs(gap) <= 1.5 ? 'green' : Math.abs(gap) <= 3 ? 'amber' : 'red' },
        { label: `湿度 ${fmtPct(point.humidity)}`, tone: 'neutral' },
      ],
      details: [
        { label: '观测时间', value: shortTime(point.timestamp) },
        { label: '目标日期', value: longDate(point.target_date) },
        { label: 'METAR', value: fmtTemp(point.metar, unit) },
        { label: 'best', value: fmtTemp(forecastValue, unit) },
        { label: 'ECMWF', value: fmtTemp(point.ecmwf, unit) },
        { label: 'HRRR', value: fmtTemp(point.hrrr, unit) },
        { label: '湿度', value: fmtPct(point.humidity) },
        { label: '来源', value: point.source || '--', wide: true },
      ],
    }
  })
  const historyCards: EvidenceCardItem[] = historyRows.map((point, index) => {
    const confidence = point.source_confidence
    const confidenceLabel = confidence === null || confidence === undefined
      ? '--'
      : Number(confidence) <= 1
        ? fmtProb(confidence)
        : `${Number(confidence).toFixed(0)}%`
    return {
      id: `history-${point.station_id || cityKey}-${point.target_date}-${index}`,
      eyebrow: point.provider || 'history',
      title: longDate(point.target_date),
      value: fmtTemp(point.actual_high, point.unit || unit),
      meta: `${point.calibration_tier || '--'} / ${point.station_id || series?.station_id || '--'}`,
      tone: point.calibration_tier === 'live_truth' ? 'green' : point.calibration_tier === 'research_truth' ? 'amber' : 'neutral',
      badges: [
        { label: point.calibration_tier || 'truth 待补', tone: point.calibration_tier === 'live_truth' ? 'green' : point.calibration_tier === 'research_truth' ? 'amber' : 'neutral' },
        { label: `湿度 ${fmtPct(point.humidity_mean)}`, tone: 'neutral' },
        { label: `置信 ${confidenceLabel}`, tone: 'cyan' },
      ],
      details: [
        { label: '日期', value: longDate(point.target_date) },
        { label: '实际最高', value: fmtTemp(point.actual_high, point.unit || unit) },
        { label: '湿度', value: fmtPct(point.humidity_mean) },
        { label: 'provider', value: point.provider || '--' },
        { label: 'truth 层级', value: point.calibration_tier || '--' },
        { label: '站点', value: point.station_id || series?.station_id || '--' },
        { label: '抓取时间', value: shortTime(point.fetched_at) },
        { label: '来源链接', value: point.source_url || '--', wide: true },
      ],
    }
  })

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
        暂无天气快照。点击顶部“手动抓取”，系统会同步预测、METAR、历史观测和盘口快照。
      </div>
    )
  }

  return (
    <div className="min-h-full space-y-2 p-3 text-[11px] text-neutral-400">
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
          <button
            type="button"
            onClick={() => setSelectedDate(todayDate)}
            className="border-l border-neutral-800 px-2 py-1 text-[10px] text-neutral-400 hover:bg-neutral-900"
          >
            今天
          </button>
        </div>
        {bestSignal?.event_url && (
          <a href={bestSignal.event_url} target="_blank" rel="noreferrer" className="inline-flex shrink-0 items-center gap-1 border border-cyan-500/30 px-2 py-1 text-[10px] text-cyan-300 hover:bg-cyan-500/10">
            Polymarket <ExternalLink className="h-3 w-3" />
          </a>
        )}
        <button
          onClick={onRefreshWeather}
          disabled={weatherRefreshing || !onRefreshWeather}
          className="inline-flex shrink-0 items-center gap-1 border border-green-500/30 px-2 py-1 text-[10px] text-green-300 hover:bg-green-500/10 disabled:opacity-40"
          title="只补当前城市的预测、小时预报和盘口快照；不会启动旧版无限扫描。"
        >
          <RefreshCw className={`h-3 w-3 ${weatherRefreshing ? 'animate-spin' : ''}`} />
          {weatherRefreshing ? '补天气中' : '补当前天气'}
        </button>
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

      <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2">
        <SourcePulse
          label="预报"
          status={forecastStatus}
          value={freshnessLabel(latestForecast?.timestamp)}
          meta={elapsedLabel(forecastRefreshStage?.elapsed_ms) || latestForecast?.source || '等待抓取'}
          details={[
            { label: '更新时间', value: shortTime(latestForecast?.timestamp) },
            { label: '目标日', value: longDate(latestForecast?.target_date ?? selectedDate) },
            { label: '最佳 / ECMWF / HRRR', value: `${fmtTemp(latestForecast?.best ?? latestForecast?.ensemble_mean, unit)} / ${fmtTemp(latestForecast?.ecmwf, unit)} / ${fmtTemp(latestForecast?.hrrr, unit)}` },
            { label: '来源', value: latestForecast?.source || '等待抓取' },
            { label: '刷新阶段', value: refreshStageLabel(forecastRefreshStage) },
          ]}
          samples={forecastSamples}
        />
        <SourcePulse
          label="METAR"
          status={metarStatus}
          value={freshnessLabel(latestMetar?.timestamp)}
          meta={latestMetar?.metar !== null && latestMetar?.metar !== undefined ? fmtTemp(latestMetar.metar, unit) : '等待观测'}
          details={[
            { label: '观测时间', value: shortTime(latestMetar?.timestamp) },
            { label: '目标日', value: longDate(latestMetar?.target_date ?? selectedDate) },
            { label: 'METAR / best', value: `${fmtTemp(latestMetar?.metar, unit)} / ${fmtTemp(latestMetar?.best ?? latestMetar?.ensemble_mean, unit)}` },
            { label: '湿度', value: fmtPct(latestMetar?.humidity) },
            { label: '来源', value: latestMetar?.source || '等待观测' },
          ]}
          samples={metarSamples}
        />
        <SourcePulse
          label="历史观测"
          status={historyStatus}
          value={latestHistoryFetch ? freshnessLabel(latestHistoryFetch) : latestHistory ? shortDate(latestHistory.target_date) : '无数据'}
          meta={latestHistory?.provider || truthTier}
          details={[
            { label: '日期', value: longDate(latestHistory?.target_date ?? selectedDate) },
            { label: '实际最高', value: fmtTemp(latestHistory?.actual_high, latestHistory?.unit || unit) },
            { label: 'provider', value: latestHistory?.provider || '无数据' },
            { label: '站点', value: latestHistory?.station_id || series?.station_id || '未映射' },
            { label: 'truth 层级', value: truthTier },
            { label: '抓取时间', value: shortTime(latestHistoryFetch) },
          ]}
          samples={historySamples}
        />
        <SourcePulse
          label="盘口/信号"
          status={orderbookRefreshStage?.ok || signalRefreshStage?.ok ? 'fresh' : 'missing'}
          value={elapsedLabel(orderbookRefreshStage?.elapsed_ms) || elapsedLabel(signalRefreshStage?.elapsed_ms) || '未刷新'}
          meta={orderbookRefreshStage?.error || signalRefreshStage?.error || productionRefresh?.message || '等待手动抓取'}
          details={[
            { label: '信号', value: `${actionableSignals.length} 可操作 / ${citySignals.length} 总数` },
            { label: '选中日期信号', value: `${selectedDateSignals.length}` },
            { label: '盘口刷新', value: refreshStageLabel(orderbookRefreshStage) },
            { label: '信号刷新', value: refreshStageLabel(signalRefreshStage) },
            { label: '最新消息', value: orderbookRefreshStage?.error || signalRefreshStage?.error || productionRefresh?.message || '等待手动抓取' },
          ]}
          samples={marketSamples}
        />
      </div>

      <div className={`grid gap-2 border px-2 py-2 md:grid-cols-[1.2fr_repeat(4,minmax(0,1fr))_auto] ${decisionTone === 'green' ? 'border-green-500/30 bg-green-500/5' : decisionTone === 'amber' ? 'border-amber-500/30 bg-amber-500/5' : 'border-neutral-800 bg-black'}`}>
        <div className="min-w-0">
          <div className="text-[10px] text-neutral-500">选中日期判断</div>
          <div className={`truncate text-sm font-medium ${decisionTone === 'green' ? 'text-green-300' : decisionTone === 'amber' ? 'text-amber-300' : 'text-neutral-200'}`}>
            {decisionLabel}
          </div>
          <div className="truncate text-[10px] text-neutral-600" title={decisionReason}>{decisionReason}</div>
        </div>
        <DecisionMetric label="推荐合约" value={signalBucketLabel(bestSignal, unit)} sub={bestSignal ? (isOpenTailBucket(bestSignal) ? '开放尾桶，需严控' : longDate(bestSignal.target_date)) : longDate(selectedDate)} />
        <DecisionMetric label="盘口" value={bestSignal?.limit_price !== undefined && bestSignal?.limit_price !== null ? fmtPrice(bestSignal.limit_price) : '--'} sub={bestSignal?.spread !== undefined && bestSignal?.spread !== null ? `spread ${fmtPrice(bestSignal.spread)}` : '等待盘口'} />
        <DecisionMetric label="模型 / Edge" value={bestSignal ? fmtProb(bestSignal.calibrated_probability ?? bestSignal.model_probability) : '--'} sub={bestSignal ? fmtSignedPct(bestSignal.probability_edge ?? bestSignal.edge) : '无概率'} />
        <DecisionMetric label="预测-METAR" value={metarGap === null ? '--' : fmtTemp(metarGap, unit)} sub={`预测 ${fmtTemp(selectedForecast, unit)}`} />
        {bestSignal?.event_url ? (
          <a href={bestSignal.event_url} target="_blank" rel="noreferrer" className="inline-flex min-h-9 items-center justify-center gap-1 border border-cyan-500/30 px-2 text-[10px] text-cyan-300 hover:bg-cyan-500/10">
            Polymarket <ExternalLink className="h-3 w-3" />
          </a>
        ) : (
          <span className="inline-flex min-h-9 items-center justify-center border border-neutral-800 px-2 text-[10px] text-neutral-600">无链接</span>
        )}
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(92px,1fr))] gap-2">
        <Metric icon={<ThermometerSun className="h-3.5 w-3.5" />} label="最新预测" value={fmtTemp(latestForecast?.best ?? forecastFallback?.mean_high, unit)} tone="green" sub={freshnessLabel(latestForecast?.timestamp)} />
        <Metric icon={<CloudSun className="h-3.5 w-3.5" />} label="METAR 实测" value={fmtTemp(latestMetar?.metar, unit)} tone="amber" sub={freshnessLabel(latestMetar?.timestamp)} />
        <Metric icon={<Database className="h-3.5 w-3.5" />} label="历史最高" value={fmtTemp(latestHistory?.actual_high, unit)} tone="cyan" sub={latestHistory?.provider || truthTier} />
        <Metric icon={<Signal className="h-3.5 w-3.5" />} label="可操作信号" value={`${actionableSignals.length}/${citySignals.length}`} tone={actionableSignals.length > 0 ? 'green' : 'neutral'} sub={bestSignal ? `${signalBucketLabel(bestSignal, unit)} · ${(((bestSignal.probability_edge ?? bestSignal.edge) || 0) * 100).toFixed(1)}%` : '暂无'} />
      </div>

      <section className="border border-neutral-800 bg-black">
        <div className="border-b border-neutral-800">
          <div className="flex flex-wrap items-center justify-between gap-2 px-2 py-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <EvidenceBadge label="Forecast" status={forecastStatus} detail={freshnessLabel(latestForecast?.timestamp)} />
              <EvidenceBadge label="METAR" status={metarStatus} detail={freshnessLabel(latestMetar?.timestamp)} />
              <EvidenceBadge label="Historical" status={historyStatus} detail={latestHistory?.provider || 'no data'} />
              <EvidenceBadge label="Cloud" status={humidityAvailable ? 'fresh' : 'missing'} detail={humidityAvailable ? 'humidity proxy ready' : 'cloud feed pending'} />
              <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500">Hourly {hourlyRows.length}</span>
            </div>
            <div className="text-[10px] text-neutral-500">PolyWX-style city workbench · local time</div>
          </div>
          <div className="flex gap-1 overflow-x-auto px-2 pb-2">
            {WORKBENCH_TABS.map(tab => (
              <WorkbenchTabButton
                key={tab.id}
                tab={tab}
                active={activeWorkbenchTab === tab.id}
                onClick={() => setActiveWorkbenchTab(tab.id)}
              />
            ))}
          </div>
        </div>

        {activeWorkbenchTab === 'forecast' && (
          <div className="space-y-2 p-2">
            <HourlyEvidencePanel
              rows={hourlyRows}
              unit={unit}
              cityName={series?.city_name ?? forecastFallback?.city_name ?? cityKey}
              selectedDate={selectedDate}
              actualHigh={selectedDateRow?.actual_high ?? latestHistory?.actual_high}
              historyProvider={latestHistory?.provider || truthTier}
            />
            <TemperatureDistributionPanel
              signal={distributionSignal}
              items={distributionChartItems}
              unit={unit}
              selectedDate={selectedDate}
              actualHigh={selectedDateRow?.actual_high ?? latestHistory?.actual_high}
            />
            <ForecastDataTable rows={hourlyRows} unit={unit} selectedDate={selectedDate} />
            <details className="border border-neutral-800 bg-neutral-950/30">
              <summary className="cursor-pointer select-none px-2 py-2 text-xs text-neutral-300 hover:bg-neutral-950">
                Forecast snapshot cards · {forecastRows.length}
              </summary>
              <div className="border-t border-neutral-800">
                <EvidenceCards empty="No forecast snapshots for this date" items={forecastCards} />
              </div>
            </details>
          </div>
        )}

        {activeWorkbenchTab === 'metar' && (
          <div className="grid gap-2 p-2 xl:grid-cols-[minmax(0,1fr)_360px]">
            <MetarObservationTable rows={hourlyRows} unit={unit} selectedDate={selectedDate} />
            <EvidenceCards empty="No METAR snapshots for this date" items={metarCards} />
          </div>
        )}

        {activeWorkbenchTab === 'historical' && (
          <div className="grid gap-2 p-2 xl:grid-cols-[minmax(0,1fr)_360px]">
            <HistoricalObservationTable rows={historyRows} unit={unit} stationId={series?.station_id} />
            <EvidenceCards empty="No historical observations yet" items={historyCards} />
          </div>
        )}

        {activeWorkbenchTab === 'diff' && (
          <div className="space-y-2 p-2">
            <DiffStatsPanel rows={hourlyRows} chartData={chartData} unit={unit} selectedDate={selectedDate} />
            <details className="border border-neutral-800 bg-neutral-950/30">
              <summary className="cursor-pointer select-none px-2 py-2 text-xs text-neutral-300 hover:bg-neutral-950">
                Calibration detail · average delta / Pearson R / truth
              </summary>
              <div className="border-t border-neutral-800">
                <BiasPanel
                  chartData={chartData}
                  series={series}
                  historyRows={historyRows}
                  forecastRows={forecastRows}
                  selectedDate={selectedDate}
                  selectedDateRow={selectedDateRow}
                  unit={unit}
                  truthTier={truthTier}
                  forecastStatus={forecastStatus}
                  metarStatus={metarStatus}
                  historyStatus={historyStatus}
                  citySignals={citySignals}
                  actionableSignals={actionableSignals}
                  latestHistory={latestHistory}
                  latestForecast={latestForecast}
                />
              </div>
            </details>
          </div>
        )}

        {activeWorkbenchTab === 'fetch' && (
          <div className="grid gap-2 p-2 xl:grid-cols-[minmax(0,1fr)_420px]">
            <EventTimeline events={eventRows} />
            <SignalCards signals={citySignals.slice(0, 18)} unit={unit} selectedDate={selectedDate} />
          </div>
        )}
      </section>

      {backfillResult && (
        <div className="border border-cyan-500/20 bg-cyan-500/5 px-2 py-1 text-[10px] text-cyan-300">
          最近补历史：写入 {backfillResult.fetched} 条，错误 {backfillResult.errors.length} 个
        </div>
      )}
    </div>
  )
}

function WorkbenchTabButton({
  tab,
  active,
  onClick,
}: {
  tab: (typeof WORKBENCH_TABS)[number]
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`min-w-[120px] shrink-0 border px-2 py-1.5 text-left ${
        active
          ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200'
          : 'border-neutral-800 bg-neutral-950/40 text-neutral-500 hover:border-neutral-700 hover:text-neutral-300'
      }`}
      title={tab.note}
    >
      <div className="text-[11px] font-medium">{tab.label}</div>
      <div className="truncate text-[9px] opacity-70">{tab.note}</div>
    </button>
  )
}

function ForecastDataTable({ rows, unit, selectedDate }: { rows: HourlyWeatherRow[]; unit: string; selectedDate: string }) {
  return (
    <section className="border border-neutral-800 bg-black">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
        <div>
          <div className="text-[10px] text-neutral-500">Forecast Data</div>
          <div className="text-xs text-neutral-100">{longDate(selectedDate)} · {rows.length} rows</div>
        </div>
        <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500">PolyWX schema</span>
      </div>
      {rows.length === 0 ? (
        <div className="max-h-[360px] overflow-auto">
          <table className="min-w-[980px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {['Time', 'Temp', 'Cloud', 'Precip', 'Wind', 'Condition', 'Pres', 'Dew', 'Changes', 'Fetched (Sys)', 'Fetched (Local)'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={11} className="px-2 py-12 text-center text-neutral-600">
                  No hourly forecast rows for this date. Use manual fetch to populate this panel.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <div className="max-h-[360px] overflow-auto">
          <table className="min-w-[980px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {['Time', 'Temp', 'Cloud', 'Precip', 'Wind', 'Condition', 'Pres', 'Dew', 'Changes', 'Fetched (Sys)', 'Fetched (Local)'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.id} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                  <td className="px-2 py-1 tabular-nums text-neutral-300">{row.label}</td>
                  <td className="px-2 py-1 tabular-nums text-green-300">{fmtTemp(row.forecast, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-amber-300">{fmtPct(row.humidity)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-600">--</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-600">--</td>
                  <td className="max-w-[140px] truncate px-2 py-1 text-neutral-400" title={`${row.source || '--'} · ${row.horizon || '--'}`}>
                    {row.source || row.horizon || '--'}
                  </td>
                  <td className="px-2 py-1 tabular-nums text-neutral-600">--</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-600">--</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{row.archive ? 'archive' : row.member_count ? `n ${row.member_count}` : '--'}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-500">{shortTime(row.timestamp)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-500">{shortHour(row.timestamp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <details className="border-t border-neutral-900 px-2 py-1 text-[9px] text-neutral-600">
        <summary className="cursor-pointer select-none hover:text-neutral-400">Schema notes</summary>
        <div className="mt-1 leading-relaxed">
          Cloud currently uses the collected humidity proxy when true cloud cover is unavailable. Precip, wind, pressure and dew point stay blank until the weather source stores those fields.
        </div>
      </details>
    </section>
  )
}

function MetarObservationTable({ rows, unit, selectedDate }: { rows: HourlyWeatherRow[]; unit: string; selectedDate: string }) {
  const metarRows = rows.filter(row => row.metar !== null && row.metar !== undefined)

  return (
    <section className="min-w-0 border border-neutral-800 bg-black">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
        <div>
          <div className="text-[10px] text-neutral-500">METAR</div>
          <div className="text-xs text-neutral-100">{longDate(selectedDate)} · {metarRows.length} observations</div>
        </div>
        <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500">local station</span>
      </div>
      {metarRows.length === 0 ? (
        <div className="max-h-[560px] overflow-auto">
          <table className="min-w-[760px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {['Time', 'Observed', 'Forecast', 'Delta', 'Humidity', 'Source', 'Fetched'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={7} className="px-2 py-12 text-center text-neutral-600">
                  No METAR observations for this date yet.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <div className="max-h-[560px] overflow-auto">
          <table className="min-w-[760px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {['Time', 'Observed', 'Forecast', 'Delta', 'Humidity', 'Source', 'Fetched'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metarRows.map(row => (
                <tr key={row.id} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                  <td className="px-2 py-1 tabular-nums text-neutral-300">{row.label}</td>
                  <td className="px-2 py-1 tabular-nums text-amber-300">{fmtTemp(row.metar, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-green-300">{fmtTemp(row.forecast, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-300">{fmtSignedTemp(row.gap, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPct(row.humidity)}</td>
                  <td className="max-w-[140px] truncate px-2 py-1 text-neutral-500" title={`${row.source || '--'} · ${row.horizon || '--'}`}>{row.source || '--'}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-500">{shortTime(row.timestamp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function HistoricalObservationTable({ rows, unit, stationId }: { rows: HistoricalWeatherPoint[]; unit: string; stationId?: string }) {
  return (
    <section className="min-w-0 border border-neutral-800 bg-black">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
        <div>
          <div className="text-[10px] text-neutral-500">Historical</div>
          <div className="text-xs text-neutral-100">{rows.length} settlement-truth rows</div>
        </div>
        <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500">{stationId || 'station pending'}</span>
      </div>
      {rows.length === 0 ? (
        <div className="max-h-[560px] overflow-auto">
          <table className="min-w-[900px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {['Date', 'Actual High', 'Humidity', 'Provider', 'Tier', 'Station', 'Fetched', 'Source'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={8} className="px-2 py-12 text-center text-neutral-600">
                  No historical observations yet. Backfill history to compare forecast against actual highs.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <div className="max-h-[560px] overflow-auto">
          <table className="min-w-[900px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {['Date', 'Actual High', 'Humidity', 'Provider', 'Tier', 'Station', 'Fetched', 'Source'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={`${row.station_id || row.city}-${row.target_date}`} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                  <td className="px-2 py-1 tabular-nums text-neutral-300">{longDate(row.target_date)}</td>
                  <td className="px-2 py-1 tabular-nums text-cyan-300">{fmtTemp(row.actual_high, row.unit || unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPct(row.humidity_mean)}</td>
                  <td className="max-w-[160px] truncate px-2 py-1 text-neutral-400" title={row.provider || '--'}>{row.provider || '--'}</td>
                  <td className="px-2 py-1 text-neutral-400">{row.calibration_tier || '--'}</td>
                  <td className="px-2 py-1 text-neutral-500">{row.station_id || stationId || '--'}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-500">{shortTime(row.fetched_at)}</td>
                  <td className="max-w-[180px] truncate px-2 py-1 text-neutral-500" title={row.source_url || '--'}>
                    {row.source_url ? (
                      <a href={row.source_url} target="_blank" rel="noreferrer" className="text-cyan-300 hover:text-cyan-100">source</a>
                    ) : '--'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function DiffStatsPanel({
  rows,
  chartData,
  unit,
  selectedDate,
}: {
  rows: HourlyWeatherRow[]
  chartData: WeatherChartRow[]
  unit: string
  selectedDate: string
}) {
  const hourlyPairs = rows
    .filter(row => row.forecast !== null && row.forecast !== undefined && row.metar !== null && row.metar !== undefined)
    .map(row => ({
      id: row.id,
      time: row.label,
      observed: Number(row.metar),
      forecast: Number(row.forecast),
      delta: Number(row.metar) - Number(row.forecast),
      source: row.source || 'METAR',
    }))
  const dailyPairs = chartData
    .filter(row => row.actual_high !== null && row.actual_high !== undefined && row.forecast_high !== null && row.forecast_high !== undefined)
    .map(row => ({
      id: row.date,
      time: longDate(row.date),
      observed: Number(row.actual_high),
      forecast: Number(row.forecast_high),
      delta: Number(row.actual_high) - Number(row.forecast_high),
      source: row.historical_provider || row.forecast_source || 'history',
    }))
  const tableRows = hourlyPairs.length > 0 ? hourlyPairs : dailyPairs.slice(-30).reverse()
  const deltas = tableRows.map(row => row.delta)
  const avgDelta = mean(deltas)
  const correlation = pearsonR(
    tableRows.map(row => row.forecast),
    tableRows.map(row => row.observed)
  )
  const maxAbsDelta = Math.max(1, ...deltas.map(delta => Math.abs(delta)))

  return (
    <section className="border border-neutral-800 bg-black">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
        <div>
          <div className="text-[10px] text-neutral-500">Diff Stats (Observed - Forecast)</div>
          <div className="text-xs text-neutral-100">{longDate(selectedDate)} · {tableRows.length} paired rows</div>
        </div>
        <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500">{hourlyPairs.length > 0 ? 'hourly' : 'daily history'}</span>
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(130px,1fr))] gap-2 border-b border-neutral-900 p-2">
        <MetricCard label="Average Delta" value={fmtSignedTemp(avgDelta, unit)} sub="Observed - Forecast" />
        <MetricCard label="Accuracy" value={fmtPearson(correlation)} sub="Pearson R" />
        <MetricCard label="Overlap" value={tableRows.length ? `${tableRows.length}` : '--'} sub="paired samples" />
        <MetricCard label="Max Abs Delta" value={fmtTemp(Math.max(0, ...deltas.map(delta => Math.abs(delta))), unit)} sub="worst visible row" />
      </div>
      {tableRows.length === 0 ? (
        <div className="max-h-[460px] overflow-auto">
          <table className="min-w-[760px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {['Time', 'Observed', 'Forecast', 'Delta', 'Magnitude', 'Source'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={6} className="px-2 py-12 text-center text-neutral-600">
                  No paired observed/forecast rows yet.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <div className="max-h-[460px] overflow-auto">
          <table className="min-w-[760px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {['Time', 'Observed', 'Forecast', 'Delta', 'Magnitude', 'Source'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows.map(row => {
                const width = Math.max(4, Math.min(100, Math.abs(row.delta) / maxAbsDelta * 100))
                const tone = errorTone(Math.abs(row.delta))
                const barClass = tone === 'green' ? 'bg-green-400/70' : tone === 'amber' ? 'bg-amber-400/75' : 'bg-red-400/75'
                return (
                  <tr key={row.id} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                    <td className="px-2 py-1 tabular-nums text-neutral-300">{row.time}</td>
                    <td className="px-2 py-1 tabular-nums text-amber-300">{fmtTemp(row.observed, unit)}</td>
                    <td className="px-2 py-1 tabular-nums text-green-300">{fmtTemp(row.forecast, unit)}</td>
                    <td className="px-2 py-1 tabular-nums text-neutral-200">{fmtSignedTemp(row.delta, unit)}</td>
                    <td className="px-2 py-1">
                      <div className="h-1.5 w-24 overflow-hidden bg-neutral-900">
                        <div className={`h-full ${barClass}`} style={{ width: `${width}%` }} />
                      </div>
                    </td>
                    <td className="max-w-[180px] truncate px-2 py-1 text-neutral-500" title={row.source}>{row.source}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function HourlyEvidencePanel({
  rows,
  unit,
  cityName,
  selectedDate,
  actualHigh,
  historyProvider,
}: {
  rows: HourlyWeatherRow[]
  unit: string
  cityName?: string
  selectedDate: string
  actualHigh?: number | null
  historyProvider?: string
}) {
  const forecastValues = rows.map(row => row.forecast).filter((value): value is number => Number.isFinite(Number(value)))
  const metarValues = rows.map(row => row.metar).filter((value): value is number => Number.isFinite(Number(value)))
  const humidityValues = rows.map(row => row.humidity).filter((value): value is number => Number.isFinite(Number(value)))
  const gapValues = rows.map(row => row.gap).filter((value): value is number => Number.isFinite(Number(value)))
  const forecastMax = forecastValues.length > 0 ? Math.max(...forecastValues) : null
  const metarMax = metarValues.length > 0 ? Math.max(...metarValues) : null
  const avgGap = mean(gapValues)
  const avgHumidity = mean(humidityValues)
  const pairedRows = rows.filter(row => row.forecast !== null && row.forecast !== undefined && row.metar !== null && row.metar !== undefined)
  const pearson = pearsonR(
    pairedRows.map(row => Number(row.forecast)),
    pairedRows.map(row => Number(row.metar))
  )
  const metarCoverage = rows.length > 0 ? metarValues.length / rows.length : null
  const actualMetarDelta = actualHigh !== null && actualHigh !== undefined && metarMax !== null
    ? Number(metarMax) - Number(actualHigh)
    : null
  const overlapLabel = actualMetarDelta === null
    ? (metarCoverage === null ? '--' : `${Math.round(metarCoverage * 100)}%`)
    : fmtSignedTemp(actualMetarDelta, unit)

  if (rows.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-4 text-center text-neutral-600">
        该日期暂无逐小时快照。点击“手动抓取”后，这里会按抓取时间展示预报、METAR、湿度和模型差异。
      </div>
    )
  }

  return (
    <div className="grid min-h-0 flex-1 gap-2 p-2 xl:grid-cols-[minmax(0,1fr)_320px]">
      <section className="min-h-0 border border-neutral-900 bg-neutral-950/30">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-neutral-900 px-2 py-1.5">
          <div>
            <div className="text-[10px] text-neutral-500">逐小时气温</div>
            <div className="text-xs text-neutral-100">{cityName || '当前城市'} · {longDate(selectedDate)}</div>
          </div>
          <div className="flex flex-wrap gap-1 text-[9px] text-neutral-500">
            <span className="border border-neutral-800 px-1.5 py-0.5">预报最高 {fmtTemp(forecastMax, unit)}</span>
            <span className="border border-neutral-800 px-1.5 py-0.5">METAR最高 {fmtTemp(metarMax, unit)}</span>
            <span className="border border-neutral-800 px-1.5 py-0.5">平均差 {fmtSignedTemp(avgGap, unit)}</span>
          </div>
        </div>

        <div
          className="h-[300px] p-2"
          role="img"
          aria-label={`${cityName || '当前城市'}逐小时天气证据图。绿线为预报，橙线为 METAR，青线和蓝线分别是 ECMWF 与 HRRR，浅橙柱为云量或湿度百分比，虚线为峰值标记。`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 8, right: 18, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="#1f1f1f" strokeDasharray="3 3" />
              <XAxis dataKey="label" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} minTickGap={12} />
              <YAxis yAxisId="temp" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} />
              <YAxis yAxisId="humidity" orientation="right" stroke="#737373" fontSize={10} tickLine={false} axisLine={false} domain={[0, 100]} />
              <Tooltip
                contentStyle={{ background: '#050505', border: '1px solid #262626', color: '#e5e5e5', fontSize: 11 }}
                formatter={(value: any, name: any) => {
                  if (name === '云量/湿度 %') return [fmtPct(Number(value)), name]
                  return [fmtTemp(Number(value), unit), name]
                }}
                labelFormatter={(_, payload) => payload?.[0]?.payload?.timestamp ? shortTime(payload[0].payload.timestamp) : ''}
              />
              <Bar yAxisId="humidity" dataKey="humidity" name="云量/湿度 %" fill="#f59e0b" fillOpacity={0.22} maxBarSize={12} radius={[1, 1, 0, 0]} />
              {forecastMax !== null && (
                <ReferenceLine yAxisId="temp" y={forecastMax} stroke="#22c55e" strokeDasharray="4 4" strokeOpacity={0.55} label={{ value: '预报峰值', fill: '#86efac', fontSize: 10 }} />
              )}
              {metarMax !== null && (
                <ReferenceLine yAxisId="temp" y={metarMax} stroke="#f97316" strokeDasharray="4 4" strokeOpacity={0.55} label={{ value: 'METAR峰值', fill: '#fdba74', fontSize: 10 }} />
              )}
              {actualHigh !== null && actualHigh !== undefined && (
                <ReferenceLine yAxisId="temp" y={Number(actualHigh)} stroke="#38bdf8" strokeDasharray="2 5" strokeOpacity={0.5} label={{ value: '历史最高', fill: '#7dd3fc', fontSize: 10 }} />
              )}
              <Line yAxisId="temp" type="monotone" dataKey="forecast" name="预报（本地时）" stroke="#22c55e" dot={false} strokeWidth={2.4} connectNulls={false} />
              <Line yAxisId="temp" type="monotone" dataKey="metar" name="METAR（本地时）" stroke="#f97316" dot={false} strokeWidth={2} connectNulls={false} />
              <Line yAxisId="temp" type="monotone" dataKey="ecmwf" name="ECMWF" stroke="#38bdf8" dot={false} strokeWidth={1.5} connectNulls={false} />
              <Line yAxisId="temp" type="monotone" dataKey="hrrr" name="HRRR" stroke="#818cf8" dot={false} strokeWidth={1.5} connectNulls={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        <details className="border-t border-neutral-900 px-2 py-1 text-[9px] text-neutral-600">
          <summary className="cursor-pointer select-none hover:text-neutral-400">数据说明</summary>
          <div className="mt-1 leading-relaxed">
            绿色为预报，橙色为 METAR，青/蓝为可用模型分量，柱状为云量或湿度百分比。中国天气实况与 PWS 实时源尚未接入时不伪造曲线，只在数据明细中保留接入位置。
          </div>
        </details>
      </section>

      <aside className="min-h-0 border border-neutral-900 bg-neutral-950/30">
        <div className="grid grid-cols-2 gap-1 border-b border-neutral-900 p-2 text-[10px]">
          <MetricCard label="平均 Δ" value={fmtSignedTemp(avgGap, unit)} sub="实测 - 预报" />
          <MetricCard label="准确度" value={fmtPearson(pearson)} sub={`n=${pairedRows.length}`} />
          <MetricCard label="历史↔METAR" value={overlapLabel} sub={actualMetarDelta === null ? `覆盖 ${metarValues.length}/${rows.length}` : historyProvider || '日高温差'} />
          <MetricCard label="云量/湿度" value={fmtPct(avgHumidity)} sub="样本均值" />
          <MetricCard label="预报高点" value={fmtTemp(forecastMax, unit)} sub="峰值标记" />
          <MetricCard label="METAR高点" value={fmtTemp(metarMax, unit)} sub="峰值标记" />
        </div>
        <div className="max-h-[360px] overflow-auto">
          <table className="w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                <th className="px-2 py-1 font-normal">时间</th>
                <th className="px-2 py-1 font-normal">预报</th>
                <th className="px-2 py-1 font-normal">METAR</th>
                <th className="px-2 py-1 font-normal">云量/湿度</th>
                <th className="px-2 py-1 font-normal">来源</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.id} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                  <td className="px-2 py-1 tabular-nums text-neutral-300">{row.label}</td>
                  <td className="px-2 py-1 tabular-nums text-green-300">{fmtTemp(row.forecast, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-amber-300">{fmtTemp(row.metar, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPct(row.humidity)}</td>
                  <td className="max-w-[90px] truncate px-2 py-1 text-neutral-500" title={`${row.source || '--'} · ${row.horizon || '--'} · n=${row.member_count ?? '--'} · ${shortTime(row.timestamp)}`}>
                    {row.archive ? 'archive' : row.source || '--'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </aside>
    </div>
  )
}

type BiasPoint = {
  date: string
  label: string
  actual: number
  forecast: number
  error: number
  metar?: number | null
  provider?: string
  calibrationTier?: string
  forecastSource?: string
}

function BiasPanel({
  chartData,
  series,
  historyRows,
  forecastRows,
  selectedDate,
  selectedDateRow,
  unit,
  truthTier,
  forecastStatus,
  metarStatus,
  historyStatus,
  citySignals,
  actionableSignals,
  latestHistory,
  latestForecast,
}: {
  chartData: WeatherChartRow[]
  series?: WeatherCitySeries
  historyRows: HistoricalWeatherPoint[]
  forecastRows: WeatherCityPoint[]
  selectedDate: string
  selectedDateRow?: WeatherChartRow
  unit: string
  truthTier: string
  forecastStatus: EvidenceStatus
  metarStatus: EvidenceStatus
  historyStatus: EvidenceStatus
  citySignals: WeatherSignal[]
  actionableSignals: WeatherSignal[]
  latestHistory?: HistoricalWeatherPoint
  latestForecast?: WeatherCityPoint
}) {
  const paired: BiasPoint[] = chartData
    .filter(row => row.actual_high !== null && row.actual_high !== undefined && row.forecast_high !== null && row.forecast_high !== undefined)
    .map(row => {
      const actual = Number(row.actual_high)
      const forecast = Number(row.forecast_high)
      return {
        date: row.date,
        label: row.label,
        actual,
        forecast,
        error: actual - forecast,
        metar: row.metar,
        provider: row.historical_provider,
        calibrationTier: row.calibration_tier,
        forecastSource: row.forecast_source,
      }
    })

  const absErrors = paired.map(point => Math.abs(point.error))
  const mae = mean(absErrors)
  const bias = mean(paired.map(point => point.error))
  const correlation = pearsonR(
    paired.map(point => point.forecast),
    paired.map(point => point.actual)
  )
  const maxAbsError = Math.max(1, ...absErrors)
  const latestPair = paired[paired.length - 1]
  const selectedPair = paired.find(point => point.date === selectedDate)
  const focusPair = selectedPair ?? latestPair
  const historyAll = series?.history_points ?? historyRows
  const historyTotal = series?.history_count ?? historyAll.length
  const forecastTotal = series?.forecast_count ?? series?.forecast_points?.length ?? series?.points?.length ?? forecastRows.length
  const liveTruth = historyAll.filter(point => point.calibration_tier === 'live_truth').length
  const researchTruth = historyAll.filter(point => point.calibration_tier === 'research_truth').length
  const eligibleTruth = liveTruth + researchTruth
  const fallbackTruth = Math.max(0, historyAll.length - eligibleTruth)
  const truthCoverage = historyAll.length > 0 ? eligibleTruth / historyAll.length : null
  const providerCounts = historyAll.reduce<Record<string, number>>((counts, point) => {
    const provider = point.provider || 'unknown'
    counts[provider] = (counts[provider] ?? 0) + 1
    return counts
  }, {})
  const providerSummary = Object.entries(providerCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([provider, count]) => `${provider} ${count}`)
    .join(' · ') || '--'
  const statusLabel = forecastStatus === 'fresh' && historyStatus === 'fresh'
    ? '可读'
    : forecastStatus === 'missing' || historyStatus === 'missing'
      ? '缺数据'
      : '需刷新'

  if (paired.length === 0) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2">
          <MetricCard label="平均 Δ" value="--" sub="实测 - 预报" />
          <MetricCard label="准确度" value="--" sub="Pearson R" />
          <MetricCard label="MAE" value="--" sub="等待配对样本" />
          <MetricCard label="配对样本" value="0" sub={`历史 ${historyTotal} · 预报 ${forecastTotal}`} />
          <MetricCard label="Truth 覆盖" value={truthCoverage === null ? '--' : fmtProb(truthCoverage)} sub={`live ${liveTruth} · research ${researchTruth}`} />
          <MetricCard label="数据状态" value={statusLabel} sub={`预报 ${forecastStatus} · METAR ${metarStatus} · 历史 ${historyStatus}`} />
        </div>
        <div className="mt-2 border border-neutral-800 bg-neutral-950/40 p-4 text-center text-neutral-500">
          <div className="text-xs text-neutral-300">暂无可配对偏差样本</div>
          <div className="mt-1 text-[10px] leading-relaxed">
            当前城市还没有同一天同时包含“历史实际最高温”和“保存预测最高温”的样本。补历史数据或完成更多日度抓取后，这里会显示最近误差、MAE 和 bias。
          </div>
          <details className="mt-3 text-left text-[10px] text-neutral-500">
            <summary className="cursor-pointer select-none text-center hover:text-neutral-300">数据明细</summary>
            <div className="mt-2 grid gap-1 md:grid-cols-2">
              <DetailLine label="选中日期" value={longDate(selectedDate)} />
              <DetailLine label="实际最高" value={fmtTemp(selectedDateRow?.actual_high, unit)} />
              <DetailLine label="预测最高" value={fmtTemp(selectedDateRow?.forecast_high, unit)} />
              <DetailLine label="provider" value={providerSummary} wide />
              <DetailLine label="truth" value={`live ${liveTruth} · research ${researchTruth} · fallback ${fallbackTruth}`} wide />
            </div>
          </details>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-2">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2">
        <MetricCard label="平均 Δ" value={bias === null ? '--' : fmtSignedTemp(bias, unit)} sub="实测 - 预报" />
        <MetricCard label="准确度" value={fmtPearson(correlation)} sub="Pearson R" />
        <MetricCard label="MAE" value={mae === null ? '--' : fmtTemp(mae, unit)} sub="平均绝对误差" />
        <MetricCard label="配对样本" value={`${paired.length}`} sub={`历史 ${historyTotal} · 预报 ${forecastTotal}`} />
        <MetricCard label="Truth 覆盖" value={truthCoverage === null ? '--' : fmtProb(truthCoverage)} sub={`live ${liveTruth} · research ${researchTruth}`} />
      </div>

      <div className="mt-2 grid min-h-0 gap-2 xl:grid-cols-[minmax(0,1fr)_280px]">
        <section className="min-h-[240px] border border-neutral-800 bg-black" aria-label="最近预测误差">
          <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
            <div>
              <div className="text-[10px] text-neutral-500">最近误差</div>
              <div className="text-xs text-neutral-100">实际最高 - 保存预测</div>
            </div>
            <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500">
              {paired.length} paired
            </span>
          </div>
          <div className="max-h-[360px] overflow-y-auto p-2">
            <div className="space-y-1">
              {paired.slice(-14).reverse().map(point => {
                const absError = Math.abs(point.error)
                const tone = errorTone(absError)
                const width = Math.max(4, Math.min(100, (absError / maxAbsError) * 100))
                const barClass = tone === 'green'
                  ? 'bg-green-400/70'
                  : tone === 'amber'
                    ? 'bg-amber-400/75'
                    : 'bg-red-400/75'
                return (
                  <div key={point.date} className={`border px-2 py-1.5 ${point.date === selectedDate ? 'border-cyan-500/35 bg-cyan-500/5' : 'border-neutral-900 bg-neutral-950/50'}`}>
                    <div className="grid grid-cols-[66px_minmax(0,1fr)_58px] items-center gap-2">
                      <span className="text-[10px] tabular-nums text-neutral-400">{shortDate(point.date)}</span>
                      <div className="h-1.5 overflow-hidden bg-neutral-900" aria-hidden="true">
                        <div className={`h-full ${barClass}`} style={{ width: `${width}%` }} />
                      </div>
                      <span className={`text-right text-[10px] tabular-nums ${tone === 'green' ? 'text-green-300' : tone === 'amber' ? 'text-amber-300' : 'text-red-300'}`}>
                        {fmtSignedTemp(point.error, unit)}
                      </span>
                    </div>
                    <div className="mt-1 grid gap-1 text-[9px] text-neutral-600 md:grid-cols-3">
                      <span>实际 {fmtTemp(point.actual, unit)}</span>
                      <span>预测 {fmtTemp(point.forecast, unit)}</span>
                      <span>{point.provider || point.calibrationTier || '--'}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </section>

        <aside className="min-h-[240px] border border-neutral-800 bg-neutral-950/30">
          <div className="border-b border-neutral-800 px-2 py-1.5">
            <div className="text-[10px] text-neutral-500">选中日期校准</div>
            <div className="text-xs text-neutral-100">{longDate(selectedDate || focusPair?.date)}</div>
          </div>
          <div className="space-y-2 p-2">
            <div className="grid grid-cols-2 gap-1">
              <DecisionMetric label="实际最高" value={fmtTemp(focusPair?.actual ?? selectedDateRow?.actual_high, unit)} sub={focusPair?.provider || latestHistory?.provider || truthTier} />
              <DecisionMetric label="预测最高" value={fmtTemp(focusPair?.forecast ?? selectedDateRow?.forecast_high, unit)} sub={focusPair?.forecastSource || latestForecast?.source || 'forecast'} />
              <DecisionMetric label="误差" value={focusPair ? fmtSignedTemp(focusPair.error, unit) : '--'} sub={focusPair ? (errorTone(Math.abs(focusPair.error)) === 'green' ? '低误差' : errorTone(Math.abs(focusPair.error)) === 'amber' ? '需关注' : '偏差大') : '未配对'} />
              <DecisionMetric label="信号" value={`${actionableSignals.length}/${citySignals.length}`} sub={statusLabel} />
            </div>

            <div className="border border-neutral-900 bg-black/40 p-2 text-[10px] leading-relaxed text-neutral-500">
              <div className="mb-1 text-neutral-300">Truth 分层</div>
              <div className="grid grid-cols-3 gap-1 text-center tabular-nums">
                <div className="border border-neutral-800 px-1 py-1">live <span className="text-neutral-200">{liveTruth}</span></div>
                <div className="border border-neutral-800 px-1 py-1">research <span className="text-neutral-200">{researchTruth}</span></div>
                <div className="border border-neutral-800 px-1 py-1">fallback <span className="text-neutral-200">{fallbackTruth}</span></div>
              </div>
            </div>

            <details className="border border-neutral-900 bg-black/40 p-2 text-[10px] text-neutral-500">
              <summary className="cursor-pointer select-none hover:text-neutral-300">更多明细</summary>
              <div className="mt-2 grid gap-1">
                <DetailLine label="最近配对" value={latestPair ? `${longDate(latestPair.date)} · ${fmtSignedTemp(latestPair.error, unit)}` : '--'} wide />
                <DetailLine label="provider" value={providerSummary} wide />
                <DetailLine label="METAR" value={fmtTemp(focusPair?.metar, unit)} />
                <DetailLine label="数据状态" value={`预报 ${forecastStatus} · METAR ${metarStatus} · 历史 ${historyStatus}`} wide />
                <DetailLine label="用途" value="这是研究/校准视图，实盘仍需独立 truth 样本和回放闸门通过。" wide />
              </div>
            </details>
          </div>
        </aside>
      </div>
    </div>
  )
}

function SignalCards({ signals, unit, selectedDate }: { signals: WeatherSignal[]; unit: string; selectedDate: string }) {
  const actionable = signals.filter(signal => signal.actionable).length
  const withPositions = signals.filter(signal => signal.paper_position).length
  const dated = selectedDate ? signals.filter(signal => signal.target_date === selectedDate).length : 0

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-2">
      <div className="mb-2 grid grid-cols-[repeat(auto-fit,minmax(110px,1fr))] gap-1 text-[10px]">
        <SignalStat label="可执行" value={`${actionable}`} tone={actionable > 0 ? 'green' : 'neutral'} />
        <SignalStat label="选中日期" value={`${dated}`} />
        <SignalStat label="模拟持仓" value={`${withPositions}`} tone={withPositions > 0 ? 'cyan' : 'neutral'} />
        <SignalStat label="总信号" value={`${signals.length}`} />
      </div>
      {signals.length === 0 && (
        <div className="flex min-h-[180px] items-center justify-center border border-neutral-900 p-4 text-neutral-600">
          该城市暂无市场信号
        </div>
      )}
      <div className="space-y-1">
        {signals.map(signal => {
          const edge = signal.probability_edge ?? signal.edge
          const probability = signal.calibrated_probability ?? signal.model_probability
          const price = signal.limit_price ?? signal.market_probability
          const isSelectedDate = !selectedDate || signal.target_date === selectedDate
          const blockedReasons = signal.decision?.reasons ?? signal.live_block_reasons ?? []
          const cautions = signal.decision?.cautions ?? signal.live_cautions ?? []
          return (
            <div
              key={signal.id ?? signal.market_id}
              className={`border px-2 py-1.5 ${
                signal.actionable
                  ? 'border-green-500/30 bg-green-500/5'
                  : isSelectedDate
                    ? 'border-amber-500/25 bg-amber-500/5'
                    : 'border-neutral-800 bg-neutral-950'
              }`}
            >
              <div className="grid gap-2 md:grid-cols-[1.3fr_repeat(4,minmax(0,1fr))_auto]">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1">
                    <span className={`border px-1.5 py-0.5 text-[9px] ${signal.actionable ? 'border-green-500/40 text-green-300' : 'border-neutral-700 text-neutral-500'}`}>
                      {signal.actionable ? 'BUY YES' : signal.status || '观察'}
                    </span>
                    {isSelectedDate && <span className="border border-cyan-500/25 px-1.5 py-0.5 text-[9px] text-cyan-300">当前日期</span>}
                    {signal.paper_position && <span className="border border-amber-500/25 px-1.5 py-0.5 text-[9px] text-amber-300">已模拟</span>}
                    {isOpenTailBucket(signal) && <span className="border border-red-500/25 px-1.5 py-0.5 text-[9px] text-red-300">开放尾桶</span>}
                  </div>
                  <div className="mt-1 truncate text-xs text-neutral-100" title={signal.question || signalBucketLabel(signal, unit)}>
                    {signalBucketLabel(signal, unit)}
                  </div>
                  <div className="truncate text-[9px] text-neutral-600">{longDate(signal.target_date)} · {signal.direction || 'YES'}</div>
                </div>
                <DecisionMetric label="盘口" value={fmtPrice(price)} sub={signal.spread !== undefined && signal.spread !== null ? `spread ${fmtPrice(signal.spread)}` : `bid ${fmtPrice(signal.bid_price)}`} />
                <DecisionMetric label="概率" value={fmtProb(probability)} sub={`市场 ${fmtProb(signal.market_probability)}`} />
                <DecisionMetric label="Edge / EV" value={fmtSignedPct(edge)} sub={signal.calibrated_edge !== undefined && signal.calibrated_edge !== null ? `cal ${fmtSignedPct(signal.calibrated_edge)}` : `raw ${fmtSignedPct(signal.raw_edge)}`} />
                <DecisionMetric label="模型" value={fmtTemp(signal.ensemble_mean, unit)} sub={`σ ${signal.ensemble_std?.toFixed?.(1) ?? '--'} · n ${signal.ensemble_members ?? '--'}`} />
                {signal.event_url ? (
                  <a href={signal.event_url} target="_blank" rel="noreferrer" className="inline-flex min-h-9 items-center justify-center gap-1 border border-cyan-500/30 px-2 text-[10px] text-cyan-300 hover:bg-cyan-500/10">
                    Polymarket <ExternalLink className="h-3 w-3" />
                  </a>
                ) : (
                  <span className="inline-flex min-h-9 items-center justify-center border border-neutral-800 px-2 text-[10px] text-neutral-600">无链接</span>
                )}
              </div>
              <details className="mt-1 border-t border-neutral-800/80 pt-1 text-[9px] text-neutral-500">
                <summary className="cursor-pointer select-none hover:text-neutral-300">信号明细</summary>
                <div className="mt-1 grid gap-1 md:grid-cols-2">
                  <DetailLine label="建议金额" value={signal.sim_amount !== null && signal.sim_amount !== undefined ? `$${Number(signal.sim_amount).toFixed(2)}` : `$${Number(signal.suggested_size ?? 0).toFixed(2)}`} />
                  <DetailLine label="份额" value={signal.shares !== null && signal.shares !== undefined ? `${Number(signal.shares).toFixed(2)}` : '--'} />
                  <DetailLine label="质量分" value={signal.strategy_score !== undefined && signal.strategy_score !== null ? signal.strategy_score.toFixed(2) : '--'} />
                  <DetailLine label="truth" value={signal.truth?.status || signal.live_risk_level || '--'} />
                  <DetailLine label="fit" value={signal.fit_samples !== undefined ? `${signal.fit_samples} samples · MAE ${signal.fit_mae_f?.toFixed?.(1) ?? '--'}F` : '--'} />
                  <DetailLine label="near-lock" value={signal.near_lock ? `${signal.near_lock.hours_left.toFixed(1)}h · obs ${fmtTemp(signal.near_lock.observed_temp, unit)}` : '--'} />
                  <DetailLine label="阻塞" value={blockedReasons.length ? blockedReasons.join(' · ') : '--'} wide />
                  <DetailLine label="提醒" value={cautions.length ? cautions.join(' · ') : '--'} wide />
                  <DetailLine label="标签" value={[...(signal.strategy_tags ?? []), ...(signal.quality_flags ?? [])].join(' · ') || '--'} wide />
                  <DetailLine label="备注" value={[signal.manual_note, signal.reasoning, ...(signal.strategy_notes ?? [])].filter(Boolean).join(' · ') || '--'} wide />
                  <DetailLine label="market" value={signal.market_id || '--'} wide />
                  <DetailLine label="YES token" value={signal.yes_token_id || '--'} wide />
                </div>
              </details>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function SignalStat({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'green' | 'cyan' | 'neutral' }) {
  const color = tone === 'green' ? 'text-green-300' : tone === 'cyan' ? 'text-cyan-300' : 'text-neutral-200'
  return (
    <div className="border border-neutral-800 px-2 py-1 text-neutral-500">
      {label} <span className={`tabular-nums ${color}`}>{value}</span>
    </div>
  )
}

function DetailLine({ label, value, wide = false }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={`min-w-0 grid grid-cols-[72px_minmax(0,1fr)] gap-1 ${wide ? 'md:col-span-2' : ''}`}>
      <span className="text-neutral-600">{label}</span>
      <span className="min-w-0 break-words text-neutral-400" title={value}>{value}</span>
    </div>
  )
}

function EventTimeline({ events }: { events: DashboardEvent[] }) {
  const rows = events.slice(0, 100)
  const durationLabel = (event: DashboardEvent) => {
    const data = event.data && typeof event.data === 'object' && !Array.isArray(event.data)
      ? event.data as Record<string, unknown>
      : {}
    const raw = data.elapsed_ms ?? data.duration_ms ?? data.duration
    if (typeof raw === 'number') return elapsedLabel(raw)
    if (typeof raw === 'string') return raw
    return '--'
  }
  const statusLabel = (event: DashboardEvent) => {
    const tone = eventTone(event)
    if (tone === 'red') return 'ERR'
    if (tone === 'amber') return 'WARN'
    if (tone === 'green' || tone === 'cyan') return 'OK'
    return 'INFO'
  }
  const statusClass = (status: string) => {
    if (status === 'ERR') return 'text-red-300'
    if (status === 'WARN') return 'text-amber-300'
    if (status === 'OK') return 'text-green-300'
    return 'text-neutral-400'
  }

  return (
    <section className="min-w-0 border border-neutral-800 bg-black">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
        <div>
          <div className="text-[10px] text-neutral-500">Fetch Log (last 100)</div>
          <div className="text-xs text-neutral-100">{rows.length} events</div>
        </div>
        <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500"># / Time / Source / Status / Duration / Message</span>
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(100px,1fr))] gap-1 border-b border-neutral-900 p-2 text-[10px]">
        {['天气', '观测', '盘口', '信号', '刷新'].map(stage => {
          const count = rows.filter(event => eventStage(event) === stage).length
          return (
            <div key={stage} className="border border-neutral-800 px-2 py-1 text-neutral-500">
              {stage} <span className="tabular-nums text-neutral-200">{count}</span>
            </div>
          )
        })}
      </div>
      {rows.length === 0 ? (
        <div className="max-h-[560px] overflow-auto">
          <table className="min-w-[860px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {['#', 'Time', 'Source', 'Status', 'Duration', 'Message'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={6} className="px-2 py-12 text-center text-neutral-600">
                  No fetch or scanner events yet.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <div className="max-h-[560px] overflow-auto">
          <table className="min-w-[860px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {['#', 'Time', 'Source', 'Status', 'Duration', 'Message'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((event, index) => {
                const status = statusLabel(event)
                const data = compactData(event.data, 360)
                const message = [event.message, data && `data: ${data}`].filter(Boolean).join(' · ') || '--'
                return (
                  <tr key={event.id ?? `${event.timestamp}-${index}`} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                    <td className="px-2 py-1 tabular-nums text-neutral-500">{index + 1}</td>
                    <td className="px-2 py-1 tabular-nums text-neutral-300">{shortTime(event.timestamp)}</td>
                    <td className="px-2 py-1 text-neutral-400">{eventStage(event)}</td>
                    <td className={`px-2 py-1 tabular-nums ${statusClass(status)}`}>{status}</td>
                    <td className="px-2 py-1 tabular-nums text-neutral-500">{durationLabel(event)}</td>
                    <td className="max-w-[480px] px-2 py-1 text-neutral-400">
                      <details>
                        <summary className="cursor-pointer truncate hover:text-neutral-200" title={message}>{message}</summary>
                        <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words border border-neutral-900 bg-neutral-950/60 p-2 font-mono text-[9px] leading-relaxed text-neutral-500">
                          {data || event.type || '--'}
                        </pre>
                      </details>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function TemperatureDistributionPanel({
  signal,
  items,
  unit,
  selectedDate,
  actualHigh,
}: {
  signal?: WeatherSignal
  items: DistributionItem[]
  unit: string
  selectedDate: string
  actualHigh?: number | null
}) {
  const distribution = signal?.distribution
  const maxProbability = Math.max(0.01, ...items.map(item => Number(item.probability || 0)))
  const forecastValue = distribution?.forecast_f === null || distribution?.forecast_f === undefined
    ? null
    : unit === 'C'
      ? (Number(distribution.forecast_f) - 32) * 5 / 9
      : Number(distribution.forecast_f)
  const sigmaValue = distribution?.sigma_f === null || distribution?.sigma_f === undefined
    ? null
    : unit === 'C'
      ? Number(distribution.sigma_f) * 5 / 9
      : Number(distribution.sigma_f)
  const chartRows = items.map(item => ({
    ...item,
    label: fmtBucket(item, unit),
    probabilityPct: Number(item.probability ?? 0) * 100,
    askPct: Number(item.ask ?? 0) * 100,
    edgePct: Number(item.probability_edge ?? item.ev ?? 0) * 100,
  }))

  const probabilityFill = (probability?: number | null, selected = false) => {
    const ratio = Math.max(0.18, Math.min(1, Number(probability ?? 0) / maxProbability))
    if (selected) return `rgba(34, 211, 238, ${0.35 + ratio * 0.55})`
    return `rgba(34, 197, 94, ${0.18 + ratio * 0.72})`
  }

  return (
    <section className="border border-neutral-800 bg-black" aria-label="当日最高温概率分布">
      <div className="border-b border-neutral-900 px-2 py-1.5">
        <div className="flex items-center justify-between gap-2">
          <div>
            <div className="text-[10px] text-neutral-500">当日最高温预测（DEB）</div>
            <div className="text-xs text-neutral-100">
              {signal ? signal.city_name : '等待信号'} · {longDate(signal?.target_date ?? selectedDate)}
            </div>
          </div>
          {signal?.event_url && (
            <a href={signal.event_url} target="_blank" rel="noreferrer" className="shrink-0 text-[10px] text-cyan-300 hover:text-cyan-100">
              Poly ↗
            </a>
          )}
        </div>
        <div className="mt-1 flex flex-wrap gap-1 text-[9px] text-neutral-500">
          <span className="border border-neutral-800 px-1 py-0.5">μ {fmtTemp(forecastValue, unit)}</span>
          <span className="border border-neutral-800 px-1 py-0.5">±σ {fmtTemp(sigmaValue, unit)}</span>
          <span className="border border-neutral-800 px-1 py-0.5">实测 {fmtTemp(actualHigh, unit)}</span>
          <span className="border border-neutral-800 px-1 py-0.5">{distribution?.normalized ? '高斯归一' : '未归一'}</span>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="flex min-h-[220px] items-center justify-center px-3 text-center text-[10px] leading-relaxed text-neutral-600">
          暂无温度桶分布。手动抓取并生成市场信号后，这里会显示各温度桶的模型概率、盘口价格和可执行 edge。
        </div>
      ) : (
        <div className="grid gap-2 p-2 xl:grid-cols-[minmax(0,1fr)_280px]">
          <div className="h-[260px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartRows} margin={{ top: 8, right: 14, bottom: 34, left: -8 }}>
                <CartesianGrid stroke="#1f1f1f" strokeDasharray="3 3" />
                <XAxis dataKey="label" stroke="#737373" fontSize={9} tickLine={false} axisLine={false} interval={0} angle={-28} textAnchor="end" height={54} />
                <YAxis stroke="#737373" fontSize={10} tickLine={false} axisLine={false} />
                <Tooltip
                  contentStyle={{ background: '#050505', border: '1px solid #262626', color: '#e5e5e5', fontSize: 11 }}
                  formatter={(value: any, name: any) => {
                    if (name === '模型概率') return [`${Number(value).toFixed(1)}%`, name]
                    if (name === '卖一') return [`${Number(value).toFixed(1)}¢`, name]
                    return [`${Number(value).toFixed(1)}%`, name]
                  }}
                />
                <Bar dataKey="probabilityPct" name="模型概率" maxBarSize={36} radius={[2, 2, 0, 0]}>
                  {chartRows.map(row => (
                    <Cell key={row.market_id || row.label} fill={probabilityFill(row.probability, row.is_signal)} stroke={row.is_signal ? '#22d3ee' : 'transparent'} strokeWidth={row.is_signal ? 1.5 : 0} />
                  ))}
                </Bar>
                <Line type="monotone" dataKey="askPct" name="卖一" stroke="#f97316" dot={false} strokeWidth={1.4} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <aside className="border border-neutral-900 bg-neutral-950/30">
            <div className="grid grid-cols-2 gap-1 border-b border-neutral-900 p-2 text-[10px]">
              <MetricCard label="最高概率" value={fmtProb(Math.max(...items.map(item => Number(item.probability ?? 0))))} sub="柱色越深概率越高" />
              <MetricCard label="信号桶" value={signal ? signalBucketLabel(signal, unit) : '--'} sub={signal?.actionable ? 'BUY YES' : '观察'} />
              <MetricCard label="市场价格" value={fmtPrice(signal?.limit_price ?? signal?.market_probability)} sub="卖一/概率" />
              <MetricCard label="Edge" value={fmtSignedPct(signal?.probability_edge ?? signal?.edge)} sub="模型 - 市场" />
            </div>
            <div className="max-h-[260px] overflow-auto">
              <table className="w-full border-collapse text-left text-[10px]">
                <thead className="sticky top-0 bg-black text-neutral-500">
                  <tr className="border-b border-neutral-900">
                    <th className="px-2 py-1 font-normal">桶</th>
                    <th className="px-2 py-1 font-normal">概率</th>
                    <th className="px-2 py-1 font-normal">卖一</th>
                    <th className="px-2 py-1 font-normal">Edge</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map(item => (
                    <tr key={item.market_id || `${item.bucket_low}-${item.bucket_high}`} className={`border-b border-neutral-900/80 ${item.is_signal ? 'bg-cyan-500/10 text-cyan-200' : 'hover:bg-neutral-900/50'}`}>
                      <td className="max-w-[108px] truncate px-2 py-1" title={item.question}>{fmtBucket(item, unit)}</td>
                      <td className="px-2 py-1 tabular-nums text-green-300">{fmtProb(item.probability)}</td>
                      <td className="px-2 py-1 tabular-nums text-amber-300">{fmtPrice(item.ask)}</td>
                      <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtSignedPct(item.probability_edge ?? item.ev)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </aside>
        </div>
      )}

      {(distribution?.notes?.length ?? 0) > 0 && (
        <details className="border-t border-neutral-900 px-2 py-1 text-[9px] text-neutral-600">
          <summary className="cursor-pointer select-none hover:text-neutral-400">分布备注</summary>
          <div className="mt-1 leading-relaxed">{distribution?.notes?.join(' · ')}</div>
        </details>
      )}
    </section>
  )
}

function toneClass(tone: EvidenceCardTone = 'neutral') {
  if (tone === 'green') return 'border-green-500/25 bg-green-500/5 text-green-200'
  if (tone === 'amber') return 'border-amber-500/25 bg-amber-500/5 text-amber-200'
  if (tone === 'red') return 'border-red-500/25 bg-red-500/5 text-red-200'
  if (tone === 'cyan') return 'border-cyan-500/25 bg-cyan-500/5 text-cyan-200'
  return 'border-neutral-800 bg-neutral-950/40 text-neutral-400'
}

function EvidenceCards({ items, empty }: { items: EvidenceCardItem[]; empty: string }) {
  if (items.length === 0) {
    return <div className="flex min-h-0 flex-1 items-center justify-center p-4 text-neutral-600">{empty}</div>
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-2">
      <div className="grid gap-2 lg:grid-cols-2">
        {items.map(item => (
          <article key={item.id} className={`min-w-0 border p-2 ${toneClass(item.tone)}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-[9px] uppercase tracking-wide opacity-70">{item.eyebrow}</div>
                <div className="truncate text-[11px] text-neutral-100" title={item.title}>{item.title}</div>
              </div>
              <div className="shrink-0 text-right">
                <div className="text-sm tabular-nums text-neutral-50">{item.value}</div>
                {item.meta && <div className="max-w-[160px] truncate text-[9px] opacity-70" title={item.meta}>{item.meta}</div>}
              </div>
            </div>

            {(item.badges?.length ?? 0) > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {item.badges?.map((badge, index) => (
                  <span key={`${badge.label}-${index}`} className={`border px-1.5 py-0.5 text-[9px] ${toneClass(badge.tone)}`}>
                    {badge.label}
                  </span>
                ))}
              </div>
            )}

            {(item.details?.length ?? 0) > 0 && (
              <details className="mt-2 border-t border-neutral-800/70 pt-1 text-[10px]">
                <summary className="cursor-pointer select-none text-neutral-500 hover:text-neutral-300">展开字段</summary>
                <div className="mt-2 grid gap-1 md:grid-cols-2">
                  {item.details?.map(detail => {
                    const isLink = /^https?:\/\//.test(detail.value)
                    return (
                      <div key={detail.label} className={detail.wide ? 'min-w-0 md:col-span-2' : 'min-w-0'}>
                        <div className="text-[9px] text-neutral-600">{detail.label}</div>
                        {isLink ? (
                          <a href={detail.value} target="_blank" rel="noreferrer" className="inline-flex max-w-full items-center gap-1 truncate text-cyan-300 hover:text-cyan-100">
                            <span className="truncate">{detail.value}</span>
                            <ExternalLink className="h-3 w-3 shrink-0" />
                          </a>
                        ) : (
                          <div className="truncate text-neutral-300" title={detail.value}>{detail.value}</div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </details>
            )}
          </article>
        ))}
      </div>
    </div>
  )
}

function DecisionMetric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0 border border-neutral-800/80 bg-black/35 px-2 py-1">
      <div className="text-[9px] text-neutral-600">{label}</div>
      <div className="truncate text-xs tabular-nums text-neutral-100" title={value}>{value}</div>
      {sub && <div className="truncate text-[9px] text-neutral-600" title={sub}>{sub}</div>}
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

function SourcePulse({
  label,
  status,
  value,
  meta,
  details = [],
  samples = [],
}: {
  label: string
  status: EvidenceStatus
  value: string
  meta?: string
  details?: Array<{ label: string; value: string }>
  samples?: SourceSample[]
}) {
  const sampleToneClass = (tone: SourceSampleTone = 'neutral') => {
    if (tone === 'green') return 'border-green-500/25 bg-green-500/5 text-green-200'
    if (tone === 'amber') return 'border-amber-500/25 bg-amber-500/5 text-amber-200'
    if (tone === 'red') return 'border-red-500/25 bg-red-500/5 text-red-200'
    if (tone === 'cyan') return 'border-cyan-500/25 bg-cyan-500/5 text-cyan-200'
    return 'border-neutral-800 bg-black/35 text-neutral-300'
  }

  return (
    <div className={`min-w-0 border px-2 py-1.5 ${statusClass(status)}`}>
      <div className="mb-0.5 flex items-center gap-1 text-[9px]">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${status === 'fresh' ? 'bg-green-300' : status === 'stale' ? 'bg-amber-300' : 'bg-neutral-600'}`} />
        <span className="truncate text-neutral-300">{label}</span>
      </div>
      <div className="truncate text-xs tabular-nums text-neutral-100">{value}</div>
      {meta && <div className="truncate text-[9px] text-neutral-500" title={meta}>{meta}</div>}
      {(details.length > 0 || samples.length > 0) && (
        <details className="mt-1 border-t border-neutral-800/70 pt-1 text-[9px] text-neutral-500">
          <summary className="cursor-pointer select-none hover:text-neutral-300">明细</summary>
          <div className="mt-1 grid gap-1">
            {details.map(item => (
              <div key={`${label}-${item.label}`} className="grid grid-cols-[64px_minmax(0,1fr)] gap-1">
                <span className="text-neutral-600">{item.label}</span>
                <span className="truncate text-neutral-400" title={item.value}>{item.value}</span>
              </div>
            ))}
          </div>
          <div className="mt-2 border-t border-neutral-800/70 pt-1">
            <div className="mb-1 text-neutral-600">最近记录</div>
            {samples.length === 0 ? (
              <div className="border border-neutral-800 bg-black/35 px-1.5 py-1 text-neutral-600">暂无最近记录</div>
            ) : (
              <div className="space-y-1">
                {samples.map(sample => (
                  <div key={`${label}-${sample.label}-${sample.value}`} className={`border px-1.5 py-1 ${sampleToneClass(sample.tone)}`}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate text-neutral-400" title={sample.label}>{sample.label}</span>
                      <span className="shrink-0 tabular-nums text-neutral-100">{sample.value}</span>
                    </div>
                    {sample.meta && <div className="mt-0.5 truncate text-[9px] text-neutral-600" title={sample.meta}>{sample.meta}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </details>
      )}
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
