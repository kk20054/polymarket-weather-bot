import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Check,
  ChevronRight,
  Gauge,
  History,
  LockKeyhole,
  RotateCcw,
  Save,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import {
  activateStrategyProfile,
  createStrategyProfile,
  fetchProductionValidation,
  fetchSchedulerStatus,
  fetchStrategyProfiles,
} from '../api'
import type { StrategyProfileParameters, StrategyProfileRevision } from '../types'

type SettingsSection = 'overview' | 'strategy' | 'versions' | 'system'
type ThemeMode = 'light' | 'dark'

interface DrawerProps {
  open: boolean
  onClose: () => void
  themeMode: ThemeMode
}

interface PanelProps {
  themeMode: ThemeMode
  onClose?: () => void
  standalone?: boolean
}

const NAV_ITEMS: Array<{ key: SettingsSection; label: string; hint: string; icon: typeof Settings2 }> = [
  { key: 'overview', label: '概览', hint: '当前状态与生效版本', icon: Gauge },
  { key: 'strategy', label: '策略与风控', hint: '仓位、闸门与入场策略', icon: SlidersHorizontal },
  { key: 'versions', label: '版本与审计', hint: '发布、激活与历史记录', icon: History },
  { key: 'system', label: '系统状态', hint: '调度器与生产阻塞', icon: Activity },
]

const STRATEGY_META: Record<string, { label: string; description: string }> = {
  single_bucket_ev: {
    label: '单桶最高温',
    description: '仅评估一个温度桶，适合模型概率与盘口价格存在明确差异时使用。',
  },
  ladder_grid: {
    label: '相邻三桶阶梯',
    description: '中心桶与左右相邻桶组成原子组合，任一盘口不满足时整组跳过。',
  },
  tail_buying: {
    label: '低价尾部',
    description: '只观察低价且概率差足够大的尾部桶，要求更长的独立结算历史。',
  },
}

function cloneParameters(value: StrategyProfileParameters): StrategyProfileParameters {
  return JSON.parse(JSON.stringify(value)) as StrategyProfileParameters
}

function shortRevision(value?: string) {
  return value ? value.slice(0, 16) : '--'
}

