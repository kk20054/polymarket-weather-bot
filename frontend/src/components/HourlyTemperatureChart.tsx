import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useState, type ReactNode } from 'react'

export type HourlyChartRow = {
  label: string
  time_minute: number
  timestamp?: string
  forecast_value: number | null
  metar_value: number | null
  historical_value: number | null
  china_live_value: number | null
  pws_value: number | null
  cloud_pct: number | null
}

type Props = {
  rows: HourlyChartRow[]
  unit: string
  cityName: string
  dateLabel: string
  forecastMax: number | null
  metarMax: number | null
  peakHour: string | null
  hasChinaLive: boolean
  hasPws: boolean
  hasHistorical: boolean
  averageDelta: Array<string | null>
  accuracy: Array<string | null>
  overlap: string | null
}

const HOUR_TICKS = Array.from({ length: 24 }, (_, hour) => hour * 60)
type SeriesKey = 'china' | 'pws' | 'metar' | 'historical' | 'forecast' | 'cloud'

function formatTemp(value: number | null, unit: string) {
  return value === null || !Number.isFinite(value) ? '--' : `${value.toFixed(1)}°${unit}`
}

function formatMinute(value: number) {
  const minutes = Math.max(0, Math.min(1439, Number(value) || 0))
  const hour = Math.floor(minutes / 60)
  const minute = Math.floor(minutes % 60)
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
}

function formatTooltipTime(dateLabel: string, value: number | string) {
  return `${dateLabel} ${formatMinute(Number(value))}`
}

type TooltipItem = {
  dataKey?: string | number
  name?: string
  value?: number | string | null
  color?: string
}

function HourlyTooltip({
  active,
  label,
  payload,
  dateLabel,
  unit,
}: {
  active?: boolean
  label?: number | string
  payload?: TooltipItem[]
  dateLabel: string
  unit: string
}) {
  if (!active || label === undefined || !payload?.length) return null
  return (
    <div className="min-w-[180px] border border-[#2C3445] bg-[#1B212C] px-2.5 py-2 text-[11px] text-[#CBD2DC] shadow-xl">
      <div className="mb-1.5 font-semibold text-white">{formatTooltipTime(dateLabel, label)}</div>
      <div className="space-y-1">
        {payload.map(item => {
          const numeric = Number(item.value)
          if (!Number.isFinite(numeric)) return null
          const cloud = item.dataKey === 'cloud_pct'
          return (
            <div key={String(item.dataKey)} className="flex items-center justify-between gap-4 tabular-nums">
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2 w-2" style={{ backgroundColor: item.color || '#94A3B8' }} />
                {item.name || item.dataKey}
              </span>
              <strong>{cloud ? `${numeric.toFixed(0)}%` : formatTemp(numeric, unit)}</strong>
            </div>
          )
        })}
      </div>
    </div>
  )
}

type DotProps = { cx?: number; cy?: number; value?: number | null; active?: boolean }

function SquareDot({ cx, cy, value, active = false }: DotProps) {
  if (cx === undefined || cy === undefined || value === null || value === undefined || !Number.isFinite(Number(value))) return null
  const size = active ? 8 : 6
  return <rect x={cx - size / 2} y={cy - size / 2} width={size} height={size} fill="#EF4444" stroke="#FEE2E2" strokeWidth={active ? 1.5 : 1} />
}

function TriangleDot({ cx, cy, value, active = false }: DotProps) {
  if (cx === undefined || cy === undefined || value === null || value === undefined || !Number.isFinite(Number(value))) return null
  const size = active ? 7 : 5
  return <path d={`M ${cx} ${cy - size} L ${cx + size} ${cy + size} L ${cx - size} ${cy + size} Z`} fill="#A855F7" stroke="#F3E8FF" strokeWidth={active ? 1.5 : 1} />
}

function HollowCircleDot({ cx, cy, value, active = false }: DotProps) {
  if (cx === undefined || cy === undefined || value === null || value === undefined || !Number.isFinite(Number(value))) return null
  return <circle cx={cx} cy={cy} r={active ? 5 : 3} fill="transparent" stroke="#3B82F6" strokeWidth={active ? 2 : 1.5} />
}

