import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  KeyRound,
  Pencil,
  PlugZap,
  RotateCcw,
  Save,
  Settings2,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import {
  createStrategyProfile,
  fetchApiSettings,
  fetchStrategyProfiles,
  testApiSetting,
  updateApiSetting,
} from '../api'
import type { ApiSettingProvider, ApiSettingTestResult, StrategyProfileParameters } from '../types'

type SettingsSection = 'sources' | 'strategy'
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

const NAV_ITEMS: Array<{ key: SettingsSection; label: string; icon: typeof Settings2 }> = [
  { key: 'sources', label: '连接服务', icon: KeyRound },
  { key: 'strategy', label: '策略设置', icon: SlidersHorizontal },
]

const API_GROUPS: Array<{ label: string; keys: string[] }> = [
  { label: '天气数据', keys: ['weather_com', 'wunderground_pws', 'visual_crossing'] },
  { label: '智能审核与通知', keys: ['minimax', 'feishu'] },
] as const

const STRATEGY_META: Record<string, { label: string; description: string }> = {
  core_modal_v1: {
    label: '动态核心温度桶',
    description: '只评估模型概率最高的前两个桶；所有可用模型按先验起步，并依据真实配对误差逐步动态调权。',
  },
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

function ApiProviderCard({ provider }: { provider: ApiSettingProvider }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(!provider.configured)
  const [value, setValue] = useState('')
  const [result, setResult] = useState<ApiSettingTestResult | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const [confirmTest, setConfirmTest] = useState(false)

  useEffect(() => {
    if (!provider.configured) setEditing(true)
  }, [provider.configured])

  const saveMutation = useMutation({
    mutationFn: () => updateApiSetting(provider.key, value, false),
    onSuccess: () => {
      setValue('')
      setEditing(false)
      setResult({ provider_key: provider.key, ok: true, status: 'success', message: '已安全保存到本机 .env。', duration_ms: 0, tested_at: new Date().toISOString() })
      queryClient.invalidateQueries({ queryKey: ['api-settings'] })
      queryClient.invalidateQueries({ queryKey: ['source-health'] })
    },
    onError: error => setResult({ provider_key: provider.key, ok: false, status: 'failed', message: error instanceof Error ? error.message : '保存失败。', duration_ms: 0, tested_at: new Date().toISOString() }),
  })

  const clearMutation = useMutation({
    mutationFn: () => updateApiSetting(provider.key, '', true),
    onSuccess: () => {
      setConfirmClear(false)
      setValue('')
      setEditing(true)
      setResult({ provider_key: provider.key, ok: true, status: 'success', message: '已从本机配置中清除。', duration_ms: 0, tested_at: new Date().toISOString() })
      queryClient.invalidateQueries({ queryKey: ['api-settings'] })
      queryClient.invalidateQueries({ queryKey: ['source-health'] })
    },
    onError: error => setResult({ provider_key: provider.key, ok: false, status: 'failed', message: error instanceof Error ? error.message : '清除失败。', duration_ms: 0, tested_at: new Date().toISOString() }),
  })

  const testMutation = useMutation({
    mutationFn: (allowSideEffect: boolean) => testApiSetting(provider.key, editing ? value : '', allowSideEffect),
    onSuccess: payload => {
      setConfirmTest(false)
      setResult(payload)
    },
    onError: error => {
      setConfirmTest(false)
      setResult({ provider_key: provider.key, ok: false, status: 'failed', message: error instanceof Error ? error.message : '连接测试失败。', duration_ms: 0, tested_at: new Date().toISOString() })
    },
  })

  const runTest = () => {
    if (provider.test_has_side_effect) {
      setConfirmTest(true)
      return
    }
    testMutation.mutate(false)
  }

  const busy = saveMutation.isPending || clearMutation.isPending || testMutation.isPending
  const canUseDraft = !editing || value.trim().length > 0

  return (
    <article className="border border-neutral-800 bg-neutral-950/20">
      <div className="flex items-start gap-3 px-3 py-3">
        <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center border border-neutral-700 text-neutral-400">
          <KeyRound className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-[12px] font-medium text-neutral-100">{provider.label}</h3>
            <span className={`ml-auto inline-flex shrink-0 items-center gap-1 text-[10px] ${provider.configured ? 'text-green-400' : 'text-neutral-500'}`}>
              <span className={`h-1.5 w-1.5 ${provider.configured ? 'bg-green-400' : 'bg-amber-300'}`} />
              {provider.configured ? '已配置' : '未配置'}
            </span>
          </div>
          <p className="mt-1 text-[10px] leading-relaxed text-neutral-500">{provider.description}</p>
        </div>
        <a href={provider.docs_url} target="_blank" rel="noreferrer" className="inline-flex h-8 w-8 shrink-0 items-center justify-center border border-neutral-800 text-neutral-500 hover:border-neutral-600 hover:text-neutral-200" title="打开服务说明" aria-label={`打开${provider.label}说明`}>
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>

      <div className="border-t border-neutral-800 px-3 py-3">
        <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_auto_auto]">
          <input
            type="password"
            autoComplete="new-password"
            value={editing ? value : provider.masked_value}
            readOnly={!editing}
            onChange={event => setValue(event.target.value)}
            placeholder={provider.configured ? '已保存到本机' : '粘贴密钥或 Webhook 地址'}
            aria-label={`${provider.label}密钥`}
            className="h-9 min-w-0 flex-1 border border-neutral-700 bg-neutral-950 px-2.5 font-mono text-[11px] text-neutral-100 outline-none placeholder:font-sans placeholder:text-neutral-600 focus:border-blue-500"
          />
          {editing ? (
            <button type="button" disabled={busy || !value.trim()} onClick={() => saveMutation.mutate()} className="inline-flex h-9 items-center justify-center gap-1 border border-blue-500 bg-blue-600 px-3 text-[10px] text-white hover:bg-blue-500 disabled:opacity-30">
              <Save className="h-3.5 w-3.5" /> 保存
            </button>
          ) : (
            <button type="button" disabled={busy} onClick={() => { setEditing(true); setResult(null) }} className="inline-flex h-9 items-center justify-center gap-1 border border-neutral-700 px-3 text-[10px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-30">
              <Pencil className="h-3.5 w-3.5" /> 更新
            </button>
          )}
          <button type="button" disabled={busy || !canUseDraft} onClick={runTest} className="inline-flex h-9 items-center justify-center gap-1 border border-neutral-700 px-3 text-[10px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-30">
            <PlugZap className="h-3.5 w-3.5" /> {testMutation.isPending ? '正在验证...' : '验证连接'}
          </button>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          {editing && provider.configured && <button type="button" disabled={busy} onClick={() => { setEditing(false); setValue(''); setResult(null) }} className="min-h-8 px-2 text-[10px] text-neutral-500 hover:text-neutral-200">取消更换</button>}
          {provider.configured && !confirmClear && <button type="button" disabled={busy} onClick={() => setConfirmClear(true)} className="ml-auto min-h-8 px-2 text-[10px] text-neutral-600 hover:text-red-300">清除</button>}
        </div>

        {confirmClear && (
          <div className="mt-2 flex items-center gap-2 border border-red-500/30 bg-red-500/5 px-2.5 py-2 text-[10px] text-neutral-300">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-300" />
            <span className="min-w-0 flex-1">确认清除这个本机密钥？</span>
            <button type="button" onClick={() => setConfirmClear(false)} className="px-2 py-1 text-neutral-400">取消</button>
            <button type="button" disabled={busy} onClick={() => clearMutation.mutate()} className="border border-red-500/40 px-2 py-1 text-red-200">清除</button>
          </div>
        )}

        {confirmTest && (
          <div className="mt-2 flex items-center gap-2 border border-amber-500/30 bg-amber-500/5 px-2.5 py-2 text-[10px] text-neutral-300">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-300" />
            <span className="min-w-0 flex-1">会向飞书发送一条测试消息。</span>
            <button type="button" onClick={() => setConfirmTest(false)} className="px-2 py-1 text-neutral-400">取消</button>
            <button type="button" disabled={busy} onClick={() => testMutation.mutate(true)} className="border border-amber-500/40 px-2 py-1 text-amber-200">确认发送</button>
          </div>
        )}

        {result && (
          <div role="status" className={`mt-2 flex items-start gap-2 border px-2.5 py-2 text-[10px] ${result.ok ? 'border-green-500/30 bg-green-500/5 text-green-200' : 'border-amber-500/30 bg-amber-500/5 text-amber-200'}`}>
            {result.ok ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
            <span className="min-w-0 flex-1"><strong className="font-medium">{result.ok ? '验证成功：' : '验证失败：'}</strong>{result.message}{result.duration_ms > 0 ? `（${result.duration_ms}ms）` : ''}</span>
          </div>
        )}
      </div>
    </article>
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
  const apiSettingsQuery = useQuery({ queryKey: ['api-settings'], queryFn: fetchApiSettings, staleTime: 30000 })
  const profiles = profilesQuery.data?.profiles ?? []
  const activeSignal = profiles.find(profile => profile.active_scopes.includes('signal_generation'))
  const activePaper = profiles.find(profile => profile.active_scopes.includes('paper_default'))
  const baseline = activeSignal ?? activePaper ?? profiles[0]
  const [section, setSection] = useState<SettingsSection>('sources')
  const [draft, setDraft] = useState<StrategyProfileParameters | null>(null)
  const [draftBaseRevision, setDraftBaseRevision] = useState('')
  const [message, setMessage] = useState('')
  const [publishConfirmOpen, setPublishConfirmOpen] = useState(false)

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
    const source = profiles.find(profile => profile.revision_id === draftBaseRevision) ?? baseline
    setDraft(cloneParameters(source.parameters))
    setDraftBaseRevision(source.revision_id)
    setMessage('')
  }

  const publishMutation = useMutation({
    mutationFn: () => createStrategyProfile({
      profile_key: baseline?.profile_key ?? 'weatherbot_conservative',
      parameters: draft!,
      change_note: 'dashboard strategy update',
      activate_scopes: ['signal_generation', 'paper_default', 'live_default'],
      confirm: true,
    }),
    onSuccess: revision => {
      setPublishConfirmOpen(false)
      setMessage('策略已保存，将在下一轮信号刷新时生效。')
      setDraft(cloneParameters(revision.parameters))
      setDraftBaseRevision(revision.revision_id)
      queryClient.invalidateQueries({ queryKey: ['strategy-profiles'] })
    },
    onError: error => {
      setPublishConfirmOpen(false)
      setMessage(error instanceof Error ? error.message : '创建版本失败')
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
  return (
    <div className={`relative flex h-full min-h-0 flex-col ${surface}`}>
      <header className="flex min-h-14 shrink-0 items-center gap-3 border-b border-neutral-800 px-4">
        <div className="flex h-8 w-8 items-center justify-center border border-neutral-700 text-neutral-300">
          <Settings2 className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-sm font-semibold text-neutral-100">设置</h1>
          <div className="text-[10px] text-neutral-500">连接数据服务，管理交易策略</div>
        </div>
        {onClose && (
          <button type="button" onClick={onClose} className="inline-flex h-8 w-8 items-center justify-center border border-neutral-700 text-neutral-400 hover:bg-neutral-800 hover:text-white" aria-label={standalone ? '返回看板' : '关闭设置'}>
            <X className="h-4 w-4" />
          </button>
        )}
      </header>

      <div className="flex min-h-0 flex-1 flex-col">
        <nav className={`grid shrink-0 grid-cols-2 gap-1 border-b border-neutral-800 p-2 ${raised}`} aria-label="设置分组">
          {NAV_ITEMS.map(item => {
            const Icon = item.icon
            const active = section === item.key
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setSection(item.key)}
                className={`flex min-h-10 items-center justify-center gap-2 border px-2.5 ${active ? 'border-blue-500/50 bg-blue-600/15 text-blue-200' : 'border-transparent text-neutral-400 hover:border-neutral-700 hover:bg-neutral-900/40 hover:text-neutral-200'}`}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="text-[11px] font-medium">{item.label}</span>
              </button>
            )
          })}
        </nav>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {profilesQuery.isLoading && section !== 'sources' && <div className="p-6 text-sm text-neutral-500">正在读取设置...</div>}

          {!profilesQuery.isLoading && section === 'strategy' && draft && (
            <section className="pb-24">
              <div className="border-b border-neutral-800 px-4 py-4 sm:px-5">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h2 className="text-[13px] font-semibold text-neutral-100">策略与风控</h2>
                    <p className="mt-1 text-[10px] leading-relaxed text-neutral-500">修改后保存，下一轮信号刷新生效。</p>
                  </div>
                  <button type="button" disabled={!changed} onClick={resetDraft} className="inline-flex min-h-8 items-center gap-1 border border-neutral-700 px-2 text-[10px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-30">
                    <RotateCcw className="h-3.5 w-3.5" /> 放弃更改
                  </button>
                </div>
              </div>

              <div className="px-4 py-4 sm:px-5">
                <div className="mb-2 text-[11px] font-semibold text-neutral-300">仓位控制</div>
                <div className="border-y border-neutral-800">
                  <SettingNumber label="仓位折扣系数" description="按凯莉公式算出仓位后再应用该系数。" value={draft.sizing.paper_kelly_multiplier} min={0} max={1} step={0.01} onChange={value => update(['sizing', 'paper_kelly_multiplier'], value)} />
                  <SettingNumber label="单笔本金比例" description={`当前为 ${percent(draft.sizing.max_paper_bankroll_fraction_per_trade)}，与账户的单笔金额上限共同取较小值。`} value={draft.sizing.max_paper_bankroll_fraction_per_trade} min={0.001} max={0.25} step={0.005} onChange={value => update(['sizing', 'max_paper_bankroll_fraction_per_trade'], value)} />
                </div>

                <div className="mb-2 mt-6 text-[11px] font-semibold text-neutral-300">盘口与证据闸门</div>
                <div className="border-y border-neutral-800">
                  <SettingNumber
                    label="最低交易优势"
                    description="校正后模型概率减去当前买入价的共同下限；核心策略还会扣除 tick 与半档价差作为执行缓冲。"
                    value={Number(((draft.decision_policy.min_paper_trade_edge ?? 0.05) * 100).toFixed(2))}
                    min={0}
                    max={50}
                    step={1}
                    suffix="%"
                    onChange={value => update(['decision_policy', 'min_paper_trade_edge'], value / 100)}
                  />
                  <SettingNumber label="最大价差" description="超过该 spread 的候选只观察，不进入交易队列。" value={draft.decision_policy.max_spread_bps} min={0} max={5000} step={10} suffix="bps" onChange={value => update(['decision_policy', 'max_spread_bps'], value)} />
                  <SettingNumber label="盘口有效期" description="成交前会读取最新本地盘口；超过该时间则拒绝。" value={draft.decision_policy.stale_book_seconds} min={30} max={3600} step={30} suffix="秒" onChange={value => update(['decision_policy', 'stale_book_seconds'], value)} />
                  <SettingNumber label="成熟校准门槛" description="用于判断策略是否积累了足够独立结算样本；研究队列仍会继续收集候选。" value={draft.decision_policy.min_bias_sample_days} min={0} max={365} step={1} suffix="天" onChange={value => update(['decision_policy', 'min_bias_sample_days'], value)} />
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
                            {'max_ask' in parameters && <SettingNumber label="最高买价" description="尾部策略不会追价到该值以上。" value={Number(parameters.max_ask)} min={0.01} max={0.5} step={0.01} onChange={value => update(['strategies', name, 'max_ask'], value)} />}
                            {'group_exposure_multiplier' in parameters && <SettingNumber label="组合仓位折扣" description="相邻三桶总仓位相对单桶建议仓位的折扣。" value={Number(parameters.group_exposure_multiplier)} min={0} max={1} step={0.05} onChange={value => update(['strategies', name, 'group_exposure_multiplier'], value)} />}
                            {'min_settlement_days' in parameters && <SettingNumber label="最低独立结算日" description="尾部策略需要更长的历史证据。" value={Number(parameters.min_settlement_days)} min={0} max={365} step={1} suffix="天" onChange={value => update(['strategies', name, 'min_settlement_days'], value)} />}
                            {'max_order_usd' in parameters && <SettingNumber label="策略单笔上限" description="仍受账户金额和本金比例上限约束。" value={Number(parameters.max_order_usd)} min={0.1} max={1000} step={0.5} suffix="$" onChange={value => update(['strategies', name, 'max_order_usd'], value)} />}
                            {'daily_candidate_cap' in parameters && <SettingNumber label="每轮候选上限" description="限制单个城市、单个日期在一次信号重建中保留的尾部候选数。" value={Number(parameters.daily_candidate_cap)} min={1} max={100} step={1} onChange={value => update(['strategies', name, 'daily_candidate_cap'], value)} />}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>

              </div>
            </section>
          )}

          {section === 'sources' && (
            <section className="px-4 py-4 sm:px-5">
              <div className="mb-5 flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-[13px] font-semibold text-neutral-100">连接服务</h2>
                  <p className="mt-1 text-[10px] leading-relaxed text-neutral-500">在这里填写、更新并验证 API。已保存的内容只显示星号，明文不会返回浏览器。</p>
                </div>
                {apiSettingsQuery.data && (
                  <div className="shrink-0 border border-neutral-700 px-2 py-1 text-[10px] text-neutral-400">
                    已配置 {apiSettingsQuery.data.providers.filter(provider => provider.configured).length}/{apiSettingsQuery.data.providers.length}
                  </div>
                )}
              </div>

              {apiSettingsQuery.isLoading && <div className="border-y border-neutral-800 py-4 text-[11px] text-neutral-500">正在读取本机连接...</div>}
              {apiSettingsQuery.isError && <div className="border border-red-500/30 bg-red-500/5 px-3 py-3 text-[11px] text-red-300">连接配置读取失败，请确认后端仍在运行。</div>}
              {apiSettingsQuery.data && (
                <div className="space-y-6">
                  {API_GROUPS.map(group => (
                    <div key={group.label}>
                      <div className="mb-2 text-[10px] font-medium text-neutral-400">{group.label}</div>
                      <div className="space-y-3">
                        {apiSettingsQuery.data.providers
                          .filter(provider => group.keys.includes(provider.key))
                          .map(provider => <ApiProviderCard key={provider.key} provider={provider} />)}
                    </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

        </div>
      </div>

      {section === 'strategy' && draft && (
        <footer className={`absolute bottom-0 left-0 right-0 z-10 flex min-h-14 items-center justify-end border-t border-neutral-700 px-4 shadow-[0_-12px_28px_rgba(0,0,0,0.24)] ${surface}`}>
          <button type="button" disabled={!changed || publishMutation.isPending} onClick={() => setPublishConfirmOpen(true)} className="inline-flex min-h-9 shrink-0 items-center gap-1 border border-blue-500 bg-blue-600 px-4 text-[10px] text-white hover:bg-blue-500 disabled:opacity-30">
            <Save className="h-3.5 w-3.5" /> 保存并应用
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
          title="保存策略设置？"
          description="新设置会同时用于信号生成与两个执行适配器，并在下一轮信号刷新时生效。"
          confirmLabel="保存并应用"
          pending={publishMutation.isPending}
          onCancel={() => setPublishConfirmOpen(false)}
          onConfirm={() => publishMutation.mutate()}
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
      <button type="button" aria-label="关闭设置" onClick={onClose} className="absolute inset-0 h-full w-full bg-[rgba(0,0,0,0.55)]" />
      <div role="dialog" aria-modal="true" aria-label="设置" className="absolute inset-y-0 right-0 w-full border-l border-neutral-800 shadow-2xl sm:w-[720px]">
        <DeveloperSettingsPanel themeMode={themeMode} onClose={onClose} />
      </div>
    </div>
  )
}
