import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, FlaskConical, LockKeyhole, RotateCcw, Save, ShieldCheck } from 'lucide-react'
import { activateStrategyProfile, createStrategyProfile, fetchProductionValidation, fetchSchedulerStatus, fetchStrategyProfiles } from '../api'
import type { StrategyProfileParameters, StrategyProfileRevision } from '../types'


const STRATEGY_LABELS: Record<string, string> = {
  single_bucket_ev: '单桶最高温',
  ladder_grid: '相邻三桶阶梯',
  tail_buying: '低价尾部',
}

function cloneParameters(value: StrategyProfileParameters): StrategyProfileParameters {
  return JSON.parse(JSON.stringify(value)) as StrategyProfileParameters
}

function shortRevision(value?: string) {
  return value ? value.slice(0, 16) : '--'
}

function NumberField({ label, value, min, max, step, onChange }: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (value: number) => void
}) {
  return (
    <label className="block text-xs text-neutral-400">
      <span>{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={event => onChange(Number(event.target.value))}
        className="mt-1 h-9 w-full border border-neutral-700 bg-black px-2 text-right font-mono text-sm text-neutral-100 outline-none focus:border-cyan-500"
      />
    </label>
  )
}

export default function DeveloperPage() {
  const queryClient = useQueryClient()
  const profilesQuery = useQuery({ queryKey: ['strategy-profiles'], queryFn: fetchStrategyProfiles })
  const schedulerQuery = useQuery({ queryKey: ['scheduler-status'], queryFn: fetchSchedulerStatus, refetchInterval: 30000 })
  const validationQuery = useQuery({ queryKey: ['production-validation'], queryFn: fetchProductionValidation, staleTime: 30000 })
  const profiles = profilesQuery.data?.profiles ?? []
  const activeSignal = profiles.find(profile => profile.active_scopes.includes('signal_generation'))
  const activePaper = profiles.find(profile => profile.active_scopes.includes('paper_default'))
  const baseline = activeSignal ?? activePaper ?? profiles[0]
  const [draft, setDraft] = useState<StrategyProfileParameters | null>(null)
  const [note, setNote] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (baseline && !draft) setDraft(cloneParameters(baseline.parameters))
  }, [baseline, draft])

  const changed = useMemo(() => {
    if (!draft || !baseline) return false
    return JSON.stringify(draft) !== JSON.stringify(baseline.parameters)
  }, [draft, baseline])

  const publishMutation = useMutation({
    mutationFn: () => createStrategyProfile({
      profile_key: baseline?.profile_key ?? 'weatherbot_conservative',
      parameters: draft!,
      change_note: note.trim() || 'local developer strategy revision',
      activate_scopes: ['signal_generation', 'paper_default'],
      confirm: true,
    }),
    onSuccess: revision => {
      setMessage(`已发布并激活 ${shortRevision(revision.revision_id)}`)
      setConfirmed(false)
      setNote('')
      queryClient.invalidateQueries({ queryKey: ['strategy-profiles'] })
    },
    onError: error => setMessage(error instanceof Error ? error.message : '发布失败'),
  })

  const activationMutation = useMutation({
    mutationFn: ({ revision, scope }: { revision: StrategyProfileRevision; scope: string }) =>
      activateStrategyProfile(revision.revision_id, scope, 'local developer activation'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['strategy-profiles'] }),
    onError: error => setMessage(error instanceof Error ? error.message : '激活失败'),
  })

  const update = (path: string[], value: number | boolean) => {
    if (!draft) return
    const next = cloneParameters(draft)
    let cursor: Record<string, unknown> = next as unknown as Record<string, unknown>
    path.slice(0, -1).forEach(key => { cursor = cursor[key] as Record<string, unknown> })
    cursor[path[path.length - 1]] = value
    setDraft(next)
  }

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <header className="flex min-h-14 items-center gap-3 border-b border-neutral-800 bg-black px-4">
        <a href="/" className="inline-flex h-9 w-9 items-center justify-center border border-neutral-800 text-neutral-300 hover:bg-neutral-900" title="返回天气工作台">
          <ArrowLeft className="h-4 w-4" />
        </a>
        <div className="min-w-0">
          <div className="text-sm font-semibold">WeatherBot 开发者模式</div>
          <div className="text-[10px] text-neutral-500">Layer 8 策略实验室 · 不可变参数版本</div>
        </div>
        <div className="ml-auto inline-flex items-center gap-1 border border-amber-500/30 px-2 py-1 text-[10px] text-amber-300">
          <LockKeyhole className="h-3.5 w-3.5" /> 实盘保持锁定
        </div>
      </header>

      <main className="mx-auto grid max-w-[1440px] gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_420px]">
        <section className="min-w-0 border border-neutral-800 bg-black">
          <div className="flex items-start justify-between gap-3 border-b border-neutral-800 p-4">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium"><FlaskConical className="h-4 w-4 text-cyan-300" /> 策略参数</div>
              <div className="mt-1 text-[11px] text-neutral-500">修改不会覆盖旧版本；发布后新信号与新模拟 cohort 才使用新 revision。</div>
            </div>
            <button
              type="button"
              disabled={!baseline}
              onClick={() => baseline && setDraft(cloneParameters(baseline.parameters))}
              className="inline-flex h-8 items-center gap-1 border border-neutral-700 px-2 text-[10px] text-neutral-300 disabled:opacity-40"
            >
              <RotateCcw className="h-3.5 w-3.5" /> 重置
            </button>
          </div>

          {!draft ? (
            <div className="p-6 text-sm text-neutral-500">正在读取策略版本...</div>
          ) : (
            <div className="space-y-5 p-4">
              <div>
                <div className="mb-2 text-xs font-medium text-neutral-300">仓位与通用闸门</div>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <NumberField label="Kelly 系数" value={draft.sizing.kelly_multiplier} min={0} max={1} step={0.01} onChange={value => update(['sizing', 'kelly_multiplier'], value)} />
                  <NumberField label="单笔本金比例" value={draft.sizing.max_bankroll_fraction_per_trade} min={0.001} max={0.25} step={0.005} onChange={value => update(['sizing', 'max_bankroll_fraction_per_trade'], value)} />
                  <NumberField label="最大价差 bps" value={draft.decision_policy.max_spread_bps} min={0} max={5000} step={10} onChange={value => update(['decision_policy', 'max_spread_bps'], value)} />
                  <NumberField label="盘口有效期（秒）" value={draft.decision_policy.stale_book_seconds} min={30} max={3600} step={30} onChange={value => update(['decision_policy', 'stale_book_seconds'], value)} />
                </div>
              </div>

              <div>
                <div className="mb-2 text-xs font-medium text-neutral-300">入场策略</div>
                <div className="grid gap-3 lg:grid-cols-3">
                  {Object.entries(draft.strategies).map(([name, parameters]) => (
                    <div key={name} className="border border-neutral-800 p-3">
                      <label className="flex items-center justify-between gap-2 text-xs text-neutral-200">
                        <span>{STRATEGY_LABELS[name] ?? name}</span>
                        <input type="checkbox" checked={Boolean(parameters.enabled)} onChange={event => update(['strategies', name, 'enabled'], event.target.checked)} />
                      </label>
                      <div className="mt-3 space-y-2">
                        {'min_edge' in parameters && <NumberField label="最低 edge" value={Number(parameters.min_edge)} min={0} max={0.5} step={0.01} onChange={value => update(['strategies', name, 'min_edge'], value)} />}
                        {'max_ask' in parameters && <NumberField label="最高买价" value={Number(parameters.max_ask)} min={0.01} max={0.5} step={0.01} onChange={value => update(['strategies', name, 'max_ask'], value)} />}
                        {'group_exposure_multiplier' in parameters && <NumberField label="组合敞口系数" value={Number(parameters.group_exposure_multiplier)} min={0} max={1} step={0.05} onChange={value => update(['strategies', name, 'group_exposure_multiplier'], value)} />}
                        {'min_settlement_days' in parameters && <NumberField label="最低独立结算日" value={Number(parameters.min_settlement_days)} min={0} max={365} step={1} onChange={value => update(['strategies', name, 'min_settlement_days'], value)} />}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="grid gap-3 border-t border-neutral-800 pt-4 sm:grid-cols-[minmax(0,1fr)_auto]">
                <div>
                  <label className="block text-xs text-neutral-400">变更说明
                    <input value={note} onChange={event => setNote(event.target.value)} className="mt-1 h-9 w-full border border-neutral-700 bg-black px-2 text-sm text-neutral-100 outline-none focus:border-cyan-500" />
                  </label>
                  <label className="mt-2 flex items-center gap-2 text-[11px] text-neutral-400">
                    <input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />
                    我确认创建不可变版本，并激活到信号生成与模拟默认作用域
                  </label>
                </div>
                <button
                  type="button"
                  disabled={!changed || !confirmed || publishMutation.isPending}
                  onClick={() => publishMutation.mutate()}
                  className="inline-flex min-h-10 items-center justify-center gap-1 self-end border border-cyan-500/40 bg-cyan-500/10 px-4 text-xs text-cyan-200 disabled:opacity-30"
                >
                  <Save className="h-4 w-4" /> 发布新版本
                </button>
              </div>
              {message && <div className="border border-neutral-800 px-3 py-2 text-xs text-neutral-300">{message}</div>}
            </div>
          )}
        </section>

        <aside className="min-w-0 border border-neutral-800 bg-black">
          <div className="border-b border-neutral-800 p-4">
            <div className="flex items-center gap-2 text-sm font-medium"><ShieldCheck className="h-4 w-4 text-green-300" /> 版本记录</div>
            <div className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
              <div className="border border-neutral-800 p-2"><div className="text-neutral-500">信号生成</div><div className="mt-1 font-mono text-neutral-200">{shortRevision(activeSignal?.revision_id)}</div></div>
              <div className="border border-neutral-800 p-2"><div className="text-neutral-500">模拟默认</div><div className="mt-1 font-mono text-neutral-200">{shortRevision(activePaper?.revision_id)}</div></div>
            </div>
          </div>
          <div className="max-h-[calc(100vh-180px)] overflow-y-auto">
            {profiles.map(profile => (
              <div key={profile.revision_id} className="border-b border-neutral-800 p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-mono text-[11px] text-neutral-200">{shortRevision(profile.revision_id)}</div>
                    <div className="mt-1 text-[10px] text-neutral-500">rev {profile.revision_no} · {profile.engine_version}</div>
                  </div>
                  {profile.active_scopes.length > 0 && <span className="inline-flex items-center gap-1 text-[9px] text-green-300"><Check className="h-3 w-3" /> 生效</span>}
                </div>
                <div className="mt-2 text-[10px] text-neutral-500">{profile.change_note || '无说明'}</div>
                <div className="mt-2 flex gap-1">
                  <button type="button" disabled={profile.active_scopes.includes('signal_generation') || activationMutation.isPending} onClick={() => activationMutation.mutate({ revision: profile, scope: 'signal_generation' })} className="border border-neutral-700 px-2 py-1 text-[9px] text-neutral-300 disabled:opacity-30">用于信号</button>
                  <button type="button" disabled={profile.active_scopes.includes('paper_default') || activationMutation.isPending} onClick={() => activationMutation.mutate({ revision: profile, scope: 'paper_default' })} className="border border-neutral-700 px-2 py-1 text-[9px] text-neutral-300 disabled:opacity-30">用于模拟</button>
                </div>
              </div>
            ))}
            <div className="border-b border-neutral-800 p-3">
              <div className="text-xs font-medium text-neutral-300">系统状态（只读）</div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
                <div className="border border-neutral-800 p-2"><div className="text-neutral-500">调度器</div><div className="mt-1 text-neutral-200">{schedulerQuery.data?.running ? '运行中' : '已停止'}</div></div>
                <div className="border border-neutral-800 p-2"><div className="text-neutral-500">生产评分</div><div className="mt-1 text-neutral-200">{validationQuery.data ? `${Math.round(validationQuery.data.score * 100)}%` : '--'}</div></div>
              </div>
              <div className="mt-2 border border-amber-500/20 bg-amber-500/5 p-2 text-[10px] text-amber-200">
                {(validationQuery.data?.hard_blockers ?? []).slice(0, 3).join('；') || '暂无生产阻塞摘要'}
              </div>
            </div>
          </div>
        </aside>
      </main>
    </div>
  )
}
