import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  KeyRound,
  LockKeyhole,
  Pencil,
  PlugZap,
  RotateCcw,
  Save,
  Settings2,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import {
  activateStrategyProfile,
  createStrategyProfile,
  fetchApiSettings,
  fetchProductionValidation,
  fetchSchedulerStatus,
  fetchSourceHealth,
  fetchStrategyProfiles,
  testApiSetting,
  updateApiSetting,
} from '../api'
import type { ApiSettingProvider, ApiSettingTestResult, StrategyProfileParameters, StrategyProfileRevision } from '../types'

type SettingsSection = 'sources' | 'strategy' | 'advanced'
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
  { key: 'advanced', label: '高级设置', icon: Settings2 },
]

const API_GROUPS: Array<{ label: string; keys: string[] }> = [
  { label: '天气数据', keys: ['weather_com', 'wunderground_pws', 'visual_crossing'] },
  { label: '智能审核与通知', keys: ['minimax', 'feishu'] },
] as const

const STRATEGY_META: Record<string, { label: string; description: string }> = {
  core_modal_v1: {
    label: '动态核心温度桶',
    description: '只评估模型概率最高的前两个桶；所有可用模型先按先验参与模拟，有真实配对误差后逐步动态调权，成熟度仅限制实盘。',
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

function shortRevision(value?: string) {
  return value ? value.slice(0, 16) : '--'
}

function percent(value: number) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`
}

function humanizeChangeNote(value?: string) {
  const note = String(value || '').trim()
  if (!note) return '未填写变更说明'
  if (note === 'Preserve existing 300-second orderbook freshness gate') return '保留 5 分钟盘口有效期限制'
  if (note === 'local developer activation') return '本机设置页激活'
  return note
}

const SOURCE_LABELS: Record<string, string> = {
  settlement_contracts: '结算规则',
  metar: 'METAR 实况',
  forecast_openmeteo: 'Open-Meteo 多模型',
  forecast_weathercom_v3: 'Weather.com v3',
  china_live: '中国 / 香港实况',
  wunderground_pws: 'Wunderground PWS',
  truth_wunderground_daily: 'WU 日结算依据',
  truth_wunderground_hourly: 'WU 历史观测',
  truth_iem_daily: 'IEM 近似结算依据',
  truth_hko_daily: 'HKO 日结算依据',
  polymarket_orderbook: 'Polymarket 盘口',
  hourly_consensus: '逐小时证据',
  signal_decisions: '信号决策',
}

function sourceStatusLabel(status: string) {
  return ({ healthy: '正常', degraded: '降级', stale: '过期', missing: '缺失', not_applicable: '不适用' } as Record<string, string>)[status] ?? status
}

function sourceToneClass(status: string) {
  if (status === 'healthy') return 'text-green-400'
  if (status === 'degraded' || status === 'stale') return 'text-amber-300'
  if (status === 'missing') return 'text-red-300'
  return 'text-neutral-500'
}

function ageLabel(seconds?: number | null) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '--'
  if (seconds < 60) return `${Math.round(seconds)} 秒前`
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`
  return `${(seconds / 3600).toFixed(1)} 小时前`
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
  const schedulerQuery = useQuery({ queryKey: ['scheduler-status'], queryFn: fetchSchedulerStatus, refetchInterval: 30000 })
  const sourceHealthQuery = useQuery({ queryKey: ['source-health'], queryFn: fetchSourceHealth, refetchInterval: 60000 })
  const apiSettingsQuery = useQuery({ queryKey: ['api-settings'], queryFn: fetchApiSettings, staleTime: 30000 })
  const validationQuery = useQuery({ queryKey: ['production-validation'], queryFn: fetchProductionValidation, staleTime: 30000 })
  const profiles = profilesQuery.data?.profiles ?? []
  const activeSignal = profiles.find(profile => profile.active_scopes.includes('signal_generation'))
  const activePaper = profiles.find(profile => profile.active_scopes.includes('paper_default'))
  const baseline = activePaper ?? activeSignal ?? profiles[0]
  const [section, setSection] = useState<SettingsSection>('sources')
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
    const source = profiles.find(profile => profile.revision_id === draftBaseRevision) ?? baseline
    setDraft(cloneParameters(source.parameters))
    setDraftBaseRevision(source.revision_id)
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
      setMessage(`已创建 ${shortRevision(revision.revision_id)}，尚未激活。请在“高级设置”的策略版本记录中选择用途。`)
      setDraft(cloneParameters(revision.parameters))
      setDraftBaseRevision(revision.revision_id)
      setNote('')
      setSection('advanced')
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
    onSuccess: (_revision, variables) => {
      setActivationTarget(null)
      setMessage(variables.scope === 'paper_default'
        ? '策略版本已更新。需要等待同版本信号重建后，策略队列才会出现可执行候选。'
        : '信号策略版本已更新，将在下一轮派生任务重建信号。')
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
  const liveConfigured = profilesQuery.data?.live_trading ?? false
  const liveExecutionReady = profilesQuery.data?.live_execution_production_ready ?? false
  const liveLocked = !(liveConfigured && liveExecutionReady)
  const liveStatusText = liveExecutionReady
    ? (liveConfigured ? '实盘已通过结构闸门' : '实盘保持锁定')
    : '实盘执行器未就绪'

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
        <div className={`hidden items-center gap-1 border px-2 py-1 text-[10px] sm:inline-flex ${liveLocked ? 'border-amber-500/30 text-amber-300' : 'border-green-500/30 text-green-300'}`}>
          <LockKeyhole className="h-3.5 w-3.5" /> {liveStatusText}
        </div>
        {onClose && (
          <button type="button" onClick={onClose} className="inline-flex h-8 w-8 items-center justify-center border border-neutral-700 text-neutral-400 hover:bg-neutral-800 hover:text-white" aria-label={standalone ? '返回看板' : '关闭设置'}>
            <X className="h-4 w-4" />
          </button>
        )}
      </header>

      <div className="flex min-h-0 flex-1 flex-col">
        <nav className={`grid shrink-0 grid-cols-3 gap-1 border-b border-neutral-800 p-2 ${raised}`} aria-label="设置分组">
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
                  <SettingNumber label="仓位折扣系数" description="按凯莉公式算出仓位后再打折，降低模型误差和连续亏损风险。" value={draft.sizing.kelly_multiplier} min={0} max={1} step={0.01} onChange={value => update(['sizing', 'kelly_multiplier'], value)} />
                  <SettingNumber label="单笔本金比例" description={`当前为 ${percent(draft.sizing.max_bankroll_fraction_per_trade)}，与模拟单笔上限共同取较小值。`} value={draft.sizing.max_bankroll_fraction_per_trade} min={0.001} max={0.25} step={0.005} onChange={value => update(['sizing', 'max_bankroll_fraction_per_trade'], value)} />
                </div>

                <div className="mb-2 mt-6 text-[11px] font-semibold text-neutral-300">盘口与证据闸门</div>
                <div className="border-y border-neutral-800">
                  <SettingNumber
                    label="最低交易优势"
                    description="校正后模型概率减去当前买入价的共同下限；核心策略还会扣除 tick 与半档价差作为执行缓冲。"
                    value={Number(((draft.decision_policy.min_trade_edge ?? 0.08) * 100).toFixed(2))}
                    min={0}
                    max={50}
                    step={1}
                    suffix="%"
                    onChange={value => update(['decision_policy', 'min_trade_edge'], value / 100)}
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
                            {'max_order_usd' in parameters && <SettingNumber label="策略单笔上限" description="仍会继续受模拟账户和本金比例上限约束。" value={Number(parameters.max_order_usd)} min={0.1} max={1000} step={0.5} suffix="$" onChange={value => update(['strategies', name, 'max_order_usd'], value)} />}
                            {'daily_candidate_cap' in parameters && <SettingNumber label="每轮候选上限" description="限制单个城市、单个日期在一次信号重建中保留的尾部候选数。" value={Number(parameters.daily_candidate_cap)} min={1} max={100} step={1} onChange={value => update(['strategies', name, 'daily_candidate_cap'], value)} />}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>

                <div className="mb-2 mt-6 text-[11px] font-semibold text-neutral-300">退出方式</div>
                <div className="border-y border-neutral-800">
                  <StatusLine label="当前模式" value="持有至结算" tone="amber" detail="赚取中间价差仍缺少卖出成交和历史盘口回放证据" />
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

              <div className="mt-5 flex items-start gap-2 border border-amber-500/25 bg-amber-500/5 px-3 py-2.5 text-[10px] leading-relaxed text-neutral-400">
                <LockKeyhole className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300" />
                <span>钱包私钥不在网页中配置。公开行情无需密钥，实盘继续保持锁定。</span>
              </div>
            </section>
          )}

          {!profilesQuery.isLoading && section === 'advanced' && (
            <section className="px-4 py-4 sm:px-5">
              <div className="mb-4">
                <h2 className="text-[13px] font-semibold text-neutral-100">高级设置</h2>
                <p className="mt-1 text-[10px] leading-relaxed text-neutral-500">日常使用无需修改。这里保留策略版本和只读诊断，便于排查与复盘。</p>
              </div>

              <div className="space-y-3">
                <details className="border border-neutral-800">
                  <summary className="cursor-pointer px-3 py-3 text-[11px] font-medium text-neutral-300 hover:bg-neutral-900/40">当前运行状态</summary>
                  <div className="border-t border-neutral-800 px-3">
                    <StatusLine label="信号策略" value={shortRevision(activeSignal?.revision_id)} tone="green" detail={humanizeChangeNote(activeSignal?.change_note)} />
                    <StatusLine label="交易策略" value={shortRevision(activePaper?.revision_id)} tone="green" detail={humanizeChangeNote(activePaper?.change_note)} />
                    <StatusLine label="数据调度" value={schedulerQuery.data?.running ? '运行中' : '已停止'} tone={schedulerQuery.data?.running ? 'green' : 'amber'} />
                    <StatusLine label="实盘" value={liveStatusText} tone={liveLocked ? 'amber' : 'green'} />
                  </div>
                </details>

                <details className="border border-neutral-800">
                  <summary className="cursor-pointer px-3 py-3 text-[11px] font-medium text-neutral-300 hover:bg-neutral-900/40">策略版本记录（{profiles.length}）</summary>
                  <div className="border-t border-neutral-800 px-3">
                    {profiles.map(profile => (
                      <div key={profile.revision_id} className="border-b border-neutral-800 py-3 last:border-b-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[11px] text-neutral-200">{shortRevision(profile.revision_id)}</span>
                          <span className="text-[9px] text-neutral-600">版本 {profile.revision_no}</span>
                          {profile.active_scopes.length > 0 && <span className="inline-flex items-center gap-1 border border-green-500/30 px-1.5 py-0.5 text-[9px] text-green-300"><Check className="h-3 w-3" /> 生效</span>}
                        </div>
                        <div className="mt-1 text-[10px] text-neutral-500">{humanizeChangeNote(profile.change_note)}</div>
                        <div className="mt-1 text-[9px] text-neutral-600">{profile.created_at ? new Date(profile.created_at).toLocaleString('zh-CN', { hour12: false }) : '--'}</div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          <button type="button" disabled={profile.active_scopes.includes('signal_generation') || activationMutation.isPending} onClick={() => setActivationTarget({ revision: profile, scope: 'signal_generation' })} className="min-h-8 border border-neutral-700 px-2.5 text-[10px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-30">用于信号</button>
                          <button type="button" disabled={profile.active_scopes.includes('paper_default') || activationMutation.isPending} onClick={() => setActivationTarget({ revision: profile, scope: 'paper_default' })} className="min-h-8 border border-neutral-700 px-2.5 text-[10px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-30">用于交易队列</button>
                          <button type="button" onClick={() => { setDraft(cloneParameters(profile.parameters)); setDraftBaseRevision(profile.revision_id); setSection('strategy'); setMessage(`已从 ${shortRevision(profile.revision_id)} 创建本地草稿。`) }} className="ml-auto inline-flex min-h-8 items-center gap-1 border border-neutral-700 px-2.5 text-[10px] text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200">调整此版本 <ChevronRight className="h-3.5 w-3.5" /></button>
                        </div>
                      </div>
                    ))}
                  </div>
                </details>

                <details className="border border-neutral-800">
                  <summary className="cursor-pointer px-3 py-3 text-[11px] font-medium text-neutral-300 hover:bg-neutral-900/40">系统诊断</summary>
                  <div className="border-t border-neutral-800 px-3 pb-3">
                    <StatusLine label="生产准备度" value={validationQuery.data ? `${Math.round(validationQuery.data.score * 100)}%` : '--'} tone={validationQuery.data && validationQuery.data.score >= 0.8 ? 'green' : 'amber'} />
                    {sourceHealthQuery.isLoading && <div className="py-3 text-[10px] text-neutral-500">正在读取数据源状态...</div>}
                    {sourceHealthQuery.isError && <div className="py-3 text-[10px] text-red-300">数据源状态读取失败。</div>}
                    {sourceHealthQuery.data?.sources.map(source => (
                      <div key={source.key} className="grid min-h-11 grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-neutral-800 py-2 last:border-b-0">
                        <div className="min-w-0">
                          <div className="text-[10px] text-neutral-400">{SOURCE_LABELS[source.key] ?? source.label}</div>
                          <div className="truncate text-[9px] text-neutral-600">覆盖 {source.coverage_pct ?? 0}% · {ageLabel(source.age_seconds)}</div>
                        </div>
                        <div className={`text-[9px] ${sourceToneClass(source.status)}`} title={(source.reasons ?? []).join(', ')}>{sourceStatusLabel(source.status)}</div>
                      </div>
                    ))}
                    {(validationQuery.data?.hard_blockers ?? []).length > 0 && (
                      <div className="mt-3 border-t border-neutral-800 pt-3 text-[10px] text-amber-200">
                        当前仍有 {(validationQuery.data?.hard_blockers ?? []).length} 项生产阻塞，实盘不会解锁。
                      </div>
                    )}
                  </div>
                </details>
                </div>
            </section>
          )}
        </div>
      </div>

      {section === 'strategy' && draft && (
        <footer className={`absolute bottom-0 left-0 right-0 z-10 flex min-h-16 items-center gap-3 border-t border-neutral-700 px-4 shadow-[0_-12px_28px_rgba(0,0,0,0.24)] ${surface}`}>
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
      <button type="button" aria-label="关闭设置" onClick={onClose} className="absolute inset-0 h-full w-full bg-[rgba(0,0,0,0.55)]" />
      <div role="dialog" aria-modal="true" aria-label="设置" className="absolute inset-y-0 right-0 w-full border-l border-neutral-800 shadow-2xl sm:w-[720px]">
        <DeveloperSettingsPanel themeMode={themeMode} onClose={onClose} />
      </div>
    </div>
  )
}
