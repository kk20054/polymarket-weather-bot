import { CheckCircle2, Database, ShieldAlert } from 'lucide-react'
import type { DataReadiness } from '../types'

interface Props {
  readiness?: DataReadiness | null
}

const REASON_LABELS: Record<string, string> = {
  settlement_rule_not_manually_verified: '规则未核验',
  settlement_contracts_missing: '事件合同缺失',
  timezone_mismatch: '时区不一致',
  resolution_source_missing: '来源缺失',
  timezone_database_unavailable: '时区库不可用',
  independent_truth_days_below_min: 'Truth 样本不足',
  legacy_truth_unknown: '旧来源未知',
  open_meteo_fallback_present: '含低置信 fallback',
  versioned_forecast_runs_missing: '预测档案缺失',
  forecast_members_missing: '成员预测缺失',
  forecast_runs_stale: '预测档案过期',
  forecast_city_coverage_incomplete: '城市覆盖不足',
  ecmwf_runs_missing: 'ECMWF 缺失',
  gfs_ensemble_runs_missing: 'GFS ensemble 缺失',
  orderbook_snapshots_missing: '盘口快照缺失',
  all_orderbooks_stale: '盘口已过期',
  fresh_clob_depth_missing: 'CLOB 深度缺失',
}

function reasonLabel(code: string) {
  return REASON_LABELS[code] ?? code
}

export function DataReadinessPanel({ readiness }: Props) {
  if (!readiness) {
    return <div className="p-3 text-[11px] text-neutral-600">数据资格尚未生成</div>
  }

  return (
    <div className="space-y-2 p-3 text-[11px]">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-cyan-300" />
          <span className="text-sm text-neutral-100">数据资格</span>
        </div>
        <span className={`border px-1.5 py-0.5 text-[9px] ${
          readiness.live_allowed
            ? 'border-green-500/30 bg-green-500/10 text-green-300'
            : 'border-red-500/30 bg-red-500/10 text-red-300'
        }`}>
          {readiness.live_allowed ? '通过' : '阻塞'}
        </span>
      </div>

      <div className="grid grid-cols-4 border border-neutral-800">
        {readiness.stages.map(stage => (
          <div
            key={stage.key}
            className="min-w-0 border-r border-neutral-800 p-1.5 last:border-r-0"
            title={stage.reasons.map(reason => `${reasonLabel(reason.code)}: ${reason.count}`).join('\n')}
          >
            <div className="mb-1 flex items-center gap-1">
              {stage.status === 'ready'
                ? <CheckCircle2 className="h-3 w-3 shrink-0 text-green-300" />
                : <ShieldAlert className="h-3 w-3 shrink-0 text-red-300" />}
              <span className="truncate text-[9px] text-neutral-400">{stage.label}</span>
            </div>
            <div className={`text-[9px] ${stage.status === 'ready' ? 'text-green-300' : 'text-red-300'}`}>
              {stage.status === 'ready' ? '可用' : `${stage.reasons.length} 项`}
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-1 text-center">
        <div className="border border-neutral-800 bg-neutral-950 p-1.5" title="满足实盘校准条件的独立城市日">
          <div className="text-[9px] text-neutral-600">Truth 日</div>
          <div className="tabular-nums text-neutral-200">{readiness.summary.eligible_truth_days}</div>
        </div>
        <div className="border border-neutral-800 bg-neutral-950 p-1.5" title="版本化预测运行数 / 成员级预测数">
          <div className="text-[9px] text-neutral-600">运行 / 成员</div>
          <div className="tabular-nums text-neutral-200">
            {readiness.summary.forecast_runs} / {readiness.summary.forecast_members}
          </div>
        </div>
        <div className="border border-neutral-800 bg-neutral-950 p-1.5" title="已保存的盘口快照数量">
          <div className="text-[9px] text-neutral-600">盘口</div>
          <div className="tabular-nums text-neutral-200">{readiness.summary.orderbook_snapshots}</div>
        </div>
      </div>

      {readiness.blockers.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {readiness.blockers.slice(0, 4).map(reason => (
            <span
              key={reason.code}
              className="border border-red-500/20 bg-red-500/5 px-1.5 py-0.5 text-[9px] text-red-200"
              title={`${reasonLabel(reason.code)}：${reason.count}`}
            >
              {reasonLabel(reason.code)} {reason.count}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
