import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { TruthDeltaAuditSummary } from '../types'
import { useT, type I18nLanguage } from '../i18n/useT'

type Props = {
  summary?: TruthDeltaAuditSummary | null
  selectedCity?: string
  language: I18nLanguage
}

function fmtC(value: number | null | undefined) {
  return value === null || value === undefined || Number.isNaN(Number(value)) ? '--' : `${Number(value).toFixed(1)}C`
}

export function DeltaAuditPanel({ summary, selectedCity, language }: Props) {
  const t = useT(language)
  const rows = summary?.rows ?? []
  const chartRows = rows
    .slice()
    .reverse()
    .map(row => ({
      date: row.date_local ?? '--',
      city: row.city ?? row.icao ?? '--',
      iem: row.iem_high_c ?? null,
      wu: row.wu_high_c ?? null,
      hko: row.hko_high_c ?? null,
      delta: row.delta_wu_minus_iem ?? null,
    }))
  const histogramRows = summary?.histogram ?? []

  return (
    <section className="space-y-3 p-3" aria-label="truth delta audit">
      <div className="border border-[#2C3445] bg-[#161A22] p-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-[#F8FAFC]">{t('delta.title')}</h2>
            <p className="mt-1 max-w-3xl text-[11px] leading-relaxed text-[#7D8694]">
              {t('delta.subtitle')}
            </p>
          </div>
          <span className="border border-[#2C3445] px-2 py-1 text-[10px] text-[#CBD2DC]">
            {selectedCity || 'all'} · {summary?.count ?? 0} rows
          </span>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="border border-[#2C3445] bg-[#161A22] p-6 text-center text-xs text-[#7D8694]">
          {t('delta.empty')}
        </div>
      ) : (
        <>
          <div className="grid gap-3 xl:grid-cols-[2fr_1fr]">
            <div className="border border-[#2C3445] bg-[#161A22] p-3">
              <div className="mb-2 text-[11px] font-medium text-[#CBD2DC]">Daily high comparison</div>
              <div className="h-[280px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartRows} margin={{ top: 8, right: 16, bottom: 20, left: -8 }}>
                    <CartesianGrid stroke="#2C3445" strokeDasharray="3 3" />
                    <XAxis dataKey="date" stroke="#7D8694" fontSize={10} tickLine={false} axisLine={false} />
                    <YAxis stroke="#7D8694" fontSize={10} tickLine={false} axisLine={false} tickFormatter={value => `${value}C`} />
                    <Tooltip contentStyle={{ background: '#1B212C', border: '1px solid #2C3445', color: '#CBD2DC', fontSize: 11 }} />
                    <Line type="monotone" dataKey="iem" name="IEM ASOS" stroke="#22C55E" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls />
                    <Line type="monotone" dataKey="wu" name="Wunderground" stroke="#3B82F6" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls />
                    <Line type="monotone" dataKey="hko" name="HKO Daily Extract" stroke="#F97316" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="border border-[#2C3445] bg-[#161A22] p-3">
              <div className="mb-2 text-[11px] font-medium text-[#CBD2DC]">WU - IEM delta histogram</div>
              <div className="h-[280px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={histogramRows} margin={{ top: 8, right: 8, bottom: 20, left: -8 }}>
                    <CartesianGrid stroke="#2C3445" strokeDasharray="3 3" />
                    <XAxis dataKey="bucket" stroke="#7D8694" fontSize={10} tickLine={false} axisLine={false} />
                    <YAxis stroke="#7D8694" fontSize={10} tickLine={false} axisLine={false} allowDecimals={false} />
                    <Tooltip contentStyle={{ background: '#1B212C', border: '1px solid #2C3445', color: '#CBD2DC', fontSize: 11 }} />
                    <Bar dataKey="count" name="days" fill="#2563EB" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          <div className="grid gap-2 text-[11px] md:grid-cols-2 xl:grid-cols-3">
            {rows.slice(0, 12).map(row => (
              <div key={`${row.icao}-${row.date_local}`} className="border border-[#2C3445] bg-[#161A22] p-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-[#F8FAFC]">{row.city || row.icao}</span>
                  <span className="text-[#7D8694]">{row.date_local}</span>
                </div>
                <div className="mt-2 grid grid-cols-3 gap-1 tabular-nums">
                  <span className="border border-[#2C3445] px-1.5 py-1">IEM {fmtC(row.iem_high_c)}</span>
                  <span className="border border-[#2C3445] px-1.5 py-1">WU {fmtC(row.wu_high_c)}</span>
                  <span className="border border-[#2C3445] px-1.5 py-1">HKO {fmtC(row.hko_high_c)}</span>
                </div>
                <div className="mt-2 text-[#7D8694]">delta WU-IEM: {fmtC(row.delta_wu_minus_iem)}</div>
              </div>
            ))}
          </div>

          <div className="grid gap-2 text-[11px] md:grid-cols-2">
            <div className="border border-amber-500/30 bg-amber-500/10 p-3 text-amber-100">
              Hong Kong: settlement_mismatch because the market uses HKO Daily Extract while the observation station is VHHH. Keep paper-only until HKO truth coverage is complete.
            </div>
            <div className="border border-red-500/30 bg-red-500/10 p-3 text-red-100">
              Seoul: monitor_only because external P&amp;L evidence is materially negative. Use it for calibration, not execution.
            </div>
          </div>
        </>
      )}
    </section>
  )
}
