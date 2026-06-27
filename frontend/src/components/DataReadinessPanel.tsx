import { useState } from 'react'
import { CheckCircle2, ChevronDown, ChevronRight, ClipboardCheck, Database, ExternalLink, ShieldAlert } from 'lucide-react'
import type { DataReadiness, SettlementContractList } from '../types'

interface Props {
  readiness?: DataReadiness | null
  contracts?: SettlementContractList | null
  contractStatus?: string
  onContractStatus?: (status: string) => void
  verifyingContractId?: string
  bulkVerifying?: boolean
  onVerifyContract?: (contractId: string, note: string) => void
  onVerifyVisibleContracts?: (contractIds: string[]) => void
}

const REASON_LABELS: Record<string, string> = {
  settlement_rule_not_manually_verified: '合同待人工核验',
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

function pct(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '0%'
  return `${Math.round(Number(value) * 100)}%`
}

function metricRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== 'object') return {}
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, Number(item) || 0])
  )
}

const REVIEW_STATUS_LABELS: Record<string, string> = {
  verified: '已核验',
  'mature-auto': '成熟自动',
  'future-auto': '未来自动',
  'manual-required': '逐条人工',
}

const REVIEW_TAG_LABELS: Record<string, string> = {
  verified: '已确认',
  auto_verified: '自动可信',
  mature: '已成熟',
  pending_settlement: '待结算',
  source_missing: '来源缺失',
  low_confidence: '低置信',
  manual_required: '人工',
}

function reviewStatusLabel(status?: string | null) {
  return status ? REVIEW_STATUS_LABELS[status] ?? status : '待分类'
}

function reviewTagLabel(tag: string) {
  return REVIEW_TAG_LABELS[tag] ?? tag
}

function reviewTagClass(tag: string) {
  if (tag === 'verified' || tag === 'auto_verified') return 'border-green-500/20 bg-green-500/5 text-green-200'
  if (tag === 'source_missing' || tag === 'low_confidence') return 'border-red-500/20 bg-red-500/5 text-red-200'
  if (tag === 'pending_settlement') return 'border-amber-500/20 bg-amber-500/5 text-amber-200'
  return 'border-neutral-800 text-neutral-500'
}

function reviewStatusClass(status?: string | null) {
  if (status === 'verified' || status === 'mature-auto') return 'border-green-500/20 bg-green-500/5 text-green-200'
  if (status === 'future-auto') return 'border-amber-500/20 bg-amber-500/5 text-amber-200'
  if (status === 'manual-required') return 'border-red-500/20 bg-red-500/5 text-red-200'
  return 'border-neutral-800 text-neutral-500'
}

