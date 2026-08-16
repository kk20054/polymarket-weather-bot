import { useEffect, useMemo, useState } from 'react'
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
import { ExternalLink, HelpCircle, History, Save, SlidersHorizontal, X } from 'lucide-react'
import { HourlyTemperatureChart, type HourlyChartRow } from './HourlyTemperatureChart'
import { ForecastRevisionDialog } from './ForecastRevisionDialog'
import {
  fetchModelWeightSettings,
  updateModelWeightSettings,
  type ModelWeightMode,
} from '../api'
import type { BucketProbabilitySummary, CityEvidenceDate, CityEvidenceDiffStatsSummary, DashboardEvent, DailyMaxPredictionSummary, DistributionItem, FetchLogRow, HistoricalWeatherPoint, HourlyBiasSourceStats, HourlyBiasStats, HourlyConsensusSummary, HourlySourcePoint, HourlySourceSeries, Layer7QueryState, Layer7ResourceState, MarketBucketSummary, ModelRepriceEvent, ProductionRefreshResult, SignalDecisionRecord, SignalDecisionSummary, WeatherCityPoint, WeatherCitySeries, WeatherForecast, WeatherSignal } from '../types'

interface Props {
  forecasts: WeatherForecast[]
  signals: WeatherSignal[]
  citySeries?: WeatherCitySeries[]
  events?: DashboardEvent[]
  fetchLog?: FetchLogRow[]
  productionRefresh?: ProductionRefreshResult | null
  marketBuckets?: MarketBucketSummary | null
  bucketProbabilities?: BucketProbabilitySummary | null
  signalDecisions?: SignalDecisionSummary | null
  dailyMaxPrediction?: DailyMaxPredictionSummary | null
  hourlySourceSeries?: HourlySourceSeries | null
  hourlyBiasStats?: HourlyBiasStats | null
  forecastPeakMarker?: HourlyConsensusSummary['forecast_peak_marker']
  hourlySourceLoading?: boolean
  alphaEvents?: ModelRepriceEvent[]
  layer7QueryState?: Layer7QueryState
  selectedCity?: string
  onSelectedCity?: (cityKey: string) => void
  selectedDate?: string
  selectedDateEvidence?: CityEvidenceDate
  onSelectedDate?: (date: string) => void
  onRefreshWeather?: () => void
  weatherRefreshing?: boolean
  onBackfillHistory?: () => void
  backfilling?: boolean
  backfillResult?: {
    fetched: number
    errors: Array<{ city: string; error: string }>
  }
  language?: 'zh' | 'en'
}

type EvidenceStatus = 'fresh' | 'stale' | 'missing'

type ModelWeightFamily = 'weathercom_v3' | 'gfs' | 'ecmwf' | 'icon' | 'gem' | 'jma'

const MODEL_WEIGHT_FAMILIES: Array<{ key: ModelWeightFamily; label: string }> = [
  { key: 'weathercom_v3', label: 'V3' },
  { key: 'gfs', label: 'GFS' },
  { key: 'ecmwf', label: 'ECMWF' },
  { key: 'icon', label: 'ICON' },
  { key: 'gem', label: 'GEM' },
  { key: 'jma', label: 'JMA' },
]

const DEFAULT_MODEL_WEIGHTS: Record<ModelWeightFamily, number> = {
  weathercom_v3: 0.484,
  gfs: 0.152,
  ecmwf: 0.104,
  icon: 0.095,
  gem: 0,
  jma: 0,
}

const CHINA_LIVE_CITY_KEYS = new Set([
  'beijing',
  'chengdu',
  'chongqing',
  'guangzhou',
  'hong-kong',
  'qingdao',
  'shanghai',
  'shenzhen',
  'wuhan',
])

const IDLE_LAYER7_RESOURCE: Layer7ResourceState = { status: 'idle' }

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
  historical?: number | null
  china_live?: number | null
  pws?: number | null
  ecmwf?: number | null
  hrrr?: number | null
  humidity?: number | null
  cloud_cover?: number | null
  forecast_cloud_cover?: number | null
  precipitation?: number | null
  precipitation_probability?: number | null
  wind_speed?: number | null
  wind_direction?: number | null
  visibility?: number | null
  pressure?: number | null
  dew_point?: number | null
  shortwave_radiation?: number | null
  condition?: string | null
  gap?: number | null
  source?: string
  horizon?: string
  member_count?: number
  archive?: boolean
  fetched_at?: string | null
  revision_count?: number
  snapshot_count?: number
  distinct_count?: number
  raw_text?: string | null
}

type LayerDistributionItem = DistributionItem & {
  bucket_key?: string | null
  bucket_label?: string | null
  bucket_direction?: string | null
  event_url?: string | null
  yes_token_id?: string | null
  order_min_size?: number | null
  tick_size?: number | null
  bid_depth?: number | null
  ask_depth?: number | null
  gate_status?: string | null
  gate_reasons?: string[]
  blocked_reason_primary?: string | null
  strategy_name?: string | null
  paper_allowed?: boolean
  paper_decision?: string | null
  position_size_usd?: number | null
  live_allowed?: boolean
  quote_timestamp?: string | null
  quote_valid?: boolean
  quote_fresh?: boolean
  ask_available?: boolean
  bid_available?: boolean
  diagnostic_label?: string
  probability_before_observed_floor?: number | null
  observed_floor_excluded?: boolean
}

type EvidenceCardTone = 'green' | 'amber' | 'red' | 'cyan' | 'neutral'

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

function alphaEventTitle(event?: ModelRepriceEvent) {
  if (!event) return ''
  const delta = event.delta_prob === null || event.delta_prob === undefined ? '--' : `${(Number(event.delta_prob) * 100).toFixed(1)}pp`
  return `ECMWF 06Z 更新后模型概率变化 ${delta}，市场未 reprice`
}

type WeatherWorkbenchTab = 'forecast' | 'metar' | 'historical' | 'diff' | 'fetch'

const WORKBENCH_TABS: Array<{ id: WeatherWorkbenchTab; zh: string; en: string }> = [
  { id: 'forecast', zh: '预报', en: 'Forecast' },
  { id: 'metar', zh: 'METAR', en: 'METAR' },
  { id: 'historical', zh: '历史观测', en: 'Historical' },
  { id: 'diff', zh: '偏差统计', en: 'Bias stats' },
  { id: 'fetch', zh: '抓取日志', en: 'Fetch log' },
]

function tr(language: 'zh' | 'en', zh: string, en: string) {
  return language === 'zh' ? zh : en
}

function fmtTemp(value?: number | null, unit = 'F') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${Number(value).toFixed(1)}°${unit}`
}

function fmtBucketTemp(value?: number | null, unit = 'F') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  const numeric = Number(value)
  const rounded = Math.round(numeric)
  const text = Math.abs(numeric - rounded) < 0.05 ? String(rounded) : numeric.toFixed(1)
  return `${text}°${unit}`
}

function fmtBucketAxisTemp(value?: number | null, unit = 'F', tail = false) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  const numeric = Number(value)
  if (tail && Math.abs(numeric - Math.round(numeric)) < 0.05) return `${Math.round(numeric)}°${unit}`
  return `${numeric.toFixed(1)}°${unit}`
}

function fmtBucketAxisLabel(item: DistributionItem, unit: string) {
  if (item.bucket_low <= -900) return `${fmtBucketAxisTemp(item.bucket_high, unit, true)} or below`
  if (item.bucket_high >= 900) return `${fmtBucketAxisTemp(item.bucket_low, unit, true)} or above`
  return `${fmtBucketAxisTemp(item.bucket_low, unit)}–${fmtBucketAxisTemp(item.bucket_high, unit)}`
}

function fmtPct(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${Number(value).toFixed(0)}%`
}

function fmtPrecip(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${Number(value).toFixed(2)}`
}

function fmtPressure(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return Number(value).toFixed(0)
}

function fmtVisibility(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return Number(value).toFixed(1)
}

function fmtWind(speed?: number | null, direction?: number | null) {
  if ((speed === null || speed === undefined || Number.isNaN(Number(speed))) && (direction === null || direction === undefined || Number.isNaN(Number(direction)))) return '--'
  const speedText = speed === null || speed === undefined || Number.isNaN(Number(speed)) ? '--' : Number(speed).toFixed(0)
  if (direction === null || direction === undefined || Number.isNaN(Number(direction))) return `-- ${speedText}`
  const degrees = Number(direction)
  return `${windCompass(degrees)} ${degrees.toFixed(0)}° ${speedText}`
}

function CloudCoverMeter({ value }: { value?: number | null }) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return <span className="text-neutral-600">--</span>
  }
  const percent = Math.max(0, Math.min(100, Number(value)))
  return (
    <span
      className="weather-cloud-meter"
      title={`Cloud cover ${percent.toFixed(0)}%`}
      aria-label={`Cloud cover ${percent.toFixed(0)} percent`}
    >
      <span className="weather-cloud-meter-fill" style={{ width: `${percent}%` }} />
      <span className="weather-cloud-meter-label">{percent.toFixed(0)}%</span>
    </span>
  )
}

function PrecipitationMetric({ amount, probability }: { amount?: number | null; probability?: number | null }) {
  if (
    (amount === null || amount === undefined || Number.isNaN(Number(amount)))
    && (probability === null || probability === undefined || Number.isNaN(Number(probability)))
  ) {
    return <span className="text-neutral-600">--</span>
  }
  const percent = probability === null || probability === undefined || Number.isNaN(Number(probability))
    ? 0
    : Math.max(0, Math.min(100, Number(probability)))
  return (
    <span className="inline-flex min-w-[68px] flex-col gap-0.5" title={`Precipitation ${fmtPrecip(amount)} / ${fmtPct(probability)}`}>
      <span className={`tabular-nums ${percent > 0 ? 'text-sky-300' : 'text-neutral-500'}`}>{fmtPrecip(amount)} / {fmtPct(probability)}</span>
      <span className="h-px w-full bg-neutral-800"><span className="block h-px bg-sky-400" style={{ width: `${percent}%` }} /></span>
    </span>
  )
}

function WindMetric({ speed, direction }: { speed?: number | null; direction?: number | null }) {
  const text = fmtWind(speed, direction)
  if (text === '--') return <span className="text-neutral-600">--</span>
  const cardinal = direction === null || direction === undefined || Number.isNaN(Number(direction))
    ? '--'
    : windCompass(Number(direction))
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap" title={text}>
      <span className="min-w-7 border border-neutral-700 bg-neutral-900/70 px-1 py-0.5 text-center text-[9px] font-medium text-neutral-300">{cardinal}</span>
      <span className="tabular-nums text-neutral-400">{text.replace(`${cardinal} `, '')}</span>
    </span>
  )
}

function windCompass(direction: number) {
  const labels = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
  return labels[Math.round((((direction % 360) + 360) % 360) / 22.5) % 16]
}

function fmtProb(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${(Number(value) * 100).toFixed(1)}%`
}

function fmtPrice(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${(Number(value) * 100).toFixed(1)}¢`
}

function fmtSignedTemp(value?: number | null, unit = 'F') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  const temp = Number(value)
  return `${temp >= 0 ? '+' : ''}${temp.toFixed(1)}°${unit}`
}

function convertTempUnit(value?: number | null, fromUnit = 'F', toUnit = 'F') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null
  const from = fromUnit.toUpperCase()
  const to = toUnit.toUpperCase()
  const numeric = Number(value)
  if (from === to) return numeric
  if (from === 'F' && to === 'C') return (numeric - 32) * 5 / 9
  if (from === 'C' && to === 'F') return numeric * 9 / 5 + 32
  return numeric
}

function convertDeltaUnit(value?: number | null, fromUnit = 'F', toUnit = 'F') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null
  const from = fromUnit.toUpperCase()
  const to = toUnit.toUpperCase()
  const numeric = Number(value)
  if (from === to) return numeric
  if (from === 'F' && to === 'C') return numeric * 5 / 9
  if (from === 'C' && to === 'F') return numeric * 9 / 5
  return numeric
}

function fmtDualTemp(value?: number | null, unit = 'F') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  const native = Number(value)
  const c = unit === 'C' ? native : (native - 32) * 5 / 9
  const f = unit === 'F' ? native : native * 9 / 5 + 32
  return `${c.toFixed(2)}°C / ${f.toFixed(2)}°F`
}

function fmtDualDelta(value?: number | null, unit = 'F') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  const native = Math.abs(Number(value))
  const c = unit === 'C' ? native : native * 5 / 9
  const f = unit === 'F' ? native : native * 9 / 5
  return `${c.toFixed(2)}°C / ${f.toFixed(2)}°F`
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

function rawHourIndex(value?: string | null) {
  if (!value) return null
  const match = String(value).match(/T(\d{2}):\d{2}/) ?? String(value).match(/\b(\d{2}):\d{2}\b/)
  if (!match) return null
  const hour = Number(match[1])
  return Number.isInteger(hour) && hour >= 0 && hour <= 23 ? hour : null
}

function hourLabel(hour: number) {
  return `${String(hour).padStart(2, '0')}:00`
}

function placeholderHourlyRow(targetDate: string, hour: number): HourlyWeatherRow {
  const label = hourLabel(hour)
  return {
    id: `placeholder-${targetDate}-${label}`,
    timestamp: `${targetDate}T${label}:00`,
    target_date: targetDate,
    label,
    forecast: null,
    metar: null,
    historical: null,
    china_live: null,
    pws: null,
    ecmwf: null,
    hrrr: null,
    humidity: null,
    cloud_cover: null,
    forecast_cloud_cover: null,
    precipitation: null,
    precipitation_probability: null,
    wind_speed: null,
    wind_direction: null,
    visibility: null,
    pressure: null,
    dew_point: null,
    shortwave_radiation: null,
    condition: null,
    gap: null,
    source: '--',
    horizon: '--',
    archive: false,
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

function fmtSignedPp(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  const points = Number(value) * 100
  return `${points >= 0 ? '+' : ''}${points.toFixed(1)}pp`
}

function fmtSignedCents(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  const cents = Number(value) * 100
  return `${cents >= 0 ? '+' : ''}${cents.toFixed(1)}¢`
}

function gateReasonLabel(reason: string, language: 'zh' | 'en') {
  const labels: Record<string, [string, string]> = {
    spread_too_wide: ['价差过宽', 'Spread too wide'],
    edge_below_min: ['概率优势不足', 'Insufficient edge'],
    low_price_tail_bucket: ['低价尾桶受限', 'Low-price tail blocked'],
    insufficient_bias_samples: ['校准样本不足', 'Insufficient calibration'],
    stale_book: ['盘口已过期', 'Stale orderbook'],
    book_timestamp_missing: ['盘口时间缺失', 'Orderbook time missing'],
    crossed_orderbook: ['盘口异常', 'Crossed orderbook'],
    invalid_best_ask: ['卖一无效', 'Invalid best ask'],
    invalid_best_bid: ['买一无效', 'Invalid best bid'],
    bucket_not_strict_match: ['市场桶未严格匹配', 'Bucket not strictly matched'],
  }
  const pair = labels[reason]
  return pair ? pair[language === 'zh' ? 0 : 1] : (language === 'zh' ? '未通过交易条件' : 'Trading gate blocked')
}

function localDateStringInTimeZone(timeZone?: string | null, date = new Date()) {
  if (!timeZone) return localDateString(date)
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(date)
    const values = Object.fromEntries(parts.map(part => [part.type, part.value]))
    if (values.year && values.month && values.day) return `${values.year}-${values.month}-${values.day}`
  } catch {
    // Browser-local time is the honest fallback for an invalid station timezone.
  }
  return localDateString(date)
}

function quoteTimestampMs(value?: string | null) {
  const text = String(value ?? '').trim()
  if (!text) return null
  const numeric = Number(text)
  if (Number.isFinite(numeric) && numeric > 0) {
    return numeric >= 1_000_000_000_000 ? numeric : numeric * 1000
  }
  const parsed = Date.parse(text)
  return Number.isFinite(parsed) ? parsed : null
}

function quoteIsFresh(value?: string | null, maxAgeMinutes = 10) {
  const timestamp = quoteTimestampMs(value)
  if (timestamp === null) return false
  const ageMs = Date.now() - timestamp
  return ageMs >= -5_000 && ageMs <= maxAgeMinutes * 60_000
}

function addDateDays(value: string, days: number) {
  const parsed = new Date(`${value || localDateString()}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return localDateString()
  parsed.setDate(parsed.getDate() + days)
  return localDateString(parsed)
}

function elapsedLabel(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return ''
  if (value < 1000) return `${Math.round(Number(value))}ms`
  return `${(Number(value) / 1000).toFixed(1)}s`
}

function visibleElapsedLabel(value?: number | string | null) {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'number') return value > 0 ? elapsedLabel(value) : ''
  const text = String(value).trim()
  if (!text || text === '0' || text === '0ms') return ''
  return text
}

function fetchLogMatches(row: FetchLogRow, patterns: string[]) {
  const text = `${row.source ?? ''} ${row.stage ?? ''} ${row.status ?? ''} ${row.message ?? ''} ${row.details ?? ''} ${row.event_type ?? ''}`.toLowerCase()
  return patterns.some(pattern => text.includes(pattern))
}

function fetchPulseDetail(fetchLog: FetchLogRow[], patterns: string[], sourceTime?: string | null) {
  const rows = fetchLog
    .filter(row => fetchLogMatches(row, patterns))
    .sort((a, b) => String(b.time ?? '').localeCompare(String(a.time ?? '')))
  const latest = rows[0]
  const age = freshnessLabel(sourceTime)
  if (!latest) return age
  const duration = visibleElapsedLabel(latest.duration)
  return [age, duration ? `(${duration})` : ''].filter(Boolean).join(' ')
}

function evidenceStatus(value?: string | null, staleAfterMinutes = 180): EvidenceStatus {
  const minutes = minutesSince(value)
  if (minutes === null) return 'missing'
  return minutes <= staleAfterMinutes ? 'fresh' : 'stale'
}