function PeakLabel({ viewBox, value }: { viewBox?: { x?: number; y?: number }; value?: string }) {
  const x = viewBox?.x
  const y = viewBox?.y
  if (x === undefined || y === undefined || !value) return null
  const width = Math.max(64, value.length * 6.4)
  return (
    <g transform={`translate(${x - width / 2}, ${Math.max(0, y - 18)})`}>
      <rect width={width} height={16} rx={2} fill="#EC4899" />
      <text x={width / 2} y={11.5} fill="#FFFFFF" fontSize={10} textAnchor="middle">{value}</text>
    </g>
  )
}

function StatRow({ label, values, empty, tone }: { label: string; values: Array<string | null>; empty: string; tone: 'green' | 'orange' }) {
  const visible = values.filter((value): value is string => Boolean(value))
  const colors = tone === 'green'
    ? 'border-green-500/30 bg-green-500/10 text-green-200'
    : 'border-orange-500/30 bg-orange-500/10 text-orange-200'
  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-[#2C3445] px-3 py-2 text-[10px]">
      <span className="min-w-[150px] text-[#7D8694]">{label}</span>
      {visible.length > 0
        ? visible.map(value => <span key={value} className={`rounded-full border px-2 py-0.5 tabular-nums ${colors}`}>{value}</span>)
        : <span className="text-[#7D8694]">{empty}</span>}
    </div>
  )
}

