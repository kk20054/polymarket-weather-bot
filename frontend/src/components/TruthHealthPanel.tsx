import type { TruthHealth } from '../types'

interface Props {
  truth?: TruthHealth | null
}

function pct(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  return `${(value * 100).toFixed(0)}%`
}

function reasonLabel(reason: string) {
  switch (reason) {
    case 'truth_independent_days_low': return '独立 truth 样本不足'
    case 'open_meteo_truth_fallback_present': return '含 Open-Meteo fallback'
    case 'legacy_truth_unknown': return '旧数据来源未知'
    default: return reason
  }
}

function providerLabel(provider?: string) {
  switch (provider) {
    case 'nws_station': return 'NWS 机场站'
    case 'visual_crossing_station': return 'Visual Crossing'
    case 'open_meteo_archive': return 'Open-Meteo fallback'
    case 'legacy_unknown': return '旧版未知'
    default: return provider || '--'
  }
}

export function TruthHealthPanel({ truth }: Props) {
  const cities = truth?.cities ?? []
  const coverage = truth?.coverage_rate ?? 0
  const fallbackRate = truth?.open_meteo_fallback_rate ?? 0
  const ready = coverage >= 0.9 && fallbackRate === 0 && (truth?.legacy_unknown ?? 0) === 0

  return (
    <div className="h-full space-y-2 overflow-y-auto p-2 text-[10px] text-neutral-500">
      <div className="flex items-center justify-between">
        <span className="uppercase tracking-wider">结算源覆盖</span>
        <span className={ready ? 'text-green-400' : 'text-red-400'}>{ready ? '可观察' : '阻塞实盘'}</span>
      </div>
      <div className="grid grid-cols-3 gap-1 text-center">
        <div className="border border-neutral-800 bg-black p-1">
          <div className="text-neutral-600">可校准</div>
          <div className="tabular-nums text-neutral-200">{truth?.eligible_observations ?? 0}/{truth?.total_observations ?? 0}</div>
        </div>
        <div className="border border-neutral-800 bg-black p-1">
          <div className="text-neutral-600">覆盖率</div>
          <div className="tabular-nums text-neutral-200">{pct(coverage)}</div>
        </div>
        <div className="border border-neutral-800 bg-black p-1">
          <div className="text-neutral-600">Fallback</div>
          <div className="tabular-nums text-amber-300">{truth?.open_meteo_fallbacks ?? 0}</div>
        </div>
      </div>
      <div className="space-y-1">
        {cities.slice(0, 8).map(city => (
          <div key={city.city} className="border border-neutral-900 bg-neutral-950 p-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-neutral-300">{city.city_name}</span>
              <span className="font-mono text-[9px] text-neutral-600">{city.station_id || '--'}</span>
            </div>
            <div className="mt-0.5 flex items-center justify-between gap-2">
              <span className="truncate">{providerLabel(city.latest_provider)} · {city.latest_date || '--'}</span>
              <span className="tabular-nums text-neutral-400">{city.eligible_observations}/{city.total_observations}</span>
            </div>
            {(city.reasons ?? []).length > 0 && (
              <div className="mt-0.5 truncate text-red-300/80" title={(city.reasons ?? []).map(reasonLabel).join(' / ')}>
                {(city.reasons ?? []).map(reasonLabel).join(' / ')}
              </div>
            )}
          </div>
        ))}
        {cities.length === 0 && (
          <div className="border border-neutral-900 bg-black p-2 text-neutral-600">
            暂无已结算 truth。系统会保持模拟观察，实盘继续阻塞。
          </div>
        )}
      </div>
    </div>
  )
}