function statusClass(status: EvidenceStatus) {
  if (status === 'fresh') return 'border-green-500/25 bg-green-500/5 text-green-200'
  if (status === 'stale') return 'border-amber-500/25 bg-amber-500/5 text-amber-200'
  return 'border-red-500/25 bg-red-500/5 text-red-200'
}

function latestBy<T>(items: T[], predicate: (item: T) => boolean, getter: (item: T) => string | undefined | null): T | undefined {
  return [...items]
    .filter(predicate)
    .sort((a, b) => String(getter(b) ?? '').localeCompare(String(getter(a) ?? '')))[0]
}

function asNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

type DebSourceRow = {
  key: string
  label: string
  role: string
  weight: number | null
  mu: number | null
  mae: number | null
  truthBasis: string
  status: string
  calibrationSamples: number
  calibrationProgress: number | null
  exclusionReason: string
  warning?: string
}

type DebDisagreementRow = DebSourceRow & {
  maeDisplay: number | null
  positionPct: number
  intervalStartPct: number
  intervalWidthPct: number
}

type DebDisagreementAnalysis = {
  rows: DebDisagreementRow[]
  axisMin: number | null
  axisMax: number | null
  center: number | null
  centerPct: number | null
  spread: number | null
  activeCount: number
}

type DebHistoryPoint = {
  issuedAt: string
  issuedAtMs: number
  [key: string]: string | number | null
}

type DebHistorySeries = {
  key: string
  label: string
  color: string
}

type DebHistoryAnalysis = {
  points: DebHistoryPoint[]
  series: DebHistorySeries[]
  yMin: number | null
  yMax: number | null
}

type DebHistorySnapshot =
  | NonNullable<DailyMaxPredictionSummary['history']>[number]
  | NonNullable<DailyMaxPredictionSummary['latest']>

const DEB_MODEL_COLORS = ['#38BDF8', '#A78BFA', '#F59E0B', '#FB7185', '#34D399', '#F472B6', '#60A5FA', '#A3E635']

function debModelColor(label: string, index = 0) {
  const colors: Record<string, string> = {
    v3: '#F472B6',
    ecmwf: '#38BDF8',
    gfs: '#F59E0B',
    icon: '#FB7185',
    gem: '#A78BFA',
    jma: '#34D399',
    cma: '#F43F5E',
    hrrr: '#60A5FA',
    nbm: '#A3E635',
  }
  return colors[label.toLowerCase()] ?? DEB_MODEL_COLORS[index % DEB_MODEL_COLORS.length]
}

function sourceShortLabel(source: unknown, family?: unknown) {
  const raw = String(family || source || '').toLowerCase()
  if (raw.includes('weathercom') || raw.includes('weather.com')) return 'v3'
  if (raw.includes('ecmwf')) return 'ecmwf'
  if (raw.includes('gfs')) return 'gfs'
  if (raw.includes('icon')) return 'icon'
  if (raw.includes('gem')) return 'gem'
  if (raw.includes('jma')) return 'jma'
  if (raw.includes('cma') || raw.includes('grapes')) return 'cma'
  if (raw.includes('hrrr')) return 'hrrr'
  if (raw.includes('nbm')) return 'nbm'
  return String(source || family || 'source').replace(/^openmeteo_/, '').replace(/_forecast$/, '')
}

function modelWeightFamilyForLabel(label: string): ModelWeightFamily | null {
  const normalized = sourceShortLabel(label).toLowerCase()
  if (normalized === 'v3') return 'weathercom_v3'
  if (normalized === 'gfs' || normalized === 'ecmwf' || normalized === 'icon' || normalized === 'gem' || normalized === 'jma') {
    return normalized
  }
  return null
}

function buildDebSourceRows(deb: DailyMaxPredictionSummary['latest'], unit: string): DebSourceRow[] {
  const components = (deb?.components ?? []) as Array<Record<string, unknown>>
  const weights = deb?.model_weights ?? {}
  return components
    .map((component, index) => {
      const source = String(component.source ?? component.family ?? `source-${index}`)
      const family = String(component.family ?? sourceShortLabel(source))
      const sourceUnit = component.model_daily_high_c !== undefined || component.peak_temp_c !== undefined ? 'C' : (deb?.unit || unit)
      const rawMu = asNumber(component.model_daily_high_c) ?? asNumber(component.model_daily_high) ?? asNumber(component.peak_temp_c)
      const weight = asNumber(component.weight_after_mae) ?? asNumber(component.weight) ?? asNumber(weights[source])
      const mae = asNumber(component.mae_7d) ?? asNumber(component.rmse_7d)
      return {
        key: `${source}-${index}`,
        label: sourceShortLabel(source, family),
        role: String(component.role ?? family),
        weight,
        mu: rawMu === null ? null : convertTempUnit(rawMu, sourceUnit, unit),
        mae,
        truthBasis: String(component.truth_basis ?? 'unknown'),
        status: String(component.weight_status ?? (Number(weight ?? 0) > 0 ? 'active' : 'unknown')),
        calibrationSamples: Number(component.bias_sample_count ?? 0),
        calibrationProgress: asNumber(component.calibration_progress),
        exclusionReason: String(component.weight_exclusion_reason ?? ''),
        warning: String(component.warning ?? ''),
      }
    })
    .sort((a, b) => Number(b.weight ?? 0) - Number(a.weight ?? 0))
}

function buildDebDisagreementAnalysis(
  sourceRows: DebSourceRow[],
  unit: string,
  modelCenter?: number | null,
): DebDisagreementAnalysis {
  const predictions = sourceRows.filter(
    (row): row is DebSourceRow & { mu: number } => row.mu !== null && Number.isFinite(row.mu),
  )
  if (predictions.length === 0) {
    return {
      rows: [],
      axisMin: null,
      axisMax: null,
      center: null,
      centerPct: null,
      spread: null,
      activeCount: 0,
    }
  }

  const values = predictions.map(row => row.mu)
  const minPrediction = Math.min(...values)
  const maxPrediction = Math.max(...values)
  const spread = maxPrediction - minPrediction
  const padding = Math.max(unit.toUpperCase() === 'F' ? 0.75 : 0.4, spread * 0.15)
  const axisMin = minPrediction - padding
  const axisMax = maxPrediction + padding
  const axisRange = Math.max(axisMax - axisMin, 0.1)
  const weightedRows = predictions.filter(row => Number(row.weight ?? 0) > 0)
  const totalWeight = weightedRows.reduce((total, row) => total + Number(row.weight ?? 0), 0)
  const weightedCenter = totalWeight > 0
    ? weightedRows.reduce((total, row) => total + row.mu * Number(row.weight ?? 0), 0) / totalWeight
    : mean(values)
  const center = modelCenter !== null && modelCenter !== undefined && Number.isFinite(modelCenter)
    ? Number(modelCenter)
    : weightedCenter
  const toPct = (value: number) => Math.max(0, Math.min(100, ((value - axisMin) / axisRange) * 100))

  return {
    rows: predictions
      .map(row => {
        const maeDisplay = row.mae === null ? null : Math.abs(Number(convertDeltaUnit(row.mae, 'C', unit)))
        const intervalStart = maeDisplay === null ? row.mu : row.mu - maeDisplay
        const intervalEnd = maeDisplay === null ? row.mu : row.mu + maeDisplay
        const intervalStartPct = toPct(intervalStart)
        const intervalEndPct = toPct(intervalEnd)
        return {
          ...row,
          maeDisplay,
          positionPct: toPct(row.mu),
          intervalStartPct,
          intervalWidthPct: Math.max(0, intervalEndPct - intervalStartPct),
        }
      })
      .sort((a, b) => a.mu - b.mu),
    axisMin,
    axisMax,
    center,
    centerPct: center === null ? null : toPct(center),
    spread,
    activeCount: weightedRows.length,
  }
}

function buildDebHistoryAnalysis(
  prediction: DailyMaxPredictionSummary | null | undefined,
  unit: string,
): DebHistoryAnalysis {
  const candidates = [
    ...(prediction?.history ?? []),
    ...(prediction?.latest ? [prediction.latest] : []),
  ]
  const snapshotsByKey = new Map<string, DebHistorySnapshot>()
  candidates.forEach(snapshot => {
    if (!snapshot?.issued_at || Number.isNaN(Date.parse(String(snapshot.issued_at)))) return
    snapshotsByKey.set(String(snapshot.issued_at), snapshot)
  })
  const snapshots = [...snapshotsByKey.values()]
    .sort((a, b) => String(a.issued_at).localeCompare(String(b.issued_at)))
  const labels = new Set<string>()

  for (const snapshot of snapshots) {
    for (const component of snapshot.components ?? []) {
      const source = String(component.source ?? component.family ?? '')
      const family = String(component.family ?? source)
      const rawMu = asNumber(component.model_daily_high_c) ?? asNumber(component.model_daily_high) ?? asNumber(component.peak_temp_c)
      if (rawMu !== null) labels.add(sourceShortLabel(source, family))
    }
  }

  const provisionalSeries = [...labels]
    .sort((a, b) => a.localeCompare(b))
    .map((label, index) => ({
      key: `model_${index}`,
      label,
      color: debModelColor(label, index),
    }))
  const seriesByLabel = new Map(provisionalSeries.map(series => [series.label, series]))
  const points = snapshots.map(snapshot => {
    const issuedAt = String(snapshot.issued_at)
    const point: DebHistoryPoint = { issuedAt, issuedAtMs: Date.parse(issuedAt) }
    for (const component of snapshot.components ?? []) {
      const source = String(component.source ?? component.family ?? '')
      const family = String(component.family ?? source)
      const label = sourceShortLabel(source, family)
      const series = seriesByLabel.get(label)
      if (!series) continue
      const sourceUnit = component.model_daily_high_c !== undefined || component.peak_temp_c !== undefined
        ? 'C'
        : (snapshot.unit || unit)
      const rawMu = asNumber(component.model_daily_high_c) ?? asNumber(component.model_daily_high) ?? asNumber(component.peak_temp_c)
      if (rawMu !== null) point[series.key] = convertTempUnit(rawMu, sourceUnit, unit)
    }
    return point
  })
  const series = provisionalSeries.filter(item =>
    points.filter(point => typeof point[item.key] === 'number' && Number.isFinite(point[item.key])).length >= 2,
  )
  const usablePoints = points.filter(point =>
    series.some(item => typeof point[item.key] === 'number' && Number.isFinite(point[item.key])),
  )
  const values = usablePoints.flatMap(point =>
    series
      .map(item => point[item.key])
      .filter((value): value is number => typeof value === 'number' && Number.isFinite(value)),
  )
  if (usablePoints.length < 2 || series.length === 0 || values.length === 0) {
    return { points: [], series: [], yMin: null, yMax: null }
  }
  const minValue = Math.min(...values)
  const maxValue = Math.max(...values)
  const padding = Math.max(unit.toUpperCase() === 'F' ? 1 : 0.5, (maxValue - minValue) * 0.12)
  return {
    points: usablePoints,
    series,
    yMin: minValue - padding,
    yMax: maxValue + padding,
  }
}

function formatDebHistoryTime(value: unknown, language: 'zh' | 'en') {
  const date = new Date(typeof value === 'number' ? value : String(value ?? ''))
  if (Number.isNaN(date.getTime())) return String(value ?? '--')
  return new Intl.DateTimeFormat(language === 'zh' ? 'zh-CN' : 'en-US', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function normalizePeakHour(value: unknown) {
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'number' && Number.isFinite(value)) {
    const hour = Math.max(0, Math.min(23, Math.round(value)))
    return hourLabel(hour)
  }
  const text = String(value)
  const match = text.match(/\b(?:H)?([01]?\d|2[0-3])(?::\d{2})?\b/i)
  if (!match) return null
  return hourLabel(Number(match[1]))
}

function dailyMaxPeakHour(prediction?: DailyMaxPredictionSummary | null, fallback?: string | null) {
  const latest = prediction?.latest as (Record<string, unknown> & { components?: Array<Record<string, unknown>> }) | null | undefined
  const direct = normalizePeakHour(latest?.peak_hour)
    ?? normalizePeakHour(latest?.peakHour)
    ?? normalizePeakHour(latest?.peak_local_hour)
    ?? normalizePeakHour(latest?.peak_local_time)
  if (direct) return direct
  for (const component of latest?.components ?? []) {
    const fromComponent = normalizePeakHour(component.peak_hour)
      ?? normalizePeakHour(component.peakHour)
      ?? normalizePeakHour(component.peak_local_hour)
      ?? normalizePeakHour(component.peak_local_time)
    if (fromComponent) return fromComponent
  }
  return normalizePeakHour(fallback)
}

type SourceStats = {
  n: number
  avgDelta: number | null
  pearson: number | null
  lastHour: string | null
}

function sourceStats(rows: Array<Record<string, unknown>>, observedKey: string): SourceStats {
  const pairs = rows
    .map(row => ({
      observed: asNumber(row[observedKey]),
      forecast: asNumber(row.forecast_value),
      label: typeof row.label === 'string' ? row.label : null,
    }))
    .filter((row): row is { observed: number; forecast: number; label: string | null } => row.observed !== null && row.forecast !== null)
  if (pairs.length === 0) return { n: 0, avgDelta: null, pearson: null, lastHour: null }
  const deltas = pairs.map(row => row.observed - row.forecast)
  const latest = pairs[pairs.length - 1]?.label
  const hour = latest?.match(/^(\d{2})/)?.[1] ?? null
  return {
    n: pairs.length,
    avgDelta: mean(deltas),
    pearson: pearsonR(pairs.map(row => row.forecast), pairs.map(row => row.observed)),
    lastHour: hour ? `@H${hour}` : null,
  }
}

function canonicalSourceStats(stats?: HourlyBiasSourceStats | null): SourceStats | null {
  if (!stats) return null
  const cutoff = typeof stats.cutoff_hour === 'string' ? stats.cutoff_hour : null
  const hour = cutoff?.match(/^(\d{2})/)?.[1] ?? null
  return {
    n: Number(stats.count || 0),
    avgDelta: asNumber(stats.avg_delta),
    pearson: asNumber(stats.pearson_r),
    lastHour: hour ? `@H${hour}` : null,
  }
}

function statDeltaPill(label: string, stats: SourceStats, unit: string) {
  if (stats.n === 0) return null
  return `${label} ${fmtSignedTemp(stats.avgDelta, unit)} n=${stats.n}${stats.lastHour ? ` ${stats.lastHour}` : ''}`
}

function statAccuracyPill(label: string, stats: SourceStats) {
  if (stats.n === 0) return null
  return `${label} ${fmtPearson(stats.pearson)} n=${stats.n}`
}

function overlapPill(rows: Array<Record<string, unknown>>) {
  const historicalRows = rows.filter(row => asNumber(row.historical_value) !== null)
  const paired = rows.filter(row => asNumber(row.historical_value) !== null && asNumber(row.metar_value) !== null)
  if (historicalRows.length === 0 || paired.length === 0) return null
  const latest = paired[paired.length - 1]
  const upTo = typeof latest.label === 'string' ? latest.label.replace(':00', ':00') : '--'
  return `${Math.round((paired.length / historicalRows.length) * 100)}% (${paired.length}/${historicalRows.length} pts, up to ${upTo})`
}

function canonicalOverlapPill(stats?: HourlyBiasStats | null) {
  const overlap = stats?.historical_metar_overlap
  const ratio = asNumber(overlap?.ratio)
  if (!overlap || ratio === null || overlap.possible <= 0) return null
  return `${Math.round(ratio * 100)}% (${overlap.count}/${overlap.possible} pts, up to ${overlap.cutoff_time || '--'})`
}

function layerDecisionRank(decision: SignalDecisionRecord) {
  const paper = decision.paper_allowed ? 1000 : 0
  const edge = asNumber(decision.edge) ?? asNumber(decision.model_bucket_probs?.edge) ?? -999
  return paper + edge
}

function latestDecisionBatch(decisions: SignalDecisionRecord[]) {
  const latestIssuedAt = decisions.reduce((latest, decision) => {
    const issuedAt = String(decision.issued_at ?? '')
    return issuedAt > latest ? issuedAt : latest
  }, '')
  if (!latestIssuedAt) return decisions
  return decisions.filter(decision => String(decision.issued_at ?? '') === latestIssuedAt)
}

function bestLayerDecision(summary?: SignalDecisionSummary | null) {
  return latestDecisionBatch(summary?.decisions ?? []).sort((a, b) => layerDecisionRank(b) - layerDecisionRank(a))[0]
}

function buildAuthoritativeDistributionItems(
  probabilities?: BucketProbabilitySummary | null,
  buckets?: MarketBucketSummary | null,
  decisions?: SignalDecisionSummary | null,
): LayerDistributionItem[] {
  if (!probabilities?.ok || probabilities.items.length === 0) return []
  const bucketByMarket = new Map((buckets?.latest ?? []).map(bucket => [String(bucket.market_id), bucket]))
  const bucketByKey = new Map((buckets?.latest ?? []).map(bucket => [String(bucket.bucket_key ?? ''), bucket]))
  const predictionId = Number(probabilities.prediction?.id ?? 0)
  const decisionByMarket = new Map<string, SignalDecisionRecord>()
  const decisionByBucket = new Map<string, SignalDecisionRecord>()
  for (const decision of decisions?.decisions ?? []) {
    const linkedPredictionId = Number(decision.evidence_links?.daily_max_prediction_id ?? 0)
    if (predictionId > 0 && linkedPredictionId !== predictionId) continue
    const marketKey = String(decision.market_id ?? '')
    const bucketKey = String(decision.model_bucket_probs?.bucket_key ?? decision.bucket_key ?? '')
    const current = decisionByMarket.get(marketKey) ?? decisionByBucket.get(bucketKey)
    if (current && layerDecisionRank(current) >= layerDecisionRank(decision)) continue
    if (marketKey) decisionByMarket.set(marketKey, decision)
    if (bucketKey) decisionByBucket.set(bucketKey, decision)
  }

  return probabilities.items.map(item => {
    const marketKey = String(item.market_id ?? '')
    const bucketKey = String(item.bucket_key ?? '')
    const bucket = bucketByMarket.get(marketKey) ?? bucketByKey.get(bucketKey)
    const decision = decisionByMarket.get(marketKey) ?? decisionByBucket.get(bucketKey)
    const probability = asNumber(item.probability) ?? 0
    const askValue = asNumber(item.best_ask) ?? asNumber(bucket?.best_ask) ?? asNumber(item.price)
    const bidValue = asNumber(item.best_bid) ?? asNumber(bucket?.best_bid)
    const ask = askValue ?? 0
    const bid = bidValue ?? 0
    const askAvailable = askValue !== null && ask >= 0 && ask <= 1
    const bidAvailable = bidValue !== null && bid >= 0 && bid <= 1
    const quoteValid = askAvailable && bidAvailable
      && ask >= 0 && ask <= 1
      && bid >= 0 && bid <= ask
    const quoteFresh = quoteIsFresh(bucket?.quote_timestamp)
    const edge = askAvailable ? probability - ask : 0
    return {
      market_id: marketKey,
      bucket_key: item.bucket_key,
      question: bucket?.question ?? item.bucket_label ?? '',
      bucket_low: asNumber(item.bucket_low) ?? -999,
      bucket_high: asNumber(item.bucket_high) ?? 999,
      probability_raw: asNumber(item.probability_raw) ?? probability,
      probability,
      probability_before_observed_floor: asNumber(item.probability_before_observed_floor),
      observed_floor_excluded: Boolean(item.observed_floor_excluded),
      ask,
      bid,
      spread: Math.max(0, ask - bid),
      probability_edge: edge,
      ev: edge,
      is_signal: Boolean(quoteFresh && decision?.paper_allowed && decision?.paper_decision === 'buy'),
      bucket_label: item.bucket_label,
      bucket_direction: item.bucket_direction,
      event_url: bucket?.event_url ?? null,
      yes_token_id: item.yes_token_id ?? bucket?.yes_token_id,
      order_min_size: bucket?.order_min_size,
      tick_size: bucket?.tick_size,
      bid_depth: bucket?.bid_depth,
      ask_depth: bucket?.ask_depth,
      gate_status: decision?.gate_status,
      gate_reasons: decision?.gate_reasons ?? decision?.reasons ?? [],
      blocked_reason_primary: decision?.blocked_reason_primary,
      strategy_name: decision?.strategy_name,
      paper_allowed: decision?.paper_allowed,
      paper_decision: decision?.paper_decision,
      position_size_usd: decision?.position_size_usd,
      live_allowed: decision?.live_allowed,
      quote_timestamp: bucket?.quote_timestamp,
      quote_valid: quoteValid,
      quote_fresh: quoteFresh,
      ask_available: askAvailable,
      bid_available: bidAvailable,
    }
  })
}

function erfApprox(value: number) {
  const sign = value < 0 ? -1 : 1
  const x = Math.abs(value)
  const a1 = 0.254829592
  const a2 = -0.284496736
  const a3 = 1.421413741
  const a4 = -1.453152027
  const a5 = 1.061405429
  const p = 0.3275911
  const t = 1 / (1 + p * x)
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x)
  return sign * y
}