function percent(value: number) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`
}

function SettingNumber({ label, description, value, min, max, step, suffix, onChange }: {
  label: string
  description: string
  value: number
  min: number
  max: number
  step: number
  suffix?: string
  onChange: (value: number) => void
}) {
  return (
    <label className="grid min-h-14 grid-cols-[minmax(0,1fr)_132px] items-center gap-4 border-b border-neutral-800 py-2.5 last:border-b-0">
      <span className="min-w-0">
        <span className="block text-[12px] font-medium text-neutral-200">{label}</span>
        <span className="mt-0.5 block text-[10px] leading-relaxed text-neutral-500">{description}</span>
      </span>
      <span className="relative block">
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={event => onChange(Number(event.target.value))}
          className="h-9 w-full border border-neutral-700 bg-neutral-950 px-2 pr-8 text-right font-mono text-[12px] text-neutral-100 outline-none focus:border-blue-500"
        />
        {suffix && <span className="pointer-events-none absolute right-2 top-2.5 text-[10px] text-neutral-500">{suffix}</span>}
      </span>
    </label>
  )
}

function Toggle({ checked, label, onChange }: { checked: boolean; label: string; onChange: (value: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 shrink-0 border transition-colors ${checked ? 'border-blue-500 bg-blue-600' : 'border-neutral-600 bg-neutral-900'}`}
    >
      <span className={`absolute top-0.5 h-3.5 w-3.5 bg-[#F8FAFC] transition-transform ${checked ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
    </button>
  )
}

function StatusLine({ label, value, tone = 'neutral', detail }: {
  label: string
  value: string
  tone?: 'neutral' | 'green' | 'amber' | 'red'
  detail?: string
}) {
  const toneClass = tone === 'green'
    ? 'text-green-400'
    : tone === 'amber'
      ? 'text-amber-300'
      : tone === 'red'
        ? 'text-red-300'
        : 'text-neutral-200'
  return (
    <div className="grid min-h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-neutral-800 py-2 last:border-b-0">
      <div className="min-w-0">
        <div className="text-[11px] text-neutral-400">{label}</div>
        {detail && <div className="mt-0.5 truncate text-[10px] text-neutral-600">{detail}</div>}
      </div>
      <div className={`font-mono text-[11px] ${toneClass}`}>{value}</div>
    </div>
  )
}

function ConfirmationDialog({ title, description, confirmLabel, pending, onCancel, onConfirm }: {
  title: string
  description: string
  confirmLabel: string
  pending: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="absolute inset-0 z-30 flex items-center justify-center bg-black/70 p-4" role="presentation">
      <div role="alertdialog" aria-modal="true" aria-labelledby="developer-confirm-title" className="w-full max-w-[420px] border border-neutral-700 bg-[#1B212C] shadow-2xl">
        <div className="border-b border-neutral-700 px-4 py-3">
          <div id="developer-confirm-title" className="text-sm font-medium text-neutral-100">{title}</div>
          <div className="mt-1 text-[11px] leading-relaxed text-neutral-400">{description}</div>
        </div>
        <div className="flex justify-end gap-2 px-4 py-3">
          <button type="button" autoFocus disabled={pending} onClick={onCancel} className="min-h-8 border border-neutral-700 px-3 text-[11px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-40">取消</button>
          <button type="button" disabled={pending} onClick={onConfirm} className="min-h-8 border border-blue-500 bg-blue-600 px-3 text-[11px] text-white hover:bg-blue-500 disabled:opacity-40">{pending ? '处理中...' : confirmLabel}</button>
        </div>
      </div>
    </div>
  )
}

export function DeveloperSettingsPanel({ themeMode, onClose, standalone = false }: PanelProps) {
  const queryClient = useQueryClient()
  const profilesQuery = useQuery({ queryKey: ['strategy-profiles'], queryFn: fetchStrategyProfiles })
  const schedulerQuery = useQuery({ queryKey: ['scheduler-status'], queryFn: fetchSchedulerStatus, refetchInterval: 30000 })
  const validationQuery = useQuery({ queryKey: ['production-validation'], queryFn: fetchProductionValidation, staleTime: 30000 })
  const profiles = profilesQuery.data?.profiles ?? []
  const activeSignal = profiles.find(profile => profile.active_scopes.includes('signal_generation'))
  const activePaper = profiles.find(profile => profile.active_scopes.includes('paper_default'))
  const baseline = activeSignal ?? activePaper ?? profiles[0]
  const [section, setSection] = useState<SettingsSection>('overview')
  const [draft, setDraft] = useState<StrategyProfileParameters | null>(null)
  const [draftBaseRevision, setDraftBaseRevision] = useState('')
  const [note, setNote] = useState('')
  const [message, setMessage] = useState('')
  const [publishConfirmOpen, setPublishConfirmOpen] = useState(false)
  const [activationTarget, setActivationTarget] = useState<{ revision: StrategyProfileRevision; scope: string } | null>(null)

  useEffect(() => {
    if (!baseline || draft) return
    setDraft(cloneParameters(baseline.parameters))
    setDraftBaseRevision(baseline.revision_id)
  }, [baseline, draft])

  const changed = useMemo(() => {
    if (!draft || !baseline) return false
    const comparison = profiles.find(profile => profile.revision_id === draftBaseRevision) ?? baseline
    return JSON.stringify(draft) !== JSON.stringify(comparison.parameters)
  }, [draft, baseline, draftBaseRevision, profiles])

  const resetDraft = () => {
    if (!baseline) return
    setDraft(cloneParameters(baseline.parameters))
    setDraftBaseRevision(baseline.revision_id)
    setNote('')
    setMessage('')
  }

  const publishMutation = useMutation({
    mutationFn: () => createStrategyProfile({
      profile_key: baseline?.profile_key ?? 'weatherbot_conservative',
      parameters: draft!,
      change_note: note.trim(),
      activate_scopes: [],
      confirm: true,
    }),
    onSuccess: revision => {
      setPublishConfirmOpen(false)
      setMessage(`已创建 ${shortRevision(revision.revision_id)}，尚未激活。请在“版本与审计”中选择作用域。`)
      setDraft(cloneParameters(revision.parameters))
      setDraftBaseRevision(revision.revision_id)
      setNote('')
      setSection('versions')
      queryClient.invalidateQueries({ queryKey: ['strategy-profiles'] })
    },
    onError: error => {
      setPublishConfirmOpen(false)
      setMessage(error instanceof Error ? error.message : '创建版本失败')
    },
  })

  const activationMutation = useMutation({
    mutationFn: ({ revision, scope }: { revision: StrategyProfileRevision; scope: string }) =>
      activateStrategyProfile(revision.revision_id, scope, 'local developer activation'),
    onSuccess: () => {
      setActivationTarget(null)
      setMessage('策略作用域已更新，新信号或新模拟 cohort 将使用该版本。')
      queryClient.invalidateQueries({ queryKey: ['strategy-profiles'] })
    },
    onError: error => {
      setActivationTarget(null)
      setMessage(error instanceof Error ? error.message : '激活失败')
    },
  })

  const update = (path: string[], value: number | boolean) => {
    if (!draft) return
    const next = cloneParameters(draft)
    let cursor: Record<string, unknown> = next as unknown as Record<string, unknown>
    path.slice(0, -1).forEach(key => { cursor = cursor[key] as Record<string, unknown> })
    cursor[path[path.length - 1]] = value
    setDraft(next)
  }

  const surface = themeMode === 'dark' ? 'bg-[#1B212C] text-[#CBD2DC]' : 'bg-white text-gray-900'
  const raised = themeMode === 'dark' ? 'bg-[#222A37]' : 'bg-gray-50'
  const liveLocked = !(profilesQuery.data?.live_trading ?? false)

  return (
    <div className={`relative flex h-full min-h-0 flex-col ${surface}`}>
      <header className="flex min-h-14 shrink-0 items-center gap-3 border-b border-neutral-800 px-4">
        <div className="flex h-8 w-8 items-center justify-center border border-neutral-700 text-neutral-300">
          <Settings2 className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-sm font-semibold text-neutral-100">开发者设置</h1>
            <span className="hidden border border-neutral-700 px-1.5 py-0.5 font-mono text-[9px] text-neutral-500 sm:inline">{shortRevision(activeSignal?.revision_id)}</span>
          </div>
          <div className="text-[10px] text-neutral-500">策略草稿、不可变参数版本与只读运行状态</div>
        </div>
        <div className={`hidden items-center gap-1 border px-2 py-1 text-[10px] sm:inline-flex ${liveLocked ? 'border-amber-500/30 text-amber-300' : 'border-green-500/30 text-green-300'}`}>
          <LockKeyhole className="h-3.5 w-3.5" /> {liveLocked ? '实盘保持锁定' : '实盘配置已开启'}
        </div>
        {onClose && (
          <button type="button" onClick={onClose} className="inline-flex h-8 w-8 items-center justify-center border border-neutral-700 text-neutral-400 hover:bg-neutral-800 hover:text-white" aria-label={standalone ? '返回看板' : '关闭开发者设置'}>
            <X className="h-4 w-4" />
          </button>
        )}
      </header>

      <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
        <nav className={`flex shrink-0 gap-1 overflow-x-auto border-b border-neutral-800 p-2 sm:w-40 sm:flex-col sm:overflow-visible sm:border-b-0 sm:border-r ${raised}`} aria-label="开发者设置分组">
          {NAV_ITEMS.map(item => {
            const Icon = item.icon
            const active = section === item.key
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setSection(item.key)}
                className={`flex min-h-10 min-w-max items-center gap-2 border px-2.5 text-left sm:min-w-0 ${active ? 'border-blue-500/50 bg-blue-600/15 text-blue-200' : 'border-transparent text-neutral-400 hover:border-neutral-700 hover:bg-neutral-900/40 hover:text-neutral-200'}`}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="min-w-0">
                  <span className="block text-[11px] font-medium">{item.label}</span>
                  <span className="hidden truncate text-[9px] text-neutral-600 sm:block">{item.hint}</span>
                </span>
              </button>
            )
          })}
        </nav>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {profilesQuery.isLoading && <div className="p-6 text-sm text-neutral-500">正在读取策略版本...</div>}

          {!profilesQuery.isLoading && section === 'overview' && (
            <section className="px-4 py-4 sm:px-5">
              <div className="mb-4">
                <h2 className="text-[13px] font-semibold text-neutral-100">当前运行边界</h2>
                <p className="mt-1 text-[10px] leading-relaxed text-neutral-500">普通交易台只消费已激活版本。这里修改的是草稿，不会覆盖历史决策或自动解锁实盘。</p>
              </div>
              <div className="border-y border-neutral-800">
                <StatusLine label="信号生成版本" value={shortRevision(activeSignal?.revision_id)} tone="green" detail={activeSignal?.change_note || '未激活'} />
                <StatusLine label="模拟默认版本" value={shortRevision(activePaper?.revision_id)} tone="green" detail={activePaper?.change_note || '未激活'} />
                <StatusLine label="实盘状态" value={liveLocked ? 'LOCKED' : 'ENABLED'} tone={liveLocked ? 'amber' : 'green'} detail="此页面不会修改 LIVE_TRADING" />
                <StatusLine label="策略版本数量" value={String(profiles.length)} detail="不可变 revision" />
              </div>
              <div className="mt-5 border border-amber-500/30 bg-amber-500/5 px-3 py-3">
                <div className="flex items-start gap-2">
                  <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
                  <div>
                    <div className="text-[11px] font-medium text-amber-200">发布与激活分离</div>
                    <div className="mt-1 text-[10px] leading-relaxed text-neutral-500">创建版本只保存参数快照；激活到“信号生成”或“模拟默认”需要在版本页再次确认。</div>
                  </div>
                </div>
              </div>
            </section>
          )}

          {!profilesQuery.isLoading && section === 'strategy' && draft && (
            <section className="pb-24">
              <div className="border-b border-neutral-800 px-4 py-4 sm:px-5">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h2 className="text-[13px] font-semibold text-neutral-100">策略与风控</h2>
                    <p className="mt-1 text-[10px] leading-relaxed text-neutral-500">基于 {shortRevision(draftBaseRevision)} 创建草稿。调整不会立即影响信号或模拟。</p>
                  </div>
                  <button type="button" disabled={!changed} onClick={resetDraft} className="inline-flex min-h-8 items-center gap-1 border border-neutral-700 px-2 text-[10px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-30">
                    <RotateCcw className="h-3.5 w-3.5" /> 放弃更改
                  </button>
                </div>
              </div>

              <div className="px-4 py-4 sm:px-5">
                <div className="mb-2 text-[11px] font-semibold text-neutral-300">仓位控制</div>
                <div className="border-y border-neutral-800">
                  <SettingNumber label="Kelly 系数" description="对 full Kelly 结果进行折扣，降低模型误差和连续亏损风险。" value={draft.sizing.kelly_multiplier} min={0} max={1} step={0.01} onChange={value => update(['sizing', 'kelly_multiplier'], value)} />
                  <SettingNumber label="单笔本金比例" description={`当前为 ${percent(draft.sizing.max_bankroll_fraction_per_trade)}，与模拟单笔上限共同取较小值。`} value={draft.sizing.max_bankroll_fraction_per_trade} min={0.001} max={0.25} step={0.005} onChange={value => update(['sizing', 'max_bankroll_fraction_per_trade'], value)} />
                </div>

                <div className="mb-2 mt-6 text-[11px] font-semibold text-neutral-300">盘口与证据闸门</div>
                <div className="border-y border-neutral-800">
                  <SettingNumber label="最大价差" description="超过该 spread 的候选只观察，不进入模拟成交。" value={draft.decision_policy.max_spread_bps} min={0} max={5000} step={10} suffix="bps" onChange={value => update(['decision_policy', 'max_spread_bps'], value)} />
                  <SettingNumber label="盘口有效期" description="成交前会读取最新本地盘口；超过该时间则拒绝。" value={draft.decision_policy.stale_book_seconds} min={30} max={3600} step={30} suffix="秒" onChange={value => update(['decision_policy', 'stale_book_seconds'], value)} />
                  <SettingNumber label="最少独立校准日" description="按独立结算日计数，不把同一天的重复快照当成新样本。" value={draft.decision_policy.min_bias_sample_days} min={0} max={365} step={1} suffix="天" onChange={value => update(['decision_policy', 'min_bias_sample_days'], value)} />
                  <SettingNumber label="低价尾部阈值" description="低于该价格的桶会进入更严格的尾部概率检查。" value={draft.decision_policy.low_price_tail_ask} min={0} max={0.5} step={0.01} onChange={value => update(['decision_policy', 'low_price_tail_ask'], value)} />
                </div>

                <div className="mb-2 mt-6 text-[11px] font-semibold text-neutral-300">入场策略</div>
                <div className="border-y border-neutral-800">
                  {Object.entries(draft.strategies).map(([name, parameters]) => {
                    const meta = STRATEGY_META[name] ?? { label: name, description: '策略参数' }
                    return (
                      <div key={name} className="border-b border-neutral-800 py-3 last:border-b-0">
                        <div className="flex items-start justify-between gap-4">
                          <div className="min-w-0">
                            <div className="text-[12px] font-medium text-neutral-200">{meta.label}</div>
                            <div className="mt-0.5 text-[10px] leading-relaxed text-neutral-500">{meta.description}</div>
                          </div>
                          <Toggle checked={Boolean(parameters.enabled)} label={`启用${meta.label}`} onChange={value => update(['strategies', name, 'enabled'], value)} />
                        </div>
                        {Boolean(parameters.enabled) && (
                          <div className="mt-3 border-l-2 border-blue-500/40 pl-3">
                            {'min_edge' in parameters && <SettingNumber label="最低 Edge" description="模型概率减去市场 ask 的最低差值。" value={Number(parameters.min_edge)} min={0} max={0.5} step={0.01} onChange={value => update(['strategies', name, 'min_edge'], value)} />}
                            {'max_ask' in parameters && <SettingNumber label="最高买价" description="尾部策略不会追价到该值以上。" value={Number(parameters.max_ask)} min={0.01} max={0.5} step={0.01} onChange={value => update(['strategies', name, 'max_ask'], value)} />}
                            {'group_exposure_multiplier' in parameters && <SettingNumber label="组合敞口系数" description="三桶总仓位相对单桶 Kelly 的折扣。" value={Number(parameters.group_exposure_multiplier)} min={0} max={1} step={0.05} onChange={value => update(['strategies', name, 'group_exposure_multiplier'], value)} />}
                            {'min_settlement_days' in parameters && <SettingNumber label="最低独立结算日" description="尾部策略需要更长的历史证据。" value={Number(parameters.min_settlement_days)} min={0} max={365} step={1} suffix="天" onChange={value => update(['strategies', name, 'min_settlement_days'], value)} />}
                            {'max_order_usd' in parameters && <SettingNumber label="策略单笔上限" description="仍会继续受 cohort 与本金比例上限约束。" value={Number(parameters.max_order_usd)} min={0.1} max={1000} step={0.5} suffix="$" onChange={value => update(['strategies', name, 'max_order_usd'], value)} />}
                            {'daily_candidate_cap' in parameters && <SettingNumber label="每日候选上限" description="限制尾部策略每天进入队列的候选数。" value={Number(parameters.daily_candidate_cap)} min={1} max={100} step={1} onChange={value => update(['strategies', name, 'daily_candidate_cap'], value)} />}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>

                <div className="mb-2 mt-6 text-[11px] font-semibold text-neutral-300">退出方式</div>
                <div className="border-y border-neutral-800">
                  <StatusLine label="当前模式" value="HOLD TO SETTLEMENT" tone="amber" detail="信息差退出尚未具备 SELL 成交与历史盘口回放证据" />
                </div>
              </div>
            </section>
          )}

          {!profilesQuery.isLoading && section === 'versions' && (
            <section className="px-4 py-4 sm:px-5">
              <div className="mb-4">
                <h2 className="text-[13px] font-semibold text-neutral-100">版本与审计</h2>
                <p className="mt-1 text-[10px] leading-relaxed text-neutral-500">版本不可修改或删除。激活只影响对应作用域的新决策，不回写旧订单。</p>
              </div>
              <div className="border-y border-neutral-800">
                {profiles.map(profile => (
                  <div key={profile.revision_id} className="border-b border-neutral-800 py-3 last:border-b-0">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[11px] text-neutral-200">{shortRevision(profile.revision_id)}</span>
                          <span className="text-[9px] text-neutral-600">rev {profile.revision_no} · {profile.engine_version}</span>
                          {profile.active_scopes.length > 0 && <span className="inline-flex items-center gap-1 border border-green-500/30 px-1.5 py-0.5 text-[9px] text-green-300"><Check className="h-3 w-3" /> 生效</span>}
                        </div>
                        <div className="mt-1 text-[10px] text-neutral-500">{profile.change_note || '无变更说明'}</div>
                        <div className="mt-1 text-[9px] text-neutral-600">{profile.created_at ? new Date(profile.created_at).toLocaleString('zh-CN', { hour12: false }) : '--'} · {profile.created_by || 'system'}</div>
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button type="button" disabled={profile.active_scopes.includes('signal_generation') || activationMutation.isPending} onClick={() => setActivationTarget({ revision: profile, scope: 'signal_generation' })} className="min-h-8 border border-neutral-700 px-2.5 text-[10px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-30">用于信号生成</button>
                      <button type="button" disabled={profile.active_scopes.includes('paper_default') || activationMutation.isPending} onClick={() => setActivationTarget({ revision: profile, scope: 'paper_default' })} className="min-h-8 border border-neutral-700 px-2.5 text-[10px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-30">用于模拟默认</button>
                      <button type="button" onClick={() => { setDraft(cloneParameters(profile.parameters)); setDraftBaseRevision(profile.revision_id); setSection('strategy'); setMessage(`已从 ${shortRevision(profile.revision_id)} 创建本地草稿。`) }} className="ml-auto inline-flex min-h-8 items-center gap-1 border border-neutral-700 px-2.5 text-[10px] text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200">基于此版本调整 <ChevronRight className="h-3.5 w-3.5" /></button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {!profilesQuery.isLoading && section === 'system' && (
            <section className="px-4 py-4 sm:px-5">
              <div className="mb-4">
                <h2 className="text-[13px] font-semibold text-neutral-100">系统状态</h2>
                <p className="mt-1 text-[10px] leading-relaxed text-neutral-500">只读摘要用于判断是否可以开始模拟或实盘验收，不在这里启动采集或交易。</p>
              </div>
              <div className="border-y border-neutral-800">
                <StatusLine label="调度器" value={schedulerQuery.data?.running ? 'RUNNING' : 'STOPPED'} tone={schedulerQuery.data?.running ? 'green' : 'amber'} detail="采集启停仍在主看板顶部控制" />
                <StatusLine label="生产准备度" value={validationQuery.data ? `${Math.round(validationQuery.data.score * 100)}%` : '--'} tone={validationQuery.data && validationQuery.data.score >= 0.8 ? 'green' : 'amber'} detail="综合数据、truth、模拟和执行闸门" />
                <StatusLine label="实盘配置" value={liveLocked ? 'LOCKED' : 'ENABLED'} tone={liveLocked ? 'amber' : 'green'} detail="前端没有解锁开关" />
              </div>
              <div className="mt-6">
                <div className="mb-2 text-[11px] font-semibold text-neutral-300">主要阻塞项</div>
                <div className="border-y border-neutral-800">
                  {(validationQuery.data?.hard_blockers ?? []).slice(0, 6).map((blocker, index) => (
                    <div key={`${blocker}-${index}`} className="flex gap-2 border-b border-neutral-800 py-2.5 text-[10px] text-neutral-400 last:border-b-0">
                      <span className="mt-1 h-1.5 w-1.5 shrink-0 bg-amber-400" />
                      <span className="leading-relaxed">{blocker}</span>
                    </div>
                  ))}
                  {(validationQuery.data?.hard_blockers ?? []).length === 0 && <div className="py-3 text-[10px] text-neutral-500">暂无生产阻塞摘要。</div>}
                </div>
              </div>
            </section>
          )}
        </div>
      </div>

      {section === 'strategy' && draft && (
        <footer className={`absolute bottom-0 left-0 right-0 z-10 flex min-h-16 items-center gap-3 border-t border-neutral-700 px-4 shadow-[0_-12px_28px_rgba(0,0,0,0.24)] sm:left-40 ${surface}`}>
          <div className="min-w-0 flex-1">
            <label className="block text-[9px] text-neutral-500">变更说明
              <input value={note} onChange={event => setNote(event.target.value)} placeholder="说明为什么调整参数" className="mt-1 h-8 w-full border border-neutral-700 bg-neutral-950 px-2 text-[11px] text-neutral-100 outline-none focus:border-blue-500" />
            </label>
          </div>
          <button type="button" disabled={!changed || note.trim().length < 3 || publishMutation.isPending} onClick={() => setPublishConfirmOpen(true)} className="inline-flex min-h-9 shrink-0 items-center gap-1 border border-blue-500 bg-blue-600 px-3 text-[10px] text-white hover:bg-blue-500 disabled:opacity-30">
            <Save className="h-3.5 w-3.5" /> 创建新版本
          </button>
        </footer>
      )}

      {message && (
        <div role="status" className="absolute bottom-20 right-4 z-20 max-w-[420px] border border-blue-500/40 bg-[#222A37] px-3 py-2 text-[10px] text-neutral-200 shadow-xl">
          {message}
        </div>
      )}

      {publishConfirmOpen && (
        <ConfirmationDialog
          title="创建不可变策略版本？"
          description={`将基于 ${shortRevision(draftBaseRevision)} 保存一份新参数快照。新版本不会自动激活，也不会改变 LIVE_TRADING。`}
          confirmLabel="确认创建"
          pending={publishMutation.isPending}
          onCancel={() => setPublishConfirmOpen(false)}
          onConfirm={() => publishMutation.mutate()}
        />
      )}

      {activationTarget && (
        <ConfirmationDialog
          title="切换策略作用域？"
          description={`将 ${shortRevision(activationTarget.revision.revision_id)} 用于${activationTarget.scope === 'signal_generation' ? '信号生成' : '模拟默认'}。只影响后续新记录，旧决策和订单保持不变。`}
          confirmLabel="确认激活"
          pending={activationMutation.isPending}
          onCancel={() => setActivationTarget(null)}
          onConfirm={() => activationMutation.mutate(activationTarget)}
        />
      )}
    </div>
  )
}

export function DeveloperSettingsDrawer({ open, onClose, themeMode }: DrawerProps) {
  useEffect(() => {
    if (!open) return undefined
    const previousOverflow = document.body.style.overflow
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[80]" role="presentation">
      <button type="button" aria-label="关闭开发者设置" onClick={onClose} className="absolute inset-0 h-full w-full bg-[rgba(0,0,0,0.55)]" />
      <div role="dialog" aria-modal="true" aria-label="开发者设置" className="absolute inset-y-0 right-0 w-full border-l border-neutral-800 shadow-2xl sm:w-[680px]">
        <DeveloperSettingsPanel themeMode={themeMode} onClose={onClose} />
      </div>
    </div>
  )
}
