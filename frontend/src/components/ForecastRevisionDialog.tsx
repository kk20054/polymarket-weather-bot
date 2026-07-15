import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { History, X } from 'lucide-react'
import { fetchForecastHistory } from '../api'
import type { ForecastRevisionHistory } from '../types'

interface Props {
  city: string
  targetDate: string
  localHour: string
  unit: string
  onClose: () => void
}

function systemTimestamp(value?: string | null) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toISOString().slice(0, 19).replace('T', ' ')
}

function localTimestamp(value?: string | null) {
  if (!value) return '--'
  const match = String(value).match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/)
  return match ? `${match[1]} ${match[2]}` : value
}

function temperature(value: number | null | undefined, unit: string) {
  return value === null || value === undefined || !Number.isFinite(value)
    ? '--'
    : `${value.toFixed(0)} °${unit}`
}

function delta(value: number | null | undefined, unit: string) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(0)} °${unit}`
}

export function ForecastRevisionDialog({ city, targetDate, localHour, unit, onClose }: Props) {
  const [data, setData] = useState<ForecastRevisionHistory | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    setData(null)
    fetchForecastHistory(city, targetDate, localHour)
      .then(result => {
        if (!cancelled) setData(result)
      })
      .catch(reason => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '预报修订读取失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [city, targetDate, localHour])

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  if (typeof document === 'undefined') return null

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4"
      role="presentation"
      onMouseDown={event => {
        if (event.currentTarget === event.target) onClose()
      }}
    >
      <section
        aria-labelledby="forecast-revision-title"
        aria-modal="true"
        className="flex max-h-[min(680px,calc(100vh-32px))] w-full max-w-[680px] flex-col overflow-hidden rounded-[6px] border border-[#2C3445] bg-[#161A22] text-[#CBD2DC] shadow-2xl"
        role="dialog"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-[#2C3445] px-4 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <History className="h-4 w-4 text-[#60A5FA]" aria-hidden="true" />
              <h2 id="forecast-revision-title" className="truncate text-sm font-semibold text-white">
                预报历史 — {targetDate} {localHour}
              </h2>
            </div>
            <p className="mt-1 text-[11px] text-[#7D8694]">
              {loading
                ? '正在读取快照…'
                : data
                  ? `${data.snapshot_count} 次快照 · ${data.revision_count} 次修订（未变化行已隐藏）`
                  : '暂无快照'}
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭预报历史"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center border border-transparent text-[#7D8694] hover:border-[#2C3445] hover:bg-[#222A37] hover:text-white"
            onClick={onClose}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>

        <div className="min-h-0 overflow-auto p-4">
          {loading && <div className="py-12 text-center text-xs text-[#7D8694]">正在读取预报修订…</div>}
          {!loading && error && (
            <div className="border border-red-500/30 bg-red-500/5 px-3 py-4 text-xs text-red-300">{error}</div>
          )}
          {!loading && !error && data && data.revisions.length === 0 && (
            <div className="py-12 text-center text-xs text-[#7D8694]">该小时没有可审计的预报快照。</div>
          )}
          {!loading && !error && data && data.revisions.length > 0 && (
            <div className="overflow-x-auto border border-[#2C3445]">
              <table className="w-full min-w-[590px] border-collapse text-left text-[11px]">
                <thead className="bg-[#222A37] text-[#7D8694]">
                  <tr>
                    <th className="px-3 py-2 font-normal">抓取（UTC）</th>
                    <th className="px-3 py-2 font-normal">抓取（{data.timezone || '本地'}）</th>
                    <th className="px-3 py-2 font-normal">气温</th>
                    <th className="px-3 py-2 font-normal">Δ 与前值</th>
                  </tr>
                </thead>
                <tbody>
                  {data.revisions.map(row => (
                    <tr key={`${row.run_id}-${row.valid_at}`} className="border-t border-[#2C3445] hover:bg-[#222A37]/60">
                      <td className="px-3 py-2 font-mono text-[#9AA4B2]">{systemTimestamp(row.fetched_at)}</td>
                      <td className="px-3 py-2 font-mono text-[#9AA4B2]">{localTimestamp(row.fetched_at_local)}</td>
                      <td className="px-3 py-2 font-mono font-semibold text-white" title={`raw ${row.temperature}`}>
                        {temperature(row.display_temperature, data.unit || unit)}
                      </td>
                      <td className={`px-3 py-2 font-mono ${
                        (row.delta_from_previous ?? 0) > 0
                          ? 'text-red-300'
                          : (row.delta_from_previous ?? 0) < 0
                            ? 'text-cyan-300'
                            : 'text-[#7D8694]'
                      }`}>
                        {delta(row.delta_from_previous, data.unit || unit)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {!loading && !error && data && data.snapshot_count > 0 && (
            <p className="mt-3 text-[10px] leading-5 text-[#667085]">
              同值快照已折叠；修订按 Weather.com 整数温度显示口径计算，原始精度保留在审计数据中。
            </p>
          )}
        </div>
      </section>
    </div>,
    document.body,
  )
}