export function DataReadinessPanel({
  readiness,
  contracts,
  contractStatus = 'mature-auto',
  onContractStatus,
  verifyingContractId,
  bulkVerifying = false,
  onVerifyContract,
  onVerifyVisibleContracts,
}: Props) {
  const [openContracts, setOpenContracts] = useState(true)
  const [expandedContract, setExpandedContract] = useState<string | null>(null)
  const [reviewNote, setReviewNote] = useState('station/date/unit/source checked')

  if (!readiness) {
    return <div className="p-3 text-[11px] text-neutral-600">数据资格尚未生成</div>
  }

  const contractSummary = contracts?.summary
  const progress = contractSummary?.manual_progress ?? 0
  const unverified = contractSummary?.unverified ?? readiness.blockers.find(item => item.code === 'settlement_rule_not_manually_verified')?.count ?? 0
  const visibleContractIds = (contracts?.contracts ?? []).map(contract => contract.contract_id)
  const phase = readiness.production_phase
  const contractStage = readiness.stages.find(stage => stage.key === 'settlement_contracts')
  const contractQueue = metricRecord(contractStage?.metrics?.contract_review_queue)
  const canBulkVerifyVisible = contractStatus === 'mature-auto'
  const queueOptions = [
    ['mature-auto', '成熟自动', contractQueue.mature_auto_verified_unreviewed ?? 0],
    ['future-auto', '未来自动', contractQueue.future_auto_verified_unreviewed ?? 0],
    ['manual-required', '逐条人工', contractQueue.manual_review_required_unverified ?? 0],
    ['source-missing', '来源缺失', contractQueue.source_missing_unverified ?? 0],
    ['low-confidence', '低置信', contractQueue.low_confidence_unverified ?? 0],
  ] as const

  return (
    <div className="space-y-3 p-3 text-[11px]">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <Database className="h-4 w-4 shrink-0 text-cyan-300" />
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-1.5">
              <div className="text-sm text-neutral-100">数据资格</div>
              {phase && (
                <span className="shrink-0 border border-cyan-500/30 bg-cyan-500/10 px-1.5 py-0.5 text-[9px] text-cyan-200">
                  {phase.label}
                </span>
              )}
            </div>
            <div className="truncate text-[10px] text-neutral-500">
              {phase?.name ?? (readiness.live_allowed ? '实盘门禁已通过' : `实盘仍锁定，剩余 ${readiness.blockers.length} 类阻塞`)}
            </div>
          </div>
        </div>
        <span className={`shrink-0 border px-1.5 py-0.5 text-[9px] ${
          readiness.live_allowed
            ? 'border-green-500/30 bg-green-500/10 text-green-300'
            : 'border-red-500/30 bg-red-500/10 text-red-300'
        }`}>
          {readiness.live_allowed ? '可实盘' : '已锁定'}
        </span>
      </div>

      {phase && (
        <div className="border border-neutral-800 bg-neutral-950 p-2">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-[10px] text-neutral-300">{phase.operator_action}</div>
              <div className="truncate text-[9px] text-neutral-600">下一步：{phase.next}</div>
            </div>
            <span className={`shrink-0 border px-1.5 py-0.5 text-[9px] ${
              phase.status === 'ready_for_next'
                ? 'border-green-500/30 text-green-300'
                : 'border-amber-500/30 text-amber-300'
            }`}>
              {phase.status === 'ready_for_next' ? '可进入下一阶段' : '进行中'}
            </span>
          </div>
        </div>
      )}

      <div className="grid grid-cols-4 border border-neutral-800">
        {readiness.stages.map(stage => (
          <div
            key={stage.key}
            className="min-w-0 border-r border-neutral-800 p-1.5 last:border-r-0"
            title={stage.reasons.map(reason => `${reasonLabel(reason.code)}: ${reason.count}`).join('\n') || '通过'}
          >
            <div className="mb-1 flex items-center gap-1">
              {stage.status === 'ready'
                ? <CheckCircle2 className="h-3 w-3 shrink-0 text-green-300" />
                : <ShieldAlert className="h-3 w-3 shrink-0 text-red-300" />}
              <span className="truncate text-[9px] text-neutral-400">{stage.label}</span>
            </div>
            <div className={`text-[9px] ${stage.status === 'ready' ? 'text-green-300' : 'text-red-300'}`}>
              {stage.status === 'ready' ? '可用' : stage.reasons.map(reason => reasonLabel(reason.code)).slice(0, 1).join('')}
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-4 gap-1 text-center">
        <Metric label="合同" value={`${contractSummary?.manual_verified ?? 0}/${contractSummary?.contracts ?? readiness.summary.settlement_contracts ?? 0}`} title="人工核验合同数 / 事件合同总数" />
        <Metric label="Truth 日" value={readiness.summary.eligible_truth_days} title="满足生产校准条件的独立城市日" />
        <Metric label="预测运行" value={readiness.summary.forecast_runs} title="版本化预测运行档案数量" />
        <Metric label="盘口" value={readiness.summary.orderbook_snapshots} title="已保存盘口快照数量" />
      </div>

      <div className="space-y-1">
        <div className="flex items-center justify-between text-[10px] text-neutral-500">
          <span>合同核验进度</span>
          <span className="tabular-nums">{pct(progress)} · 待核验 {unverified}</span>
        </div>
        <div className="h-1.5 overflow-hidden bg-neutral-900">
          <div className="h-full bg-cyan-300" style={{ width: `${Math.max(0, Math.min(progress, 1)) * 100}%` }} />
        </div>
      </div>

      {Object.keys(contractQueue).length > 0 && (
        <div className="grid grid-cols-4 gap-1 text-center" title="按生产闸门处理顺序拆分合同核验 backlog">
          <Metric label="成熟自动" value={contractQueue.mature_auto_verified_unreviewed ?? 0} title="已过结算日且自动解析可信，可人工快速确认" />
          <Metric label="未来自动" value={contractQueue.future_auto_verified_unreviewed ?? 0} title="自动解析可信，但还未过当地结算日" />
          <Metric label="逐条人工" value={contractQueue.manual_review_required_unverified ?? 0} title="未通过自动可信规则，需要逐条确认" />
          <Metric label="来源缺失" value={contractQueue.source_missing_unverified ?? 0} title="缺少规则文本或来源 URL，不能直接核验" />
        </div>
      )}

      {readiness.blockers.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {readiness.blockers.slice(0, 4).map(reason => (
            <span
              key={reason.code}
              className="border border-red-500/20 bg-red-500/5 px-1.5 py-0.5 text-[9px] text-red-200"
              title={`${reasonLabel(reason.code)}: ${reason.count}`}
            >
              {reasonLabel(reason.code)} {reason.count}
            </span>
          ))}
        </div>
      )}

      {(readiness.next_actions ?? []).length > 0 && (
        <div className="space-y-1 border border-neutral-800 bg-neutral-950 p-2">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[10px] text-neutral-400">修复路线</div>
            <span className="text-[9px] text-neutral-600">按顺序处理</span>
          </div>
          {(readiness.next_actions ?? []).slice(0, 3).map(action => (
            <div
              key={action.key}
              className="border border-neutral-900 bg-black/40 p-1.5"
              title={`${action.impact}\n${action.command}${action.apply_command ? `\napply: ${action.apply_command}` : ''}`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 truncate text-[10px] text-neutral-200">{action.label}</div>
                <span className="shrink-0 tabular-nums text-[9px] text-neutral-500">{action.count}</span>
              </div>
              <div className="truncate text-[9px] text-neutral-600">
                {action.requires_operator ? '需人工确认' : '工具可执行'} · {action.command}
              </div>
              {action.targets?.[0] && (
                <div className="truncate text-[9px] text-neutral-500">
                  目标：{action.targets[0].city_name || action.targets[0].city} · {action.targets[0].target_date || action.targets[0].target_local_date} · {action.targets[0].station_id}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="border-t border-neutral-800 pt-2">
        <div className="mb-2 flex flex-wrap gap-1">
          {queueOptions.map(([status, label, count]) => (
            <button
              key={status}
              type="button"
              onClick={() => onContractStatus?.(status)}
              className={`border px-1.5 py-0.5 text-[9px] ${
                contractStatus === status
                  ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200'
                  : 'border-neutral-800 text-neutral-500 hover:border-neutral-700 hover:text-neutral-300'
              }`}
              title={`${label}: ${count}`}
            >
              {label} <span className="tabular-nums">{count}</span>
            </button>
          ))}
        </div>
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => setOpenContracts(value => !value)}
            className="flex min-w-0 flex-1 items-center gap-1 text-left text-[11px] text-neutral-200"
          >
            {openContracts ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
            <span className="truncate">待核验合同</span>
            <span className="shrink-0 tabular-nums text-neutral-500">
              {(contracts?.contracts.length ?? 0) > 0 ? `${contracts?.contracts.length}/${contracts?.total}` : contracts?.total ?? unverified}
            </span>
          </button>
          <button
            type="button"
            disabled={!canBulkVerifyVisible || !onVerifyVisibleContracts || visibleContractIds.length === 0 || bulkVerifying}
            onClick={() => onVerifyVisibleContracts?.(visibleContractIds)}
            className="shrink-0 border border-cyan-500/30 px-1.5 py-1 text-[9px] text-cyan-200 hover:bg-cyan-500/10 disabled:opacity-40"
            title="只确认已过当地结算日、且后端自动校验通过的合同；未来待结算合同会被跳过"
          >
            {bulkVerifying ? '写入中' : '确认成熟'}
          </button>
        </div>
        <input
          value={reviewNote}
          onChange={event => setReviewNote(event.target.value)}
          className="mt-2 w-full border border-neutral-800 bg-black px-2 py-1 text-[10px] text-neutral-300 outline-none focus:border-cyan-500/50"
          placeholder="人工核验备注"
          aria-label="人工核验备注"
        />

        {openContracts && (
          <div className="mt-2 divide-y divide-neutral-900 border border-neutral-900">
            {(contracts?.contracts ?? []).length === 0 ? (
              <div className="p-2 text-[10px] text-neutral-500">暂无待核验合同</div>
            ) : contracts?.contracts.map(contract => {
              const expanded = expandedContract === contract.contract_id
              const verifying = verifyingContractId === contract.contract_id
              const confidence = Math.round(Number(contract.parse_confidence ?? 0) * 100)
              return (
                <div key={contract.contract_id} className="bg-neutral-950/40">
                  <div className="grid grid-cols-[1fr_auto] gap-2 p-2">
                    <button
                      type="button"
                      onClick={() => setExpandedContract(expanded ? null : contract.contract_id)}
                      className="min-w-0 text-left"
                      title={contract.event_slug}
                    >
                      <div className="truncate text-[11px] text-neutral-100">
                        {contract.city_name} · {contract.target_local_date}
                      </div>
                      <div className="mt-1 flex min-w-0 flex-wrap items-center gap-1">
                        <span className="truncate text-[10px] text-neutral-500">
                          {contract.station_id} · {contract.station_name}
                        </span>
                        <span className={`shrink-0 border px-1 py-0.5 text-[9px] ${reviewStatusClass(contract.review_status)}`}>
                          {reviewStatusLabel(contract.review_status)}
                        </span>
                        <span className="shrink-0 border border-neutral-800 px-1 py-0.5 text-[9px] text-neutral-500">
                          置信 {confidence}%
                        </span>
                      </div>
                    </button>
                    <div className="flex items-start gap-1">
                      {contract.source_url && (
                        <a
                          href={contract.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="border border-neutral-800 p-1 text-neutral-400 hover:border-cyan-400/40 hover:text-cyan-200"
                          title="打开结算来源"
                        >
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                      <button
                        type="button"
                        disabled={!onVerifyContract || verifying}
                        onClick={() => onVerifyContract?.(contract.contract_id, reviewNote)}
                        className="inline-flex items-center gap-1 border border-cyan-500/30 px-1.5 py-1 text-[9px] text-cyan-200 hover:bg-cyan-500/10 disabled:opacity-40"
                        title="确认该事件的站点、日期、单位和来源 URL 与规则一致"
                      >
                        <ClipboardCheck className="h-3 w-3" />
                        {verifying ? '写入中' : '确认'}
                      </button>
                    </div>
                  </div>
                  {expanded && (
                    <div className="space-y-1 border-t border-neutral-900 p-2 text-[10px] text-neutral-400">
                      {(contract.review_tags ?? []).length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {(contract.review_tags ?? []).map(tag => (
                            <span key={tag} className={`border px-1 py-0.5 text-[9px] ${reviewTagClass(tag)}`}>
                              {reviewTagLabel(tag)}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="grid grid-cols-2 gap-x-3 gap-y-1">
                        <Detail label="时区" value={contract.timezone} />
                        <Detail label="单位" value={contract.unit} />
                        <Detail label="边界" value={contract.bucket_boundary} />
                        <Detail label="舍入" value={contract.rounding_rule} />
                      </div>
                      <div className="line-clamp-3 text-neutral-500" title={contract.resolution_source_text ?? ''}>
                        {contract.resolution_source_text || '无规则文本'}
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {(contract.verification_evidence ?? []).slice(0, 5).map(item => (
                          <span key={item} className="border border-neutral-800 px-1 py-0.5 text-[9px] text-neutral-500">
                            {item}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function Metric({ label, value, title }: { label: string; value: string | number; title: string }) {
  return (
    <div className="border border-neutral-800 bg-neutral-950 p-1.5" title={title}>
      <div className="text-[9px] text-neutral-600">{label}</div>
      <div className="truncate tabular-nums text-neutral-200">{value}</div>
    </div>
  )
}

function Detail({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="min-w-0">
      <span className="text-neutral-600">{label}</span>
      <span className="ml-1 text-neutral-300">{value || '--'}</span>
    </div>
  )
}
