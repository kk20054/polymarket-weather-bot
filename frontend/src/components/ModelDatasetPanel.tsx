import { BrainCircuit, ShieldAlert } from 'lucide-react'
import type { ForecastArchiveManifest, ModelDatasetAudit } from '../types'

const REASON_LABELS: Record<string, string> = {
  contract_not_manually_verified: '合同未核验',
  eligible_truth_missing: 'truth 缺失',
  no_no_leak_forecast_run: '无无泄漏预测',
  forecast_members_missing: '成员缺失',
  core_source_coverage_incomplete: '核心源不全',
  future_or_undated_forecast_rejected: '疑似未来数据',
  orderbook_replay_missing: '盘口回放缺失',
}

function pct(numerator: number, denominator: number) {
  if (!denominator) return '0%'
  return `${Math.round((numerator / denominator) * 100)}%`
}

function label(key: string) {
  return REASON_LABELS[key] ?? key
}

export function ModelDatasetPanel({
  audit,
  archiveManifest,
}: {
  audit?: ModelDatasetAudit | null
  archiveManifest?: ForecastArchiveManifest | null
}) {
  if (!audit) {
    return <div className="p-3 text-[11px] text-neutral-600">模型样本池尚未生成</div>
  }
  const summary = audit.summary
  const topReasons = Object.entries(audit.training_reason_counts ?? audit.reason_counts ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
  const progress = pct(summary.baseline_ready_samples, audit.required_samples)
  const archiveSources = Object.entries(archiveManifest?.by_source ?? {}).sort((a, b) => b[1] - a[1])
  const archiveCities = Object.entries(archiveManifest?.by_city ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 4)

  return (
    <div className="space-y-3 p-3 text-[11px]">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <BrainCircuit className="h-4 w-4 shrink-0 text-violet-300" />
          <div className="min-w-0">
            <div className="text-sm text-neutral-100">模型样本池</div>
            <div className="truncate text-[10px] text-neutral-500">
              Phase 2 无泄漏训练 / 回放准备度
            </div>
          </div>
        </div>
        <span className={`shrink-0 border px-1.5 py-0.5 text-[9px] ${
          audit.status === 'ready'
            ? 'border-green-500/30 bg-green-500/10 text-green-300'
            : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
        }`}>
          {audit.status === 'ready' ? '可训练' : '未达标'}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-1 text-center">
        <Metric label="事件日" value={summary.event_days} />
        <Metric label="可训练" value={summary.training_eligible_samples} />
        <Metric label="Baseline" value={`${summary.baseline_ready_samples}/${audit.required_samples}`} />
      </div>
      <div className="grid grid-cols-2 gap-1 text-center">
        <Metric label="已成熟" value={summary.mature_event_days ?? 0} />
        <Metric label="待结算" value={summary.pending_settlement_samples ?? 0} />
      </div>

      <div className="space-y-1">
        <div className="flex items-center justify-between text-[10px] text-neutral-500">
          <span>Phase 2 样本门槛</span>
          <span className="tabular-nums">{progress}</span>
        </div>
        <div className="h-1.5 overflow-hidden bg-neutral-900">
          <div
            className="h-full bg-violet-300"
            style={{ width: `${Math.min(100, Math.round((summary.baseline_ready_samples / Math.max(1, audit.required_samples)) * 100))}%` }}
          />
        </div>
      </div>

      {topReasons.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {topReasons.map(([reason, count]) => (
            <span key={reason} className="inline-flex items-center gap-1 border border-amber-500/20 bg-amber-500/5 px-1.5 py-0.5 text-[9px] text-amber-100">
              <ShieldAlert className="h-2.5 w-2.5" />
              {label(reason)} {count}
            </span>
          ))}
        </div>
      )}

      {archiveManifest && archiveManifest.record_count > 0 && (
        <div className="space-y-2 border-t border-neutral-800 pt-2">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[10px] text-neutral-500">Archive 缺口</div>
            <div
              className="tabular-nums text-[10px] text-amber-300"
              title={`${archiveManifest.template_command}\n${archiveManifest.import_dry_run_command}`}
            >
              {archiveManifest.record_count} 条
            </div>
          </div>
          {archiveSources.length > 0 && (
            <div className="space-y-1">
              {archiveSources.map(([source, count]) => {
                const width = Math.min(100, Math.round((count / Math.max(1, archiveManifest.record_count)) * 100))
                return (
                  <div key={source} className="space-y-0.5">
                    <div className="flex items-center justify-between text-[9px]">
                      <span className="truncate text-neutral-500">{source.toUpperCase()}</span>
                      <span className="tabular-nums text-neutral-400">{count}</span>
                    </div>
                    <div className="h-1 overflow-hidden bg-neutral-900">
                      <div className="h-full bg-amber-300" style={{ width: `${width}%` }} />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
          {archiveCities.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {archiveCities.map(([city, count]) => (
                <span key={city} className="border border-neutral-800 bg-neutral-950 px-1.5 py-0.5 text-[9px] text-neutral-400">
                  {city} <span className="tabular-nums text-neutral-500">{count}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {audit.operational_counts && (
        <div
          className="grid grid-cols-2 gap-1 border-t border-neutral-800 pt-2 text-center"
          title="运营 backlog 不等同于 Phase 2 训练阻塞；未来待结算事件会先进入等待池。"
        >
          <Metric label="合同 backlog" value={audit.operational_counts.unverified_contract_event_days} />
          <Metric label="自动可信" value={audit.operational_counts.auto_verified_unreviewed_contracts} />
        </div>
      )}

      {(audit.next_actions ?? []).length > 0 && (
        <div className="space-y-1 border-t border-neutral-800 pt-2">
          <div className="text-[10px] text-neutral-500">下一步补数路线</div>
          {(audit.next_actions ?? []).slice(0, 3).map(action => (
            <div
              key={action.key}
              className="border border-neutral-800 bg-neutral-950 p-1.5"
              title={`${action.impact}\n${action.command}${action.apply_command ? `\napply: ${action.apply_command}` : ''}`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 truncate text-[10px] text-neutral-200">{action.label}</div>
                <span className="shrink-0 tabular-nums text-[9px] text-neutral-500">{action.count}</span>
              </div>
              <div className="truncate text-[9px] text-neutral-600">
                {action.requires_operator ? '需要人工确认' : '可由工具补齐'} · {action.command}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-1 text-[10px]">
        {(audit.cities ?? []).slice(0, 4).map(city => (
          <div key={city.city} className="min-w-0 border border-neutral-800 bg-neutral-950 p-1.5" title={city.city_name}>
            <div className="truncate text-neutral-300">{city.city_name}</div>
            <div className="tabular-nums text-neutral-600">
              train {city.training_eligible}/{city.samples} · replay {city.replay_ready}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="border border-neutral-800 bg-neutral-950 p-1.5">
      <div className="text-[9px] text-neutral-600">{label}</div>
      <div className="truncate tabular-nums text-neutral-200">{value}</div>
    </div>
  )
}
