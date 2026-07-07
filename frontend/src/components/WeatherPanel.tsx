import { useEffect, useMemo, useState } from 'react'
import {
  Area,
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
import { ExternalLink } from 'lucide-react'
import type { CityEvidenceDate, CityEvidenceDiffStatsSummary, DashboardEvent, DailyMaxPredictionSummary, DistributionItem, FetchLogRow, HistoricalWeatherPoint, MarketBucketSummary, ModelRepriceEvent, ProductionRefreshResult, SignalDecisionRecord, SignalDecisionSummary, WeatherCityPoint, WeatherCitySeries, WeatherForecast, WeatherSignal } from '../types'

interface Props {
  forecasts: WeatherForecast[]
  signals: WeatherSignal[]
  citySeries?: WeatherCitySeries[]
  events?: DashboardEvent[]
  fetchLog?: FetchLogRow[]
  productionRefresh?: ProductionRefreshResult | null
  marketBuckets?: MarketBucketSummary | null
  signalDecisions?: SignalDecisionSummary | null
  dailyMaxPrediction?: DailyMaxPredictionSummary | null
  alphaEvents?: ModelRepriceEvent[]
  layer7Loading?: boolean
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
  paper_allowed?: boolean
  live_allowed?: boolean
  quote_timestamp?: string | null
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

const gridColsMap: Record<number, string> = {
  6: 'grid-cols-6',
  7: 'grid-cols-7',
  8: 'grid-cols-8',
  9: 'grid-cols-9',
  10: 'grid-cols-10',
  11: 'grid-cols-11',
  12: 'grid-cols-12',
}

function bucketGridClass(count: number) {
  if (count <= 6) return gridColsMap[6]
  if (count >= 12) return gridColsMap[12]
  return gridColsMap[count] ?? gridColsMap[6]
}

function marketMid(item: LayerDistributionItem) {
  const bid = item.bid === null || item.bid === undefined ? null : Number(item.bid)
  const ask = item.ask === null || item.ask === undefined ? null : Number(item.ask)
  if (bid !== null && ask !== null && Number.isFinite(bid) && Number.isFinite(ask)) return (bid + ask) / 2
  if (ask !== null && Number.isFinite(ask)) return ask
  if (bid !== null && Number.isFinite(bid)) return bid
  return null
}

function alphaEventTitle(event?: ModelRepriceEvent) {
  if (!event) return ''
  const delta = event.delta_prob === null || event.delta_prob === undefined ? '--' : `${(Number(event.delta_prob) * 100).toFixed(1)}pp`
  return `ECMWF 06Z 更新后模型概率变化 ${delta}，市场未 reprice`
}

type WeatherWorkbenchTab = 'forecast' | 'metar' | 'historical' | 'diff' | 'fetch'

const WORKBENCH_TABS: Array<{ id: WeatherWorkbenchTab; label: string }> = [
  { id: 'forecast', label: '预报' },
  { id: 'metar', label: 'METAR' },
  { id: 'historical', label: '历史观测' },
  { id: 'diff', label: '偏差统计' },
  { id: 'fetch', label: '抓取日志' },
]

const CONTINENTS = ['全部', 'Americas', 'Europe', 'Asia', 'Pacific', 'Africa', 'Other'] as const
const HOUR_LABELS = Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, '0')}:00`)

function cityContinent(cityKey?: string, cityName?: string) {
  const value = `${cityKey || ''} ${cityName || ''}`.toLowerCase()
  if (/london|paris|munich|madrid|milan|amsterdam|warsaw|helsinki|moscow|istanbul|ankara/.test(value)) return 'Europe'
  if (/tokyo|seoul|shanghai|beijing|wuhan|singapore|taipei|hong|busan|chengdu|chongqing|guangzhou|jakarta|jeddah|karachi|kuala|lucknow|manila|qingdao|tel-aviv/.test(value)) return 'Asia'
  if (/sydney|wellington/.test(value)) return 'Pacific'
  if (/cape|lagos/.test(value)) return 'Africa'
  if (/new-york|nyc|chicago|miami|dallas|seattle|atlanta|toronto|sao|paulo|austin|denver|houston|los-angeles|san-francisco|mexico|panama|buenos/.test(value)) return 'Americas'
  return 'Other'
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

function fmtBucketLabel(raw?: string | null, fallback?: number | null, unit = 'F') {
  const fallbackNative =
    fallback === null || fallback === undefined || Number.isNaN(Number(fallback))
      ? null
      : unit === 'C'
        ? (Number(fallback) - 32) * 5 / 9
        : Number(fallback)
  if (!raw) return fmtTemp(fallbackNative, unit)
  const normalized = String(raw).trim()
  const tailMatch = normalized.match(/^\s*(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?\s+or\s+(below|above)\s*$/i)
  if (tailMatch) {
    const value = Number(tailMatch[1])
    const labelUnit = (tailMatch[2] || unit).toUpperCase()
    return `${fmtBucketTemp(value, labelUnit)} or ${tailMatch[3].toLowerCase()}`
  }
  const match = normalized.match(/^\s*(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?\s*[-–—]\s*(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?\s*$/i)
  if (!match) return normalized.replace(/掳/g, '°')
  const low = Number(match[1])
  const high = Number(match[3])
  const labelUnit = (match[4] || match[2] || unit).toUpperCase()
  if (low <= -900) return `${fmtBucketTemp(high, labelUnit)} or below`
  if (high >= 900) return `${fmtBucketTemp(low, labelUnit)} or above`
  return `${fmtBucketTemp(low, labelUnit)}–${fmtBucketTemp(high, labelUnit)}`
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

function fetchPulseDetail(fetchLog: FetchLogRow[], patterns: string[], fallback: string) {
  const rows = fetchLog
    .filter(row => fetchLogMatches(row, patterns))
    .sort((a, b) => String(b.time ?? '').localeCompare(String(a.time ?? '')))
  const latest = rows[0]
  if (!latest) return fallback
  const age = latest.time ? freshnessLabel(latest.time) : fallback
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

type DotShapeProps = {
  cx?: number
  cy?: number
  fill?: string
  stroke?: string
  active?: boolean
}

function SquareDot({ cx, cy, fill = '#EF4444', active = false }: DotShapeProps) {
  if (cx === undefined || cy === undefined) return null
  const size = active ? 8 : 6
  return <rect x={cx - size / 2} y={cy - size / 2} width={size} height={size} fill={fill} stroke="#FEE2E2" strokeWidth={active ? 1.5 : 1} />
}

function TriangleDot({ cx, cy, fill = '#A855F7', active = false }: DotShapeProps) {
  if (cx === undefined || cy === undefined) return null
  const size = active ? 7 : 5
  return <path d={`M ${cx} ${cy - size} L ${cx + size} ${cy + size} L ${cx - size} ${cy + size} Z`} fill={fill} stroke="#F3E8FF" strokeWidth={active ? 1.5 : 1} />
}

function HollowCircleDot({ cx, cy, stroke = '#3B82F6', active = false }: DotShapeProps) {
  if (cx === undefined || cy === undefined) return null
  return <circle cx={cx} cy={cy} r={active ? 5 : 3} fill="transparent" stroke={stroke} strokeWidth={active ? 2 : 1.5} />
}

function PeakReferenceLabel({ viewBox, value }: { viewBox?: { x?: number; y?: number }; value?: string }) {
  const x = viewBox?.x
  const y = viewBox?.y
  if (x === undefined || y === undefined || !value) return null
  const width = Math.max(64, value.length * 6.4)
  return (
    <g transform={`translate(${x - width / 2}, ${Math.max(0, y - 18)})`}>
      <rect width={width} height={16} rx={2} fill="#EC4899" />
      <text x={width / 2} y={11.5} fill="#FFFFFF" fontSize={10} textAnchor="middle">
        {value}
      </text>
    </g>
  )
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

function StatBadgeRow({ label, items, empty, tone = 'green' }: { label: string; items: Array<string | null>; empty: string; tone?: 'green' | 'orange' }) {
  const visible = items.filter((item): item is string => Boolean(item))
  const toneClass = tone === 'orange'
    ? 'border-orange-500/25 bg-orange-500/10 text-orange-200'
    : 'border-green-500/25 bg-green-500/10 text-green-200'
  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-[#2C3445] px-2 py-1.5 text-[10px]">
      <span className="min-w-[150px] text-[#7D8694]">{label}</span>
      {visible.length > 0 ? visible.map(item => (
        <span key={`${label}-${item}`} className={`rounded-full border px-2 py-0.5 tabular-nums ${toneClass}`}>
          {item}
        </span>
      )) : (
        <span className="text-[#7D8694]">{empty}</span>
      )}
    </div>
  )
}

function decisionBucket(decision?: SignalDecisionRecord) {
  return decision?.model_bucket_probs ?? {}
}

function decisionPrice(decision?: SignalDecisionRecord) {
  return asNumber(decision?.market_ask)
    ?? asNumber(decision?.model_bucket_probs?.best_ask)
    ?? asNumber(decision?.model_bucket_probs?.price)
    ?? asNumber(decision?.market_implied_probability)
    ?? asNumber(decision?.market_probability)
}

function decisionBid(decision?: SignalDecisionRecord) {
  return asNumber(decision?.market_bid) ?? asNumber(decision?.model_bucket_probs?.best_bid)
}

function decisionSpread(decision?: SignalDecisionRecord) {
  const ask = decisionPrice(decision)
  const bid = decisionBid(decision)
  if (ask === null || bid === null) return null
  return Math.max(0, ask - bid)
}

function layerDecisionRank(decision: SignalDecisionRecord) {
  const paper = decision.paper_allowed ? 1000 : 0
  const edge = asNumber(decision.edge) ?? asNumber(decision.model_bucket_probs?.edge) ?? -999
  return paper + edge
}

function bestLayerDecision(summary?: SignalDecisionSummary | null) {
  return [...(summary?.decisions ?? [])].sort((a, b) => layerDecisionRank(b) - layerDecisionRank(a))[0]
}

function findBucketForDecision(decision: SignalDecisionRecord | undefined, buckets?: MarketBucketSummary | null) {
  if (!decision) return undefined
  return (buckets?.latest ?? []).find(bucket => {
    if (decision.market_id && bucket.market_id === decision.market_id) return true
    if (decision.model_bucket_probs?.bucket_key && bucket.bucket_key === decision.model_bucket_probs.bucket_key) return true
    return false
  })
}

function buildLayerDistributionItems(buckets?: MarketBucketSummary | null, decisions?: SignalDecisionSummary | null): LayerDistributionItem[] {
  const decisionByMarket = new Map<string, SignalDecisionRecord>()
  const decisionByBucket = new Map<string, SignalDecisionRecord>()
  for (const decision of decisions?.decisions ?? []) {
    if (decision.market_id) decisionByMarket.set(String(decision.market_id), decision)
    const bucketKey = decision.model_bucket_probs?.bucket_key ?? decision.bucket_key
    if (bucketKey) decisionByBucket.set(String(bucketKey), decision)
  }

  return [...(buckets?.latest ?? [])]
    .sort((a, b) => {
      const low = Number(a.bucket_low ?? -999) - Number(b.bucket_low ?? -999)
      if (low !== 0) return low
      return Number(a.bucket_high ?? 999) - Number(b.bucket_high ?? 999)
    })
    .map(bucket => {
      const decision = decisionByMarket.get(String(bucket.market_id)) ?? decisionByBucket.get(String(bucket.bucket_key ?? ''))
      const modelBucket = decisionBucket(decision)
      const probability = asNumber(modelBucket.probability) ?? asNumber(decision?.model_probability) ?? 0
      const ask = asNumber(bucket.best_ask) ?? asNumber(modelBucket.best_ask) ?? asNumber(bucket.price) ?? 0
      const bid = asNumber(bucket.best_bid) ?? asNumber(modelBucket.best_bid) ?? 0
      const spread = asNumber(bucket.spread) ?? Math.max(0, ask - bid)
      const edge = asNumber(decision?.edge) ?? asNumber(modelBucket.edge) ?? (probability - ask)
      return {
        market_id: String(bucket.market_id),
        bucket_key: bucket.bucket_key ?? modelBucket.bucket_key,
        question: bucket.question ?? '',
        bucket_low: asNumber(bucket.bucket_low) ?? asNumber(modelBucket.bucket_low) ?? -999,
        bucket_high: asNumber(bucket.bucket_high) ?? asNumber(modelBucket.bucket_high) ?? 999,
        probability_raw: asNumber(modelBucket.probability_raw) ?? probability,
        probability,
        ask,
        bid,
        spread,
        probability_edge: edge,
        ev: edge,
        is_signal: Boolean(decision?.paper_allowed || (edge !== null && edge > 0)),
        bucket_label: bucket.bucket_label ?? modelBucket.bucket_label,
        bucket_direction: bucket.bucket_direction ?? modelBucket.bucket_direction,
        event_url: bucket.event_url ?? null,
        yes_token_id: bucket.yes_token_id ?? modelBucket.yes_token_id,
        order_min_size: bucket.order_min_size ?? decision?.order_min_size,
        tick_size: bucket.tick_size ?? decision?.tick_size,
        bid_depth: bucket.bid_depth,
        ask_depth: bucket.ask_depth,
        gate_status: decision?.gate_status,
        gate_reasons: decision?.gate_reasons ?? decision?.reasons ?? [],
        paper_allowed: decision?.paper_allowed,
        live_allowed: decision?.live_allowed,
        quote_timestamp: bucket.quote_timestamp,
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
  const bucketSize = unit === 'C'
    ? Math.max(0.5, Math.round((sigmaValue / 1.4) * 2) / 2)
    : Math.max(1, Math.round(sigmaValue / 1.4))
  const start = Math.floor((center - 3 * sigmaValue) / bucketSize) * bucketSize
  const raw = Array.from({ length: 9 }, (_, index) => {
    const low = start + index * bucketSize
    const high = low + bucketSize
    const probability = normalCdf((high - center) / sigmaValue) - normalCdf((low - center) / sigmaValue)
    return { low, high, probability }
  })
  const total = raw.reduce((sum, item) => sum + item.probability, 0)
  if (total <= 0) return []
  return raw.map((item, index) => ({
    market_id: `fallback-gaussian-${index}`,
    question: '暂无匹配市场桶，仅展示 DEB 高斯模型分布',
    bucket_low: item.low,
    bucket_high: item.high,
    probability_raw: item.probability,
    probability: item.probability / total,
    ask: 0,
    bid: 0,
    spread: 0,
    probability_edge: 0,
    ev: 0,
    is_signal: false,
    bucket_label: `${fmtBucketTemp(item.low, unit)}–${fmtBucketTemp(item.high, unit)}`,
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
  signalDecisions,
  dailyMaxPrediction,
  alphaEvents = [],
  layer7Loading = false,
  selectedCity,
  onSelectedCity,
  selectedDate: controlledSelectedDate,
  selectedDateEvidence,
  onSelectedDate,
  backfillResult,
}: Props) {
  const cities = useMemo(() => uniqueCities(citySeries, forecasts), [citySeries, forecasts])
  const [internalSelected, setInternalSelected] = useState(cities[0]?.key ?? '')
  const [internalSelectedDate, setInternalSelectedDate] = useState(() => {
    if (typeof window === 'undefined') return ''
    return new URLSearchParams(window.location.search).get('date') ?? ''
  })
  const [activeWorkbenchTab, setActiveWorkbenchTab] = useState<WeatherWorkbenchTab>('forecast')
  const [continentFilter, setContinentFilter] = useState<(typeof CONTINENTS)[number]>('全部')
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

  const filteredCities = useMemo(() => {
    if (continentFilter === '全部') return cities
    const rows = cities.filter(city => cityContinent(city.key, city.name) === continentFilter)
    if (selected && !rows.some(city => city.key === selected)) {
      const current = cities.find(city => city.key === selected)
      return current ? [current, ...rows] : rows
    }
    return rows
  }, [cities, continentFilter, selected])

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
  const layerDecision = useMemo(() => bestLayerDecision(signalDecisions), [signalDecisions])
  const layerDecisionBucket = decisionBucket(layerDecision)
  const layerDecisionMarketBucket = useMemo(() => findBucketForDecision(layerDecision, marketBuckets), [layerDecision, marketBuckets])
  const layerDistributionItems = useMemo(() => buildLayerDistributionItems(marketBuckets, signalDecisions), [marketBuckets, signalDecisions])
  const probabilityItems = layerDistributionItems.length > 0 ? layerDistributionItems : distributionChartItems
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
  const forecastPulseDetail = fetchPulseDetail(fetchLog, ['forecast', 'openmeteo', 'daily_max', 'predictor'], freshnessLabel(latestForecast?.timestamp))
  const metarPulseDetail = fetchPulseDetail(fetchLog, ['metar', 'asos'], freshnessLabel(latestMetar?.timestamp))
  const historyPulseDetail = fetchPulseDetail(fetchLog, ['historical', 'history', 'truth', 'actual'], latestHistory ? freshnessLabel(latestHistory.fetched_at ?? latestHistory.target_date) : '无数据')
  const truthTier = latestHistory?.calibration_tier === 'live_truth'
    ? '实盘 truth'
    : latestHistory?.calibration_tier === 'research_truth'
      ? '研究 truth'
      : 'truth 待补'

  useEffect(() => {
    const fallbackDate = availableDates[availableDates.length - 1] ?? forecastFallback?.target_date ?? latestForecast?.target_date ?? ''
    if (!selectedDate && fallbackDate) {
      setSelectedDate(fallbackDate)
    }
  }, [availableDates, forecastFallback?.target_date, latestForecast?.target_date, selectedDate])

  const selectedDateRow = chartData.find(row => row.date === selectedDate)
    ?? (selectedDate ? { date: selectedDate, label: shortDate(selectedDate) } : chartData[chartData.length - 1])
  const hasLayerDecision = Boolean(layerDecision)
  const layerPrice = decisionPrice(layerDecision)
  const layerSpread = decisionSpread(layerDecision)
  const layerProbability = asNumber(layerDecision?.model_probability) ?? asNumber(layerDecisionBucket.probability)
  const layerEdge = asNumber(layerDecision?.edge) ?? asNumber(layerDecisionBucket.edge)
  const layerBucketLabel = layerDecisionBucket.bucket_label ?? layerDecisionMarketBucket?.bucket_label
  const layerEventUrl = layerDecisionMarketBucket?.event_url
  const decisionLabel = hasLayerDecision
    ? layerDecision?.paper_allowed
      ? 'Paper ok'
      : '观察 / 跳过'
    : bestSignal?.actionable
      ? 'BUY YES'
      : bestSignal
        ? '观察'
        : '等待信号'
  const decisionTone = hasLayerDecision
    ? layerDecision?.paper_allowed ? 'green' : 'amber'
    : bestSignal?.actionable ? 'green' : bestSignal ? 'amber' : 'neutral'
  const decisionReason = hasLayerDecision
    ? (layerDecision?.gate_reasons?.[0] ?? layerDecision?.blocked_reason_primary ?? layerDecision?.gate_status ?? 'gate recorded')
    : bestSignal?.decision?.reasons?.[0] ?? bestSignal?.status ?? (bestSignal ? '未通过执行闸门' : '抓取后生成')
  const selectedForecast = dailyMaxPrediction?.latest?.mu ?? selectedDateRow?.forecast_high ?? latestForecast?.best ?? latestForecast?.ensemble_mean ?? forecastFallback?.mean_high
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
        No city evidence for this date.
      </div>
    )
  }

  return (
    <div className="min-h-full space-y-2 bg-transparent p-3 text-[11px] text-[#CBD2DC]">
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={continentFilter}
          onChange={event => setContinentFilter(event.target.value as (typeof CONTINENTS)[number])}
          className="min-w-[130px] border border-gray-200 bg-white px-2 py-1 text-gray-900 outline-none focus:border-gray-400"
          aria-label="按大洲筛选"
        >
          {CONTINENTS.map(continent => (
            <option key={continent} value={continent}>{continent}</option>
          ))}
        </select>
        <select
          value={cityKey}
          onChange={event => setSelected(event.target.value)}
          className="min-w-[180px] flex-1 border border-gray-200 bg-white px-2 py-1 text-gray-900 outline-none focus:border-gray-400"
          aria-label="选择城市"
        >
          {filteredCities.map(row => (
            <option key={row.key} value={row.key}>{row.name}</option>
          ))}
        </select>
        <div className="inline-flex shrink-0 items-center border border-neutral-800">
          <button
            type="button"
            onClick={() => setSelectedDate(addDateDays(selectedDate || todayDate, -1))}
            className="px-2 py-1 text-[10px] text-neutral-400 hover:bg-neutral-900 disabled:opacity-30"
          >
            前一天
          </button>
          <input
            type="date"
            value={selectedDate || todayDate}
            onChange={event => setSelectedDate(event.target.value)}
            className="w-[136px] border-x border-neutral-800 bg-black px-2 py-1 text-center text-[10px] tabular-nums text-neutral-200 outline-none"
            aria-label="选择日期"
          />
          <button
            type="button"
            onClick={() => setSelectedDate(addDateDays(selectedDate || todayDate, 1))}
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
      </div>

      <details className={`border px-2 py-1.5 text-[10px] ${decisionTone === 'green' ? 'border-green-500/30 bg-green-500/5' : decisionTone === 'amber' ? 'border-amber-500/30 bg-amber-500/5' : 'border-neutral-800 bg-black'}`}>
        <summary className="flex cursor-pointer select-none items-center justify-between gap-3 text-neutral-300 hover:text-neutral-100">
          <span className="min-w-0 truncate">
            选中日期判断：<span className={decisionTone === 'green' ? 'text-green-300' : decisionTone === 'amber' ? 'text-amber-300' : 'text-neutral-200'}>{decisionLabel}</span>
          </span>
          <span className="shrink-0 tabular-nums text-neutral-500">{hasLayerDecision ? fmtSignedPct(layerEdge) : bestSignal ? fmtSignedPct(bestSignal.probability_edge ?? bestSignal.edge) : '--'}</span>
        </summary>
        <div className="mt-2 grid gap-2 md:grid-cols-[1.2fr_repeat(4,minmax(0,1fr))_auto]">
          <div className="min-w-0">
            <div className="text-[10px] text-neutral-500">原因链</div>
            <div className="truncate text-[10px] text-neutral-600" title={decisionReason}>{decisionReason}</div>
          </div>
          <DecisionMetric
            label="推荐合约"
            value={hasLayerDecision ? fmtBucketLabel(layerBucketLabel, layerDecision?.bucket_lower, unit) : signalBucketLabel(bestSignal, unit)}
            sub={hasLayerDecision ? `${layerDecision?.gate_status ?? 'gate'} · ${longDate(layerDecision?.target_date ?? selectedDate)}` : bestSignal ? (isOpenTailBucket(bestSignal) ? '开放尾桶，需严控' : longDate(bestSignal.target_date)) : longDate(selectedDate)}
          />
          <DecisionMetric
            label="盘口"
            value={hasLayerDecision ? fmtPrice(layerPrice) : bestSignal?.limit_price !== undefined && bestSignal?.limit_price !== null ? fmtPrice(bestSignal.limit_price) : '--'}
            sub={hasLayerDecision ? `spread ${fmtPrice(layerSpread)} · min ${layerDecision?.order_min_size ?? '--'}` : bestSignal?.spread !== undefined && bestSignal?.spread !== null ? `spread ${fmtPrice(bestSignal.spread)}` : '等待盘口'}
          />
          <DecisionMetric
            label="模型 / Edge"
            value={hasLayerDecision ? fmtProb(layerProbability) : bestSignal ? fmtProb(bestSignal.calibrated_probability ?? bestSignal.model_probability) : '--'}
            sub={hasLayerDecision ? fmtSignedPct(layerEdge) : bestSignal ? fmtSignedPct(bestSignal.probability_edge ?? bestSignal.edge) : '无概率'}
          />
          <DecisionMetric label="预测-METAR" value={metarGap === null ? '--' : fmtTemp(metarGap, unit)} sub={`预测 ${fmtTemp(selectedForecast, unit)}`} />
          {(layerEventUrl || bestSignal?.event_url) ? (
            <a href={layerEventUrl || bestSignal?.event_url || undefined} target="_blank" rel="noreferrer" className="inline-flex min-h-9 items-center justify-center gap-1 border border-cyan-500/30 px-2 text-[10px] text-cyan-300 hover:bg-cyan-500/10">
              Polymarket <ExternalLink className="h-3 w-3" />
            </a>
          ) : (
            <span className="inline-flex min-h-9 items-center justify-center border border-neutral-800 px-2 text-[10px] text-neutral-600">无链接</span>
          )}
        </div>
      </details>

      <section className="border border-[#2C3445] bg-[#1B212C]">
        <div className="border-b border-[#2C3445]">
          <div className="flex flex-wrap items-center justify-between gap-2 px-2 py-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <EvidenceBadge label="预报" status={forecastStatus} detail={forecastPulseDetail} />
              <EvidenceBadge label="METAR" status={metarStatus} detail={metarPulseDetail} />
              <EvidenceBadge label="历史观测" status={historyStatus} detail={historyPulseDetail} />
            </div>
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
              dailyMaxPrediction={dailyMaxPrediction}
            />
            <TemperatureDistributionPanel
              signal={distributionSignal}
              decision={layerDecision}
              items={probabilityItems}
              unit={unit}
              selectedDate={selectedDate}
              actualHigh={selectedDateRow?.actual_high ?? latestHistory?.actual_high}
              cityName={series?.city_name ?? forecastFallback?.city_name ?? cityKey}
              dailyMaxPrediction={dailyMaxPrediction}
              alphaEvents={alphaEvents}
              loading={layer7Loading}
            />
            <ForecastDataTable rows={hourlyRows} unit={unit} selectedDate={selectedDate} />
          </div>
        )}

        {activeWorkbenchTab === 'metar' && (
          <div className="space-y-2 p-2">
            <MetarObservationTable rows={hourlyRows} unit={unit} selectedDate={selectedDate} />
            <details className="border border-[#2C3445] bg-[#161A22]">
              <summary className="cursor-pointer select-none px-2 py-2 text-xs text-[#CBD2DC] hover:bg-[#222A37]">
                METAR snapshots · {metarCards.length}
              </summary>
              <div className="border-t border-neutral-800">
                <EvidenceCards empty="No METAR snapshots for this date" items={metarCards} />
              </div>
            </details>
          </div>
        )}

        {activeWorkbenchTab === 'historical' && (
          <div className="space-y-2 p-2">
            <HistoricalHourlyObservationTable rows={hourlyRows} unit={unit} selectedDate={selectedDate} />
            <HistoricalObservationTable rows={historyRows} unit={unit} stationId={series?.station_id} />
            <details className="border border-[#2C3445] bg-[#161A22]">
              <summary className="cursor-pointer select-none px-2 py-2 text-xs text-[#CBD2DC] hover:bg-[#222A37]">
                Historical truth snapshots · {historyCards.length}
              </summary>
              <div className="border-t border-neutral-800">
                <EvidenceCards empty="No historical observations yet" items={historyCards} />
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
            />
            <details className="border border-[#2C3445] bg-[#161A22]">
              <summary className="cursor-pointer select-none px-2 py-2 text-xs text-[#CBD2DC] hover:bg-[#222A37]">
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
          <div className="p-2">
            <EventTimeline events={eventRows} fetchLog={fetchLog} productionRefresh={productionRefresh} />
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

function tabCopy(tab: WeatherWorkbenchTab) {
  const copy: Record<WeatherWorkbenchTab, { label: string }> = {
    forecast: { label: '预报' },
    metar: { label: 'METAR' },
    historical: { label: '历史' },
    diff: { label: '偏差统计' },
    fetch: { label: '抓取日志' },
  }
  return copy[tab]
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
      className={`min-w-[120px] shrink-0 border px-2 py-1.5 text-left rounded-none ${
        active
          ? 'border-[#2563EB] bg-[#2563EB] text-white'
          : 'border-[#2C3445] bg-[#161A22] text-[#7D8694] hover:bg-[#222A37] hover:text-[#CBD2DC]'
      }`}
      title={tabCopy(tab.id).label}
    >
      <div className="text-[11px] font-medium">{tabCopy(tab.id).label}</div>
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
                  No forecast rows for this date.
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
                  <td className="px-2 py-1 tabular-nums text-amber-300">{fmtPct(row.forecast_cloud_cover)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPrecip(row.precipitation)} / {fmtPct(row.precipitation_probability)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtWind(row.wind_speed, row.wind_direction)}</td>
                  <td className="max-w-[140px] truncate px-2 py-1 text-neutral-400" title={`${row.source || '--'} · ${row.horizon || '--'}`}>
                    {row.condition || row.source || row.horizon || '--'}
                  </td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPressure(row.pressure)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtTemp(row.dew_point, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{row.archive ? 'archive' : row.member_count ? `n ${row.member_count}` : '--'}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-500">{shortTime(row.timestamp)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-500">{shortHour(row.timestamp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function MetarObservationTable({ rows, unit, selectedDate }: { rows: HourlyWeatherRow[]; unit: string; selectedDate: string }) {
  const metarRows = rows.filter(row => row.metar !== null && row.metar !== undefined)
  const columns = ['Time', 'Observed', 'Forecast', 'Delta', 'Humidity', 'Cloud', 'Wx', 'Vis', 'Wind', 'Pres', 'Dew', 'Fetched']

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
          <table className="min-w-[1080px] w-full border-collapse text-left text-[10px]">
            <thead className="sticky top-0 bg-black text-neutral-500">
              <tr className="border-b border-neutral-900">
                {columns.map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={columns.length} className="px-2 py-12 text-center text-neutral-600">
                  No METAR observations for this date yet.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
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
              {metarRows.map(row => (
                <tr key={row.id} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                  <td className="px-2 py-1 tabular-nums text-neutral-300">{row.label}</td>
                  <td className="px-2 py-1 tabular-nums text-amber-300">{fmtTemp(row.metar, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-green-300">{fmtTemp(row.forecast, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-300">{fmtSignedTemp(row.gap, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPct(row.humidity)}</td>
                  <td className="px-2 py-1 tabular-nums text-amber-300">{fmtPct(row.cloud_cover)}</td>
                  <td className="max-w-[140px] truncate px-2 py-1 text-neutral-500" title={`${row.condition || row.source || '--'} · ${row.horizon || '--'}`}>{row.condition || row.source || '--'}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtVisibility(row.visibility)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtWind(row.wind_speed, row.wind_direction)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPressure(row.pressure)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtTemp(row.dew_point, unit)}</td>
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

function HistoricalHourlyObservationTable({ rows, unit, selectedDate }: { rows: HourlyWeatherRow[]; unit: string; selectedDate: string }) {
  const historicalRows = rows.filter(row => row.historical !== null && row.historical !== undefined)
  const columns = ['Time', 'Historical', 'Forecast', 'Delta', 'Humidity', 'Cloud', 'Wx', 'Vis', 'Wind', 'Pres', 'Dew', 'Fetched']

  return (
    <section className="min-w-0 border border-neutral-800 bg-black">
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-2 py-1.5">
        <div>
          <div className="text-[10px] text-neutral-500">Historical hourly</div>
          <div className="text-xs text-neutral-100">{longDate(selectedDate)} · {historicalRows.length} rows</div>
        </div>
        <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500">display-only research</span>
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
                  No historical hourly observations for this date. Run Open-Meteo history backfill to populate this table.
                </td>
              </tr>
            ) : historicalRows.map(row => {
              const delta = row.forecast !== null && row.forecast !== undefined && row.historical !== null && row.historical !== undefined
                ? Number(row.historical) - Number(row.forecast)
                : null
              return (
                <tr key={`historical-${row.id}`} className="border-b border-neutral-900/80 hover:bg-neutral-900/50">
                  <td className="px-2 py-1 tabular-nums text-neutral-300">{row.label}</td>
                  <td className="px-2 py-1 tabular-nums text-green-300">{fmtTemp(row.historical, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-blue-300">{fmtTemp(row.forecast, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-300">{fmtSignedTemp(delta, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPct(row.humidity)}</td>
                  <td className="px-2 py-1 tabular-nums text-amber-300">{fmtPct(row.cloud_cover)}</td>
                  <td className="max-w-[140px] truncate px-2 py-1 text-neutral-500" title={`${row.condition || row.source || '--'} · ${row.horizon || '--'}`}>{row.condition || row.source || '--'}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtVisibility(row.visibility)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtWind(row.wind_speed, row.wind_direction)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtPressure(row.pressure)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtTemp(row.dew_point, unit)}</td>
                  <td className="px-2 py-1 tabular-nums text-neutral-500">{shortTime(row.timestamp)}</td>
                </tr>
              )
            })}
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
}: {
  rows: HourlyWeatherRow[]
  chartData: WeatherChartRow[]
  unit: string
  selectedDate: string
  evidenceSummary?: CityEvidenceDiffStatsSummary
}) {
  const hourlyPairs = rows
    .filter(row => row.forecast !== null && row.forecast !== undefined && row.metar !== null && row.metar !== undefined)
    .map(row => ({
      id: row.id,
      time: row.label,
      observed: Number(row.metar),
      forecast: Number(row.forecast),
      delta: Number(row.metar) - Number(row.forecast),
      cloud_cover: row.cloud_cover,
      condition: row.condition,
      wind_speed: row.wind_speed,
      wind_direction: row.wind_direction,
      pressure: row.pressure,
      dew_point: row.dew_point,
      fetched_sys: row.timestamp,
      fetched_local: row.timestamp,
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
  const tableRows = hourlyPairs.length > 0 ? hourlyPairs : dailyPairs.slice(-30).reverse()
  const diffColumns = ['Time', 'Temp', 'Cloud', 'Wx', 'Vis', 'Wind', 'Pres', 'Dew', 'Fetched (Sys)', 'Fetched (Local)']
  const deltas = tableRows.map(row => row.delta)
  const avgDelta = mean(deltas)
  const correlation = pearsonR(
    tableRows.map(row => row.forecast),
    tableRows.map(row => row.observed)
  )
  const maxAbsDelta = Math.max(1, ...deltas.map(delta => Math.abs(delta)))
  const summaryCount = evidenceSummary?.count ?? tableRows.length
  const summaryAvgDelta = evidenceSummary?.avg_delta ?? avgDelta
  const summaryMae = evidenceSummary?.mae ?? (deltas.length ? mean(deltas.map(delta => Math.abs(delta))) : null)
  const summaryPearson = evidenceSummary?.pearson_r ?? correlation
  const summaryOverlap = evidenceSummary?.overlap_ratio
  const summaryOverlapLabel = summaryOverlap === null || summaryOverlap === undefined
    ? (summaryCount ? `${summaryCount}` : '--')
    : fmtProb(summaryOverlap)
  const summaryOverlapSub = summaryOverlap === null || summaryOverlap === undefined
    ? 'paired samples'
    : `${evidenceSummary?.overlap_count ?? 0}/${Math.max(evidenceSummary?.metar_hours ?? 0, evidenceSummary?.forecast_hours ?? 0, 1)} hours`
  const historyMetarOverlap = evidenceSummary?.historical_metar_overlap_ratio

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
        <MetricCard label="Average Delta" value={fmtSignedTemp(summaryAvgDelta, unit)} sub="Observed - Forecast" />
        <MetricCard label="MAE" value={summaryMae === null ? '--' : fmtTemp(summaryMae, unit)} sub="mean abs error" />
        <MetricCard label="Accuracy" value={fmtPearson(summaryPearson)} sub="Pearson R" />
        <MetricCard label="Overlap" value={summaryOverlapLabel} sub={summaryOverlapSub} />
        <MetricCard label="Hist↔METAR" value={historyMetarOverlap === null || historyMetarOverlap === undefined ? '--' : fmtProb(historyMetarOverlap)} sub={`${evidenceSummary?.historical_metar_overlap_count ?? 0} hrs`} />
        <MetricCard label="Max Abs Delta" value={fmtTemp(Math.max(0, ...deltas.map(delta => Math.abs(delta))), unit)} sub="worst visible row" />
      </div>
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
                  No paired observed/forecast rows yet.
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
                    <td className="px-2 py-1 tabular-nums text-amber-300">{fmtPct(row.cloud_cover)}</td>
                    <td className="max-w-[120px] truncate px-2 py-1 text-neutral-400" title={row.condition || row.source}>{row.condition || row.source || '--'}</td>
                    <td className="px-2 py-1 tabular-nums text-neutral-500">--</td>
                    <td className="px-2 py-1 tabular-nums text-neutral-400">{fmtWind(row.wind_speed, row.wind_direction)}</td>
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

function HourlyEvidencePanel({
  rows,
  unit,
  cityName,
  selectedDate,
  dailyMaxPrediction,
}: {
  rows: HourlyWeatherRow[]
  unit: string
  cityName?: string
  selectedDate: string
  dailyMaxPrediction?: DailyMaxPredictionSummary | null
}) {
  const numericValues = (values: unknown[]) =>
    values.map(asNumber).filter((value): value is number => value !== null)
  const forecastValues = numericValues(rows.map(row => row.forecast))
  const metarValues = numericValues(rows.map(row => row.metar))
  const historicalValues = numericValues(rows.map(row => row.historical))
  const chinaLiveValues = numericValues(rows.map(row => row.china_live))
  const pwsValues = numericValues(rows.map(row => row.pws))
  const forecastMax = forecastValues.length > 0 ? Math.max(...forecastValues) : null
  const metarMax = metarValues.length > 0 ? Math.max(...metarValues) : null
  const chartRows = rows.map(row => ({
    ...row,
    forecast_value: asNumber(row.forecast),
    metar_value: asNumber(row.metar),
    historical_value: asNumber(row.historical),
    china_live_value: asNumber(row.china_live),
    pws_value: asNumber(row.pws),
    gap_value: asNumber(row.gap),
    cloud_pct: asNumber(row.forecast_cloud_cover),
  }))
  const hasChartEvidence = chartRows.some(row =>
    row.forecast_value !== null
    || row.metar_value !== null
    || row.historical_value !== null
    || row.china_live_value !== null
    || row.pws_value !== null
    || row.cloud_pct !== null
  )
  const metarStats = sourceStats(chartRows, 'metar_value')
  const historicalStats = sourceStats(chartRows, 'historical_value')
  const overlapStats = overlapPill(chartRows)
  const hasHistorical = historicalValues.length > 0
  const hasChinaLive = chinaLiveValues.length > 0
  const hasPws = pwsValues.length > 0
  const peakRow = chartRows
    .filter(row => row.forecast_value !== null)
    .sort((a, b) => Number(b.forecast_value ?? -Infinity) - Number(a.forecast_value ?? -Infinity))[0]
  const peakHour = dailyMaxPeakHour(dailyMaxPrediction, peakRow?.label)
  if (rows.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-4 text-center text-neutral-600">
        No hourly rows for this date.
      </div>
    )
  }
  if (!hasChartEvidence) {
    return (
      <section className="min-h-0 border border-[#2C3445] bg-[#161A22]">
        <div className="border-b border-[#2C3445] px-2 py-1.5">
          <div className="text-[10px] text-[#7D8694]">Hourly Temperature</div>
          <div className="text-xs text-[#CBD2DC]">{cityName || '褰撳墠鍩庡競'} 路 {longDate(selectedDate)}</div>
        </div>
        <div className="flex min-h-[260px] items-center justify-center p-4 text-center text-xs text-[#7D8694]">
          当前日期存在小时记录，但没有可绘制的温度、METAR 或云量字段。请查看抓取日志，或重新抓取当前城市/日期。
        </div>
      </section>
    )
  }

  return (
    <section className="min-h-0 border border-[#2C3445] bg-[#161A22]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#2C3445] px-2 py-1.5">
        <div>
          <div className="text-[10px] text-[#7D8694]">Hourly Temperature</div>
          <div className="text-xs text-[#CBD2DC]">{cityName || '当前城市'} · {longDate(selectedDate)}</div>
        </div>
        <div className="flex flex-wrap gap-1 text-[9px] text-[#7D8694]">
          <span className="border border-[#2C3445] px-1.5 py-0.5">预报最高 {fmtTemp(forecastMax, unit)}</span>
          <span className="border border-[#2C3445] px-1.5 py-0.5">METAR最高 {fmtTemp(metarMax, unit)}</span>
          <span className="border border-[#2C3445] px-1.5 py-0.5">峰值 {peakRow?.label ?? '--'}</span>
        </div>
      </div>

      <div
        className="p-2"
        role="img"
        aria-label={`${cityName || '当前城市'}逐小时温度图：METAR 为橙色实线，历史为绿色实线，中国实况为红色方块，PWS 为紫色三角，预报为蓝色虚线空心点，云量为灰色面积，峰值用粉色竖线标记。`}
      >
        <div className="mb-2 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-[10px] text-[#7D8694]">
          <span className={`inline-flex items-center gap-1 ${hasChinaLive ? '' : 'opacity-45'}`}><span className="h-2.5 w-2.5 bg-[#EF4444]" />中国实况</span>
          <span className={`inline-flex items-center gap-1 ${hasPws ? '' : 'opacity-45'}`}><span className="h-0 w-0 border-x-[5px] border-b-[9px] border-x-transparent border-b-[#A855F7]" />PWS（实时）</span>
          <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-[#F97316]" />METAR（本地时）</span>
          <span className={`inline-flex items-center gap-1 ${hasHistorical ? '' : 'opacity-45'}`}><span className="h-2.5 w-2.5 rounded-full bg-[#22C55E]" />历史（本地时）</span>
          <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full border border-[#3B82F6]" />预报（本地时）</span>
          <span className="inline-flex items-center gap-1"><span className="h-2.5 w-3 bg-[#94A3B8]/30" />云量 %</span>
        </div>
        <div className="relative h-[320px]">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={chartRows} margin={{ top: 22, right: 18, bottom: 0, left: -6 }}>
              <CartesianGrid stroke="#2C3445" strokeDasharray="3 3" />
              <XAxis dataKey="label" ticks={HOUR_LABELS} interval={0} stroke="#7D8694" fontSize={9} tickLine={false} axisLine={false} minTickGap={0} height={34} />
              <YAxis yAxisId="temp" stroke="#7D8694" fontSize={10} tickLine={false} axisLine={false} tickFormatter={value => `${Number(value).toFixed(0)}°${unit}`} />
              <YAxis yAxisId="percent" orientation="right" domain={[0, 100]} stroke="#475569" fontSize={10} tickLine={false} axisLine={false} tickFormatter={value => `${Number(value).toFixed(0)}%`} />
              <Tooltip
                contentStyle={{ background: '#1B212C', border: '1px solid #2C3445', color: '#CBD2DC', fontSize: 11 }}
                formatter={(value: any, name: any) => {
                  if (name === '云量 %') return [`${Number(value).toFixed(0)}%`, name]
                  return [fmtTemp(Number(value), unit), name]
                }}
                labelFormatter={(_, payload) => payload?.[0]?.payload?.timestamp ? shortTime(payload[0].payload.timestamp) : ''}
              />
              <Area yAxisId="percent" type="monotone" dataKey="cloud_pct" name="云量 %" stroke="#94A3B8" fill="#94A3B8" fillOpacity={0.25} strokeOpacity={0.65} connectNulls={false} />
              <Line yAxisId="temp" type="monotone" dataKey="metar_value" name="METAR" stroke="#F97316" dot={{ r: 3, fill: '#F97316', stroke: '#F97316', strokeWidth: 1 }} activeDot={{ r: 5 }} strokeWidth={2} connectNulls={false} />
              <Line yAxisId="temp" type="monotone" dataKey="historical_value" name="历史" stroke="#22C55E" dot={{ r: 3, fill: '#22C55E', stroke: '#22C55E', strokeWidth: 1 }} activeDot={{ r: 5 }} strokeWidth={2} connectNulls={false} />
              {hasChinaLive && (
                <Line yAxisId="temp" type="monotone" dataKey="china_live_value" name="中国实况" stroke="#EF4444" dot={<SquareDot fill="#EF4444" />} activeDot={<SquareDot fill="#EF4444" active />} strokeWidth={2} connectNulls={false} />
              )}
              {hasPws && (
                <Line yAxisId="temp" type="monotone" dataKey="pws_value" name="PWS" stroke="#A855F7" dot={<TriangleDot fill="#A855F7" />} activeDot={<TriangleDot fill="#A855F7" active />} strokeWidth={2} connectNulls={false} />
              )}
              <Line yAxisId="temp" type="monotone" dataKey="forecast_value" name="预报" stroke="#3B82F6" strokeDasharray="4 4" dot={<HollowCircleDot stroke="#3B82F6" />} activeDot={<HollowCircleDot stroke="#3B82F6" active />} strokeWidth={2} connectNulls={false} />
              {peakHour && (
                <ReferenceLine
                  yAxisId="temp"
                  x={peakHour}
                  stroke="#EC4899"
                  strokeDasharray="4 4"
                  label={<PeakReferenceLabel value={`peak ${peakHour}`} />}
                />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      <StatBadgeRow label="AVG Δ (OBS−FC)" items={[statDeltaPill('METAR', metarStats, unit), statDeltaPill('Historical', historicalStats, unit)]} empty="No diff stats yet" tone="green" />
      <StatBadgeRow label="ACCURACY (PEARSON R)" items={[statAccuracyPill('METAR', metarStats), statAccuracyPill('Historical', historicalStats)]} empty="No accuracy stats yet" tone="orange" />
      <StatBadgeRow label="HIST↔METAR OVERLAP" items={[overlapStats]} empty="No overlap data yet" tone="green" />

    </section>
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
                <DetailLine label="用途" value="这是研究/校准视图，实盘仍需独立 truth 样本和回放闸门通过。" wide />
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
  const productionRows: NormalizedFetchLogRow[] = productionRefresh?.stages?.map((stage, index) => {
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
  const stageGroup = (stage: string) => {
    const lower = stage.toLowerCase()
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
          <div className="text-[10px] text-neutral-500">Fetch Log (last 100)</div>
          <div className="text-xs text-neutral-100">{rows.length} events</div>
        </div>
        <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] text-neutral-500"># / Time / Source / Status / Duration / Message</span>
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(100px,1fr))] gap-1 border-b border-neutral-900 p-2 text-[10px]">
        {[
          ['weather', '天气'],
          ['observation', '观测'],
          ['orderbook', '盘口'],
          ['signal', '信号'],
          ['system', '系统'],
        ].map(([stage, label]) => {
          const count = rows.filter(row => stageGroup(row.stage) === stage).length
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
                {['#', 'Time', 'Source', 'Status', 'Duration', 'Message'].map(column => (
                  <th key={column} className="px-2 py-1 font-normal">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={6} className="px-2 py-12 text-center text-neutral-600">
                  No log entries.
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
  cityName,
  dailyMaxPrediction,
  alphaEvents = [],
  loading = false,
}: {
  signal?: WeatherSignal
  decision?: SignalDecisionRecord
  items: LayerDistributionItem[]
  unit: string
  selectedDate: string
  actualHigh?: number | null
  cityName?: string
  dailyMaxPrediction?: DailyMaxPredictionSummary | null
  alphaEvents?: ModelRepriceEvent[]
  loading?: boolean
}) {
  const distribution = signal?.distribution
  const deb = dailyMaxPrediction?.latest
  const debUnit = deb?.unit || unit
  const forecastValue = deb?.mu !== null && deb?.mu !== undefined
    ? convertTempUnit(Number(deb.mu), debUnit, unit)
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
  const fallbackItems = items.length === 0 && deb ? buildGaussianFallbackItems(forecastValue, sigmaValue, unit) : []
  const displayItems = items.length > 0 ? items : fallbackItems
  const fallbackMode = items.length === 0 && fallbackItems.length > 0
  const chartRows = displayItems.map(item => ({
    ...item,
    label: fmtBucketAxisLabel(item, unit),
    probabilityPct: Number(item.probability ?? 0) * 100,
    edgePct: Number(item.probability_edge ?? item.ev ?? 0) * 100,
  }))
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
  const observedLabel = actualHigh === null || actualHigh === undefined
    ? '实测 --'
    : `实测 ${fmtDualTemp(actualHigh, unit)} (metar, ${deb?.member_count ?? '--'} 样本)`
  const debVersionLabel = deb?.deb_version || distributionMethod || 'DEB-v1'
  const debUpdatedLabel = freshnessLabel(deb?.updated_at ?? deb?.issued_at)

  return (
    <section className="border border-[#2C3445] bg-[#161A22]" aria-label="当日最高温概率分布">
      <div className="border-b border-[#2C3445] px-2 py-1.5">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="text-[10px] text-[#7D8694]">Daily Max Prediction (DEB)</div>
            <div className="mt-1 text-sm font-semibold text-[#F8FAFC]">
              μ ± σ <span className="tabular-nums">{fmtDualTemp(forecastValue, unit)}</span>{' '}
              <span className="mx-1 text-[#7D8694]">±</span>
              <span className="tabular-nums">{fmtDualDelta(sigmaValue, unit)}</span>
            </div>
            <div className="mt-0.5 truncate text-[10px] text-[#7D8694]" title={observedLabel}>
              {cityName || signal?.city_name || '等待信号'} · {longDate(decision?.target_date ?? signal?.target_date ?? selectedDate)} · {observedLabel}
            </div>
          </div>
          <div className="shrink-0 text-right">
            <div className="text-[10px] text-[#CBD2DC]">{debVersionLabel}</div>
            <div className="text-[9px] text-[#7D8694]">更新 {debUpdatedLabel}</div>
          </div>
        </div>
      </div>

      <div className="border-b border-[#2C3445] px-2 py-1.5">
        <div className="flex items-center justify-between gap-2">
          <div className="text-[10px] text-[#7D8694]">Probability buckets (Gaussian)</div>
          {fallbackMode && <div className="text-[9px] text-amber-300">暂无匹配市场桶 · 模型分布</div>}
        </div>
      </div>

      {displayItems.length === 0 ? (
        <div className="flex min-h-[220px] items-center justify-center px-3 text-center text-[10px] leading-relaxed text-neutral-600">
          {loading ? '正在加载 DEB / 市场桶...' : 'No probability buckets for this date.'}
        </div>
      ) : (
        <div className="grid gap-2 p-2">
          <div className="h-[260px] max-h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartRows} margin={{ top: 8, right: 14, bottom: 12, left: -8 }}>
                <CartesianGrid stroke="#2C3445" strokeDasharray="3 3" />
                <XAxis dataKey="label" stroke="#7D8694" fontSize={9} tickLine={false} axisLine={false} interval={0} angle={-18} textAnchor="end" height={38} />
                <YAxis domain={[0, 25]} ticks={[0, 5, 10, 15, 20, 25]} stroke="#7D8694" fontSize={10} tickLine={false} axisLine={false} tickFormatter={value => `${Number(value).toFixed(0)}%`} />
                <Tooltip
                  contentStyle={{ background: '#1B212C', border: '1px solid #2C3445', color: '#CBD2DC', fontSize: 11 }}
                  formatter={(value: any, name: any) => {
                    if (name === '模型概率') return [`${Number(value).toFixed(1)}%`, name]
                    if (name === '卖一') return [`${Number(value).toFixed(1)}¢`, name]
                    return [`${Number(value).toFixed(1)}%`, name]
                  }}
                />
                <Bar dataKey="probabilityPct" name="模型概率" maxBarSize={36} radius={[0, 0, 0, 0]}>
                  {chartRows.map((row, index) => (
                    <Cell key={`${row.market_id || row.label || row.bucket_low || 'bucket'}-${index}`} fill={topBucketIndexes.has(index) ? '#2563EB' : '#4B5563'} stroke={row.is_signal ? '#22d3ee' : 'transparent'} strokeWidth={row.is_signal ? 1.5 : 0} />
                  ))}
                </Bar>
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <aside className="border border-[#2C3445] bg-[#161A22]">
            <div className={`grid gap-1 p-2 text-[10px] ${bucketGridClass(displayItems.length)}`}>
              {displayItems.map((item, index) => {
                const edge = Number(item.probability_edge ?? item.ev ?? 0)
                const alpha = alphaForItem(item)
                const mid = marketMid(item)
                return (
                  <div
                    key={`${item.market_id || item.bucket_key || item.bucket_label || `${item.bucket_low}-${item.bucket_high}` || 'bucket'}-${index}`}
                    className={`min-h-[112px] border p-2 ${item.is_signal ? 'border-cyan-500/40 bg-cyan-500/10' : 'border-[#2C3445] bg-[#1B212C]'} ${Math.abs(edge) > 0.08 ? 'animate-pulse shadow-[0_0_0_1px_rgba(34,197,94,0.25)]' : ''}`}
                    title={`${item.question || fmtBucketAxisLabel(item, unit)} | bid/ask ${fallbackMode ? '--' : `${fmtPrice(item.bid)} / ${fmtPrice(item.ask)}`}`}
                  >
                    <div className="flex items-start justify-between gap-1">
                      <span className="min-w-0 truncate font-semibold text-[#F8FAFC]">{fmtBucketAxisLabel(item, unit)}</span>
                      {alpha ? <span title={alphaEventTitle(alpha)} className="shrink-0 text-amber-300">⚡</span> : null}
                    </div>
                    <div className={`mt-2 text-lg font-semibold tabular-nums ${edge >= 0 ? 'text-green-300' : 'text-red-300'}`}>
                      {fallbackMode ? '--' : fmtSignedPct(edge)}
                    </div>
                    <div className="mt-1 flex justify-between gap-2 text-[10px] tabular-nums text-[#7D8694]">
                      <span>model {fmtProb(item.probability)}</span>
                      <span>market {fallbackMode ? '--' : fmtPrice(mid)}</span>
                    </div>
                  </div>
                )
              })}
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

function EvidenceBadge({ label, status, detail }: { label: string; status: EvidenceStatus; detail: string }) {
  return (
    <span className={`inline-flex max-w-full items-center gap-1 border px-1.5 py-0.5 text-[9px] ${statusClass(status)}`} title={detail}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${status === 'fresh' ? 'bg-green-300' : status === 'stale' ? 'bg-amber-300' : 'bg-red-300'}`} />
      <span className="shrink-0">{label}</span>
      <span className="max-w-[180px] truncate opacity-70">{detail}</span>
    </span>
  )
}