function normalCdf(value: number) {
  return 0.5 * (1 + erfApprox(value / Math.sqrt(2)))
}

function buildGaussianFallbackItems(mu?: number | null, sigma?: number | null, unit = 'F'): LayerDistributionItem[] {
  const center = asNumber(mu)
  const rawSigma = asNumber(sigma)
  if (center === null || rawSigma === null) return []
  const sigmaFloor = unit === 'C' ? 0.5 : 0.9
  const sigmaValue = Math.max(Math.abs(rawSigma), sigmaFloor)
  // PolyWX's DEB chart is a fixed diagnostic density grid, not the market's
  // variable integer buckets. Keep the two views separate and comparable.
  const bucketSize = unit === 'C' ? 0.5 : 1
  const bucketCount = 18
  const roundedCenter = Math.round(center / bucketSize) * bucketSize
  const startCenter = roundedCenter - (bucketCount / 2 - 1) * bucketSize
  const raw = Array.from({ length: bucketCount }, (_, index) => {
    const diagnosticCenter = startCenter + index * bucketSize
    const low = diagnosticCenter - bucketSize / 2
    const high = diagnosticCenter + bucketSize / 2
    const probability = normalCdf((high - center) / sigmaValue) - normalCdf((low - center) / sigmaValue)
    return { low, high, diagnosticCenter, probability }
  })
  if (raw.every(item => item.probability <= 0)) return []
  return raw.map((item, index) => ({
    market_id: `fallback-gaussian-${index}`,
    question: '暂无匹配市场桶，仅展示 DEB 高斯模型分布',
    bucket_low: item.low,
    bucket_high: item.high,
    probability_raw: item.probability,
    probability: item.probability,
    ask: 0,
    bid: 0,
    spread: 0,
    probability_edge: 0,
    ev: 0,
    is_signal: false,
    bucket_label: `${fmtBucketTemp(item.low, unit)}–${fmtBucketTemp(item.high, unit)}`,
    diagnostic_label: fmtBucketAxisTemp(item.diagnosticCenter, unit),
    bucket_direction: 'range',
    gate_status: 'model_distribution_only',
    gate_reasons: ['missing_market_bucket_match'],
    paper_allowed: false,
    live_allowed: false,
  }))
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

function forecastTableRowsFromSeries(points: HourlySourcePoint[] | undefined, fallback: HourlyWeatherRow[]): HourlyWeatherRow[] {
  if (!points?.length) return fallback
  return points.map((point, index) => ({
    id: `forecast-${point.timestamp}-${index}`,
    timestamp: point.timestamp,
    target_date: point.target_date,
    label: String(point.local_hour || point.local_time || point.timestamp.slice(11, 16)),
    forecast: asNumber(point.temperature ?? point.ensemble_mean ?? point.best),
    humidity: asNumber(point.humidity),
    cloud_cover: null,
    forecast_cloud_cover: asNumber(point.cloud_cover),
    precipitation: asNumber(point.precipitation),
    precipitation_probability: asNumber(point.precipitation_probability),
    wind_speed: asNumber(point.wind_speed),
    wind_direction: asNumber(point.wind_direction),
    pressure: asNumber(point.pressure),
    dew_point: asNumber(point.dew_point),
    condition: point.condition ?? null,
    source: point.source || 'weathercom_v3_forecast',
    horizon: point.horizon || '--',
    member_count: point.member_count,
    archive: Boolean(point.archive),
    fetched_at: point.retrieved_at ?? null,
    revision_count: point.revision_count,
    snapshot_count: point.snapshot_count,
    distinct_count: point.distinct_count,
  }))
}

function observationTableRowsFromSeries(
  points: HourlySourcePoint[] | undefined,
  kind: 'metar' | 'historical',
): HourlyWeatherRow[] {
  return (points ?? []).map((point, index) => {
    const temperature = asNumber(point.temperature)
    return {
      id: `${kind}-${point.timestamp}-${index}`,
      timestamp: point.timestamp,
      target_date: point.target_date,
      label: String(point.local_time || point.local_hour || point.timestamp.slice(11, 16)),
      metar: kind === 'metar' ? temperature : null,
      historical: kind === 'historical' ? temperature : null,
      humidity: asNumber(point.humidity),
      cloud_cover: asNumber(point.cloud_cover),
      wind_speed: asNumber(point.wind_speed),
      wind_direction: asNumber(point.wind_direction),
      visibility: asNumber(point.visibility),
      pressure: asNumber(point.pressure),
      dew_point: asNumber(point.dew_point),
      condition: point.condition ?? null,
      source: point.source || kind,
      fetched_at: point.retrieved_at ?? null,
      raw_text: point.raw_text ?? null,
    }
  })
}

function buildHourlyRows(series?: WeatherCitySeries, selectedDate?: string): HourlyWeatherRow[] {
  const rows = new Map<string, HourlyWeatherRow>()
  const sourcePoints = (series?.hourly_points?.length ? series.hourly_points : (series?.forecast_points ?? series?.points ?? []))
  for (const point of sourcePoints) {
    if (selectedDate && point.target_date !== selectedDate) continue
    if (!point.timestamp) continue
    const extendedPoint = point as WeatherCityPoint & {
      historical?: number | null
      historical_temp?: number | null
      pws?: number | null
      pws_temp?: number | null
    }
    const hour = rawHourIndex(point.timestamp)
    if (hour === null) continue
    const forecast = point.best ?? point.ensemble_mean ?? null
    const metar = point.metar ?? null
    const gap = forecast !== null && forecast !== undefined && metar !== null && metar !== undefined
      ? Number(metar) - Number(forecast)
      : null
    const label = hourLabel(hour)
    const key = `${point.target_date}:${label}`
    rows.set(key, {
      id: key,
      timestamp: point.timestamp,
      target_date: point.target_date,
      label,
      forecast,
      metar,
      historical: extendedPoint.historical ?? extendedPoint.historical_temp ?? null,
      china_live: point.china_live ?? null,
      pws: extendedPoint.pws ?? extendedPoint.pws_temp ?? null,
      ecmwf: point.ecmwf ?? null,
      hrrr: point.hrrr ?? null,
      humidity: point.humidity ?? null,
      cloud_cover: point.cloud_cover ?? null,
      forecast_cloud_cover: point.forecast_cloud_cover ?? null,
      precipitation: point.precipitation ?? null,
      precipitation_probability: point.precipitation_probability ?? null,
      wind_speed: point.wind_speed ?? null,
      wind_direction: point.wind_direction ?? null,
      visibility: point.visibility ?? null,
      pressure: point.pressure ?? null,
      dew_point: point.dew_point ?? null,
      shortwave_radiation: point.shortwave_radiation ?? null,
      condition: point.condition ?? null,
      gap,
      source: point.source || '--',
      horizon: point.horizon || '--',
      member_count: point.member_count,
      archive: point.archive,
    })
  }
  const targetDate = selectedDate || [...rows.values()][0]?.target_date || localDateString()
  return Array.from({ length: 24 }, (_, hour) => {
    const label = hourLabel(hour)
    return rows.get(`${targetDate}:${label}`) ?? placeholderHourlyRow(targetDate, hour)
  })
}

export function WeatherPanel({
  forecasts,
  signals,
  citySeries = [],
  events = [],
  fetchLog = [],
  productionRefresh = null,
  marketBuckets,
  bucketProbabilities,
  signalDecisions,
  dailyMaxPrediction,
  hourlySourceSeries,
  hourlyBiasStats,
  forecastPeakMarker,
  hourlySourceLoading = false,
  alphaEvents = [],
  layer7QueryState,
  selectedCity,
  onSelectedCity,
  selectedDate: controlledSelectedDate,
  selectedDateEvidence,
  onSelectedDate,
  backfillResult,
  language = 'zh',
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

  const series = citySeries.find(row => row.city_key === selected) ?? (!selected ? citySeries[0] : undefined)
  const forecastFallback = forecasts.find(row => row.city_key === selected) ?? (!selected ? forecasts[0] : undefined)
  const cityKey = series?.city_key ?? forecastFallback?.city_key ?? selected
  const unit = series?.unit ?? 'F'
  const todayDate = localDateStringInTimeZone(series?.settlement_timezone)

  const citySignals = useMemo(() => signals.filter(signal => signal.city_key === cityKey), [signals, cityKey])
  const actionableSignals = citySignals.filter(signal => signal.actionable)
  const selectedDateSignals = citySignals.filter(signal => !selectedDate || signal.target_date === selectedDate)
  const bestSignal = [...selectedDateSignals]
    .sort((a, b) => {
      const actionDelta = Number(Boolean(b.actionable)) - Number(Boolean(a.actionable))
      if (actionDelta !== 0) return actionDelta
      return Math.abs((b.probability_edge ?? b.edge ?? 0)) - Math.abs((a.probability_edge ?? a.edge ?? 0))
    })[0]
  const distributionSignal = useMemo(() => {
    const dated = citySignals.filter(signal => !selectedDate || signal.target_date === selectedDate)
    const withDistribution = dated.filter(signal => (signal.distribution?.items?.length ?? 0) > 0)
    return [...withDistribution].sort((a, b) => {
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
  const layerDecision = useMemo(() => bestLayerDecision(signalDecisions), [signalDecisions])
  const layerDistributionItems = useMemo(
    () => buildAuthoritativeDistributionItems(bucketProbabilities, marketBuckets, signalDecisions),
    [bucketProbabilities, marketBuckets, signalDecisions],
  )
  const probabilityItems = layer7QueryState
    ? layerDistributionItems
    : layerDistributionItems.length > 0
      ? layerDistributionItems
      : distributionChartItems
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
  const latestForecastSource = latestBy<HourlySourcePoint>(
    hourlySourceSeries?.forecast ?? [],
    point => asNumber(point.temperature ?? point.ensemble_mean ?? point.best) !== null,
    point => point.retrieved_at ?? point.timestamp,
  )
  const latestMetarSource = latestBy<HourlySourcePoint>(
    hourlySourceSeries?.metar ?? [],
    point => point.temperature !== null && point.temperature !== undefined,
    point => point.timestamp,
  )
  const latestHistoricalSource = latestBy<HourlySourcePoint>(
    hourlySourceSeries?.historical ?? [],
    point => point.temperature !== null && point.temperature !== undefined,
    point => point.retrieved_at ?? point.timestamp,
  )
  const latestChinaLiveSource = latestBy<HourlySourcePoint>(
    hourlySourceSeries?.china_live ?? [],
    point => point.temperature !== null && point.temperature !== undefined,
    point => point.retrieved_at ?? point.timestamp,
  )
  const chartData = useMemo(() => buildChartData(series), [series])
  const hourlyRows = useMemo(() => buildHourlyRows(series, selectedDate), [series, selectedDate])
  const forecastTableRows = useMemo(
    () => forecastTableRowsFromSeries(hourlySourceSeries?.forecast, hourlyRows),
    [hourlySourceSeries, hourlyRows],
  )
  const metarTableRows = useMemo(
    () => observationTableRowsFromSeries(hourlySourceSeries?.metar, 'metar'),
    [hourlySourceSeries],
  )
  const historicalTableRows = useMemo(
    () => observationTableRowsFromSeries(hourlySourceSeries?.historical, 'historical'),
    [hourlySourceSeries],
  )
  const availableDates = useMemo(() => {
    return [...new Set(chartData.map(row => String(row.date)).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b))
  }, [chartData])
  const forecastSourceTime = latestForecastSource?.retrieved_at ?? latestForecast?.timestamp
  const metarSourceTime = latestMetarSource?.retrieved_at ?? latestMetarSource?.timestamp ?? latestMetar?.timestamp
  const historySourceTime = latestHistoricalSource?.retrieved_at ?? latestHistoricalSource?.timestamp ?? latestHistory?.fetched_at
  const chinaLiveSourceTime = latestChinaLiveSource?.retrieved_at ?? latestChinaLiveSource?.timestamp
  const forecastStatus = evidenceStatus(forecastSourceTime, 60)
  const metarStatus = evidenceStatus(metarSourceTime, 45)
  const historyStatus = evidenceStatus(historySourceTime, 60)
  const chinaLiveStatus = evidenceStatus(chinaLiveSourceTime, 30)
  const forecastPulseDetail = fetchPulseDetail(fetchLog, ['forecast', 'openmeteo', 'daily_max', 'predictor'], forecastSourceTime)
  const metarPulseDetail = fetchPulseDetail(fetchLog, ['metar', 'asos'], metarSourceTime)
  const historyPulseDetail = fetchPulseDetail(fetchLog, ['historical', 'history', 'truth', 'actual'], historySourceTime)
  const chinaLivePulseDetail = fetchPulseDetail(fetchLog, ['china_live', 'china weather', 'weather.com.cn'], chinaLiveSourceTime)
  const supportsChinaLive = CHINA_LIVE_CITY_KEYS.has(cityKey)
  const truthTier = latestHistory?.calibration_tier === 'live_truth'
    ? '结算数据'
    : latestHistory?.calibration_tier === 'research_truth'
      ? '校准数据'
      : 'truth 待补'

  useEffect(() => {
    const datesNotAfterToday = availableDates.filter(date => date <= todayDate)
    const fallbackDate = availableDates.includes(todayDate)
      ? todayDate
      : datesNotAfterToday[datesNotAfterToday.length - 1] ?? todayDate
    if (!selectedDate && fallbackDate) {
      setSelectedDate(fallbackDate)
    }
  }, [availableDates, selectedDate, todayDate])

  const selectedDateRow = chartData.find(row => row.date === selectedDate)
    ?? (selectedDate ? { date: selectedDate, label: shortDate(selectedDate) } : chartData[chartData.length - 1])
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
        {tr(language, '该日期暂无城市证据。', 'No city evidence for this date.')}
      </div>
    )
  }

  return (
    <div className="weather-panel min-h-full min-w-0 space-y-2 overflow-x-hidden bg-transparent p-3 text-[11px] text-[#CBD2DC]">
      <div className="flex flex-wrap items-center gap-2">
        <EvidenceBadge label={tr(language, '预报', 'Forecast')} status={forecastStatus} detail={forecastPulseDetail} />
        <EvidenceBadge label="METAR" status={metarStatus} detail={metarPulseDetail} />
        <EvidenceBadge label={tr(language, '历史观测', 'Historical')} status={historyStatus} detail={historyPulseDetail} />
        {supportsChinaLive && (
          <EvidenceBadge label={tr(language, '中国天气实况', 'China live')} status={chinaLiveStatus} detail={chinaLivePulseDetail} />
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex shrink-0 items-center border border-neutral-800">
          <button
            type="button"
            onClick={() => setSelectedDate(addDateDays(selectedDate || todayDate, -1))}
            className="px-2 py-1 text-[10px] text-neutral-400 hover:bg-neutral-900 disabled:opacity-30"
          >
            {tr(language, '前一天', 'Previous')}
          </button>
          <input
            type="date"
            value={selectedDate || todayDate}
            onChange={event => setSelectedDate(event.target.value)}
            className="w-[136px] border-x border-neutral-800 bg-black px-2 py-1 text-center text-[10px] tabular-nums text-neutral-200 outline-none"
            aria-label={tr(language, '选择日期', 'Select date')}
          />
          <button
            type="button"
            onClick={() => setSelectedDate(addDateDays(selectedDate || todayDate, 1))}
            className="px-2 py-1 text-[10px] text-neutral-400 hover:bg-neutral-900 disabled:opacity-30"
          >
            {tr(language, '后一天', 'Next')}
          </button>
          <button
            type="button"
            onClick={() => setSelectedDate(todayDate)}
            className="border-l border-neutral-800 px-2 py-1 text-[10px] text-neutral-400 hover:bg-neutral-900"
          >
            {tr(language, '今天', 'Today')}
          </button>
        </div>
        {bestSignal?.event_url && (
          <a href={bestSignal.event_url} target="_blank" rel="noreferrer" className="inline-flex shrink-0 items-center gap-1 border border-cyan-500/30 px-2 py-1 text-[10px] text-cyan-300 hover:bg-cyan-500/10">
            Polymarket <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>

      <section className="border border-[#2C3445] bg-[#1B212C]">
        <div className="border-b border-[#2C3445]">
          <div className="flex gap-1 overflow-x-auto px-2 py-2">
            {WORKBENCH_TABS.map(tab => (
              <WorkbenchTabButton
                key={tab.id}
                tab={{ id: tab.id, label: language === 'zh' ? tab.zh : tab.en }}
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
              sourceSeries={hourlySourceSeries}
              biasStats={hourlyBiasStats}
              unit={unit}
              cityName={series?.city_name ?? forecastFallback?.city_name ?? cityKey}
              selectedDate={selectedDate}
              dailyMaxPrediction={dailyMaxPrediction}
              forecastPeakMarker={forecastPeakMarker}
              loading={hourlySourceLoading}
              language={language}
            />
            <TemperatureDistributionPanel
              signal={distributionSignal}
              decision={layerDecision}
              items={probabilityItems}
              unit={unit}
              selectedDate={selectedDate}
              actualHigh={selectedDateRow?.actual_high}
              observedSampleCount={metarTableRows.length}
              cityName={series?.city_name ?? forecastFallback?.city_name ?? cityKey}
              dailyMaxPrediction={dailyMaxPrediction}
              bucketProbabilities={bucketProbabilities}
              alphaEvents={alphaEvents}
              queryState={layer7QueryState}
              language={language}
            />
            <ForecastDataTable rows={forecastTableRows} unit={unit} selectedDate={selectedDate} city={cityKey} language={language} />
          </div>
        )}

        {activeWorkbenchTab === 'metar' && (
          <div className="space-y-2 p-2">
            <MetarObservationTable rows={metarTableRows} unit={unit} selectedDate={selectedDate} loading={hourlySourceLoading} language={language} />
            <details className="border border-[#2C3445] bg-[#161A22]">
              <summary className="cursor-pointer select-none px-2 py-2 text-xs text-[#CBD2DC] hover:bg-[#222A37]">
                {tr(language, 'METAR 快照', 'METAR snapshots')} · {metarCards.length}
              </summary>
              <div className="border-t border-neutral-800">
                <EvidenceCards empty={tr(language, '该日期暂无 METAR 快照', 'No METAR snapshots for this date')} items={metarCards} />
              </div>
            </details>
          </div>
        )}

        {activeWorkbenchTab === 'historical' && (
          <div className="space-y-2 p-2">
            <HistoricalHourlyObservationTable rows={historicalTableRows} unit={unit} selectedDate={selectedDate} loading={hourlySourceLoading} language={language} />
            <details className="border border-[#2C3445] bg-[#161A22]">
              <summary className="cursor-pointer select-none px-2 py-2 text-xs text-[#CBD2DC] hover:bg-[#222A37]">
                {tr(language, '每日结算高温', 'Daily settlement high')} · {historyRows.length}
              </summary>
              <HistoricalObservationTable rows={historyRows} unit={unit} stationId={series?.station_id} />
            </details>
            <details className="border border-[#2C3445] bg-[#161A22]">
              <summary className="cursor-pointer select-none px-2 py-2 text-xs text-[#CBD2DC] hover:bg-[#222A37]">
                {tr(language, '历史真值快照', 'Historical truth snapshots')} · {historyCards.length}
              </summary>
              <div className="border-t border-neutral-800">
                <EvidenceCards empty={tr(language, '暂无历史观测', 'No historical observations yet')} items={historyCards} />
              </div>
            </details>
          </div>
        )}

        {activeWorkbenchTab === 'diff' && (
          <div className="space-y-2 p-2">
            <DiffStatsPanel
              rows={hourlyRows}
              chartData={chartData}
              unit={unit}
              selectedDate={selectedDate}
              evidenceSummary={selectedDateEvidence?.modules?.diff_stats?.summary}
              sourceSeries={hourlySourceSeries}
              biasStats={hourlyBiasStats}
              language={language}
            />
            <details className="border border-[#2C3445] bg-[#161A22]">
              <summary className="cursor-pointer select-none px-2 py-2 text-xs text-[#CBD2DC] hover:bg-[#222A37]">
                {tr(language, '校准明细 · 平均偏差 / Pearson R / truth', 'Calibration detail · average delta / Pearson R / truth')}
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
          <div className="p-2">
            <EventTimeline events={eventRows} fetchLog={fetchLog} productionRefresh={productionRefresh} />
          </div>
        )}
      </section>

      {backfillResult && (
        <div className="border border-cyan-500/20 bg-cyan-500/5 px-2 py-1 text-[10px] text-cyan-300">
          {tr(language, '最近补历史', 'Latest history backfill')}：{tr(language, '写入', 'wrote')} {backfillResult.fetched}，{tr(language, '错误', 'errors')} {backfillResult.errors.length}
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
  tab: { id: WeatherWorkbenchTab; label: string }
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`min-w-[120px] shrink-0 border px-2 py-1.5 text-left rounded-none ${
        active
          ? 'border-[#2563EB] bg-[#2563EB] text-white'
          : 'border-[#2C3445] bg-[#161A22] text-[#7D8694] hover:bg-[#222A37] hover:text-[#CBD2DC]'
      }`}
      title={tab.label}
    >
      <div className="text-[11px] font-medium">{tab.label}</div>
    </button>
  )
}

function ForecastDataTable({ rows, unit, selectedDate, city, language }: { rows: HourlyWeatherRow[]; unit: string; selectedDate: string; city: string; language: 'zh' | 'en' }) {
  const [revisionRow, setRevisionRow] = useState<HourlyWeatherRow | null>(null)
  const columns = language === 'zh'
    ? ['时间', '气温', '云量', '降水', '风', '天气状况', '气压', '露点', '变更', '抓取（系统时）', '抓取（本地时）']
    : ['Time', 'Temp', 'Cloud', 'Precip', 'Wind', 'Condition', 'Pressure', 'Dew', 'Changes', 'Fetched (Sys)', 'Fetched (Local)']
  return (
    <>
    <section className="border border-neutral-800 bg-black">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
        <div>
          <div className="text-[10px] text-neutral-500">{tr(language, '预报数据', 'Forecast data')}</div>
          <div className="text-xs text-neutral-100">{longDate(selectedDate)} · {rows.length} {tr(language, '行', 'rows')}</div>
        </div>
      </div>
      {rows.length === 0 ? (
        <div className="max-h-[360px] overflow-auto">
          <table className="min-w-[980px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {columns.map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={11} className="px-2 py-12 text-center text-neutral-600">
                  {tr(language, '该日期暂无预报数据。', 'No forecast rows for this date.')}
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
                {columns.map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.id} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                  <td className="px-2 py-1 tabular-nums text-neutral-300">{row.label}</td>
                  <td className="px-2 py-1 tabular-nums text-green-300">{fmtTemp(row.forecast, unit)}</td>
                  <td className="px-2 py-1"><CloudCoverMeter value={row.forecast_cloud_cover} /></td>
                  <td className="px-2 py-1"><PrecipitationMetric amount={row.precipitation} probability={row.precipitation_probability} /></td>
                  <td className="px-2 py-1"><WindMetric speed={row.wind_speed} direction={row.wind_direction} /></td>
                  <td className="max-w-[140px] truncate px-2 py-1 text-neutral-400" title={`${row.source || '--'} · ${row.horizon || '--'}`}>
                    {row.condition || row.source || row.horizon || '--'}
                  </td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPressure(row.pressure)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtTemp(row.dew_point, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">
                    {(row.snapshot_count ?? 0) > 1 && (row.revision_count ?? 0) > 0 ? (
                      <button
                        type="button"
                        className="inline-flex min-h-6 items-center gap-1 border border-amber-500/25 bg-amber-500/10 px-1.5 text-[10px] text-amber-300 hover:border-amber-400/50 hover:bg-amber-500/15"
                        title={`${row.snapshot_count} 次快照 · ${row.revision_count ?? 0} 次修订`}
                        onClick={() => setRevisionRow(row)}
                      >
                        <History className="h-3 w-3" aria-hidden="true" />
                        {row.revision_count ?? 0}
                      </button>
                    ) : (row.snapshot_count ?? 0) > 1 ? (
                      <span title={`${row.snapshot_count} 次快照 · 无温度修订`}>--</span>
                    ) : row.archive ? 'archive' : row.member_count ? `n ${row.member_count}` : '--'}
                  </td>
                  <td className="px-2 py-1 tabular-nums text-neutral-500">{shortTime(row.fetched_at)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-500">{shortHour(row.fetched_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
    {revisionRow && (
      <ForecastRevisionDialog
        city={city}
        targetDate={selectedDate}
        localHour={revisionRow.label}
        unit={unit}
        onClose={() => setRevisionRow(null)}
      />
    )}
    </>
  )
}

function MetarObservationTable({ rows, unit, selectedDate, loading = false, language }: { rows: HourlyWeatherRow[]; unit: string; selectedDate: string; loading?: boolean; language: 'zh' | 'en' }) {
  const metarRows = rows.filter(row => row.metar !== null && row.metar !== undefined)
  const columns = language === 'zh'
    ? ['时间', '气温', '云量', '天气现象', '能见度', '风', '气压', '露点', 'METAR 原文', '抓取（系统时）', '抓取（本地时）']
    : ['Time', 'Temp', 'Cloud', 'Weather', 'Visibility', 'Wind', 'Pressure', 'Dew', 'Raw METAR', 'Fetched (Sys)', 'Fetched (Local)']
  const columnWidths = ['88px', '68px', '68px', '110px', '82px', '128px', '78px', '72px', '390px', '132px', '132px']

  return (
    <section className="min-w-0 border border-neutral-800 bg-black">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
        <div>
          <div className="text-[10px] text-neutral-500">{tr(language, 'METAR 观测', 'METAR observations')}</div>
          <div className="text-xs text-neutral-100">{longDate(selectedDate)} · {metarRows.length} {tr(language, '行', 'rows')}</div>
        </div>
        <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500">{tr(language, '机场原始报文', 'Raw airport report')}</span>
      </div>
      {metarRows.length === 0 ? (
        <div className="max-h-[560px] overflow-auto">
          <table className="w-full min-w-[1350px] table-fixed border-collapse text-left text-[10px]">
            <colgroup>{columnWidths.map((width, index) => <col key={index} style={{ width }} />)}</colgroup>
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {columns.map(column => (
                  <th key={column} className="whitespace-nowrap px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={columns.length} className="px-2 py-12 text-center text-neutral-600">
                  {loading ? tr(language, '正在读取 METAR 原始序列…', 'Loading raw METAR series…') : tr(language, '该日期暂无 METAR 观测。', 'No METAR observations for this date.')}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <div className="max-h-[560px] overflow-auto">
          <table className="w-full min-w-[1350px] table-fixed border-collapse text-left text-[10px]">
            <colgroup>{columnWidths.map((width, index) => <col key={index} style={{ width }} />)}</colgroup>
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {columns.map(column => (
                  <th key={column} className="whitespace-nowrap px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metarRows.map(row => (
                <tr key={row.id} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                   <td className="whitespace-nowrap px-2 py-1 tabular-nums text-neutral-300">{row.label}</td>
                   <td className="whitespace-nowrap px-2 py-1 tabular-nums text-amber-300">{fmtTemp(row.metar, unit)}</td>
                   <td className="whitespace-nowrap px-2 py-1"><CloudCoverMeter value={row.cloud_cover} /></td>
                   <td className="truncate whitespace-nowrap px-2 py-1 text-neutral-500" title={row.condition || '--'}>{row.condition || '--'}</td>
                   <td className="whitespace-nowrap px-2 py-1 tabular-nums text-neutral-400">{fmtVisibility(row.visibility)}</td>
                   <td className="whitespace-nowrap px-2 py-1"><WindMetric speed={row.wind_speed} direction={row.wind_direction} /></td>
                   <td className="whitespace-nowrap px-2 py-1 tabular-nums text-neutral-400">{fmtPressure(row.pressure)}</td>
                   <td className="whitespace-nowrap px-2 py-1 tabular-nums text-neutral-400">{fmtTemp(row.dew_point, unit)}</td>
                   <td className="truncate whitespace-nowrap px-2 py-1 font-mono text-neutral-400" title={row.raw_text || '--'}>{row.raw_text || '--'}</td>
                   <td className="whitespace-nowrap px-2 py-1 tabular-nums text-neutral-500">{shortTime(row.fetched_at)}</td>
                   <td className="whitespace-nowrap px-2 py-1 tabular-nums text-neutral-500">{shortHour(row.fetched_at)}</td>
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

function HistoricalHourlyObservationTable({ rows, unit, selectedDate, loading = false, language }: { rows: HourlyWeatherRow[]; unit: string; selectedDate: string; loading?: boolean; language: 'zh' | 'en' }) {
  const historicalRows = rows.filter(row => row.historical !== null && row.historical !== undefined)
  const columns = language === 'zh'
    ? ['时间', '气温', '云量', '天气现象', '能见度', '风', '气压', '露点', '抓取（系统时）', '抓取（本地时）']
    : ['Time', 'Temp', 'Cloud', 'Weather', 'Visibility', 'Wind', 'Pressure', 'Dew', 'Fetched (Sys)', 'Fetched (Local)']

  return (
    <section className="min-w-0 border border-neutral-800 bg-black">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
        <div>
          <div className="text-[10px] text-neutral-500">{tr(language, '历史观测', 'Historical observations')}</div>
          <div className="text-xs text-neutral-100">{longDate(selectedDate)} · {historicalRows.length} {tr(language, '行', 'rows')}</div>
        </div>
        <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500">Wunderground</span>
      </div>
      <div className="max-h-[560px] overflow-auto">
        <table className="min-w-[1080px] w-full border-collapse text-left text-[10px]">
          <thead className="sticky top-0 bg-black text-neutral-500">
            <tr className="border-b border-neutral-900">
              {columns.map(column => (
                <th key={column} className="px-2 py-1 font-normal">{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {historicalRows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-2 py-12 text-center text-neutral-600">
                  {loading ? tr(language, '正在读取 Wunderground 历史观测…', 'Loading Wunderground observations…') : tr(language, '该日期暂无 Wunderground 历史观测。', 'No Wunderground observations for this date.')}
                </td>
              </tr>
            ) : historicalRows.map(row => (
                <tr key={`historical-${row.id}`} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                  <td className="px-2 py-1 tabular-nums text-neutral-300">{row.label}</td>
                  <td className="px-2 py-1 tabular-nums text-green-300">{fmtTemp(row.historical, unit)}</td>
                  <td className="px-2 py-1"><CloudCoverMeter value={row.cloud_cover} /></td>
                  <td className="max-w-[140px] truncate px-2 py-1 text-neutral-500" title={row.condition || '--'}>{row.condition || '--'}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtVisibility(row.visibility)}</td>
                  <td className="px-2 py-1"><WindMetric speed={row.wind_speed} direction={row.wind_direction} /></td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPressure(row.pressure)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtTemp(row.dew_point, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-500">{shortTime(row.fetched_at)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-500">{shortHour(row.fetched_at)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function DiffStatsPanel({
  rows,
  chartData,
  unit,
  selectedDate,
  evidenceSummary,
  sourceSeries,
  biasStats,
  language,
}: {
  rows: HourlyWeatherRow[]
  chartData: WeatherChartRow[]
  unit: string
  selectedDate: string
  evidenceSummary?: CityEvidenceDiffStatsSummary
  sourceSeries?: HourlySourceSeries | null
  biasStats?: HourlyBiasStats | null
  language: 'zh' | 'en'
}) {
  const [observedSource, setObservedSource] = useState<'metar' | 'historical'>('metar')
  const forecastByHour = new Map(
    (sourceSeries?.forecast ?? []).map(point => [String(point.local_hour || point.local_time || '').slice(0, 5), point]),
  )
  const nativeObserved = observedSource === 'metar' ? sourceSeries?.metar : sourceSeries?.historical
  const nativePairs = (nativeObserved ?? []).flatMap((point, index) => {
    const localTime = String(point.local_time || point.local_hour || '').slice(0, 5)
    if (!localTime.endsWith(':00')) return []
    const forecastPoint = forecastByHour.get(localTime)
    const observed = asNumber(point.temperature)
    const forecast = asNumber(forecastPoint?.temperature ?? forecastPoint?.ensemble_mean ?? forecastPoint?.best)
    if (observed === null || forecast === null) return []
    return [{
      id: `${observedSource}-${point.timestamp}-${index}`,
      time: String(point.local_time || point.local_hour || point.timestamp.slice(11, 16)),
      observed,
      forecast,
      delta: observed - forecast,
      cloud_cover: asNumber(point.cloud_cover),
      condition: point.condition ?? null,
      wind_speed: asNumber(point.wind_speed),
      wind_direction: asNumber(point.wind_direction),
      pressure: asNumber(point.pressure),
      dew_point: asNumber(point.dew_point),
      fetched_sys: point.retrieved_at || point.timestamp,
      fetched_local: point.retrieved_at || point.timestamp,
      source: point.source || observedSource,
    }]
  })
  const hourlyPairs = rows
    .filter(row => row.forecast !== null && row.forecast !== undefined && (observedSource === 'metar' ? row.metar : row.historical) !== null && (observedSource === 'metar' ? row.metar : row.historical) !== undefined)
    .map(row => ({
      id: row.id,
      time: row.label,
      observed: Number(observedSource === 'metar' ? row.metar : row.historical),
      forecast: Number(row.forecast),
      delta: Number(observedSource === 'metar' ? row.metar : row.historical) - Number(row.forecast),
      cloud_cover: row.cloud_cover,
      condition: row.condition,
      wind_speed: row.wind_speed,
      wind_direction: row.wind_direction,
      pressure: row.pressure,
      dew_point: row.dew_point,
      fetched_sys: row.timestamp,
      fetched_local: row.timestamp,
      source: row.source || observedSource,
    }))
  const dailyPairs = chartData
    .filter(row => row.actual_high !== null && row.actual_high !== undefined && row.forecast_high !== null && row.forecast_high !== undefined)
    .map(row => ({
      id: row.date,
      time: longDate(row.date),
      observed: Number(row.actual_high),
      forecast: Number(row.forecast_high),
      delta: Number(row.actual_high) - Number(row.forecast_high),
      cloud_cover: null,
      condition: row.calibration_tier || row.historical_provider,
      wind_speed: null,
      wind_direction: null,
      pressure: null,
      dew_point: null,
      fetched_sys: row.forecast_timestamp || row.date,
      fetched_local: row.forecast_timestamp || row.date,
      source: row.historical_provider || row.forecast_source || 'history',
    }))
  const tableRows = nativePairs.length > 0 ? nativePairs : hourlyPairs.length > 0 ? hourlyPairs : dailyPairs.slice(-30).reverse()
  const diffColumns = language === 'zh'
    ? ['时间', '气温', '云量', '天气', '能见度', '风', '气压', '露点', '抓取（系统时）', '抓取（本地时）']
    : ['Time', 'Temp', 'Cloud', 'Weather', 'Visibility', 'Wind', 'Pressure', 'Dew', 'Fetched (Sys)', 'Fetched (Local)']
  const deltas = tableRows.map(row => row.delta)
  const avgDelta = mean(deltas)
  const correlation = pearsonR(
    tableRows.map(row => row.forecast),
    tableRows.map(row => row.observed)
  )
  const maxAbsDelta = Math.max(1, ...deltas.map(delta => Math.abs(delta)))
  const canonical = biasStats?.[observedSource]
  const nativeSelected = nativePairs.length > 0
  const summaryCount = canonical?.count ?? (nativeSelected ? tableRows.length : evidenceSummary?.count ?? tableRows.length)
  const summaryAvgDelta = canonical?.avg_delta ?? (nativeSelected ? avgDelta : evidenceSummary?.avg_delta ?? avgDelta)
  const summaryMae = nativeSelected ? (deltas.length ? mean(deltas.map(delta => Math.abs(delta))) : null) : evidenceSummary?.mae ?? (deltas.length ? mean(deltas.map(delta => Math.abs(delta))) : null)
  const summaryPearson = canonical?.pearson_r ?? (nativeSelected ? correlation : evidenceSummary?.pearson_r ?? correlation)
  const canonicalOverlap = biasStats?.historical_metar_overlap
  const summaryOverlap = canonicalOverlap?.ratio ?? evidenceSummary?.overlap_ratio
  const summaryOverlapLabel = summaryOverlap === null || summaryOverlap === undefined
    ? (summaryCount ? `${summaryCount}` : '--')
    : fmtProb(summaryOverlap)
  const summaryOverlapSub = summaryOverlap === null || summaryOverlap === undefined
    ? tr(language, '配对样本', 'paired samples')
    : canonicalOverlap
      ? `${canonicalOverlap.count}/${canonicalOverlap.possible} ${tr(language, '点', 'points')}`
      : `${evidenceSummary?.overlap_count ?? 0}/${Math.max(evidenceSummary?.metar_hours ?? 0, evidenceSummary?.forecast_hours ?? 0, 1)} ${tr(language, '小时', 'hours')}`
  const historyMetarOverlap = canonicalOverlap?.ratio ?? evidenceSummary?.historical_metar_overlap_ratio
  const historyMetarOverlapCount = canonicalOverlap?.count ?? evidenceSummary?.historical_metar_overlap_count ?? 0
  let cumulative = 0
  const diffChartRows = tableRows.map((row, index) => {
    cumulative += row.delta
    return { ...row, cumulative_avg: cumulative / (index + 1) }
  })

  return (
    <section className="border border-neutral-800 bg-black">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
        <div>
          <div className="text-[10px] text-neutral-500">{tr(language, '偏差统计（实测 − 预报）', 'Bias statistics (observed − forecast)')}</div>
          <div className="text-xs text-neutral-100">{longDate(selectedDate)} · {tableRows.length} {tr(language, '个匹配点', 'matched points')}</div>
        </div>
        <div className="inline-flex border border-neutral-800 p-0.5 text-[10px]">
          {(['metar', 'historical'] as const).map(source => (
            <button
              key={source}
              type="button"
              onClick={() => setObservedSource(source)}
              className={`px-2 py-1 ${observedSource === source ? 'bg-blue-600 text-white' : 'text-neutral-500 hover:text-neutral-200'}`}
            >
              {source === 'metar' ? 'METAR' : tr(language, '历史观测', 'Historical')}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(130px,1fr))] gap-2 border-b border-neutral-900 p-2">
        <MetricCard label={tr(language, '平均偏差', 'Average delta')} value={fmtSignedTemp(summaryAvgDelta, unit)} sub={tr(language, '实测 − 预报', 'Observed − forecast')} />
        <MetricCard label="MAE" value={summaryMae === null ? '--' : fmtTemp(summaryMae, unit)} sub="mean abs error" />
        <MetricCard label={tr(language, '准确度', 'Accuracy')} value={fmtPearson(summaryPearson)} sub="Pearson R" />
        <MetricCard label={tr(language, '重合度', 'Overlap')} value={summaryOverlapLabel} sub={summaryOverlapSub} />
        <MetricCard label="Hist↔METAR" value={historyMetarOverlap === null || historyMetarOverlap === undefined ? '--' : fmtProb(historyMetarOverlap)} sub={`${historyMetarOverlapCount} pts`} />
        <MetricCard label={tr(language, '最大绝对偏差', 'Max abs delta')} value={fmtTemp(Math.max(0, ...deltas.map(delta => Math.abs(delta))), unit)} sub={tr(language, '当前可见最差值', 'worst visible row')} />
      </div>
      {diffChartRows.length > 0 && (
        <div className="h-[300px] border-b border-neutral-900 p-3" role="img" aria-label="实测减预报偏差柱状图与累计均值线">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={diffChartRows} margin={{ top: 16, right: 24, bottom: 24, left: 4 }}>
              <CartesianGrid stroke="#2C3445" strokeDasharray="3 3" />
              <XAxis dataKey="time" stroke="#7D8694" fontSize={9} tickLine={false} axisLine={false} />
              <YAxis stroke="#7D8694" fontSize={9} tickLine={false} axisLine={false} tickFormatter={value => `${Number(value).toFixed(1)}°${unit}`} />
              <ReferenceLine y={0} stroke="#64748B" />
              <Tooltip
                contentStyle={{ background: 'var(--tooltip-bg)', border: '1px solid var(--tooltip-border)', color: 'var(--tooltip-text)', fontSize: 11 }}
                labelStyle={{ color: 'var(--tooltip-text)' }}
                itemStyle={{ color: 'var(--tooltip-text)' }}
                formatter={(value: unknown, name: string) => [fmtSignedTemp(Number(value), unit), name === 'delta' ? tr(language, '偏差（实测−预报）', 'Delta (obs−fc)') : tr(language, '累计均值', 'Cumulative mean')]}
              />
              <Bar dataKey="delta" name="偏差（实测−预报）" maxBarSize={42}>
                {diffChartRows.map(row => <Cell key={row.id} fill={row.delta >= 0 ? '#22C55E' : '#EF4444'} fillOpacity={0.75} />)}
              </Bar>
              <Line type="monotone" dataKey="cumulative_avg" name="累计均值" stroke="#6366F1" strokeWidth={2} dot={{ r: 2, fill: '#6366F1' }} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      {tableRows.length === 0 ? (
        <div className="max-h-[460px] overflow-auto">
          <table className="min-w-[980px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {diffColumns.map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={diffColumns.length} className="px-2 py-12 text-center text-neutral-600">
                  {tr(language, '暂无配对的实测/预报数据。', 'No paired observed/forecast rows yet.')}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <div className="max-h-[460px] overflow-auto">
          <table className="min-w-[980px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {diffColumns.map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows.map(row => {
                const width = Math.max(4, Math.min(60, Math.abs(row.delta) / maxAbsDelta * 60))
                const tone = errorTone(Math.abs(row.delta))
                const barClass = tone === 'green' ? 'bg-green-400/70' : tone === 'amber' ? 'bg-amber-400/75' : 'bg-red-400/75'
                return (
                  <tr key={row.id} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                    <td className="px-2 py-1 tabular-nums text-neutral-300">{row.time}</td>
                    <td className="min-w-[160px] px-2 py-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="tabular-nums text-neutral-200">{fmtSignedTemp(row.delta, unit)}</span>
                        <span className="tabular-nums text-neutral-500">{fmtTemp(row.observed, unit)} / {fmtTemp(row.forecast, unit)}</span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden bg-neutral-900">
                        <div className={`h-full ${barClass}`} style={{ width: `${width}%` }} />
                      </div>
                    </td>
                    <td className="px-2 py-1"><CloudCoverMeter value={row.cloud_cover} /></td>
                    <td className="max-w-[120px] truncate px-2 py-1 text-neutral-400" title={row.condition || row.source}>{row.condition || row.source || '--'}</td>
                    <td className="px-2 py-1 tabular-nums text-neutral-500">--</td>
                    <td className="px-2 py-1"><WindMetric speed={row.wind_speed} direction={row.wind_direction} /></td>
                    <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPressure(row.pressure)}</td>
                    <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtTemp(row.dew_point, unit)}</td>
                    <td className="px-2 py-1 tabular-nums text-neutral-500">{shortTime(row.fetched_sys)}</td>
                    <td className="px-2 py-1 tabular-nums text-neutral-500">{shortTime(row.fetched_local)}</td>
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

function mergeNativeHourlySeries(series: HourlySourceSeries): HourlyChartRow[] {
  const rows = new Map<number, HourlyChartRow>()
  const put = (
    point: HourlySourcePoint,
    field: 'forecast_value' | 'metar_value' | 'historical_value' | 'china_live_value' | 'pws_value',
  ) => {
    const timestamp = String(point.timestamp || '')
    const label = String(point.local_time || point.local_hour || timestamp.slice(11, 16))
    if (!timestamp || !label) return
    const [hourText, minuteText] = label.split(':')
    const timeMinute = Number(hourText) * 60 + Number(minuteText || 0)
    if (!Number.isFinite(timeMinute)) return
    const row = rows.get(timeMinute) ?? {
      label,
      time_minute: timeMinute,
      timestamp,
      forecast_value: null,
      metar_value: null,
      historical_value: null,
      china_live_value: null,
      pws_value: null,
      cloud_pct: null,
    }
    row[field] = field === 'forecast_value'
      ? asNumber(point.temperature ?? point.ensemble_mean ?? point.best)
      : asNumber(point.temperature)
    if (field === 'forecast_value') row.cloud_pct = asNumber(point.cloud_cover)
    rows.set(timeMinute, row)
  }

  for (const point of series.forecast ?? []) put(point, 'forecast_value')
  for (const point of series.metar ?? []) put(point, 'metar_value')
  for (const point of series.historical ?? []) put(point, 'historical_value')
  for (const point of series.china_live ?? []) put(point, 'china_live_value')
  for (const point of series.pws ?? []) put(point, 'pws_value')
  return [...rows.values()].sort((left, right) => left.time_minute - right.time_minute)
}

function HourlyEvidencePanel({
  rows,
  sourceSeries,
  biasStats,
  unit,
  cityName,
  selectedDate,
  dailyMaxPrediction,
  forecastPeakMarker,
  loading,
  language,
}: {
  rows: HourlyWeatherRow[]
  sourceSeries?: HourlySourceSeries | null
  biasStats?: HourlyBiasStats | null
  unit: string
  cityName?: string
  selectedDate: string
  dailyMaxPrediction?: DailyMaxPredictionSummary | null
  forecastPeakMarker?: HourlyConsensusSummary['forecast_peak_marker']
  loading?: boolean
  language: 'zh' | 'en'
}) {
  const hourlyChartRows = rows.map(row => ({
    ...row,
    time_minute: Number(row.label.slice(0, 2)) * 60 + Number(row.label.slice(3, 5) || 0),
    forecast_value: asNumber(row.forecast),
    metar_value: asNumber(row.metar),
    historical_value: asNumber(row.historical),
    china_live_value: asNumber(row.china_live),
    pws_value: asNumber(row.pws),
    gap_value: asNumber(row.gap),
    cloud_pct: asNumber(row.forecast_cloud_cover),
  }))
  const nativeSeries = sourceSeries ?? {}
  const hasNativeSeries = Object.values(nativeSeries).some(seriesRows => (seriesRows?.length ?? 0) > 0)
  const chartRows = hasNativeSeries
    ? mergeNativeHourlySeries(nativeSeries)
    : hourlyChartRows
  const numericValues = (values: unknown[]) =>
    values.map(asNumber).filter((value): value is number => value !== null)
  const forecastValues = numericValues(chartRows.map(row => row.forecast_value))
  const metarValues = numericValues(chartRows.map(row => row.metar_value))
  const historicalValues = numericValues(chartRows.map(row => row.historical_value))
  const chinaLiveValues = numericValues(chartRows.map(row => row.china_live_value))
  const pwsValues = numericValues(chartRows.map(row => row.pws_value))
  const forecastMax = forecastValues.length > 0 ? Math.max(...forecastValues) : null
  const metarMax = metarValues.length > 0 ? Math.max(...metarValues) : null

  if (loading && !hasNativeSeries) {
    return (
      <section className="border border-[#2C3445] bg-[#161A22] px-3 py-16 text-center text-xs text-[#7D8694]">
        {tr(language, '正在读取预报、METAR 与历史观测原始序列…', 'Loading forecast, METAR, and historical observations…')}
      </section>
    )
  }
  const hasChartEvidence = chartRows.some(row =>
    row.forecast_value !== null
    || row.metar_value !== null
    || row.historical_value !== null
    || row.china_live_value !== null
    || row.pws_value !== null
    || row.cloud_pct !== null
  )
  // Keep native-frequency points for the chart, but use the backend's
  // PolyWX-aligned exact-hour contract for delta and correlation statistics.
  const metarStats = canonicalSourceStats(biasStats?.metar) ?? sourceStats(hourlyChartRows, 'metar_value')
  const historicalStats = canonicalSourceStats(biasStats?.historical) ?? sourceStats(hourlyChartRows, 'historical_value')
  const overlapStats = canonicalOverlapPill(biasStats) ?? overlapPill(hourlyChartRows)
  const hasHistorical = historicalValues.length > 0
  const hasChinaLive = chinaLiveValues.length > 0
  const hasPws = pwsValues.length > 0
  const peakRow = chartRows
    .filter(row => row.forecast_value !== null)
    .sort((a, b) => Number(b.forecast_value ?? -Infinity) - Number(a.forecast_value ?? -Infinity))[0]
  const peakHour = normalizePeakHour(forecastPeakMarker?.local_time)
    ?? dailyMaxPeakHour(dailyMaxPrediction, peakRow?.label)
  if (chartRows.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-4 text-center text-neutral-600">
        {tr(language, '该日期暂无逐小时数据。', 'No hourly rows for this date.')}
      </div>
    )
  }
  if (!hasChartEvidence) {
    return (
      <section className="min-h-0 border border-[#2C3445] bg-[#161A22]">
        <div className="border-b border-[#2C3445] px-2 py-1.5">
          <div className="text-[10px] text-[#7D8694]">{tr(language, '逐小时气温', 'Hourly temperature')}</div>
          <div className="text-xs text-[#CBD2DC]">{cityName || tr(language, '当前城市', 'Current city')} · {longDate(selectedDate)}</div>
        </div>
        <div className="flex min-h-[260px] items-center justify-center p-4 text-center text-xs text-[#7D8694]">
          {tr(
            language,
            '当前日期存在小时记录，但没有可绘制的温度、METAR 或云量字段。请查看抓取日志。',
            'Hourly rows exist, but no temperature, METAR, or cloud fields can be plotted. Check the fetch log.',
          )}
        </div>
      </section>
    )
  }

  return (
    <HourlyTemperatureChart
      rows={chartRows}
      unit={unit}
      cityName={cityName || tr(language, '当前城市', 'Current city')}
      dateLabel={longDate(selectedDate)}
      forecastMax={forecastMax}
      metarMax={metarMax}
      peakHour={peakHour || null}
      hasChinaLive={hasChinaLive}
      hasPws={hasPws}
      hasHistorical={hasHistorical}
      averageDelta={[statDeltaPill('METAR', metarStats, unit), statDeltaPill('Historical', historicalStats, unit)]}
      accuracy={[statAccuracyPill('METAR', metarStats), statAccuracyPill('Historical', historicalStats)]}
      overlap={overlapStats}
      language={language}
    />
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
            当前城市还没有同一天同时包含“历史实际最高温”和“保存预测最高温”的样本。自动抓取或完成更多日度抓取后，这里会显示最近误差、MAE 和 bias。
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
                const width = Math.max(4, Math.min(60, (absError / maxAbsError) * 60))
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
                <DetailLine label="用途" value="用于复核观测、预报与结算数据来源。" wide />
              </div>
            </details>
          </div>
        </aside>
      </div>
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

type NormalizedFetchLogRow = {
  index: number
  key: string
  time?: string
  source: string
  stage: string
  status: string
  duration: string
  message: string
  details: string
}

function EventTimeline({
  events,
  fetchLog = [],
  productionRefresh,
}: {
  events: DashboardEvent[]
  fetchLog?: FetchLogRow[]
  productionRefresh?: ProductionRefreshResult | null
}) {
  const durationLabel = (event: DashboardEvent) => {
    const data = event.data && typeof event.data === 'object' && !Array.isArray(event.data)
      ? event.data as Record<string, unknown>
      : {}
    const raw = data.elapsed_ms ?? data.duration_ms ?? data.duration
    if (typeof raw === 'number') return visibleElapsedLabel(raw) || '--'
    if (typeof raw === 'string') return visibleElapsedLabel(raw) || '--'
    return '--'
  }
  const sourceLabel = (event: DashboardEvent) => {
    const data = event.data && typeof event.data === 'object' && !Array.isArray(event.data)
      ? event.data as Record<string, unknown>
      : {}
    const raw = data.source ?? data.provider ?? data.stage ?? data.action ?? event.type
    if (typeof raw === 'string' && raw.trim()) return raw.trim()
    if (typeof raw === 'number') return String(raw)
    return eventStage(event)
  }
  const statusLabel = (event: DashboardEvent) => {
    const tone = eventTone(event)
    if (tone === 'red') return 'ERR'
    if (tone === 'amber') return 'WARN'
    if (tone === 'green' || tone === 'cyan') return 'OK'
    return 'INFO'
  }
  const showRunningProductionRefresh = Boolean(
    productionRefresh?.running
    || productionRefresh?.production_refresh_running
    || productionRefresh?.stages?.some((stage) => stage.running),
  )
  const productionRows: NormalizedFetchLogRow[] = showRunningProductionRefresh
    ? productionRefresh?.stages?.map((stage, index) => {
    const payload = compactData(stage.payload, 420)
    const status = stage.running ? 'RUN' : stage.skipped ? 'SKIP' : stage.ok ? 'OK' : 'ERR'
    const message = stage.error || stage.reason || stage.name
    return {
      index: index + 1,
      key: `production-refresh-${productionRefresh.requested_at || 'current'}-${stage.name}-${index}`,
      time: productionRefresh.requested_at,
      source: 'production-refresh',
      stage: stage.name,
      status,
      duration: visibleElapsedLabel(stage.elapsed_ms) || '--',
      message,
      details: payload || message || '--',
    }
      }) ?? []
    : []
  const sourceRows: NormalizedFetchLogRow[] = fetchLog.length > 0
    ? fetchLog.slice(0, 100).map((row, index) => {
      const duration = visibleElapsedLabel(row.duration) || '--'
      return {
        index: row.index ?? index + 1,
        key: String(row.event_id ?? `${row.time || 'fetch'}-${index}`),
        time: row.time,
        source: row.source || row.event_type || '--',
        stage: row.stage || 'system',
        status: row.status || 'INFO',
        duration,
        message: row.message || row.details || '--',
        details: row.details || row.event_type || '--',
      }
    })
    : events.slice(0, 100).map((event, index) => {
      const data = compactData(event.data, 360)
      const message = [event.message, data && `data: ${data}`].filter(Boolean).join(' / ') || '--'
      return {
        index: index + 1,
        key: String(event.id ?? `${event.timestamp || 'event'}-${index}`),
        time: event.timestamp,
        source: sourceLabel(event),
        stage: eventStage(event),
        status: statusLabel(event),
        duration: durationLabel(event),
        message,
        details: data || event.type || '--',
      }
    })
  const rows = [...productionRows, ...sourceRows].slice(0, 120)
  const statusClass = (status: string) => {
    if (status === 'RUN') return 'text-cyan-300'
    if (status === 'ERR') return 'text-red-300'
    if (status === 'WARN') return 'text-amber-300'
    if (status === 'SKIP') return 'text-neutral-500'
    if (status === 'OK') return 'text-green-300'
    return 'text-neutral-400'
  }
  const stageGroup = (row: NormalizedFetchLogRow) => {
    const lower = [row.stage, row.source, row.message].join(' ').toLowerCase()
    if (/forecast|openmeteo|daily|max|hourly/.test(lower)) return 'weather'
    if (/metar|truth|observation|historical/.test(lower)) return 'observation'
    if (/orderbook|bucket|clob|market/.test(lower)) return 'orderbook'
    if (/signal|decision/.test(lower)) return 'signal'
    return 'system'
  }

  return (
    <section className="min-w-0 border border-neutral-800 bg-black">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
        <div>
          <div className="text-[10px] text-neutral-500">抓取日志（当前城市）</div>
          <div className="text-xs text-neutral-100">{rows.length} 条</div>
        </div>
        <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500">时间 / 来源 / 状态 / 耗时 / 信息</span>
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(100px,1fr))] gap-1 border-b border-neutral-900 p-2 text-[10px]">
        {[
          ['weather', '天气'],
          ['observation', '观测'],
          ['orderbook', '盘口'],
          ['signal', '信号'],
          ['system', '系统'],
        ].map(([stage, label]) => {
          const count = rows.filter(row => stageGroup(row) === stage).length
          return (
            <div key={stage} className="border border-neutral-800 px-2 py-1 text-neutral-500">
              {label} <span className="tabular-nums text-neutral-200">{count}</span>
            </div>
          )
        })}
      </div>
      {rows.length === 0 ? (
        <div className="max-h-[560px] overflow-auto">
          <table className="min-w-[860px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {['#', '时间', '来源', '状态', '耗时', '信息'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={6} className="px-2 py-12 text-center text-neutral-600">
                  暂无该城市的抓取记录
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
                {['#', '时间', '来源', '状态', '耗时', '信息'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                  <tr key={row.key} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                    <td className="px-2 py-1 tabular-nums text-neutral-500">{row.index}</td>
                    <td className="px-2 py-1 tabular-nums text-neutral-300">{shortTime(row.time)}</td>
                    <td className="max-w-[160px] truncate px-2 py-1 text-neutral-400" title={row.stage}>{row.source}</td>
                    <td className={`px-2 py-1 tabular-nums ${statusClass(row.status)}`}>{row.status}</td>
                    <td className="px-2 py-1 tabular-nums text-neutral-500">{row.duration}</td>
                    <td className="max-w-[480px] px-2 py-1 text-neutral-400">
                      <details>
                        <summary className="cursor-pointer truncate hover:text-neutral-200" title={row.message}>{row.message}</summary>
                        <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words border border-neutral-900 bg-neutral-950/60 p-2 font-mono text-[9px] leading-relaxed text-neutral-500">
                          {row.details}
                        </pre>
                      </details>
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

function TemperatureDistributionPanel({
  signal,
  decision,
  items,
  unit,
  selectedDate,
  actualHigh,
  observedSampleCount = 0,
  cityName,
  dailyMaxPrediction,
  bucketProbabilities,
  alphaEvents = [],
  queryState,
  language,
}: {
  signal?: WeatherSignal
  decision?: SignalDecisionRecord
  items: LayerDistributionItem[]
  unit: string
  selectedDate: string
  actualHigh?: number | null
  observedSampleCount?: number
  cityName?: string
  dailyMaxPrediction?: DailyMaxPredictionSummary | null
  bucketProbabilities?: BucketProbabilitySummary | null
  alphaEvents?: ModelRepriceEvent[]
  queryState?: Layer7QueryState
  language: 'zh' | 'en'
}) {
  const [sourceDialogOpen, setSourceDialogOpen] = useState(false)
  const [sourceAnalysisView, setSourceAnalysisView] = useState<'history' | 'disagreement'>('disagreement')
  const [modelWeightMode, setModelWeightMode] = useState<ModelWeightMode>('dynamic')
  const [modelWeights, setModelWeights] = useState<Record<ModelWeightFamily, number>>(DEFAULT_MODEL_WEIGHTS)
  const [modelWeightLoading, setModelWeightLoading] = useState(false)
  const [modelWeightSaving, setModelWeightSaving] = useState(false)
  const [modelWeightMessage, setModelWeightMessage] = useState('')
  const distribution = signal?.distribution
  const deb = dailyMaxPrediction?.latest
  const debUnit = deb?.unit || unit
  const modelMu = deb?.model_mu ?? deb?.mu
  const effectiveMu = deb?.effective_mu ?? deb?.mu
  const forecastValue = modelMu !== null && modelMu !== undefined
    ? convertTempUnit(Number(modelMu), debUnit, unit)
    : distribution?.forecast_f === null || distribution?.forecast_f === undefined
    ? null
    : unit === 'C'
      ? (Number(distribution.forecast_f) - 32) * 5 / 9
      : Number(distribution.forecast_f)
  const sigmaValue = deb?.sigma !== null && deb?.sigma !== undefined
    ? convertDeltaUnit(Number(deb.sigma), debUnit, unit)
    : distribution?.sigma_f === null || distribution?.sigma_f === undefined
    ? null
    : unit === 'C'
      ? Number(distribution.sigma_f) * 5 / 9
      : Number(distribution.sigma_f)
  const gaussianItems = deb ? buildGaussianFallbackItems(forecastValue, sigmaValue, unit) : []
  const fallbackItems = items.length === 0 ? gaussianItems : []
  const displayItems = items.length > 0 ? items : fallbackItems
  const fallbackMode = items.length === 0 && fallbackItems.length > 0
  const chartItems = gaussianItems.length > 0 ? gaussianItems : displayItems
  const chartRows = chartItems.map(item => ({
    ...item,
    label: item.diagnostic_label ?? fmtBucketAxisLabel(item, unit),
    probabilityPct: Number(item.probability ?? 0) * 100,
    edgePct: Number(item.probability_edge ?? item.ev ?? 0) * 100,
  }))
  const chartMaxPct = Math.max(0, ...chartRows.map(row => Number(row.probabilityPct || 0)))
  const chartYAxisMax = Math.max(25, Math.ceil(chartMaxPct / 5) * 5)
  const chartYAxisTicks = Array.from({ length: Math.floor(chartYAxisMax / 5) + 1 }, (_, index) => index * 5)
  const topBucketIndexes = new Set(
    chartRows
      .map((row, index) => ({ index, probability: Number(row.probability ?? 0) }))
      .sort((a, b) => b.probability - a.probability)
      .slice(0, Math.min(2, chartRows.length))
      .map(row => row.index)
  )
  const alphaByMarket = useMemo(() => {
    const byMarket = new Map<string, ModelRepriceEvent>()
    const byBucket = new Map<string, ModelRepriceEvent>()
    for (const event of alphaEvents) {
      if (!event.alpha_candidate) continue
      if (event.market_id) byMarket.set(String(event.market_id), event)
      if (event.bucket_key) byBucket.set(String(event.bucket_key), event)
    }
    return { byMarket, byBucket }
  }, [alphaEvents])
  const alphaForItem = (item: LayerDistributionItem) =>
    alphaByMarket.byMarket.get(String(item.market_id || '')) ?? alphaByMarket.byBucket.get(String(item.bucket_key || ''))
  const distributionMethod = decision?.model_distribution?.method ?? deb?.method ?? distribution?.notes?.[0] ?? 'gaussian-cdf'
  const observedValue = actualHigh ?? (deb?.observed_floor === null || deb?.observed_floor === undefined
    ? null
    : convertTempUnit(Number(deb.observed_floor), debUnit, unit))
  const observedLabel = observedValue === null || observedValue === undefined
    ? tr(language, '实测 --', 'Observed --')
    : tr(language, `实测 ${fmtDualTemp(observedValue, unit)}（METAR，${observedSampleCount || '--'} 个样本）`, `Observed ${fmtDualTemp(observedValue, unit)} (METAR, ${observedSampleCount || '--'} samples)`)
  const debVersionLabel = deb?.deb_version || distributionMethod || 'DEB-v1'
  const debUpdatedLabel = freshnessLabel(deb?.updated_at ?? deb?.issued_at)
  const sourceRows = buildDebSourceRows(deb, unit)
  const sourceHistory = buildDebHistoryAnalysis(dailyMaxPrediction, unit)
  const sourceDisagreement = buildDebDisagreementAnalysis(sourceRows, unit, forecastValue)
  const hasSourceHistory = sourceHistory.points.length >= 2 && sourceHistory.series.length > 0
  const activeSourceAnalysisView = hasSourceHistory && sourceAnalysisView === 'history' ? 'history' : 'disagreement'
  const buildWarnings = deb?.build_warnings ?? []
  useEffect(() => {
    if (!sourceDialogOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSourceDialogOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [sourceDialogOpen])
  useEffect(() => {
    if (!sourceDialogOpen) return undefined
    let active = true
    setModelWeightLoading(true)
    setModelWeightMessage('')
    fetchModelWeightSettings()
      .then(settings => {
        if (!active) return
        const nextWeights = { ...DEFAULT_MODEL_WEIGHTS }
        for (const family of MODEL_WEIGHT_FAMILIES) {
          const value = Number(settings.weights?.[family.key])
          if (Number.isFinite(value) && value >= 0) nextWeights[family.key] = value
        }
        setModelWeightMode(settings.mode === 'manual' ? 'manual' : 'dynamic')
        setModelWeights(nextWeights)
      })
      .catch(error => {
        if (!active) return
        setModelWeightMessage(tr(language, '权重读取失败', 'Weights unavailable'))
        console.error('model weight settings fetch failed', error)
      })
      .finally(() => {
        if (active) setModelWeightLoading(false)
      })
    return () => {
      active = false
    }
  }, [language, sourceDialogOpen])

  const saveModelWeights = async () => {
    setModelWeightSaving(true)
    setModelWeightMessage('')
    try {
      const saved = await updateModelWeightSettings(modelWeightMode, modelWeights)
      const nextWeights = { ...DEFAULT_MODEL_WEIGHTS }
      for (const family of MODEL_WEIGHT_FAMILIES) {
        const value = Number(saved.weights?.[family.key])
        if (Number.isFinite(value) && value >= 0) nextWeights[family.key] = value
      }
      setModelWeightMode(saved.mode === 'manual' ? 'manual' : 'dynamic')
      setModelWeights(nextWeights)
      setModelWeightMessage(tr(language, '已保存', 'Saved'))
    } catch (error) {
      setModelWeightMessage(tr(language, '保存失败', 'Save failed'))
      console.error('model weight settings update failed', error)
    } finally {
      setModelWeightSaving(false)
    }
  }
  const editModelWeight = (family: ModelWeightFamily, nextWeight: number) => {
    const effectiveWeights = sourceRows.reduce<Partial<Record<ModelWeightFamily, number>>>((result, row) => {
      const rowFamily = modelWeightFamilyForLabel(row.label)
      if (rowFamily && row.weight !== null && Number.isFinite(row.weight)) result[rowFamily] = Math.max(0, row.weight)
      return result
    }, {})
    setModelWeights(current => ({
      ...current,
      ...(modelWeightMode === 'dynamic' ? effectiveWeights : {}),
      [family]: nextWeight,
    }))
    setModelWeightMode('manual')
    setModelWeightMessage('')
  }
  const peakLock = deb?.peak_lock_candidate as Record<string, unknown> | undefined
  const peakLockCandidate = Boolean(peakLock?.candidate)
  const debState = queryState?.deb ?? IDLE_LAYER7_RESOURCE
  const bucketState = queryState?.buckets ?? IDLE_LAYER7_RESOURCE
  const probabilityState = queryState?.probabilities ?? IDLE_LAYER7_RESOURCE
  const signalState = queryState?.signals ?? IDLE_LAYER7_RESOURCE
  const hasBlockingError = [debState, bucketState, probabilityState, signalState].some(state => state.status === 'error')
  const qualityReasons = dailyMaxPrediction?.quality_reasons ?? []
  const rejectedLegacyPrediction = !deb && qualityReasons.length > 0
  const debEmptyLabel = debState.status === 'loading'
    ? tr(language, '正在读取 DEB…', 'Loading DEB…')
    : rejectedLegacyPrediction
      ? tr(language, '该日期预测不可审计', 'Forecast is not auditable')
    : debState.status === 'error'
      ? tr(language, 'DEB 读取失败', 'DEB failed to load')
      : debState.status === 'idle'
        ? tr(language, '等待城市和日期', 'Select a city and date')
        : tr(language, '暂无 DEB', 'No DEB yet')
  const resourceLabel = (label: string, state: Layer7ResourceState) => {
    if (state.status === 'loading') return tr(language, `${label}：加载中`, `${label}: loading`)
    if (state.status === 'error') return tr(language, `${label}：失败`, `${label}: failed`)
    if (state.status === 'empty') return tr(language, `${label}：暂无`, `${label}: empty`)
    if (state.status === 'idle') return tr(language, `${label}：等待`, `${label}: waiting`)
    if (state.refresh_error) return tr(language, `${label}：缓存`, `${label}: cached`)
    if (state.refreshing) return tr(language, `${label}：刷新中`, `${label}: refreshing`)
    return tr(language, `${label}：就绪`, `${label}: ready`)
  }
  const completeSetLastCost = !fallbackMode
    && displayItems.length > 1
    && displayItems.every(item => item.ask_available !== false && Number.isFinite(Number(item.ask)))
      ? displayItems.reduce((sum, item) => sum + Number(item.ask), 0)
      : null
  const completeSetFresh = completeSetLastCost !== null && displayItems.every(item => item.quote_fresh !== false)
  const completeSetCost = completeSetFresh ? completeSetLastCost : null
  const completeSetGap = completeSetCost === null ? null : 1 - completeSetCost

  return (
    <section className="min-w-0 overflow-hidden border border-[#2C3445] bg-[#161A22]" aria-label={tr(language, '当日最高温概率分布', 'Daily maximum temperature probability distribution')}>
      <div className="border-b border-[#2C3445] px-2 py-1.5">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="text-[10px] text-[#7D8694]">{tr(language, '当日最高温预测（DEB）', 'Daily Max Prediction (DEB)')}</div>
            <div className="mt-1 text-sm font-semibold text-[#F8FAFC]">
              {deb ? (
                <>
                  μ ± σ <span className="tabular-nums">{fmtDualTemp(forecastValue, unit)}</span>{' '}
                  <span className="mx-1 text-[#7D8694]">±</span>
                  <span className="tabular-nums">{fmtDualDelta(sigmaValue, unit)}</span>
                </>
              ) : (
                <span className={debState.status === 'error' ? 'text-red-300' : 'text-[#9AA4B2]'} title={debState.error}>
                  {debEmptyLabel}
                </span>
              )}
            </div>
            <div className="mt-0.5 truncate text-[10px] text-[#7D8694]" title={observedLabel}>
              {cityName || signal?.city_name || tr(language, '等待信号', 'Waiting for signal')} · {longDate(decision?.target_date ?? signal?.target_date ?? selectedDate)} · {observedLabel}
            </div>
            {deb?.mu_observed_floor_applied && effectiveMu !== null && effectiveMu !== undefined && (
              <div className="mt-0.5 text-[9px] text-amber-300" title="交易概率已按当日实测最高温排除不可能结果。">
                {tr(language, '概率中心', 'Probability center')} {fmtDualTemp(convertTempUnit(Number(effectiveMu), debUnit, unit), unit)} · {tr(language, '已应用实测最高温下限', 'observed-high floor applied')}
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-start gap-2 text-right">
            <div>
            <div className="text-[10px] text-[#CBD2DC]" title={debVersionLabel}>{tr(language, '多模型融合', 'Multi-model blend')}</div>
            {deb && debState.refresh_error ? (
              <div className="text-[9px] text-amber-300" title={debState.refresh_error}>{tr(language, '刷新失败 · 显示缓存', 'Refresh failed · cached')}</div>
            ) : deb && debState.refreshing ? (
              <div className="text-[9px] text-cyan-300">{tr(language, '刷新中 · 显示缓存', 'Refreshing · cached')}</div>
            ) : (
              <div className="text-[9px] text-[#7D8694]">{tr(language, '更新', 'Updated')} {debUpdatedLabel}</div>
            )}
            </div>
            <button
              type="button"
              onClick={() => setSourceDialogOpen(true)}
              className="inline-flex min-h-8 items-center gap-1 border border-[#2C3445] px-2 text-[10px] text-[#CBD2DC] hover:bg-[#222A37]"
              aria-label={tr(language, '查看 DEB 模型来源与权重', 'View DEB model sources and weights')}
            >
              <SlidersHorizontal className="h-3.5 w-3.5" />
              {tr(language, '模型来源', 'Sources')} {sourceRows.length}
            </button>
          </div>
        </div>
        {queryState?.aggregate_error && (
          <div
            className={`mt-1 truncate text-[9px] ${hasBlockingError ? 'text-red-300' : 'text-amber-300'}`}
            title={queryState.aggregate_error}
          >
            {hasBlockingError ? '读取异常' : '刷新异常'}：{queryState.aggregate_error}
          </div>
        )}
        {rejectedLegacyPrediction && (
          <div className="mt-1 text-[9px] text-amber-300" title={qualityReasons.join(', ')}>
            旧版预测缺少可审计的模型批次，已安全拒绝展示。
          </div>
        )}
        {(peakLockCandidate || buildWarnings.length > 0) && (
          <div className="mt-1 flex flex-wrap gap-1 text-[9px]">
            {peakLockCandidate && <span className="border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-200">{tr(language, '峰值可能已锁定', 'Peak may be locked')}</span>}
            {buildWarnings.length > 0 && (
              <button type="button" onClick={() => setSourceDialogOpen(true)} className="border border-[#2C3445] bg-[#1B212C] px-1.5 py-0.5 text-[#9AA4B2]">
                {tr(language, `数据提示 ${buildWarnings.length}`, `${buildWarnings.length} data notes`)}
              </button>
            )}
          </div>
        )}
      </div>
      <div className="border-b border-[#2C3445] px-2 py-1.5">
        <div className="flex items-center justify-between gap-2">
          <div className="text-[10px] text-[#7D8694]">{tr(language, '模型分布（高斯） / 结算概率（实况约束）', 'Gaussian model distribution / settlement probabilities')}</div>
          <div className="flex items-center gap-2 text-[9px] text-[#7D8694]">
            <span className={bucketState.status === 'error' ? 'text-red-300' : bucketState.refresh_error ? 'text-amber-300' : ''} title={bucketState.error ?? bucketState.refresh_error}>
              {resourceLabel(tr(language, '市场桶', 'Buckets'), bucketState)}
            </span>
            <span className={signalState.status === 'error' ? 'text-red-300' : signalState.refresh_error ? 'text-amber-300' : ''} title={signalState.error ?? signalState.refresh_error}>
              {resourceLabel(tr(language, '信号', 'Signals'), signalState)}
            </span>
            <span className={probabilityState.status === 'error' ? 'text-red-300' : probabilityState.refresh_error ? 'text-amber-300' : ''} title={probabilityState.error ?? probabilityState.refresh_error}>
              {resourceLabel(tr(language, '结算概率', 'Settlement probability'), probabilityState)}
            </span>
            {fallbackMode && <span className="text-amber-300">{tr(language, '模型分布', 'Model only')}</span>}
          </div>
        </div>
      </div>

      {chartRows.length === 0 ? (
        <div className="flex min-h-[220px] items-center justify-center px-3 text-center text-[10px] leading-relaxed text-neutral-600">
          {hasBlockingError
            ? tr(language, `读取失败：${queryState?.aggregate_error ?? debState.error ?? bucketState.error ?? signalState.error}`, `Load failed: ${queryState?.aggregate_error ?? debState.error ?? bucketState.error ?? signalState.error}`)
            : rejectedLegacyPrediction
              ? tr(language, '旧版预测缺少可审计的模型批次，无法生成可信概率分布。', 'The legacy forecast lacks an auditable model run, so no trusted distribution is shown.')
            : [debState, bucketState, probabilityState, signalState].some(state => state.status === 'loading')
              ? tr(language, '正在读取 DEB、市场桶和信号决策…', 'Loading DEB, market buckets, and signal decisions…')
              : tr(language, '该日期暂无概率分布。', 'No probability distribution for this date.')}
        </div>
      ) : (
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-2 overflow-hidden p-2">
          <div className="min-w-0">
            <div className="h-[280px] max-h-[300px] min-w-0 border border-[#2C3445] p-2">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartRows} margin={{ top: 8, right: 14, bottom: 12, left: -8 }}>
                <CartesianGrid stroke="#2C3445" strokeDasharray="3 3" />
                <XAxis dataKey="label" stroke="#7D8694" fontSize={9} tickLine={false} axisLine={false} interval={0} angle={-18} textAnchor="end" height={38} />
                <YAxis domain={[0, chartYAxisMax]} ticks={chartYAxisTicks} stroke="#7D8694" fontSize={10} tickLine={false} axisLine={false} tickFormatter={value => `${Number(value).toFixed(0)}%`} />
                <Tooltip
                  contentStyle={{ background: 'var(--tooltip-bg)', border: '1px solid var(--tooltip-border)', color: 'var(--tooltip-text)', fontSize: 11 }}
                  labelStyle={{ color: 'var(--tooltip-text)' }}
                  itemStyle={{ color: 'var(--tooltip-text)' }}
                  formatter={(value: any, name: any) => {
                    if (name === '模型概率' || name === 'Model probability') return [`${Number(value).toFixed(1)}%`, name]
                    return [`${Number(value).toFixed(1)}%`, name]
                  }}
                />
                <Bar dataKey="probabilityPct" name={tr(language, '模型概率', 'Model probability')} maxBarSize={44} radius={[0, 0, 0, 0]}>
                  {chartRows.map((row, index) => (
                    <Cell key={`${row.market_id || row.label || row.bucket_low || 'bucket'}-${index}`} fill={topBucketIndexes.has(index) ? '#2563EB' : '#4B5563'} stroke={row.is_signal ? '#22d3ee' : 'transparent'} strokeWidth={row.is_signal ? 1.5 : 0} />
                  ))}
                </Bar>
              </ComposedChart>
            </ResponsiveContainer>
            </div>
          </div>

          <aside className="min-w-0 overflow-hidden border border-[#2C3445] bg-[#161A22]">
            {bucketProbabilities?.observed_floor_applied_to_distribution && (
              <div className="border-b border-amber-500/20 bg-amber-500/5 px-2 py-1.5 text-[9px] text-amber-200">
                已按实测最高温排除 {bucketProbabilities.observed_floor_excluded_bucket_count ?? 0} 个不可能温度桶
              </div>
            )}
            <div className="flex items-center justify-between gap-2 border-b border-[#2C3445] px-2 py-1.5 text-[9px] text-[#9AA4B2]">
              <span title={tr(language, '概率优势 = 模型结算概率 - YES 卖一价。毛 EV/份与该差值数值相同，但尚未扣除费用、滑点和未成交风险；正数不等于可以买。', 'Probability advantage = model settlement probability - YES best ask. Gross EV/share has the same numeric value before fees, slippage, and fill risk; positive does not mean buy.')}>
                {tr(language, '模型概率与 YES 盘口', 'Model probability vs YES market')}
              </span>
              <span
                className={completeSetGap !== null && completeSetGap > 0 ? 'text-amber-300' : 'text-[#7D8694]'}
                title="同时买入全部互斥温度桶的毛成本；未扣费用，也未校验同等可成交深度。"
              >
                {completeSetCost === null
                  ? completeSetLastCost === null
                    ? tr(language, '全桶成本 --', 'All-bucket cost --')
                    : tr(language, `盘口已过期 · 最近全桶 ${(completeSetLastCost * 100).toFixed(1)}¢`, `Book stale · last all-bucket ${(completeSetLastCost * 100).toFixed(1)}¢`)
                  : tr(
                    language,
                    `全桶成本 ${(completeSetCost * 100).toFixed(1)}¢ · ${completeSetGap !== null && completeSetGap > 0 ? '毛价差候选' : '无完整集价差'}`,
                    `All-bucket cost ${(completeSetCost * 100).toFixed(1)}¢ · ${completeSetGap !== null && completeSetGap > 0 ? 'gross spread candidate' : 'no complete-set spread'}`,
                  )}
              </span>
            </div>
            <div className="overflow-x-auto p-2">
              <div className="flex min-w-max gap-1 text-[10px]">
                {displayItems.map((item, index) => {
                const alpha = alphaForItem(item)
                const quoteFresh = item.quote_fresh !== false
                const askAvailable = item.ask_available !== undefined ? item.ask_available : item.quote_valid !== false
                const bidAvailable = item.bid_available !== undefined ? item.bid_available : item.quote_valid !== false
                const edge = !askAvailable || !quoteFresh ? null : Number(item.probability_edge ?? item.ev ?? 0)
                const grossEvPerShare = edge
                const suggestedAmount = Number(item.position_size_usd ?? 0)
                const minimumShares = Number(item.order_min_size ?? 0)
                const meetsOrderMinimum = askAvailable
                  && Number(item.ask) > 0
                  && minimumShares > 0
                  && suggestedAmount / Number(item.ask) + 1e-9 >= minimumShares
                const tradeCandidate = Boolean(
                  item.quote_valid !== false
                  && quoteFresh
                  && item.paper_allowed
                  && item.paper_decision === 'buy'
                  && meetsOrderMinimum
                )
                const gateReason = item.blocked_reason_primary ?? item.gate_reasons?.[0] ?? ''
                const quoteState = !quoteFresh
                  ? tr(language, '盘口过期', 'Stale')
                  : !askAvailable
                    ? tr(language, '暂无卖盘', 'No ask')
                    : !bidAvailable
                      ? tr(language, '暂无买盘', 'No bid')
                      : tr(language, '观察', 'Watch')
                const title = [
                  item.question || fmtBucketAxisLabel(item, unit),
                  `bid/ask ${fallbackMode ? '--' : `${bidAvailable ? fmtPrice(item.bid) : '--'} / ${askAvailable ? fmtPrice(item.ask) : '--'}`}`,
                  edge === null
                    ? ''
                    : tr(language, `模型概率 - YES 卖一 = ${fmtSignedPp(edge)}`, `Model probability - YES best ask = ${fmtSignedPp(edge)}`),
                  tradeCandidate
                    ? tr(language, `买入候选 · ${item.strategy_name || '策略通过'}；执行前仍会复核盘口、深度、最小订单与 Kelly 金额。`, `Buy candidate · ${item.strategy_name || 'strategy passed'}; execution still rechecks quote, depth, minimum size, and Kelly sizing.`)
                    : tr(
                      language,
                      `观察 · ${!meetsOrderMinimum && item.paper_allowed ? 'Kelly 金额不足最小份额' : gateReason ? gateReasonLabel(gateReason, language) : quoteState}`,
                      `Watch · ${!meetsOrderMinimum && item.paper_allowed ? 'Kelly size below market minimum' : gateReason ? gateReasonLabel(gateReason, language) : quoteState}`,
                    ),
                ].join(' | ')
                const card = (
                  <>
                    <div className="flex items-start justify-between gap-1">
                      <span className="min-w-0 truncate font-semibold text-[#F8FAFC]">{fmtBucketAxisLabel(item, unit)}</span>
                      <span className="flex shrink-0 items-center gap-1">
                        {tradeCandidate
                          ? <span className="text-[9px] text-cyan-300" title={tr(language, '模型优势和策略条件已通过，执行时会再次复核盘口。', 'Model edge and strategy conditions passed; the quote is rechecked at execution.')}>{tr(language, '买入候选', 'Buy candidate')}</span>
                          : <span className="text-[9px] text-[#7D8694]">{quoteState}</span>}
                        {alpha ? <span title={alphaEventTitle(alpha)} className="text-amber-300">⚡</span> : null}
                        {item.event_url ? <ExternalLink className="h-3 w-3 text-[#7D8694]" aria-hidden="true" /> : null}
                      </span>
                    </div>
                    <div className={`mt-2 font-semibold tabular-nums ${quoteFresh ? 'text-base' : 'text-sm'} ${edge === null ? 'text-[#9AA4B2]' : tradeCandidate ? 'text-cyan-200' : edge < 0 ? 'text-red-300' : 'text-[#9AA4B2]'}`}>
                      {fallbackMode
                        ? '--'
                        : quoteFresh
                          ? edge === null ? '--' : fmtSignedPp(edge)
                          : fmtProb(item.probability)}
                    </div>
                    <div className="mt-1 flex justify-between gap-2 whitespace-nowrap text-[10px] tabular-nums text-[#7D8694]">
                      {quoteFresh ? (
                        <>
                          <span>{tr(language, '模型', 'Model')} {fmtProb(item.probability)}</span>
                          <span>{tr(language, '卖一', 'Ask')} {fallbackMode || !askAvailable ? '--' : fmtPrice(item.ask)}</span>
                        </>
                      ) : (
                        <>
                          <span>{tr(language, '旧卖一', 'Old ask')} {fallbackMode || !askAvailable ? '--' : fmtPrice(item.ask)}</span>
                          <span className="shrink-0">{tr(language, '差值 --', 'Edge --')}</span>
                        </>
                      )}
                    </div>
                    {edge !== null && quoteFresh && (
                      <div className="mt-1 text-[9px] tabular-nums text-[#9AA4B2]" title={tr(language, '每份 YES 在模型概率成立时的毛期望收益，未扣费用与滑点。', 'Gross expected profit per YES share before fees and slippage.')}>
                        {tr(language, '毛 EV/份', 'Gross EV/share')} {fmtSignedCents(grossEvPerShare)}
                      </div>
                    )}
                  </>
                )
                const key = `${item.market_id || item.bucket_key || item.bucket_label || `${item.bucket_low}-${item.bucket_high}` || 'bucket'}-${index}`
                const className = `market-bucket-card min-h-[104px] w-[144px] shrink-0 border p-2 transition-colors ${tradeCandidate ? 'border-cyan-500/50 bg-cyan-500/10 hover:border-cyan-300' : quoteFresh ? 'border-[#2C3445] bg-[#1B212C] hover:border-[#4B5563]' : 'border-[#252C38] bg-[#181D26] opacity-80 hover:opacity-100'}`
                return item.event_url ? (
                  <a key={key} href={item.event_url} target="_blank" rel="noreferrer" className={className} title={title} aria-label={tr(language, `打开 Polymarket：${item.question || fmtBucketAxisLabel(item, unit)}`, `Open Polymarket: ${item.question || fmtBucketAxisLabel(item, unit)}`)}>
                    {card}
                  </a>
                ) : (
                  <div key={key} className={className} title={title}>{card}</div>
                )
                })}
              </div>
            </div>
          </aside>
        </div>
      )}

      {(distribution?.notes?.length ?? 0) > 0 && (
        <details className="border-t border-neutral-900 px-2 py-1 text-[9px] text-neutral-600">
          <summary className="cursor-pointer select-none hover:text-neutral-400">{tr(language, '分布备注', 'Distribution notes')}</summary>
          <div className="mt-1 leading-relaxed">{distribution?.notes?.join(' · ')}</div>
        </details>
      )}
      {sourceDialogOpen && (
        <div
          className="deb-source-backdrop fixed inset-0 z-50 flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-label={tr(language, 'DEB 模型分析', 'DEB model analysis')}
          onMouseDown={event => {
            if (event.currentTarget === event.target) setSourceDialogOpen(false)
          }}
        >
          <section className="deb-source-dialog max-h-[88vh] w-full max-w-4xl overflow-hidden border border-[#2C3445] bg-[#161A22] shadow-2xl">
            <header className="border-b border-[#2C3445] px-4 py-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <div className="text-base font-semibold text-[#F8FAFC]">{tr(language, '模型分析', 'Model analysis')}</div>
                  <div className="group relative">
                    <button
                      type="button"
                      className="inline-flex h-6 w-6 items-center justify-center rounded-full text-[#7D8694] transition-colors hover:bg-[#222A37] hover:text-[#F8FAFC] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#3B82F6]"
                      aria-label={tr(language, '模型分析说明', 'About model analysis')}
                      aria-describedby="deb-model-analysis-help"
                    >
                      <HelpCircle className="h-4 w-4" />
                    </button>
                    <div
                      id="deb-model-analysis-help"
                      role="tooltip"
                      className="weatherbot-tooltip pointer-events-none invisible absolute left-0 top-full z-[70] mt-2 w-72 border p-3 text-[10px] leading-relaxed opacity-0 shadow-2xl transition-opacity group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
                    >
                      <p>{tr(language, '先看融合权重与误差，再查看每次预报如何修订。', 'Read blend weights and errors first, then inspect forecast revisions.')}</p>
                      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-t border-[#2C3445] pt-2 tabular-nums">
                        <dt className="text-[#7D8694]">{tr(language, '模型', 'Models')}</dt>
                        <dd>{sourceRows.length}</dd>
                        <dt className="text-[#7D8694]">{tr(language, '融合中心', 'Blend center')}</dt>
                        <dd>{fmtDualTemp(sourceDisagreement.center, unit)}</dd>
                        <dt className="text-[#7D8694]">{tr(language, '算法', 'Algorithm')}</dt>
                        <dd className="break-all font-mono text-[9px]">{debVersionLabel}</dd>
                      </dl>
                    </div>
                  </div>
                </div>
                <div className="ml-auto flex items-center gap-1.5">
                  <label className="inline-flex h-8 cursor-pointer items-center gap-2 border border-[#2C3445] px-2.5 text-[10px] text-[#CBD5E1] hover:bg-[#222A37]">
                    <input
                      type="checkbox"
                      checked={modelWeightMode === 'dynamic'}
                      disabled={modelWeightLoading || modelWeightSaving}
                      onChange={event => setModelWeightMode(event.target.checked ? 'dynamic' : 'manual')}
                      className="h-3.5 w-3.5 accent-[#2563EB]"
                    />
                    <span>{tr(language, '自动权重', 'Auto weights')}</span>
                  </label>
                  <button
                    type="button"
                    disabled={modelWeightLoading || modelWeightSaving}
                    onClick={saveModelWeights}
                    className="inline-flex h-8 w-8 items-center justify-center border border-[#2C3445] text-[#9AA4B2] hover:bg-[#222A37] hover:text-white disabled:opacity-40"
                    aria-label={modelWeightSaving ? tr(language, '保存中', 'Saving') : tr(language, '保存权重', 'Save weights')}
                    title={modelWeightSaving ? tr(language, '保存中', 'Saving') : tr(language, '保存权重', 'Save weights')}
                  >
                    <Save className="h-4 w-4" />
                  </button>
                  <button type="button" onClick={() => setSourceDialogOpen(false)} className="inline-flex h-8 w-8 shrink-0 items-center justify-center border border-[#2C3445] text-[#9AA4B2] hover:bg-[#222A37] hover:text-[#F8FAFC]" aria-label={tr(language, '关闭', 'Close')}>
                    <X className="h-4 w-4" />
                  </button>
                </div>
              </div>
              {modelWeightMessage && <div className={`mt-2 text-right text-[9px] ${modelWeightMessage.includes('失败') || modelWeightMessage.includes('fail') ? 'text-red-300' : 'text-emerald-300'}`}>{modelWeightMessage}</div>}
              <div className="mt-3 inline-flex border border-[#2C3445]" role="tablist" aria-label={tr(language, '模型分析视图', 'Model analysis view')}>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeSourceAnalysisView === 'disagreement'}
                  onClick={() => setSourceAnalysisView('disagreement')}
                  className={`min-h-9 px-4 text-[10px] font-medium ${activeSourceAnalysisView === 'disagreement' ? 'bg-[#2563EB] text-white' : 'text-[#9AA4B2] hover:bg-[#1B212C] hover:text-[#F8FAFC]'}`}
                >
                  {tr(language, '模型排名', 'Model ranking')}
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeSourceAnalysisView === 'history'}
                  disabled={!hasSourceHistory}
                  title={!hasSourceHistory ? tr(language, '至少需要两个真实预测批次', 'At least two real forecast runs are required') : undefined}
                  onClick={() => setSourceAnalysisView('history')}
                  className={`min-h-9 border-l border-[#2C3445] px-4 text-[10px] font-medium ${activeSourceAnalysisView === 'history' ? 'bg-[#2563EB] text-white' : 'text-[#9AA4B2] hover:bg-[#1B212C] hover:text-[#F8FAFC]'} disabled:cursor-not-allowed disabled:opacity-40`}
                >
                  {tr(language, '预测轨迹', 'Forecast paths')}
                </button>
              </div>
            </header>
            <div className="max-h-[calc(88vh-142px)] overflow-auto p-4">
              {activeSourceAnalysisView === 'history' ? (
                <section aria-label={tr(language, '逐模型预测轨迹', 'Per-model forecast paths')}>
                  <div className="flex items-center gap-2">
                    <div className="text-sm font-semibold text-[#F8FAFC]">{tr(language, '最高温预测轨迹', 'Daily-high forecast paths')}</div>
                    <div className="group relative">
                      <button
                        type="button"
                        className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[#7D8694] transition-colors hover:bg-[#222A37] hover:text-[#F8FAFC] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#3B82F6]"
                        aria-label={tr(language, '预测轨迹说明', 'About forecast paths')}
                        aria-describedby="deb-forecast-path-help"
                      >
                        <HelpCircle className="h-3.5 w-3.5" />
                      </button>
                      <div
                        id="deb-forecast-path-help"
                        role="tooltip"
                        className="weatherbot-tooltip pointer-events-none invisible absolute left-0 top-full z-[70] mt-2 w-72 border p-3 text-[10px] leading-relaxed opacity-0 shadow-2xl transition-opacity group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
                      >
                        <p>{tr(language, '每条线只使用已保存的真实模型批次，阶梯变化代表一次新预报修订。线越稳定，说明临近结算时修订越少；模型间的垂直距离代表分歧。', 'Each line uses persisted model runs. A step is a new revision; steadier lines mean fewer late revisions, and vertical distance shows disagreement.')}</p>
                        <p className="mt-2 border-t border-[#2C3445] pt-2 tabular-nums text-[#9AA4B2]">
                          {sourceHistory.points.length} {tr(language, '批次', 'runs')} · {sourceHistory.series.length} {tr(language, '模型', 'models')}
                        </p>
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-[10px] text-[#9AA4B2]">
                    {sourceHistory.series.map(series => (
                      <span key={series.key} className="inline-flex items-center gap-1.5">
                        <span className="h-0.5 w-4" style={{ backgroundColor: series.color }} />
                        <span className="font-medium uppercase">{series.label}</span>
                      </span>
                    ))}
                  </div>
                  <div className="mt-2 h-[300px] border-y border-[#2C3445] py-3">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={sourceHistory.points} margin={{ top: 8, right: 16, bottom: 8, left: 2 }}>
                        <CartesianGrid stroke="var(--border)" strokeDasharray="3 5" vertical={false} />
                        <XAxis
                          dataKey="issuedAtMs"
                          type="number"
                          scale="time"
                          domain={['dataMin', 'dataMax']}
                          stroke="#7D8694"
                          fontSize={9}
                          tickLine={false}
                          axisLine={false}
                          minTickGap={44}
                          tickFormatter={value => formatDebHistoryTime(value, language)}
                        />
                        <YAxis
                          domain={[sourceHistory.yMin ?? 0, sourceHistory.yMax ?? 1]}
                          stroke="#7D8694"
                          fontSize={9}
                          tickLine={false}
                          axisLine={false}
                          width={44}
                          tickFormatter={value => `${Number(value).toFixed(1)}°`}
                        />
                        <Tooltip
                          contentStyle={{ background: 'var(--tooltip-bg)', border: '1px solid var(--tooltip-border)', color: 'var(--tooltip-text)', fontSize: 10 }}
                          labelStyle={{ color: 'var(--tooltip-text)' }}
                          itemStyle={{ color: 'var(--tooltip-text)' }}
                          labelFormatter={value => `${tr(language, '发布', 'Issued')} ${formatDebHistoryTime(value, language)}`}
                          formatter={(value: any, name: any) => [fmtTemp(Number(value), unit), String(name).toUpperCase()]}
                        />
                        {sourceHistory.series.map(series => (
                          <Line
                            key={series.key}
                            type="stepAfter"
                            dataKey={series.key}
                            name={series.label}
                            stroke={series.color}
                            strokeWidth={2}
                            dot={false}
                            activeDot={{ r: 4, strokeWidth: 0 }}
                            connectNulls={false}
                            isAnimationActive={false}
                          />
                        ))}
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                </section>
              ) : sourceRows.length > 0 ? (
                <section aria-label={tr(language, '模型融合权重排名', 'Model blend ranking')}>
                  <div className="grid grid-cols-2 gap-px border border-[#2C3445] bg-[#2C3445] sm:grid-cols-4">
                    <div className="bg-[#1B212C] px-3 py-2.5">
                      <div className="text-[9px] text-[#7D8694]">{tr(language, '融合中心', 'Blend center')}</div>
                      <div className="mt-1 text-sm font-semibold tabular-nums text-[#F8FAFC]">{fmtDualTemp(sourceDisagreement.center, unit)}</div>
                    </div>
                    <div className="bg-[#1B212C] px-3 py-2.5">
                      <div className="text-[9px] text-[#7D8694]">{tr(language, '模型跨度', 'Model spread')}</div>
                      <div className="mt-1 text-sm font-semibold tabular-nums text-[#F8FAFC]">{fmtDualDelta(sourceDisagreement.spread, unit)}</div>
                    </div>
                    <div className="bg-[#1B212C] px-3 py-2.5">
                      <div className="text-[9px] text-[#7D8694]">{tr(language, '参与模型', 'Models in blend')}</div>
                      <div className="mt-1 text-sm font-semibold tabular-nums text-[#F8FAFC]">{sourceDisagreement.activeCount}/{sourceRows.length}</div>
                    </div>
                    <div className="bg-[#1B212C] px-3 py-2.5">
                      <div className="text-[9px] text-[#7D8694]">{tr(language, '误差可用', 'Auditable MAE')}</div>
                      <div className="mt-1 text-sm font-semibold tabular-nums text-[#F8FAFC]">{sourceRows.filter(row => row.mae !== null).length}/{sourceRows.length}</div>
                    </div>
                  </div>

                  <div className="mt-4 flex items-center gap-2">
                    <div className="text-sm font-semibold text-[#F8FAFC]">{tr(language, '融合权重排名', 'Blend weight ranking')}</div>
                    <div className="group relative">
                      <button
                        type="button"
                        className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[#7D8694] transition-colors hover:bg-[#222A37] hover:text-[#F8FAFC] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#3B82F6]"
                        aria-label={tr(language, '融合权重说明', 'About blend weights')}
                        aria-describedby="deb-blend-ranking-help"
                      >
                        <HelpCircle className="h-3.5 w-3.5" />
                      </button>
                      <div
                        id="deb-blend-ranking-help"
                        role="tooltip"
                        className="weatherbot-tooltip pointer-events-none invisible absolute left-0 top-full z-[70] mt-2 w-72 border p-3 text-[10px] leading-relaxed opacity-0 shadow-2xl transition-opacity group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
                      >
                        {tr(language, '权重决定模型对 DEB 的影响，不等同于单独准确率排名。误差只使用无泄漏预测与真实结算的配对样本；完成结算配对后才显示误差。', 'Weight controls influence on DEB, not standalone accuracy. Error uses leakage-safe forecast/truth pairs and appears only after settlement pairing.')}
                      </div>
                    </div>
                  </div>

                  <div className="mt-2 border-y border-[#2C3445]">
                    {sourceRows.map((row, index) => {
                      const modelColor = debModelColor(row.label, index)
                      const weightPct = row.weight === null ? null : Math.max(0, Math.min(100, row.weight * 100))
                      const weightFamily = modelWeightFamilyForLabel(row.label)
                      const editableWeightPct = weightFamily
                        ? Math.max(0, Math.min(100, (modelWeightMode === 'dynamic' ? Number(row.weight ?? modelWeights[weightFamily]) : modelWeights[weightFamily]) * 100))
                        : null
                      const maeDisplay = row.mae === null ? null : convertDeltaUnit(row.mae, 'C', unit)
                      return (
                        <article
                          key={row.key}
                          className="grid grid-cols-2 gap-3 border-b border-[#222A38] px-3 py-3 last:border-b-0 sm:grid-cols-[minmax(140px,1fr)_125px_minmax(180px,1.4fr)_140px]"
                          title={[row.role, `truth ${row.truthBasis}`, row.exclusionReason, row.warning].filter(Boolean).join(' · ')}
                        >
                          <div className="col-span-2 flex min-w-0 items-center gap-3 sm:col-span-1">
                            <span className="w-5 shrink-0 text-center text-xs font-semibold tabular-nums text-[#7D8694]">{index + 1}</span>
                            <span className="h-2.5 w-2.5 shrink-0" style={{ backgroundColor: modelColor }} />
                            <div className="min-w-0">
                              <div className="truncate text-sm font-semibold uppercase text-[#F8FAFC]">{row.label}</div>
                              <div className="mt-0.5 text-[9px] text-[#7D8694]">
                                {tr(language, '样本', 'samples')} n={row.calibrationSamples}
                              </div>
                            </div>
                          </div>
                          <div>
                            <div className="text-[9px] text-[#7D8694]">{tr(language, '预测最高', 'Daily high')}</div>
                            <div className="mt-1 text-xs font-semibold tabular-nums text-[#F8FAFC]">{fmtDualTemp(row.mu, unit)}</div>
                          </div>
                          <div>
                            <div className="flex items-center justify-between gap-2 text-[9px] text-[#7D8694]">
                              <span>{tr(language, '融合权重', 'Blend weight')}</span>
                              {weightFamily && editableWeightPct !== null ? (
                                <label className="inline-flex h-7 w-[84px] items-center border border-[#303A4C] bg-[#12161D] px-2 focus-within:border-[#3B82F6]" title={modelWeightMode === 'dynamic' ? tr(language, '修改后自动切换为自定义权重', 'Editing switches to custom weights') : undefined}>
                                  <input
                                    type="text"
                                    inputMode="decimal"
                                    value={editableWeightPct.toFixed(1)}
                                    disabled={modelWeightLoading || modelWeightSaving}
                                    onChange={event => editModelWeight(weightFamily, Math.max(0, Math.min(100, Number(event.target.value) || 0)) / 100)}
                                    className="min-w-0 flex-1 bg-transparent text-right text-[10px] font-semibold tabular-nums text-[#F8FAFC] outline-none disabled:opacity-50"
                                    aria-label={`${row.label} ${tr(language, '权重百分比', 'weight percent')}`}
                                  />
                                  <span className="ml-1 text-[9px] text-[#7D8694]">%</span>
                                </label>
                              ) : (
                                <span className="tabular-nums text-[#CBD2DC]">{weightPct === null ? '--' : `${weightPct.toFixed(1)}%`}</span>
                              )}
                            </div>
                            <div className="mt-2 h-1.5 bg-[#263044]">
                              <span className="block h-full" style={{ width: `${editableWeightPct ?? weightPct ?? 0}%`, backgroundColor: modelColor }} />
                            </div>
                          </div>
                          <div>
                            <div className="text-[9px] text-[#7D8694]">{tr(language, '近 7 日误差', '7-day MAE')}</div>
                            {maeDisplay === null ? (
                              <>
                                <div className="mt-1 text-xs font-medium text-[#9AA4B2]">{tr(language, '待真实结算', 'Pending truth')}</div>
                                <div className="mt-0.5 text-[9px] text-[#7D8694]">{tr(language, '暂无可审计误差', 'No auditable MAE yet')}</div>
                              </>
                            ) : (
                              <>
                                <div className="mt-1 text-xs font-semibold tabular-nums text-[#F8FAFC]">±{Number(maeDisplay).toFixed(2)}°{unit}</div>
                                <div className="mt-0.5 text-[9px] text-[#7D8694]">{tr(language, '越低越稳定', 'Lower is better')}</div>
                              </>
                            )}
                          </div>
                        </article>
                      )
                    })}
                  </div>
                </section>
              ) : (
                <div className="py-10 text-center text-[11px] text-[#7D8694]">
                  {tr(language, '暂无可比较的模型数据。', 'No comparable model data yet.')}
                </div>
              )}

              {buildWarnings.length > 0 && (
                <details className="mt-4 border-t border-[#2C3445] pt-3 text-[10px] text-[#9AA4B2]">
                  <summary className="cursor-pointer select-none hover:text-[#F8FAFC]">{tr(language, `数据说明（${buildWarnings.length}）`, `Data notes (${buildWarnings.length})`)}</summary>
                  <ul className="mt-2 space-y-1 font-mono text-[9px] leading-relaxed">
                    {buildWarnings.map(warning => <li key={warning} className="break-all">{warning}</li>)}
                  </ul>
                </details>
              )}
            </div>
          </section>
        </div>
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

function EvidenceBadge({ label, status, detail }: { label: string; status: EvidenceStatus; detail: string }) {
  return (
    <span className={`inline-flex max-w-full items-center gap-1 border px-1.5 py-0.5 text-[9px] ${statusClass(status)}`} title={detail}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${status === 'fresh' ? 'bg-green-300' : status === 'stale' ? 'bg-amber-300' : 'bg-red-300'}`} />
      <span className="shrink-0">{label}</span>
      <span className="max-w-[180px] truncate opacity-70">{detail}</span>
    </span>
  )
}