export function HourlyTemperatureChart({
  rows,
  unit,
  cityName,
  dateLabel,
  forecastMax,
  metarMax,
  peakHour,
  hasChinaLive,
  hasPws,
  hasHistorical,
  averageDelta,
  accuracy,
  overlap,
}: Props) {
  const peakMinute = peakHour ? HOUR_TICKS.find(value => formatMinute(value) === peakHour) ?? null : null
  const temperatureValues = rows.flatMap(row => [
    row.forecast_value,
    row.metar_value,
    row.historical_value,
    row.china_live_value,
    row.pws_value,
  ]).filter((value): value is number => value !== null && Number.isFinite(value))
  const rawMin = temperatureValues.length ? Math.min(...temperatureValues) : 0
  const rawMax = temperatureValues.length ? Math.max(...temperatureValues) : 10
  const minPadding = unit === 'F' ? 2 : 1
  const padding = Math.max(minPadding, (rawMax - rawMin) * 0.15)
  const temperatureDomain: [number, number] = [
    Math.floor((rawMin - padding) * 2) / 2,
    Math.ceil((rawMax + padding) * 2) / 2,
  ]
  const [visibleSeries, setVisibleSeries] = useState<Record<SeriesKey, boolean>>({
    china: true,
    pws: true,
    metar: true,
    historical: true,
    forecast: true,
    cloud: true,
  })
  const toggleSeries = (key: SeriesKey) => {
    setVisibleSeries(current => ({ ...current, [key]: !current[key] }))
  }
  const legendButton = (key: SeriesKey, label: string, marker: ReactNode, available = true) => (
    <button
      type="button"
      onClick={() => available && toggleSeries(key)}
      disabled={!available}
      aria-pressed={visibleSeries[key]}
      className={`inline-flex items-center gap-1 border-0 bg-transparent p-0 text-[10px] transition ${available && visibleSeries[key] ? 'text-[#AEB7C4]' : 'text-[#596272] opacity-55'}`}
      title={available ? `${visibleSeries[key] ? '隐藏' : '显示'}${label}` : `${label}当前不可用`}
    >
      {marker}{label}
    </button>
  )
  return (
    <section className="min-h-0 border border-[#2C3445] bg-[#161A22]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#2C3445] px-2 py-1.5">
        <div>
          <div className="text-[10px] text-[#7D8694]">逐小时气温</div>
          <div className="text-xs text-[#CBD2DC]">{cityName} · {dateLabel}</div>
        </div>
        <div className="flex flex-wrap gap-1 text-[9px] text-[#7D8694]">
          <span className="border border-[#2C3445] px-1.5 py-0.5">预报最高 {formatTemp(forecastMax, unit)}</span>
          <span className="border border-[#2C3445] px-1.5 py-0.5">METAR 最高 {formatTemp(metarMax, unit)}</span>
          <span className="border border-[#2C3445] px-1.5 py-0.5">峰值 {peakHour ?? '--'}</span>
        </div>
      </div>

      <div className="p-2" role="img" aria-label={`${cityName} 逐小时温度图`}>
        <div className="mb-2 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-[10px] text-[#7D8694]">
          {legendButton('china', '中国实况', <span className="h-2.5 w-2.5 bg-[#EF4444]" />, hasChinaLive)}
          {legendButton('pws', hasPws ? 'PWS（实时）' : 'PWS（未授权/无数据）', <span className="h-0 w-0 border-x-[5px] border-b-[9px] border-x-transparent border-b-[#A855F7]" />, hasPws)}
          {legendButton('metar', 'METAR（本地时）', <span className="h-2.5 w-2.5 rounded-full bg-[#F97316]" />)}
          {legendButton('historical', '历史观测（本地时）', <span className="h-2.5 w-2.5 rounded-full bg-[#22C55E]" />, hasHistorical)}
          {legendButton('forecast', '本系统预报（本地时）', <span className="h-2.5 w-2.5 rounded-full border border-[#3B82F6]" />)}
          {legendButton('cloud', '云量 %', <span className="h-2.5 w-3 bg-[#94A3B8]/30" />)}
        </div>
        <div className="relative h-[340px]">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 22, right: 18, bottom: 0, left: -6 }}>
              <CartesianGrid stroke="#2C3445" strokeDasharray="3 3" />
              <XAxis type="number" dataKey="time_minute" domain={[0, 1380]} ticks={HOUR_TICKS} interval={0} tickFormatter={formatMinute} stroke="#7D8694" fontSize={8} tickLine={false} axisLine={false} minTickGap={0} angle={-45} textAnchor="end" height={52} />
              <YAxis yAxisId="temp" domain={temperatureDomain} allowDataOverflow stroke="#7D8694" fontSize={10} tickLine={false} axisLine={false} tickFormatter={value => `${Number(value).toFixed(0)}°${unit}`} />
              <YAxis yAxisId="percent" orientation="right" domain={[0, 100]} stroke="#475569" fontSize={10} tickLine={false} axisLine={false} tickFormatter={value => `${Number(value).toFixed(0)}%`} />
              <Tooltip content={<HourlyTooltip dateLabel={dateLabel} unit={unit} />} />
              {visibleSeries.cloud && <Area yAxisId="percent" type="monotone" dataKey="cloud_pct" name="云量 %" stroke="#94A3B8" fill="#94A3B8" fillOpacity={0.25} strokeOpacity={0.65} connectNulls />}
              {visibleSeries.metar && <Line yAxisId="temp" type="linear" dataKey="metar_value" name="METAR" stroke="#F97316" dot={{ r: 3, fill: '#F97316', stroke: '#F97316', strokeWidth: 1 }} activeDot={{ r: 5 }} strokeWidth={2} connectNulls />}
              {visibleSeries.historical && <Line yAxisId="temp" type="linear" dataKey="historical_value" name="历史观测" stroke="#22C55E" dot={{ r: 3, fill: '#22C55E', stroke: '#22C55E', strokeWidth: 1 }} activeDot={{ r: 5 }} strokeWidth={2} connectNulls />}
              {hasChinaLive && visibleSeries.china && <Line yAxisId="temp" type="linear" dataKey="china_live_value" name="中国实况" stroke="#EF4444" dot={<SquareDot />} activeDot={<SquareDot active />} strokeWidth={2} connectNulls />}
              {hasPws && visibleSeries.pws && <Line yAxisId="temp" type="linear" dataKey="pws_value" name="PWS" stroke="#A855F7" dot={<TriangleDot />} activeDot={<TriangleDot active />} strokeWidth={2} connectNulls />}
              {visibleSeries.forecast && <Line yAxisId="temp" type="linear" dataKey="forecast_value" name="预报" stroke="#3B82F6" strokeDasharray="4 4" dot={<HollowCircleDot />} activeDot={<HollowCircleDot active />} strokeWidth={2} connectNulls />}
              {peakMinute !== null && <ReferenceLine yAxisId="temp" x={peakMinute} stroke="#EC4899" strokeDasharray="4 4" label={<PeakLabel value={`peak ${peakHour}`} />} />}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      <StatRow label="AVG Δ (OBS−FC)" values={averageDelta} empty="No diff stats yet" tone="green" />
      <StatRow label="ACCURACY (PEARSON R)" values={accuracy} empty="No accuracy stats yet" tone="orange" />
      <StatRow label="HIST↔METAR OVERLAP" values={[overlap]} empty="No overlap data yet" tone="green" />
    </section>
  )
}
