import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  CheckCircle2,
  FlaskConical,
  ListChecks,
  PauseCircle,
  RefreshCw,
  ShieldAlert,
  Star,
  Wallet,
} from 'lucide-react'
import {
  backfillWeatherHistory,
  fetchDashboard,
  fetchDailyMaxPredictions,
  fetchForecastArchiveManifest,
  fetchMarketBuckets,
  fetchModelRepriceEvents,
  fetchProductionRefreshStatus,
  fetchProductionValidation,
  fetchSchedulerStatus,
  fetchSignalDecisions,
  fetchSettlementContracts,
  fetchTruthDeltaAudit,
  placeLiveOrder,
  resetSimulation,
  runProductionAction,
  runProductionRefresh,
  setAutoSimulation,
  setStationEnabled,
  settleTradesApi,
  startScheduler,
  stopScheduler,
  stopBot,
  updateSignalStatus,
  verifySettlementContract,
  verifySettlementContractsBulk,
} from './api'
import { DataReadinessPanel } from './components/DataReadinessPanel'
import { ModelDatasetPanel } from './components/ModelDatasetPanel'
import { SignalsTable } from './components/SignalsTable'
import { TradesTable } from './components/TradesTable'
import { TruthHealthPanel } from './components/TruthHealthPanel'
import { WeatherPanel } from './components/WeatherPanel'
import { DeltaAuditPanel } from './components/DeltaAuditPanel'
import { useT, type I18nLanguage } from './i18n/useT'
import type { AutoSimulationStatus, BotStats, CityStatusConfig, CityTradingStatus, DashboardRecommendationItem, DataReadiness, ProductionActionRunResult, ProductionRefreshResult, ProductionValidationAction, ProductionValidationReport, SchedulerPollerStatus, SchedulerStatus } from './types'

type TradeMode = 'paper' | 'live'
type UiLanguage = 'zh' | 'en'
type ThemeMode = 'light' | 'dark'
type MainView = 'workbench' | 'delta'
type ProductionRefreshOptions = {
  cities?: string[]
  days?: number
  limit?: number
  startDate?: string
  endDate?: string
  source?: 'manual' | 'auto'
}
type RefreshNotice = {
  id: number
  tone: 'running' | 'success' | 'warning' | 'error'
  title: string
  message: string
  details?: string[]
}

const APP_VERSION = 'v6.0'

const UI_COPY = {
  zh: {
    subtitle: '天气量化交易平台',
    data: '数据',
    manual: '手动刷新',
    legacyRunning: '旧扫描运行中',
    autoOn: '一键模拟运行中',
    autoOff: '一键模拟关闭',
    liveReady: '实盘可用',
    liveLocked: '实盘锁定',
    refreshCurrent: '刷新当前城市',
    schedulerStart: '启动调度器',
    schedulerStop: '停止调度器',
    fetching: '抓取中',
    refresh: '刷新',
    stopLegacy: '停止旧扫描',
    language: '语言',
    theme: '主题',
  },
  en: {
    subtitle: 'weather quant trading platform',
    data: 'Data',
    manual: 'Manual refresh',
    legacyRunning: 'Legacy scan running',
    autoOn: 'Auto paper running',
    autoOff: 'Auto paper off',
    liveReady: 'Live ready',
    liveLocked: 'Live locked',
    refreshCurrent: 'Refresh city',
    schedulerStart: 'Start scheduler',
    schedulerStop: 'Stop scheduler',
    fetching: 'Fetching',
    refresh: 'Refresh',
    stopLegacy: 'Stop legacy scan',
    language: 'Language',
    theme: 'Theme',
  },
} satisfies Record<UiLanguage, Record<string, string>>

const MAINLAND_CITY_KEYS = new Set(['shanghai', 'beijing', 'wuhan', 'qingdao', 'shenzhen'])
const ASIA_OTHER_CITY_KEYS = new Set(['hong-kong', 'tokyo', 'seoul', 'taipei', 'singapore'])
const ROUND5_STATUS_FALLBACK: Record<string, CityStatusConfig> = {
  shanghai: { status: 'fully_active', rank: 1, settlement: 'verified WU' },
  'hong-kong': { status: 'paper_only', rank: 2, settlement: 'HKO mismatch' },
  seoul: { status: 'monitor_only', rank: 3, settlement: 'verified WU', reason: 'external_pnl_negative' },
  tokyo: { status: 'fully_active', rank: 4, settlement: 'verified WU' },
  beijing: { status: 'fully_active', rank: 5, settlement: 'verified WU' },
  singapore: { status: 'fully_active', rank: 6, settlement: 'verified WU' },
  taipei: { status: 'fully_active', rank: 7, settlement: 'verified WU' },
  shenzhen: { status: 'fully_active', rank: 8, settlement: 'verified WU' },
  wuhan: { status: 'fully_active', rank: 9, settlement: 'verified WU' },
  qingdao: { status: 'fully_active', rank: 10, settlement: 'verified WU' },
}

const STATUS_ICON: Record<CityTradingStatus, string> = {
  fully_active: '🟢',
  paper_only: '🟡',
  monitor_only: '🔴',
  observation_only: '⚪',
}

function resolveCityTradingStatus(
  cityKey?: string,
  verificationStatus?: string,
  statusMap?: Record<string, CityStatusConfig>,
): CityTradingStatus {
  const key = cityKey || ''
  const configured = (statusMap?.[key]?.status || ROUND5_STATUS_FALLBACK[key]?.status || '') as CityTradingStatus | ''
  if (configured === 'fully_active' || configured === 'paper_only' || configured === 'monitor_only' || configured === 'observation_only') return configured
  if (verificationStatus === 'settlement_mismatch') return 'paper_only'
  if (verificationStatus === 'no_active_market') return 'observation_only'
  return 'observation_only'
}

function statusTone(status: CityTradingStatus) {
  if (status === 'fully_active') return 'border-green-500/30 bg-green-500/10 text-green-200'
  if (status === 'paper_only') return 'border-amber-500/35 bg-amber-500/10 text-amber-100'
  if (status === 'monitor_only') return 'border-red-500/35 bg-red-500/10 text-red-100'
  return 'border-neutral-700 bg-neutral-900/50 text-neutral-400'
}

function cityGroupKey(cityKey: string, continent?: string) {
  if (MAINLAND_CITY_KEYS.has(cityKey)) return 'mainland'
  if (ASIA_OTHER_CITY_KEYS.has(cityKey)) return 'asia'
  return continent === 'Asia Pacific' || continent === 'Asia' ? 'asia' : 'us'
}

const EMPTY_STATS: BotStats = {
  is_running: false,
  last_run: null,
  total_trades: 0,
  open_trades: 0,
  settled_trades: 0,
  total_pnl: 0,
  bankroll: 40,
  winning_trades: 0,
  win_rate: 0,
  simulation_started_at: null,
}

function money(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${Number(value) >= 0 ? '' : '-'}$${Math.abs(Number(value)).toFixed(2)}`
}

function timeText(value?: string | null) {
  if (!value) return '暂无'
  try {
    return new Date(value).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch {
    return value
  }
}

function dataAge(minutes?: number | null) {
  if (minutes === null || minutes === undefined) return '暂无'
  if (minutes < 60) return `${minutes.toFixed(0)} 分钟前`
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)} 小时前`
  return `${(minutes / 1440).toFixed(1)} 天前`
}

function relativeTime(value?: string | null) {
  if (!value) return '--'
  const timestamp = new Date(value).getTime()
  if (!Number.isFinite(timestamp)) return '--'
  const seconds = Math.max(0, (Date.now() - timestamp) / 1000)
  if (seconds < 60) return `${Math.round(seconds)} 秒前`
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时前`
  return `${(seconds / 86400).toFixed(1)} 天前`
}

function pollerAgeLabel(poller?: SchedulerPollerStatus | null) {
  const age = poller?.age_seconds
  if (age === null || age === undefined || !Number.isFinite(Number(age))) return 'never'
  if (Number(age) < 60) return `${Math.round(Number(age))}s ago`
  if (Number(age) < 3600) return `${Math.round(Number(age) / 60)}m ago`
  if (Number(age) < 86400) return `${(Number(age) / 3600).toFixed(1)}h ago`
  return `${(Number(age) / 86400).toFixed(1)}d ago`
}

function durationLabel(ms?: number | null) {
  if (ms === null || ms === undefined || !Number.isFinite(Number(ms))) return '--ms'
  if (Number(ms) < 1000) return `${Math.round(Number(ms))}ms`
  return `${(Number(ms) / 1000).toFixed(1)}s`
}

function ageSecondsLabel(seconds?: number | null) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return '--'
  if (Number(seconds) < 60) return `${Math.round(Number(seconds))}s ago`
  if (Number(seconds) < 3600) return `${Math.round(Number(seconds) / 60)}m ago`
  return `${(Number(seconds) / 3600).toFixed(1)}h ago`
}

function tempLabel(value?: number | null, unit = '') {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return `${Number(value).toFixed(1)}°${unit || ''}`
}

function probabilityLabel(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  return `${(Number(value) * 100).toFixed(1)}%`
}

function edgeLabel(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--'
  const sign = Number(value) >= 0 ? '+' : ''
  return `${sign}${(Number(value) * 100).toFixed(1)}pp`
}

function pollerTone(poller?: SchedulerPollerStatus | null) {
  if (poller?.running) return 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200'
  const age = poller?.age_seconds
  if (age === null || age === undefined || !Number.isFinite(Number(age))) return 'border-neutral-800 text-neutral-500'
  if (Number(age) < 600) return 'border-green-500/30 text-green-300'
  if (Number(age) < 3600) return 'border-amber-500/30 text-amber-300'
  return 'border-red-500/30 text-red-300'
}

function SchedulerBadge({ poller, label }: { poller?: SchedulerPollerStatus | null; label: string }) {
  const fails = Number(poller?.fails_last_hour ?? 0)
  return (
    <span
      className={`shrink-0 border px-2 py-1 tabular-nums ${pollerTone(poller)}`}
      title={`${label} next: ${poller?.next_run_at ? timeText(poller.next_run_at) : '--'} · ${poller?.last_message || 'waiting'}`}
    >
      {label} {pollerAgeLabel(poller)} ({durationLabel(poller?.last_duration_ms)})
      {fails > 0 ? ` ⚠ ${fails} fail/hr` : ''}
    </span>
  )
}

function RecommendationCard({
  item,
  selected,
  onSelect,
}: {
  item: DashboardRecommendationItem
  selected: boolean
  onSelect: () => void
}) {
  const isObservationOnly = item.type === 'observation_only'
  const age = ageSecondsLabel(item.metar_age_seconds)
  const verified = item.verification_status || (item.settlement_rule_verified_at ? 'verified' : 'unverified')
  const blocker = item.blocked_reasons?.[0] ? reasonLabel(item.blocked_reasons[0]) : (item.paper_allowed ? 'paper allowed' : 'watch')
  const cardTone = selected
    ? 'border-cyan-500/50 bg-cyan-500/10 text-cyan-50'
    : isObservationOnly
      ? 'border-red-400/30 bg-red-500/5 text-neutral-200 hover:border-red-300/50'
      : 'border-amber-500/30 bg-amber-500/5 text-neutral-200 hover:border-amber-400/60'

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect()
        }
      }}
      className={`min-w-0 cursor-pointer border p-2 text-left transition ${cardTone}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[12px] font-semibold">{item.city_name}</div>
          <div className="mt-0.5 truncate text-[9px] text-neutral-500">
            {item.station_id || '--'} · METAR age {age} · verified {verified}
          </div>
        </div>
        <span className={`shrink-0 border px-1.5 py-0.5 text-[9px] ${
          isObservationOnly ? 'border-red-400/30 text-red-200' : item.paper_allowed ? 'border-green-400/30 text-green-200' : 'border-amber-400/30 text-amber-200'
        }`}>
          {isObservationOnly ? '仅观测分析（无市场）' : item.paper_allowed ? 'Paper ok' : 'Spread watch'}
        </span>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-1 text-[10px]">
        <div className="border border-neutral-800/80 px-2 py-1">
          <div className="text-neutral-500">当前观测</div>
          <div className="mt-0.5 tabular-nums text-neutral-100">{tempLabel(item.current_temp, item.current_temp_unit)}</div>
        </div>
        <div className="border border-neutral-800/80 px-2 py-1">
          <div className="text-neutral-500">DEB μ±σ</div>
          <div className="mt-0.5 tabular-nums text-neutral-100">
            {tempLabel(item.deb_mu, item.deb_unit)} ± {item.deb_sigma === null || item.deb_sigma === undefined ? '--' : Number(item.deb_sigma).toFixed(2)}
          </div>
        </div>
      </div>

      {isObservationOnly ? (
        <div className="mt-2 border border-neutral-800/80 px-2 py-1.5 text-[10px] text-neutral-400">
          China Weather Live {tempLabel(item.china_live_temp, item.current_temp_unit)}
          {item.china_live_observed_at ? ` · ${relativeTime(item.china_live_observed_at)}` : ''} · 交易字段已隐藏
        </div>
      ) : (
        <>
          <div className="mt-2 grid grid-cols-[minmax(0,1fr)_82px] gap-1 text-[10px]">
            <div className="min-w-0 border border-neutral-800/80 px-2 py-1">
              <div className="text-neutral-500">最优桶</div>
              <div className="mt-0.5 truncate text-neutral-100">{item.bucket_label || item.bucket_key || '--'}</div>
            </div>
            <div className="border border-neutral-800/80 px-2 py-1">
              <div className="text-neutral-500">Edge</div>
              <div className={`mt-0.5 tabular-nums ${Number(item.edge ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'}`}>
                {edgeLabel(item.edge)}
              </div>
            </div>
          </div>
          <div className="mt-1 flex flex-wrap gap-1 text-[9px]">
            <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">model {probabilityLabel(item.model_probability)}</span>
            <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">ask {probabilityLabel(item.market_ask)}</span>
            {item.blocked_reasons?.length ? (
              <span className="border border-amber-500/30 px-1.5 py-0.5 text-amber-200">{blocker}</span>
            ) : null}
            {item.polymarket_url ? (
              <a
                href={item.polymarket_url}
                target="_blank"
                rel="noreferrer"
                onClick={event => event.stopPropagation()}
                className="border border-cyan-500/30 px-1.5 py-0.5 text-cyan-200 hover:bg-cyan-500/10"
              >
                Polymarket
              </a>
            ) : null}
          </div>
        </>
      )}
    </div>
  )
}

function reasonLabel(reason: string) {
  const map: Record<string, string> = {
    truth_observations_below_min: '高置信 truth 样本不足',
    open_meteo_truth_fallback_present: '仍有 Open-Meteo fallback',
    legacy_truth_unknown: '存在旧版未知 truth',
    settled_sample_missing: '已结算样本不足',
    sample_low: '回放样本不足',
    pnl_negative: '允许组仍亏损',
    roi_negative: 'ROI 为负',
    win_rate_low: '胜率偏低',
    strategy_not_ready: '策略尚未达标',
    resolved_sample_below_30: '已结算样本 < 30',
    allowed_sample_below_20: '允许组样本 < 20',
    allowed_group_pnl_negative: '允许组 PnL 为负',
    allowed_group_roi_negative: '允许组 ROI 为负',
    allowed_win_rate_low: '允许组胜率偏低',
    settlement_rule_not_manually_verified: '结算规则未核验',
    settlement_contracts_missing: '事件级合同缺失',
    timezone_mismatch: '规则时区不一致',
    independent_truth_days_below_min: '独立 truth 日不足',
    all_orderbooks_stale: '盘口快照已过期',
    fresh_clob_depth_missing: 'CLOB 深度缺失',
    fresh_clob_depth_below_min: 'CLOB 深度不足',
    forecast_city_coverage_incomplete: '预测城市覆盖不足',
  }
  return map[reason] ?? reason
}

function cityPageSlug(city: { key: string; station?: string }) {
  return `${city.key}${city.station ? `-${city.station.toLowerCase()}` : ''}`
}

function cityKeyFromParam(value: string | null) {
  if (!value) return ''
  return value.split('-').slice(0, -1).join('-') || value
}

const CONTINENT_FILTERS = ['全部', 'Americas', 'Europe', 'Asia', 'Pacific', 'Africa', 'Other'] as const

function cityContinent(cityKey?: string, cityName?: string) {
  const value = `${cityKey || ''} ${cityName || ''}`.toLowerCase()
  if (/london|paris|munich|madrid|milan|amsterdam|warsaw|helsinki|moscow|istanbul|ankara/.test(value)) return 'Europe'
  if (/tokyo|seoul|shanghai|beijing|wuhan|singapore|taipei|hong|busan|chengdu|chongqing|guangzhou|jakarta|jeddah|karachi|kuala|lucknow|manila|qingdao|tel-aviv/.test(value)) return 'Asia'
  if (/sydney|wellington/.test(value)) return 'Pacific'
  if (/cape|lagos/.test(value)) return 'Africa'
  if (/new-york|nyc|chicago|miami|dallas|seattle|atlanta|toronto|sao|paulo|austin|denver|houston|los-angeles|san-francisco|mexico|panama|buenos/.test(value)) return 'Americas'
  return 'Other'
}

function validationActionLimit(action: ProductionValidationAction) {
  const raw = Number(action.targets_count ?? action.count ?? 20)
  if (!Number.isFinite(raw) || raw <= 0) return 20
  return Math.max(1, Math.min(Math.ceil(raw), 20))
}

function refreshDaysForDate(date?: string | null) {
  if (!date) return 2
  const selectedTime = new Date(`${date}T00:00:00`).getTime()
  if (!Number.isFinite(selectedTime)) return 2
  const today = new Date()
  const todayTime = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
  const diffDays = Math.ceil((selectedTime - todayTime) / 86400000)
  return Math.max(1, Math.min(diffDays + 2, 7))
}

function productionRefreshTarget(result?: ProductionRefreshResult | null) {
  if (!result) return '--'
  const requestCities = result.request?.cities ?? []
  const cityLabel = requestCities.length === 0
    ? '全部城市'
    : requestCities.length === 1
      ? requestCities[0]
      : `${requestCities.length} 个城市`
  const dateLabel = result.target_date || result.request?.start_date || result.request?.end_date || '--'
  return `${cityLabel} · ${dateLabel}`
}

function productionRefreshFailures(result?: ProductionRefreshResult | null) {
  if (!result) return []
  const explicit = result.failed_stages ?? []
  if (explicit.length > 0) return explicit
  return (result.stages ?? [])
    .filter(stage => stage.ok === false && !stage.running)
    .map(stage => stage.name)
}

function productionRefreshNotice(result: ProductionRefreshResult, language: UiLanguage): RefreshNotice {
  const failed = productionRefreshFailures(result)
  const okStages = (result.stages ?? []).filter(stage => stage.ok && !stage.running).length
  const totalStages = (result.stages ?? []).length
  const target = productionRefreshTarget(result)
  const nonWeatherStages = new Set(['orderbook_backfill', 'signal_scan', 'signal_migration'])
  const weatherCriticalFailures = failed.filter(stage => !nonWeatherStages.has(stage))
  if (result.ok && failed.length === 0) {
    return {
      id: Date.now(),
      tone: 'success',
      title: language === 'zh' ? '数据自动更新成功' : 'Data refresh completed',
      message: language === 'zh'
        ? `${target} 已更新，完成 ${okStages}/${totalStages || okStages} 个阶段。`
        : `${target} refreshed, ${okStages}/${totalStages || okStages} stages completed.`,
      details: totalStages ? [`stages: ${okStages}/${totalStages}`] : undefined,
    }
  }
  if (failed.length > 0 && weatherCriticalFailures.length === 0) {
    return {
      id: Date.now(),
      tone: 'warning',
      title: language === 'zh' ? '天气数据已更新，交易数据异常' : 'Weather data updated, trading data warning',
      message: language === 'zh'
        ? `${target} 的 forecast / METAR / DEB 已更新，但盘口或信号后处理阶段失败。`
        : `${target} forecast / METAR / DEB refreshed, but orderbook or signal post-processing failed.`,
      details: failed.slice(0, 6),
    }
  }
  return {
    id: Date.now(),
    tone: 'error',
    title: language === 'zh' ? '数据抓取异常' : 'Data refresh failed',
    message: language === 'zh'
      ? `${target} 有 ${failed.length || 1} 个阶段失败，请查看抓取日志。`
      : `${target} has ${failed.length || 1} failed stage(s). Check the fetch log.`,
    details: failed.length ? failed.slice(0, 6) : [result.message || 'unknown failure'],
  }
}

function productionActionSummary(result?: ProductionActionRunResult | null) {
  if (!result) return ''
  if (result.reason) return result.reason
  if (result.message) return result.message
  const payload = result.payload ?? {}
  const parts = ['requested', 'ok', 'eligible', 'failed']
    .filter(key => payload[key] !== undefined)
    .map(key => `${key} ${String(payload[key])}`)
  return parts.length ? parts.join(' / ') : result.status
}

function ReadinessBanner({ stats, readiness }: { stats: BotStats; readiness?: DataReadiness | null }) {
  const ready = Boolean(stats.strategy_live_ready)
  const reasons = stats.strategy_readiness_reasons ?? []
  const phase = readiness?.production_phase

  return (
    <div className={`border px-3 py-2 ${ready ? 'border-green-500/30 bg-green-500/10' : 'border-amber-500/30 bg-amber-500/10'}`}>
      <div className="flex items-center gap-2">
        {ready ? <CheckCircle2 className="h-4 w-4 text-green-300" /> : <ShieldAlert className="h-4 w-4 text-amber-300" />}
        <div className="min-w-0">
          <div className="text-sm font-medium text-neutral-100">
            {phase ? `${phase.label}：${phase.name}` : ready ? '实盘门槛已通过，但仍建议从 $1-$2 canary 开始' : '当前只允许模拟观察，实盘按钮已锁定'}
          </div>
          <div className="truncate text-[10px] text-neutral-500">
            {ready ? '实盘门槛已通过，但仍建议从 $1-$2 canary 开始。' : phase?.operator_action ?? '当前只允许模拟观察，实盘按钮已锁定。'}
          </div>
        </div>
      </div>
      {!ready && (
        <div className="mt-2 flex flex-wrap gap-1">
          {reasons.length ? reasons.slice(0, 8).map(reason => (
            <span key={reason} className="border border-amber-500/20 bg-black/30 px-1.5 py-0.5 text-[10px] text-amber-100">
              {reasonLabel(reason)}
            </span>
          )) : (
            <span className="text-[11px] text-neutral-500">等待更多模拟和结算样本。</span>
          )}
        </div>
      )}
    </div>
  )
}

function ProductionValidationPanel({
  report,
  loading,
  runningActionKey,
  actionResult,
  onDryRunAction,
  onExecuteAction,
}: {
  report?: ProductionValidationReport | null
  loading?: boolean
  runningActionKey?: string | null
  actionResult?: ProductionActionRunResult | null
  onDryRunAction?: (action: ProductionValidationAction) => void
  onExecuteAction?: (action: ProductionValidationAction) => void
}) {
  const status = report?.status ?? (loading ? 'loading' : 'missing')
  const readyForCanary = Boolean(report?.live_allowed)
  const score = report ? Math.round(Number(report.score ?? 0) * 100) : 0
  const layers = report?.layers ?? []
  const actions = report?.next_actions ?? []

  return (
    <div className="border border-neutral-800 bg-black p-3">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <ListChecks className="h-4 w-4 shrink-0 text-cyan-300" />
          <div className="min-w-0">
            <div className="text-sm font-medium text-neutral-100">生产验证</div>
            <div className="truncate text-[10px] text-neutral-500">
              {report ? `更新 ${timeText(report.generated_at)}` : '等待后端验证报告'}
            </div>
          </div>
        </div>
        <span className={`shrink-0 border px-2 py-1 text-[10px] ${readyForCanary ? 'border-green-500/30 bg-green-500/10 text-green-200' : 'border-amber-500/30 bg-amber-500/10 text-amber-200'}`}>
          {readyForCanary ? '可 Canary' : status === 'loading' ? '读取中' : '阻塞'}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 text-[10px]">
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-500">评分</div>
          <div className="mt-1 tabular-nums text-neutral-100">{report ? `${score}%` : '--'}</div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-500">层级</div>
          <div className="mt-1 tabular-nums text-neutral-100">
            {report ? `${report.ready_layers}/${report.total_layers}` : '--'}
          </div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-500">实盘</div>
          <div className={`mt-1 ${readyForCanary ? 'text-green-300' : 'text-amber-300'}`}>
            {readyForCanary ? '放行' : '锁定'}
          </div>
        </div>
      </div>

      <div className="mt-3 space-y-1">
        {layers.length ? layers.map(layer => {
          const blockedText = layer.blockers?.[0] ? reasonLabel(layer.blockers[0]) : '已通过'
          return (
            <div key={layer.key} className="flex items-center justify-between gap-2 border border-neutral-800 px-2 py-1.5 text-[10px]">
              <div className="min-w-0">
                <div className="truncate text-neutral-200">{layer.label}</div>
                <div className="truncate text-neutral-500" title={(layer.blockers ?? []).join(', ')}>
                  {blockedText}
                </div>
              </div>
              <span className={`shrink-0 tabular-nums ${layer.ready ? 'text-green-300' : 'text-amber-300'}`}>
                {layer.ready ? 'ready' : 'blocked'}
              </span>
            </div>
          )
        }) : (
          <div className="border border-neutral-800 px-2 py-2 text-[10px] text-neutral-500">暂无生产验证层级。</div>
        )}
      </div>

      <details className="mt-3 text-[10px] text-neutral-500">
        <summary className="cursor-pointer select-none hover:text-neutral-300">下一步动作</summary>
        <div className="mt-2 space-y-1">
          {actions.length ? actions.slice(0, 5).map((action, index) => (
            <div key={`${action.key ?? action.label ?? 'action'}-${index}`} className="border border-neutral-800 px-2 py-1.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-neutral-300">{action.label ?? action.key ?? '待处理'}</div>
                  <div className="mt-0.5 flex flex-wrap gap-1 text-[9px] text-neutral-600">
                    {action.layer && <span>{String(action.layer)}</span>}
                    {action.requires_operator && <span className="text-amber-300">需要确认</span>}
                    {(action.targets_count !== undefined || action.count !== undefined) && (
                      <span className="tabular-nums">本次最多 {validationActionLimit(action)}</span>
                    )}
                  </div>
                </div>
                {typeof action.count === 'number' && <span className="shrink-0 tabular-nums text-neutral-500">{action.count}</span>}
              </div>
              {action.command && <code className="mt-1 block break-all text-[9px] text-neutral-600">{action.command}</code>}
              {action.key && (
                <div className="mt-2 grid grid-cols-2 gap-1">
                  <button
                    type="button"
                    disabled={runningActionKey === action.key}
                    onClick={() => onDryRunAction?.(action)}
                    className="border border-neutral-700 px-2 py-1 text-neutral-300 hover:bg-neutral-900 disabled:cursor-wait disabled:opacity-50"
                  >
                    {runningActionKey === action.key ? '运行中' : '预检'}
                  </button>
                  <button
                    type="button"
                    disabled={runningActionKey === action.key}
                    onClick={() => onExecuteAction?.(action)}
                    className="border border-amber-500/30 px-2 py-1 text-amber-200 hover:bg-amber-500/10 disabled:cursor-wait disabled:opacity-50"
                  >
                    执行
                  </button>
                </div>
              )}
            </div>
          )) : (
            <div className="text-neutral-500">暂无新增动作。</div>
          )}
          {actionResult && (
            <div className={`border px-2 py-1.5 ${actionResult.ok ? 'border-green-500/20 text-green-200' : 'border-red-500/20 text-red-200'}`}>
              <div className="flex items-center justify-between gap-2">
                <span>{actionResult.action?.label ?? actionResult.action_key}</span>
                <span className="tabular-nums">{actionResult.status}</span>
              </div>
              <div className="mt-1 text-[9px] text-neutral-500">{productionActionSummary(actionResult)}</div>
            </div>
          )}
        </div>
      </details>
    </div>
  )
}

function TradeModeSwitch({
  mode,
  liveAvailable,
  onMode,
}: {
  mode: TradeMode
  liveAvailable: boolean
  onMode: (mode: TradeMode) => void
}) {
  const locked = !liveAvailable

  return (
    <div className="border border-neutral-800 bg-black p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-neutral-100">交易模式</div>
        </div>
        <span
          className={`shrink-0 border px-2 py-1 text-[10px] ${
            mode === 'paper'
              ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200'
              : 'border-blue-500/30 bg-blue-500/10 text-blue-200'
          }`}
          aria-live="polite"
        >
          {mode === 'paper' ? '模拟盘' : '实盘检查'}
        </span>
      </div>

      <div className="grid grid-cols-2 border border-neutral-800" role="group" aria-label="选择交易模式">
        <button
          type="button"
          onClick={() => onMode('paper')}
          aria-pressed={mode === 'paper'}
          className={`inline-flex min-h-10 items-center justify-center gap-2 px-3 py-2 text-xs ${
            mode === 'paper' ? 'bg-cyan-500/15 text-cyan-200' : 'text-neutral-500 hover:bg-neutral-900'
          }`}
        >
          <FlaskConical className="h-4 w-4" />
          模拟
        </button>
        <button
          type="button"
          onClick={() => liveAvailable && onMode('live')}
          disabled={!liveAvailable}
          aria-pressed={mode === 'live'}
          aria-describedby={locked ? 'live-mode-unavailable' : undefined}
          className={`inline-flex min-h-10 items-center justify-center gap-2 border-l border-neutral-800 px-3 py-2 text-xs ${
            mode === 'live'
              ? 'bg-blue-500/15 text-blue-200'
              : liveAvailable
                ? 'text-neutral-400 hover:bg-neutral-900'
                : 'cursor-not-allowed text-neutral-700'
          }`}
        >
          <Wallet className="h-4 w-4" />
          实盘
        </button>
      </div>

      <div className="mt-2 flex items-center justify-between gap-2 text-[10px]">
        <span className={`border px-1.5 py-0.5 ${locked ? 'border-amber-500/30 text-amber-300' : 'border-green-500/30 text-green-300'}`}>
          {locked ? '实盘锁定' : '可用 canary'}
        </span>
        <details className="min-w-0 text-right text-neutral-500">
          <summary className="cursor-pointer select-none hover:text-neutral-300">执行说明</summary>
          <p id="live-mode-unavailable" className="mt-1 max-w-[260px] text-left leading-relaxed text-neutral-500">
            {locked
              ? '策略闸门或实盘配置未通过，买入只会写入模拟账户。'
              : '实盘会先执行 canary 风控、盘口、tick 和 orderMinSize 检查。'}
          </p>
        </details>
      </div>
    </div>
  )
}

function SimulationCard({
  stats,
  value,
  clearMarks,
  autoSimulation,
  onValue,
  onClearMarks,
  onReset,
  onSettle,
  onToggleAuto,
  resetting,
  settling,
  autoPending,
}: {
  stats: BotStats
  value: string
  clearMarks: boolean
  autoSimulation: AutoSimulationStatus
  onValue: (value: string) => void
  onClearMarks: (value: boolean) => void
  onReset: () => void
  onSettle: () => void
  onToggleAuto: () => void
  resetting: boolean
  settling: boolean
  autoPending: boolean
}) {
  const autoRunning = autoSimulation.enabled
  const lastResult = autoSimulation.last_result

  return (
    <div className="border border-neutral-800 bg-black p-3">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Wallet className="h-4 w-4 text-cyan-300" />
          <div>
            <div className="text-sm font-medium text-neutral-100">模拟账户</div>
          </div>
        </div>
        <span className={`shrink-0 border px-1.5 py-0.5 text-[9px] ${
          autoRunning ? 'border-green-500/30 text-green-300' : 'border-neutral-700 text-neutral-500'
        }`}>
          {autoRunning ? '运行中' : '已停止'}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">权益</div>
          <div className="tabular-nums text-lg text-neutral-100">{money(stats.bankroll)}</div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">未实现</div>
          <div className={`tabular-nums text-lg ${(stats.unrealized_pnl ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'}`}>
            {money(stats.unrealized_pnl ?? 0)}
          </div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">现金 / 占用</div>
          <div className="tabular-nums text-neutral-200">{money(stats.cash_balance ?? stats.bankroll)} / {money(stats.reserved_capital ?? 0)}</div>
        </div>
        <div className="border border-neutral-800 p-2">
          <div className="text-neutral-600">持仓 / 结算</div>
          <div className="tabular-nums text-neutral-200">{stats.open_trades ?? 0} / {stats.settled_trades ?? 0}</div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-[1fr_auto] gap-2">
        <label className="grid min-w-0 grid-cols-[auto_1fr] items-center border border-neutral-800 bg-neutral-950/70 px-2 py-1 text-[10px] text-neutral-500">
          本金
          <input
            type="number"
            min="0"
            step="1"
            value={value}
            onChange={event => onValue(event.target.value)}
            className="min-w-0 border-0 bg-transparent p-0 text-right text-xs tabular-nums text-neutral-100 focus:outline-none"
            aria-label="设置模拟本金"
          />
        </label>
        <button
          onClick={onReset}
          disabled={resetting}
          className="border border-cyan-500/30 px-2 py-1 text-[11px] text-cyan-300 hover:bg-cyan-500/10 disabled:opacity-40"
        >
          应用
        </button>
      </div>

      <label className="mt-2 flex items-center gap-2 text-[10px] text-neutral-500">
        <input
          type="checkbox"
          checked={clearMarks}
          onChange={event => onClearMarks(event.target.checked)}
          className="h-3 w-3 p-0"
        />
        重置时清除模拟标记
      </label>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <button
          onClick={onToggleAuto}
          disabled={autoPending}
          className={`border px-2 py-1.5 disabled:opacity-40 ${
            autoRunning
              ? 'border-red-500/30 bg-red-500/5 text-red-300 hover:bg-red-500/10'
              : 'border-green-500/30 bg-green-500/10 text-green-300 hover:border-green-500/60'
          }`}
        >
          {autoPending ? '更新中...' : autoRunning ? '停止模拟' : '一键模拟'}
        </button>
        <button
          onClick={onSettle}
          disabled={settling || (stats.open_trades ?? 0) === 0}
          className="border border-amber-500/30 px-2 py-1.5 text-amber-300 hover:bg-amber-500/10 disabled:opacity-40"
        >
          检查结算
        </button>
      </div>

      {(lastResult || autoSimulation.last_error) && (
        <div className="mt-3 border border-neutral-800 p-2 text-[10px] leading-relaxed text-neutral-400">
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="text-neutral-200">最近检查</span>
            <span className="tabular-nums text-neutral-500">{timeText(autoSimulation.last_run)}</span>
          </div>
          {lastResult && (
            <div>
              买入 {lastResult.count}，跳过 {lastResult.skipped}，花费 {money(lastResult.spent)}，剩余 {money(lastResult.remaining)}
              {lastResult.orderbooks_refreshed !== undefined && (
                <span title={`盘口刷新失败 ${lastResult.orderbook_refresh_failed ?? 0} 个`}>
                  {' '}· 盘口 {lastResult.orderbooks_refreshed}
                </span>
              )}
            </div>
          )}
          {autoSimulation.last_error && <div className="text-red-300">{autoSimulation.last_error}</div>}
        </div>
      )}

      <details className="mt-3 text-[10px] text-neutral-600">
        <summary className="cursor-pointer select-none hover:text-neutral-300">估值口径</summary>
        <p className="mt-1 leading-relaxed">
          新买入会按卖一成交、按买一估值，spread 先进入未实现亏损；这只是执行成本，不代表最终结算已经错。
        </p>
      </details>
    </div>
  )
}

function ForecastOptionsCard({
  cityName,
  station,
  selectedDate,
  dataAgeLabel,
  signals,
  actionable,
  refreshing,
  language,
  onRefresh,
}: {
  cityName: string
  station: string
  selectedDate: string
  dataAgeLabel: string
  signals: number
  actionable: number
  refreshing: boolean
  language: UiLanguage
  onRefresh: () => void
}) {
  const zh = language === 'zh'
  return (
    <div className="grid gap-2 border border-neutral-800 bg-black p-2 text-[10px] text-neutral-500">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-xs text-neutral-200">Forecast Options</div>
          <div className="mt-0.5 text-[9px] text-neutral-600">
            {cityName || (zh ? '等待城市' : 'Waiting city')} {station ? `· ${station}` : ''}
          </div>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="inline-flex items-center gap-1 border border-cyan-500/30 px-2 py-1 text-[10px] text-cyan-300 hover:bg-cyan-500/10 disabled:opacity-40"
        >
          <RefreshCw className={`h-3 w-3 ${refreshing ? 'animate-spin' : ''}`} />
          {refreshing ? (zh ? '抓取中' : 'Fetching') : (zh ? '刷新' : 'Refresh')}
        </button>
      </div>
      <div className="grid grid-cols-3 gap-1">
        <StatusTile label={zh ? '日期' : 'Date'} value={selectedDate || '--'} />
        <StatusTile label={zh ? '数据' : 'Data'} value={dataAgeLabel} />
        <StatusTile label={zh ? '信号' : 'Signals'} value={`${actionable}/${signals}`} tone={actionable > 0 ? 'green' : 'neutral'} />
      </div>
      <div className="border border-neutral-800 bg-neutral-950/60 p-2">
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="text-xs text-neutral-200">Alerts</span>
          <span className={`border px-1.5 py-0.5 text-[9px] ${actionable > 0 ? 'border-green-500/30 text-green-300' : 'border-neutral-700 text-neutral-500'}`}>
            {actionable > 0 ? (zh ? '有可行动信号' : 'Actionable') : (zh ? '观察中' : 'Watching')}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-1">
          <span className="border border-neutral-800 px-2 py-1">{zh ? '峰值温度' : 'Peak temp'}</span>
          <span className="border border-neutral-800 px-2 py-1">{zh ? '信号队列' : 'Signal queue'}</span>
          <span className="border border-neutral-800 px-2 py-1">{zh ? '盘口刷新' : 'Orderbook'}</span>
          <span className="border border-neutral-800 px-2 py-1">{zh ? '结算样本' : 'Truth sample'}</span>
        </div>
      </div>
    </div>
  )
}

function App() {
  const queryClient = useQueryClient()
  const [tradeMode, setTradeMode] = useState<TradeMode>('paper')
  const [activityView, setActivityView] = useState<'signals' | 'trades'>('signals')
  const [selectedCity, setSelectedCity] = useState(() => {
    if (typeof window === 'undefined') return ''
    return cityKeyFromParam(new URLSearchParams(window.location.search).get('city'))
  })
  const [selectedDate, setSelectedDate] = useState(() => {
    if (typeof window === 'undefined') return ''
    return new URLSearchParams(window.location.search).get('date') ?? ''
  })
  const [simBalance, setSimBalance] = useState('40')
  const [clearMarks, setClearMarks] = useState(false)
  const [contractStatus, setContractStatus] = useState('mature-auto')
  const [citySearch, setCitySearch] = useState('')
  const [citySort, setCitySort] = useState<'signal' | 'alpha'>('signal')
  const [activeMainView, setActiveMainView] = useState<MainView>('workbench')
  const [continentFilter, setContinentFilter] = useState<(typeof CONTINENT_FILTERS)[number]>('全部')
  const [uiLanguage, setUiLanguage] = useState<UiLanguage>(() => {
    if (typeof window === 'undefined') return 'zh'
    return window.localStorage.getItem('weatherbot-ui-language') === 'en' ? 'en' : 'zh'
  })
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => {
    if (typeof window === 'undefined') return 'light'
    return window.localStorage.getItem('weatherbot-ui-theme') === 'dark' ? 'dark' : 'light'
  })
  const [productionActionResult, setProductionActionResult] = useState<ProductionActionRunResult | null>(null)
  const [refreshNotices, setRefreshNotices] = useState<RefreshNotice[]>([])
  const balanceInitRef = useRef(false)
  const productionRefreshRunningRef = useRef(false)
  const seenSchedulerRunsRef = useRef<Record<string, string>>({})
  const copy = UI_COPY[uiLanguage]
  const i18nLanguage: I18nLanguage = uiLanguage === 'zh' ? 'zh-CN' : 'en'
  const t = useT(i18nLanguage)

  const showRefreshNotice = (notice: RefreshNotice, ttlMs = 7000) => {
    setRefreshNotices(current => [notice, ...current.filter(item => item.id !== notice.id)].slice(0, 5))
    if (ttlMs > 0) {
      window.setTimeout(() => {
        setRefreshNotices(current => current.filter(item => item.id !== notice.id))
      }, ttlMs)
    }
  }

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['dashboard'],
    queryFn: fetchDashboard,
    refetchInterval: 10000,
    retry: 1,
  })

  const contractsQuery = useQuery({
    queryKey: ['settlement-contracts', contractStatus],
    queryFn: () => fetchSettlementContracts(contractStatus, 12),
    refetchInterval: 120000,
  })

  const forecastArchiveManifestQuery = useQuery({
    queryKey: ['forecast-archive-manifest'],
    queryFn: fetchForecastArchiveManifest,
    refetchInterval: 120000,
  })

  const productionValidationQuery = useQuery({
    queryKey: ['production-validation'],
    queryFn: fetchProductionValidation,
    refetchInterval: 120000,
  })

  const productionRefreshStatusQuery = useQuery({
    queryKey: ['production-refresh-status'],
    queryFn: fetchProductionRefreshStatus,
    refetchInterval: 3000,
    retry: 1,
  })

  const schedulerStatusQuery = useQuery({
    queryKey: ['scheduler-status'],
    queryFn: fetchSchedulerStatus,
    refetchInterval: 30000,
    retry: 1,
  })

  const selectedEvidenceReadyForLayer7 = Boolean(selectedCity && selectedDate)

  const marketBucketsQuery = useQuery({
    queryKey: ['market-buckets', selectedCity, selectedDate],
    queryFn: () => fetchMarketBuckets(selectedCity, selectedDate, 120),
    enabled: selectedEvidenceReadyForLayer7,
    refetchInterval: 30000,
    retry: 1,
  })

  const signalDecisionsQuery = useQuery({
    queryKey: ['signal-decisions', selectedCity, selectedDate],
    queryFn: () => fetchSignalDecisions(selectedCity, selectedDate, 120),
    enabled: selectedEvidenceReadyForLayer7,
    refetchInterval: 30000,
    retry: 1,
  })

  const dailyMaxPredictionQuery = useQuery({
    queryKey: ['daily-max-predictions', selectedCity, selectedDate],
    queryFn: () => fetchDailyMaxPredictions(selectedCity, selectedDate),
    enabled: selectedEvidenceReadyForLayer7,
    refetchInterval: 60000,
    retry: 1,
  })

  const truthDeltaAuditQuery = useQuery({
    queryKey: ['truth-delta-audit', selectedCity],
    queryFn: () => fetchTruthDeltaAudit(selectedCity || '', 500),
    enabled: Boolean(selectedCity),
    refetchInterval: 120000,
    retry: 1,
  })

  const modelRepriceEventsQuery = useQuery({
    queryKey: ['model-reprice-events', selectedCity, selectedDate],
    queryFn: () => fetchModelRepriceEvents(selectedCity || '', selectedDate || '', true, 200),
    enabled: selectedEvidenceReadyForLayer7,
    refetchInterval: 30000,
    retry: 1,
  })

  const stopMutation = useMutation({
    mutationFn: stopBot,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  })

  const signalStatusMutation = useMutation({
    mutationFn: ({ signalId, status, amount }: { signalId: number; status: string; amount?: number }) =>
      updateSignalStatus(signalId, status, amount),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const liveOrderMutation = useMutation({
    mutationFn: ({ signalId, amount }: { signalId: number; amount?: number }) => placeLiveOrder(signalId, amount),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const autoSimulationMutation = useMutation({
    mutationFn: (enabled: boolean) => setAutoSimulation(enabled, 300),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const schedulerStartMutation = useMutation({
    mutationFn: startScheduler,
    onMutate: () => {
      showRefreshNotice({
        id: Date.now(),
        tone: 'running',
        title: uiLanguage === 'zh' ? '调度器启动中' : 'Scheduler starting',
        message: uiLanguage === 'zh'
          ? '后端将按源轮询 enabled 城市：METAR 5 分钟、China Live 5 分钟、Forecast 60 分钟、Historical 15 分钟。'
          : 'Backend pollers will refresh enabled cities by source frequency.',
        details: ['scheduler'],
      }, 6000)
    },
    onSuccess: result => {
      queryClient.invalidateQueries({ queryKey: ['scheduler-status'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      showRefreshNotice({
        id: Date.now() + 1,
        tone: 'success',
        title: uiLanguage === 'zh' ? '调度器已启动' : 'Scheduler started',
        message: result.message || (uiLanguage === 'zh' ? '常驻 poller 已在后端运行。' : 'Server-side pollers are running.'),
        details: ['METAR', 'China Live', 'Forecast', 'Historical'],
      }, 8000)
    },
    onError: error => {
      showRefreshNotice({
        id: Date.now() + 2,
        tone: 'error',
        title: uiLanguage === 'zh' ? '调度器启动失败' : 'Scheduler start failed',
        message: error instanceof Error ? error.message : String(error),
        details: ['scheduler/start'],
      }, 12000)
    },
  })

  const schedulerStopMutation = useMutation({
    mutationFn: stopScheduler,
    onSuccess: result => {
      queryClient.invalidateQueries({ queryKey: ['scheduler-status'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      showRefreshNotice({
        id: Date.now() + 3,
        tone: 'warning',
        title: uiLanguage === 'zh' ? '调度器已停止' : 'Scheduler stopped',
        message: result.message || (uiLanguage === 'zh' ? '后端 poller 已停止，数据只会由手动刷新更新。' : 'Backend pollers stopped.'),
        details: ['scheduler/stop'],
      }, 8000)
    },
  })

  const stationEnabledMutation = useMutation({
    mutationFn: ({ cityKey, enabled }: { cityKey: string; enabled: boolean }) =>
      setStationEnabled(cityKey, enabled),
    onSuccess: result => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['scheduler-status'] })
      showRefreshNotice({
        id: Date.now() + 4,
        tone: result.enabled ? 'success' : 'warning',
        title: result.enabled
          ? (uiLanguage === 'zh' ? '城市已加入调度' : 'City enabled')
          : (uiLanguage === 'zh' ? '城市已暂停调度' : 'City disabled'),
        message: `${result.city_key} · tier ${result.tier}`,
        details: ['stations enabled watchlist'],
      }, 6000)
    },
    onError: error => {
      showRefreshNotice({
        id: Date.now() + 5,
        tone: 'error',
        title: uiLanguage === 'zh' ? '城市调度状态更新失败' : 'City watchlist update failed',
        message: error instanceof Error ? error.message : String(error),
        details: ['stations/enabled'],
      }, 10000)
    },
  })

  const verifyContractMutation = useMutation({
    mutationFn: ({ contractId, note }: { contractId: string; note: string }) =>
      verifySettlementContract(contractId, true, note || 'dashboard manual review'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['production-refresh-status'] })
      queryClient.invalidateQueries({ queryKey: ['settlement-contracts'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
      queryClient.invalidateQueries({ queryKey: ['market-buckets'] })
      queryClient.invalidateQueries({ queryKey: ['signal-decisions'] })
      queryClient.invalidateQueries({ queryKey: ['daily-max-predictions'] })
    },
  })

  const bulkVerifyContractMutation = useMutation({
    mutationFn: ({ contractIds, note }: { contractIds: string[]; note: string }) =>
      verifySettlementContractsBulk(contractIds, true, true, note || 'dashboard visible batch review'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settlement-contracts'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const productionRefreshMutation = useMutation({
    mutationFn: (options: ProductionRefreshOptions | undefined) =>
      runProductionRefresh({
        cities: options?.cities ?? [],
        days: options?.days ?? 2,
        limit: options?.limit ?? 20,
        startDate: options?.startDate ?? '',
        endDate: options?.endDate ?? '',
        skipSignalScan: true,
      }),
    onMutate: options => {
      const cityLabel = options?.cities?.length ? options.cities.join(', ') : '全部城市'
      const dateLabel = options?.startDate || options?.endDate || '--'
      showRefreshNotice({
        id: Date.now(),
        tone: 'running',
        title: uiLanguage === 'zh' ? '数据抓取已启动' : 'Data refresh started',
        message: uiLanguage === 'zh'
          ? `${cityLabel} · ${dateLabel} 正在刷新 forecast / METAR / DEB / market buckets。`
          : `${cityLabel} · ${dateLabel} is refreshing forecast / METAR / DEB / market buckets.`,
        details: ['production-refresh-v2'],
      }, 0)
    },
    onSuccess: result => {
      queryClient.invalidateQueries({ queryKey: ['settlement-contracts'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
      queryClient.invalidateQueries({ queryKey: ['market-buckets'] })
      queryClient.invalidateQueries({ queryKey: ['signal-decisions'] })
      queryClient.invalidateQueries({ queryKey: ['daily-max-predictions'] })
      queryClient.invalidateQueries({ queryKey: ['production-refresh-status'] })
      const notice = productionRefreshNotice(result, uiLanguage)
      showRefreshNotice(notice, result.ok ? 7000 : 14000)
    },
    onError: error => {
      showRefreshNotice({
        id: Date.now(),
        tone: 'error',
        title: uiLanguage === 'zh' ? '数据抓取请求失败' : 'Refresh request failed',
        message: error instanceof Error ? error.message : String(error),
        details: ['dashboard_server 或网络连接异常'],
      }, 14000)
    },
  })

  const productionActionMutation = useMutation({
    mutationFn: ({ action, apply, operatorConfirmed }: { action: ProductionValidationAction; apply: boolean; operatorConfirmed?: boolean }) =>
      runProductionAction({
        actionKey: String(action.key),
        apply,
        operatorConfirmed: operatorConfirmed ?? false,
        limit: validationActionLimit(action),
        skipSignalScan: true,
      }),
    onSuccess: result => {
      setProductionActionResult(result)
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
      if (result.status === 'executed') {
        queryClient.invalidateQueries({ queryKey: ['dashboard'] })
        queryClient.invalidateQueries({ queryKey: ['settlement-contracts'] })
        queryClient.invalidateQueries({ queryKey: ['forecast-archive-manifest'] })
        queryClient.invalidateQueries({ queryKey: ['market-buckets'] })
        queryClient.invalidateQueries({ queryKey: ['signal-decisions'] })
        queryClient.invalidateQueries({ queryKey: ['daily-max-predictions'] })
      }
    },
  })

  const resetSimulationMutation = useMutation({
    mutationFn: ({ balance, clear }: { balance: number; clear: boolean }) => resetSimulation(balance, clear),
    onSuccess: result => {
      setSimBalance(String(result.balance))
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const settleMutation = useMutation({
    mutationFn: settleTradesApi,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const historyBackfillMutation = useMutation({
    mutationFn: () => backfillWeatherHistory(30),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['production-validation'] })
    },
  })

  const stats = data?.stats ?? EMPTY_STATS
  const signals = data?.weather_signals ?? []
  const forecasts = data?.weather_forecasts ?? []
  const citySeries = data?.weather_city_series ?? []
  const cityStatusMap = data?.city_statuses ?? ROUND5_STATUS_FALLBACK
  const cityEvidence = data?.city_evidence ?? []
  const events = data?.events ?? []
  const fetchLog = data?.fetch_log ?? []
  const trades = data?.recent_trades ?? []
  const truthHealth = data?.truth_health ?? null
  const dataReadiness = data?.data_readiness ?? null
  const productionRefresh = productionRefreshStatusQuery.data ?? productionRefreshMutation.data ?? data?.production_refresh ?? null
  const productionRefreshRunning = Boolean(productionRefreshMutation.isPending || productionRefresh?.running)
  const productionRefreshStages = productionRefresh?.stages ?? []
  const productionRefreshDone = productionRefreshStages.filter(stage => stage.ok && !stage.running).length
  const productionRefreshCurrent = productionRefreshStages.find(stage => stage.running) ?? productionRefreshStages[productionRefreshStages.length - 1]
  const schedulerStatus: SchedulerStatus | null = schedulerStatusQuery.data ?? data?.scheduler_status ?? null
  const schedulerRunning = Boolean(schedulerStatus?.running || schedulerStartMutation.isPending)
  const currentRefreshOptions = useMemo<ProductionRefreshOptions>(() => ({
    cities: selectedCity ? [selectedCity] : [],
    days: refreshDaysForDate(selectedDate),
    limit: 20,
    startDate: selectedDate || '',
    endDate: selectedDate || '',
  }), [selectedCity, selectedDate])
  const productionValidation = productionValidationQuery.data ?? null
  const modelDatasetAudit = data?.model_dataset_audit ?? null
  const forecastArchiveManifest = forecastArchiveManifestQuery.data ?? null
  const actionable = signals.filter(signal => signal.actionable).length
  const liveAvailable = Boolean(stats.strategy_live_ready && data?.v3?.config?.live_trading)
  const needsManualRefresh = data?._meta?.reason === 'manual_refresh_required'
  const autoSimulation = stats.auto_simulation ?? {
    enabled: false,
    interval_seconds: 300,
    last_run: null,
    last_result: null,
    last_error: null,
  }
  const runValidationActionDryRun = (action: ProductionValidationAction) => {
    if (!action.key) return
    productionActionMutation.mutate({ action, apply: false })
  }
  const runValidationActionExecute = (action: ProductionValidationAction) => {
    if (!action.key) return
    const needsConfirmation = Boolean(action.requires_operator)
    const confirmed = !needsConfirmation || window.confirm('这个动作会写入本地状态或执行受控数据刷新，确认继续？')
    if (!confirmed) return
    productionActionMutation.mutate({ action, apply: true, operatorConfirmed: needsConfirmation })
  }
  const cityOptions = useMemo(() => {
    const rows = new Map<string, {
      key: string
      name: string
      station?: string
      stationName?: string
      settlementStation?: string
      settlementStationName?: string
      settlementRuleVerifiedAt?: string | null
      settlementTimezone?: string
      settlementUnit?: string
      settlementTimeBasis?: string
      primarySettlementSource?: string
      verificationStatus?: string
      continent: string
      unit: string
      latest?: number | null
      latestMetar?: number | null
      forecastCount: number
      historyCount: number
      humidityStatus?: string
      signals: number
      actionable: number
      enabled: boolean
      tier: number
      lastRefreshedAt?: string | null
    }>()

    for (const row of citySeries) {
      rows.set(row.city_key, {
        key: row.city_key,
        name: row.city_name,
        station: row.station_id,
        stationName: row.station_name,
        settlementStation: row.settlement_station_id,
        settlementStationName: row.settlement_station_name,
        settlementRuleVerifiedAt: row.settlement_rule_verified_at,
        settlementTimezone: row.settlement_timezone,
        settlementUnit: row.settlement_unit,
        settlementTimeBasis: row.settlement_time_basis,
        primarySettlementSource: row.primary_settlement_source,
        verificationStatus: row.verification_status,
        continent: cityContinent(row.city_key, row.city_name),
        unit: row.unit || 'F',
        latest: row.latest_best ?? null,
        latestMetar: row.latest_metar ?? null,
        forecastCount: row.forecast_count ?? row.forecast_points?.length ?? row.points?.length ?? 0,
        historyCount: row.history_count ?? row.history_points?.length ?? 0,
        humidityStatus: row.humidity_status,
        signals: 0,
        actionable: 0,
        enabled: Boolean(row.enabled),
        tier: row.tier ?? 9,
        lastRefreshedAt: row.last_refreshed_at ?? row.latest_timestamp ?? null,
      })
    }

    for (const row of forecasts) {
      if (!rows.has(row.city_key)) {
        rows.set(row.city_key, {
          key: row.city_key,
          name: row.city_name,
          station: undefined,
          stationName: undefined,
          settlementStation: undefined,
          settlementStationName: undefined,
          settlementRuleVerifiedAt: null,
          settlementTimezone: undefined,
          settlementUnit: undefined,
          settlementTimeBasis: undefined,
          primarySettlementSource: undefined,
          verificationStatus: 'unverified',
          continent: cityContinent(row.city_key, row.city_name),
          unit: 'F',
          latest: row.mean_high,
          latestMetar: null,
          forecastCount: 1,
          historyCount: 0,
          humidityStatus: 'not_collected',
          signals: 0,
          actionable: 0,
          enabled: false,
          tier: 9,
          lastRefreshedAt: null,
        })
      }
    }

    for (const signal of signals) {
      const row = rows.get(signal.city_key) ?? {
        key: signal.city_key,
        name: signal.city_name,
        station: undefined,
        stationName: undefined,
        settlementStation: undefined,
        settlementStationName: undefined,
        settlementRuleVerifiedAt: null,
        settlementTimezone: undefined,
        settlementUnit: undefined,
        settlementTimeBasis: undefined,
        primarySettlementSource: undefined,
        verificationStatus: 'unverified',
        continent: cityContinent(signal.city_key, signal.city_name),
        unit: 'F',
        latest: null,
        latestMetar: null,
        forecastCount: 0,
        historyCount: 0,
        humidityStatus: 'not_collected',
        signals: 0,
        actionable: 0,
        enabled: false,
        tier: 9,
        lastRefreshedAt: null,
      }
      row.signals += 1
      if (signal.actionable) row.actionable += 1
      rows.set(signal.city_key, row)
    }

    return [...rows.values()].sort((a, b) => {
      if (citySort === 'alpha') return a.name.localeCompare(b.name)
      if (b.actionable !== a.actionable) return b.actionable - a.actionable
      if (b.signals !== a.signals) return b.signals - a.signals
      return a.name.localeCompare(b.name)
    })
  }, [citySeries, forecasts, signals, citySort])

  useEffect(() => {
    if (!balanceInitRef.current && data?.stats?.bankroll !== undefined) {
      setSimBalance(String(Math.round(data.stats.bankroll)))
      balanceInitRef.current = true
    }
  }, [data?.stats?.bankroll])

  useEffect(() => {
    if (!liveAvailable && tradeMode === 'live') {
      setTradeMode('paper')
    }
  }, [liveAvailable, tradeMode])

  useEffect(() => {
    if (!selectedCity && cityOptions[0]?.key) {
      setSelectedCity(cityOptions[0].key)
    } else if (selectedCity && cityOptions.length > 0 && !cityOptions.some(city => city.key === selectedCity)) {
      setSelectedCity(cityOptions[0].key)
    }
  }, [cityOptions, selectedCity])

  const selectedCityMeta = cityOptions.find(city => city.key === selectedCity)
  const selectedCityEvidence = cityEvidence.find(city => city.city_key === selectedCity)
  const selectedDateEvidence = selectedCityEvidence?.dates.find(item => item.target_date === selectedDate) ?? selectedCityEvidence?.dates[0]
  const selectedTradingStatus = resolveCityTradingStatus(selectedCityMeta?.key, selectedCityMeta?.verificationStatus, cityStatusMap)
  const selectedStatusConfig = selectedCityMeta?.key ? (cityStatusMap[selectedCityMeta.key] ?? ROUND5_STATUS_FALLBACK[selectedCityMeta.key]) : undefined
  const selectedMarketBucket = marketBucketsQuery.data?.latest?.find(bucket => bucket.event_slug || bucket.event_url)
  const cityGroups = useMemo(() => {
    const groups: Record<'mainland' | 'asia' | 'us', typeof cityOptions> = {
      mainland: [],
      asia: [],
      us: [],
    }
    for (const city of cityOptions) {
      groups[cityGroupKey(city.key, city.continent)].push(city)
    }
    return groups
  }, [cityOptions])
  const recommendations = data?.recommendations ?? null
  const recommendedItems = recommendations?.items ?? []
  const actionableCityCount = recommendations?.trade_candidate_count ?? cityOptions.filter(city => city.actionable > 0).length
  const selectedEvidenceCount = (selectedCityMeta?.forecastCount ?? 0)
    + (selectedCityMeta?.historyCount ?? 0)
    + (selectedCityMeta?.latestMetar !== null && selectedCityMeta?.latestMetar !== undefined ? 1 : 0)
    + (selectedCityMeta?.humidityStatus === 'available' ? 1 : 0)
  const selectedEvidenceReady = selectedEvidenceCount > 0

  useEffect(() => {
    productionRefreshRunningRef.current = productionRefreshRunning
  }, [productionRefreshRunning])

  useEffect(() => {
    const pollers = schedulerStatus?.pollers ?? {}
    for (const key of ['forecast_poller', 'metar_poller', 'china_live_poller', 'derive_poller']) {
      const poller = pollers[key]
      const runKey = poller?.last_run_at
      if (!poller || !runKey || seenSchedulerRunsRef.current[key] === runKey) continue
      seenSchedulerRunsRef.current[key] = runKey
      const cityResults = poller.last_result?.city_results ?? []
      for (const [index, result] of cityResults.entries()) {
        const city = result.city || result.station_id || 'unknown'
        const ok = Boolean(result.ok)
        showRefreshNotice({
          id: Date.now() + index + (key === 'metar_poller' ? 100 : key === 'forecast_poller' ? 200 : key === 'china_live_poller' ? 250 : 300),
          tone: ok ? 'success' : 'error',
          title: ok
            ? `${poller.label} ${city} ${uiLanguage === 'zh' ? '更新完成' : 'updated'}`
            : `${poller.label} ${city} ${uiLanguage === 'zh' ? '更新失败' : 'failed'}`,
          message: ok
            ? `${poller.label} · ${durationLabel(poller.last_duration_ms)} · ${relativeTime(runKey)}`
            : (result.error || poller.last_message || 'scheduler city refresh failed'),
          details: [
            result.station_id ? `station ${result.station_id}` : city,
            result.rows_upserted !== null && result.rows_upserted !== undefined
              ? `upserted ${result.rows_upserted}`
              : (result.reports_upserted !== null && result.reports_upserted !== undefined ? `upserted ${result.reports_upserted}` : `poller ${key}`),
          ],
        }, ok ? 5000 : 14000)
      }
    }
  }, [schedulerStatus, uiLanguage])

  const runProductionRefreshFromDashboard = (source: 'manual' | 'auto' = 'manual') => {
    if (productionRefreshRunningRef.current) {
      showRefreshNotice({
        id: Date.now(),
        tone: 'warning',
        title: uiLanguage === 'zh' ? '已有抓取正在运行' : 'Refresh already running',
        message: uiLanguage === 'zh'
          ? '请等待当前 production-refresh 完成；后端锁会阻止重复抓取。'
          : 'Please wait for the current production-refresh to finish. The backend lock prevents duplicate fetches.',
        details: ['production-refresh lock'],
      }, 9000)
      return
    }
    productionRefreshMutation.mutate({
      ...currentRefreshOptions,
      source,
    })
  }

  const filteredCityOptions = cityOptions.filter(city => {
    const query = citySearch.trim().toLowerCase()
    const continentOk = continentFilter === '全部' || city.continent === continentFilter
    if (!continentOk) return false
    if (!query) return true
    return `${city.name} ${city.station ?? ''} ${city.key} ${city.continent}`.toLowerCase().includes(query)
  })
  const cityHref = (city: { key: string; station?: string }) => {
    const params = new URLSearchParams()
    params.set('city', cityPageSlug(city))
    if (selectedDate) params.set('date', selectedDate)
    return `?${params.toString()}`
  }

  useEffect(() => {
    if (!selectedCityMeta || typeof window === 'undefined') return
    const params = new URLSearchParams(window.location.search)
    const nextCity = cityPageSlug(selectedCityMeta)
    if (params.get('city') === nextCity) return
    params.set('city', nextCity)
    const nextUrl = `${window.location.pathname}?${params.toString()}`
    window.history.replaceState(null, '', nextUrl)
  }, [selectedCityMeta])

  useEffect(() => {
    if (!selectedDate || typeof window === 'undefined') return
    const params = new URLSearchParams(window.location.search)
    if (params.get('date') === selectedDate) return
    params.set('date', selectedDate)
    const nextUrl = `${window.location.pathname}?${params.toString()}`
    window.history.replaceState(null, '', nextUrl)
  }, [selectedDate])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem('weatherbot-ui-language', uiLanguage)
    document.documentElement.lang = uiLanguage === 'zh' ? 'zh-CN' : 'en'
  }, [uiLanguage])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem('weatherbot-ui-theme', themeMode)
    document.documentElement.dataset.theme = themeMode
    const background = themeMode === 'dark' ? '#161A22' : '#ffffff'
    document.documentElement.style.backgroundColor = background
    document.body.style.backgroundColor = background
  }, [themeMode])

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-black text-neutral-300">
        <div className="text-center">
          <div className="mx-auto mb-4 h-9 w-9 animate-spin border-2 border-neutral-800 border-t-green-400" />
          <div className="text-xs text-neutral-500">正在连接本地看板 API...</div>
        </div>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="flex h-screen items-center justify-center bg-black text-neutral-300">
        <div className="max-w-md border border-red-500/30 bg-red-500/5 p-5 text-center">
          <div className="mb-2 text-sm text-red-300">后端未连接</div>
          <p className="mb-4 text-[12px] leading-relaxed text-neutral-500">
            请确认 dashboard_server 正在运行于 http://127.0.0.1:8765，然后刷新页面。
          </p>
          <button onClick={() => refetch()} className="border border-neutral-700 px-3 py-1.5 text-xs text-neutral-200">
            重试
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className={`${themeMode === 'dark' ? 'polywx-dark bg-[#161A22] text-[#CBD2DC]' : 'polywx-light bg-white text-gray-900'} flex min-h-screen flex-col xl:h-screen xl:overflow-hidden`}>
      <header className="flex shrink-0 flex-wrap items-start gap-2 border-b border-neutral-800 px-3 py-2">
        <div className="min-w-0 flex-1 basis-[130px]">
          <div className="flex items-baseline gap-2">
            <h1 className="text-sm font-semibold tracking-wide text-neutral-100">WeatherBot</h1>
            <span className="border border-neutral-800 px-1.5 py-0.5 text-[9px] tabular-nums text-neutral-500">{APP_VERSION}</span>
          </div>
          <div className="text-[11px] text-neutral-600">{t('app.subtitle')}</div>
        </div>
        <div className="order-last flex min-w-0 basis-full flex-nowrap items-center gap-1.5 overflow-x-auto text-[10px] xl:overflow-visible">
          <span className="shrink-0 border border-neutral-800 px-2 py-1 text-neutral-400">{copy.data} {dataAge(stats.data_age_minutes)}</span>
          <SchedulerBadge poller={schedulerStatus?.pollers?.forecast_poller} label="Forecast" />
          <SchedulerBadge poller={schedulerStatus?.pollers?.metar_poller} label="METAR" />
          <SchedulerBadge poller={schedulerStatus?.pollers?.china_live_poller} label="China Live" />
          <SchedulerBadge poller={schedulerStatus?.pollers?.derive_poller} label="Historical" />
          <span
            className={`shrink-0 border px-2 py-1 ${
              productionRefreshRunning
                ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300'
                : productionRefresh?.ok
                  ? 'border-green-500/30 text-green-300'
                  : productionRefresh
                    ? 'border-amber-500/30 text-amber-300'
                    : 'border-neutral-800 text-neutral-500'
            }`}
            title={(productionRefresh?.failed_stages ?? []).length > 0 ? `失败阶段：${productionRefresh?.failed_stages?.join(', ')}` : `刷新目标：${productionRefresh?.target_date || productionRefresh?.request?.start_date || '--'}`}
          >
            {productionRefreshRunning
              ? `抓取中 ${productionRefreshDone}/${productionRefreshStages.length || 11}${productionRefreshCurrent?.name ? ` · ${productionRefreshCurrent.name}` : ''}`
              : productionRefresh?.requested_at
                ? `抓取 ${productionRefresh?.ok ? '完成' : '异常'} · ${productionRefresh?.target_date || productionRefresh?.request?.start_date || '--'}`
                : '抓取未运行'}
          </span>
          <span className={`shrink-0 border px-2 py-1 ${stats.is_running ? 'border-green-500/30 text-green-300' : 'border-neutral-800 text-neutral-500'}`}>
            {stats.is_running ? copy.legacyRunning : copy.manual}
          </span>
          <span className={`shrink-0 border px-2 py-1 ${autoSimulation.enabled ? 'border-cyan-500/30 text-cyan-300' : 'border-neutral-800 text-neutral-500'}`}>
            {autoSimulation.enabled ? copy.autoOn : copy.autoOff}
          </span>
          <span className={`shrink-0 border px-2 py-1 ${liveAvailable ? 'border-green-500/30 text-green-300' : 'border-amber-500/30 text-amber-300'}`}>
            {liveAvailable ? copy.liveReady : copy.liveLocked}
          </span>
        </div>
        <label className="inline-flex items-center gap-1 border border-neutral-800 px-2 py-1.5 text-[11px] text-neutral-400">
          <span>{t('city.selector')}</span>
          <select
            value={selectedCity}
            onChange={event => setSelectedCity(event.target.value)}
            className="min-w-[180px] bg-transparent text-neutral-100 outline-none"
            aria-label={t('city.selector')}
          >
            <optgroup label={t('city.group.mainland')}>
              {cityGroups.mainland.map(city => {
                const status = resolveCityTradingStatus(city.key, city.verificationStatus, cityStatusMap)
                return <option key={city.key} value={city.key}>{STATUS_ICON[status]} {city.name} · {city.station || '--'}</option>
              })}
            </optgroup>
            <optgroup label={t('city.group.asia')}>
              {cityGroups.asia.map(city => {
                const status = resolveCityTradingStatus(city.key, city.verificationStatus, cityStatusMap)
                return <option key={city.key} value={city.key}>{STATUS_ICON[status]} {city.name} · {city.station || '--'}</option>
              })}
            </optgroup>
            <optgroup label={t('city.group.us')}>
              {cityGroups.us.map(city => {
                const status = resolveCityTradingStatus(city.key, city.verificationStatus, cityStatusMap)
                return <option key={city.key} value={city.key}>{STATUS_ICON[status]} {city.name} · {city.station || '--'}</option>
              })}
            </optgroup>
          </select>
        </label>
        <label className="inline-flex items-center gap-1 border border-neutral-800 px-2 py-1.5 text-[11px] text-neutral-400" aria-label={t('language.label')}>
          <span>{t('language.label')}</span>
          <select
            value={uiLanguage}
            onChange={event => setUiLanguage(event.target.value === 'en' ? 'en' : 'zh')}
            className="bg-transparent text-neutral-100 outline-none"
          >
            <option value="zh">{t('language.zh')}</option>
            <option value="en">{t('language.en')}</option>
          </select>
        </label>
        <div className="inline-flex items-center border border-neutral-800 text-[11px]" aria-label={copy.theme}>
          <button
            type="button"
            onClick={() => setThemeMode('light')}
            className={`px-2 py-1.5 ${themeMode === 'light' ? 'bg-neutral-100 text-black' : 'text-neutral-500 hover:bg-neutral-900 hover:text-neutral-200'}`}
          >
            {uiLanguage === 'zh' ? '浅色' : 'Light'}
          </button>
          <button
            type="button"
            onClick={() => setThemeMode('dark')}
            className={`border-l border-neutral-800 px-2 py-1.5 ${themeMode === 'dark' ? 'bg-[#2563EB] text-white' : 'text-neutral-500 hover:bg-neutral-900 hover:text-neutral-200'}`}
          >
            {uiLanguage === 'zh' ? '深色' : 'Dark'}
          </button>
        </div>
        <button
          onClick={() => runProductionRefreshFromDashboard('manual')}
          disabled={productionRefreshRunning}
          className="inline-flex items-center gap-1 whitespace-nowrap border border-green-500/30 px-2 py-1.5 text-[11px] text-green-300 hover:bg-green-500/10 disabled:opacity-40"
          title="刷新当前城市/日期一次：production-refresh-v2。不会启动常驻调度器。"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${productionRefreshRunning ? 'animate-spin' : ''}`} />
          {productionRefreshRunning ? copy.fetching : copy.refreshCurrent}
        </button>
        <button
          onClick={() => {
            if (schedulerRunning) schedulerStopMutation.mutate()
            else schedulerStartMutation.mutate()
          }}
          disabled={schedulerStartMutation.isPending || schedulerStopMutation.isPending}
          className={`inline-flex items-center gap-1 whitespace-nowrap border px-2 py-1.5 text-[11px] disabled:opacity-40 ${
            schedulerRunning
              ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200 hover:bg-cyan-500/15'
              : 'border-neutral-700 text-neutral-300 hover:bg-neutral-900'
          }`}
          title={`后端常驻调度器。下一次 METAR：${schedulerStatus?.pollers?.metar_poller?.next_run_at ? timeText(schedulerStatus.pollers.metar_poller.next_run_at) : '--'}`}
        >
          <Activity className={`h-3.5 w-3.5 ${schedulerRunning ? 'animate-pulse' : ''}`} />
          {schedulerRunning ? copy.schedulerStop : copy.schedulerStart}
        </button>
        {stats.is_running && (
          <button
            onClick={() => stopMutation.mutate()}
            disabled={stopMutation.isPending}
            className="inline-flex items-center gap-1 whitespace-nowrap border border-red-500/30 px-2 py-1.5 text-[11px] text-red-300 hover:bg-red-500/10 disabled:opacity-40"
            title="停止旧版 weatherbet.py 循环扫描。v3 数据刷新不依赖这个进程。"
          >
            <PauseCircle className="h-3.5 w-3.5" />
            {copy.stopLegacy}
          </button>
        )}
        <button
          onClick={() => refetch()}
          className="inline-flex items-center gap-1 whitespace-nowrap border border-neutral-700 px-2 py-1.5 text-[11px] text-neutral-300 hover:bg-neutral-900"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          {copy.refresh}
        </button>
      </header>

      {refreshNotices.length > 0 && (
        <div
          role="status"
          aria-live="polite"
          className="fixed right-3 top-16 z-50 flex w-[min(380px,calc(100vw-24px))] flex-col gap-2"
        >
          {refreshNotices.map(refreshNotice => (
            <div
              key={refreshNotice.id}
              className={`border p-3 text-xs shadow-xl ${
                themeMode === 'dark' ? 'bg-[#1B212C] text-[#CBD2DC]' : 'bg-white text-gray-900'
              } ${
                refreshNotice.tone === 'success'
                  ? 'border-green-500/40'
                  : refreshNotice.tone === 'error'
                    ? 'border-red-500/50'
                    : refreshNotice.tone === 'warning'
                      ? 'border-amber-500/50'
                      : 'border-cyan-500/40'
              }`}
            >
              <div className="flex items-start gap-2">
                {refreshNotice.tone === 'success' ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-400" />
                ) : refreshNotice.tone === 'error' ? (
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
                ) : refreshNotice.tone === 'warning' ? (
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
                ) : (
                  <RefreshCw className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-cyan-400" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{refreshNotice.title}</div>
                  <div className={themeMode === 'dark' ? 'mt-1 text-[#7D8694]' : 'mt-1 text-gray-500'}>
                    {refreshNotice.message}
                  </div>
                  {refreshNotice.details?.length ? (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {refreshNotice.details.map(detail => (
                        <span
                          key={detail}
                          className={`border px-1.5 py-0.5 text-[10px] ${
                            refreshNotice.tone === 'error'
                              ? 'border-red-500/30 text-red-300'
                              : refreshNotice.tone === 'success'
                                ? 'border-green-500/30 text-green-300'
                                : refreshNotice.tone === 'warning'
                                  ? 'border-amber-500/30 text-amber-300'
                                  : 'border-cyan-500/30 text-cyan-300'
                          }`}
                        >
                          {detail}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={() => setRefreshNotices(current => current.filter(item => item.id !== refreshNotice.id))}
                  className={themeMode === 'dark' ? 'text-[#7D8694] hover:text-white' : 'text-gray-400 hover:text-gray-900'}
                  aria-label="关闭数据抓取提示"
                >
                  ×
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <main className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto xl:grid-cols-[260px_minmax(560px,1fr)_340px] xl:overflow-hidden">
        <aside className="order-2 border-b border-neutral-800 bg-neutral-950/40 xl:order-1 xl:min-h-0 xl:overflow-y-auto xl:border-b-0 xl:border-r">
          <div className="p-3">
            <div className="mb-2 flex items-center justify-between">
              <div>
                <div className="text-sm font-medium text-neutral-100">城市</div>
                <div className="text-[10px] text-neutral-600">
                  {actionableCityCount > 0 ? `${actionableCityCount} 个城市有可执行信号` : '无信号时按城市浏览证据'}
                </div>
              </div>
              <span className="border border-neutral-800 px-1.5 py-0.5 text-[10px] text-neutral-500">{cityOptions.length}</span>
            </div>
            <div className="mb-2 grid gap-1.5">
              <div className="grid grid-cols-[minmax(0,1fr)_112px] gap-1.5">
                <input
                  value={citySearch}
                  onChange={event => setCitySearch(event.target.value)}
                  placeholder="搜索城市或机场"
                  className="w-full border border-neutral-800 bg-black px-2 py-1.5 text-[11px]"
                  aria-label="搜索城市或机场"
                />
                <select
                  value={continentFilter}
                  onChange={event => setContinentFilter(event.target.value as (typeof CONTINENT_FILTERS)[number])}
                  className="border border-neutral-800 bg-black px-1.5 py-1.5 text-[11px] text-neutral-300"
                  aria-label="按大洲筛选城市"
                >
                  {CONTINENT_FILTERS.map(continent => (
                    <option key={continent} value={continent}>{continent}</option>
                  ))}
                </select>
              </div>
              <select
                value={citySort}
                onChange={event => setCitySort(event.target.value as 'signal' | 'alpha')}
                className="border border-neutral-800 bg-black px-1.5 py-1.5 text-[11px] text-neutral-300"
                aria-label="城市排序"
              >
                <option value="signal">按信号</option>
                <option value="alpha">字母</option>
              </select>
            </div>
            <div className="space-y-1">
              {cityOptions.length === 0 && (
                <div className="border border-neutral-800 bg-black/40 p-3 text-[11px] leading-relaxed text-neutral-500">
                  暂无城市快照。点击顶部“自动抓取”后，这里会按城市列出预报、站点和信号数量。
                </div>
              )}
              {cityOptions.length > 0 && filteredCityOptions.length === 0 && (
                <div className="border border-neutral-800 bg-black/40 p-3 text-[11px] text-neutral-500">
                  没有匹配的城市。
                </div>
              )}
              {filteredCityOptions.map(city => (
                <div
                  key={city.key}
                  title={`预报 ${city.forecastCount} · METAR ${city.latestMetar !== null && city.latestMetar !== undefined ? Number(city.latestMetar).toFixed(1) + '°' + city.unit : '--'} · 历史 ${city.historyCount} · 湿度 ${city.humidityStatus === 'available' ? '可用' : '缺失'} · 信号 ${city.actionable}/${city.signals}`}
                  className={`flex min-h-[58px] w-full items-stretch gap-2 border px-2 py-2 text-left transition ${
                    selectedCity === city.key
                      ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-100'
                      : 'border-neutral-800 bg-black/40 text-neutral-300 hover:border-neutral-700'
                  }`}
                >
                  <button
                    type="button"
                    onClick={event => {
                      event.preventDefault()
                      event.stopPropagation()
                      stationEnabledMutation.mutate({ cityKey: city.key, enabled: !city.enabled })
                    }}
                    disabled={stationEnabledMutation.isPending}
                    className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center border disabled:opacity-40 ${
                      city.enabled
                        ? 'border-amber-400/40 bg-amber-400/10 text-amber-300'
                        : 'border-neutral-800 text-neutral-600 hover:text-neutral-200'
                    }`}
                    aria-label={`${city.enabled ? '暂停' : '启用'} ${city.name} 调度`}
                    title={city.enabled ? '点击后暂停该城市调度' : '点击后加入后端调度 watchlist'}
                  >
                    <Star className="h-3.5 w-3.5" fill={city.enabled ? 'currentColor' : 'none'} />
                  </button>
                  <a
                    href={cityHref(city)}
                    onClick={event => {
                      event.preventDefault()
                      setSelectedCity(city.key)
                    }}
                    className="min-w-0 flex-1"
                  >
                    <div className="flex items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className={`h-2 w-2 shrink-0 rounded-full ${city.enabled ? 'bg-amber-300' : city.actionable > 0 ? 'bg-green-300' : city.forecastCount > 0 ? 'bg-cyan-300' : 'bg-neutral-700'}`} />
                      <div className="min-w-0">
                        <div className="truncate text-xs font-medium leading-tight">{city.name}</div>
                        <div className="mt-1 truncate text-[10px] leading-tight text-neutral-600">
                          {city.station || 'station 未映射'} · {city.enabled ? `已启用 · ${relativeTime(city.lastRefreshedAt)}` : '未调度'}
                        </div>
                      </div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="tabular-nums text-[11px] leading-tight text-neutral-200">
                        {city.latest === null || city.latest === undefined ? '--' : `${Number(city.latest).toFixed(1)}°${city.unit}`}
                      </div>
                      <div className="mt-1 text-[9px] tabular-nums text-neutral-600">T{city.tier}</div>
                    </div>
                    </div>
                  </a>
                </div>
              ))}
            </div>
          </div>

        </aside>

        <section className="order-1 min-h-[720px] overflow-hidden xl:order-2 xl:flex xl:min-h-0 xl:flex-col">
          <div className="z-20 shrink-0 flex flex-wrap items-center justify-between gap-2 border-b border-neutral-800 bg-black/95 px-3 py-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-neutral-100">
                {selectedCityMeta?.name ?? '城市天气证据'} · {selectedCityMeta?.station || 'station 未映射'}
              </div>
              <div className="mt-0.5 flex flex-wrap gap-1.5 text-[10px]">
                <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">{selectedDate || '日期待定'}</span>
                <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">
                  {selectedCityMeta?.latest === null || selectedCityMeta?.latest === undefined ? '预测 --' : `预测 ${Number(selectedCityMeta.latest).toFixed(1)}°${selectedCityMeta.unit}`}
                </span>
                <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">
                  {selectedCityMeta?.latestMetar === null || selectedCityMeta?.latestMetar === undefined ? 'METAR --' : `METAR ${Number(selectedCityMeta.latestMetar).toFixed(1)}°${selectedCityMeta.unit}`}
                </span>
                <span className={`border px-1.5 py-0.5 ${selectedEvidenceReady ? 'border-cyan-500/30 text-cyan-200' : 'border-neutral-800 text-neutral-500'}`}>
                  证据 F{selectedCityMeta?.forecastCount ?? 0} / H{selectedCityMeta?.historyCount ?? 0}
                </span>
                <span className={`border px-1.5 py-0.5 ${(selectedDateEvidence?.ready_modules ?? 0) > 0 ? 'border-blue-500/30 text-blue-200' : 'border-neutral-800 text-neutral-500'}`}>
                  模块 {selectedDateEvidence?.ready_modules ?? 0}/{selectedDateEvidence?.module_count ?? 8}
                </span>
                <span className={`border px-1.5 py-0.5 ${actionable > 0 ? 'border-green-500/30 text-green-300' : 'border-neutral-800 text-neutral-500'}`}>
                  信号 {actionable}/{signals.length}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1.5 text-[9px]">
                <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500" title={selectedCityMeta?.settlementStationName || selectedCityMeta?.stationName || ''}>
                  Settlement station {selectedCityMeta?.settlementStation || selectedCityMeta?.station || '--'}
                </span>
                <span className={`border px-1.5 py-0.5 ${
                  selectedCityMeta?.verificationStatus === 'verified'
                    ? 'border-green-500/30 text-green-300'
                    : selectedCityMeta?.verificationStatus === 'settlement_mismatch'
                      ? 'border-red-500/30 text-red-300'
                      : 'border-amber-500/30 text-amber-300'
                }`}>
                  Rule verified {selectedCityMeta?.settlementRuleVerifiedAt ? relativeTime(selectedCityMeta.settlementRuleVerifiedAt) : 'unverified'}
                </span>
                <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">
                  Timezone {selectedCityMeta?.settlementTimezone || '--'}
                </span>
                <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-500">
                  Truth source {selectedCityMeta?.primarySettlementSource || 'pending'}
                </span>
                <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-600">
                  Non-truth metar_reports/IEM display only
                </span>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5 text-[10px]">
              {needsManualRefresh && (
                <span className="border border-cyan-500/30 bg-cyan-500/10 px-1.5 py-0.5 text-cyan-200">
                  等待自动抓取
                </span>
              )}
              <span className="border border-neutral-800 px-1.5 py-0.5 text-neutral-400">数据 {dataAge(stats.data_age_minutes)}</span>
            </div>
          </div>

          {selectedCityMeta && (
            <div className={`border-b px-3 py-2 text-[11px] ${statusTone(selectedTradingStatus)}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">
                  {STATUS_ICON[selectedTradingStatus]} {t(`city.status.${selectedTradingStatus}`)}
                </span>
                {selectedTradingStatus === 'paper_only' && selectedCityMeta.key === 'hong-kong' ? (
                  <span>{t('banner.hk')}</span>
                ) : selectedTradingStatus === 'monitor_only' && selectedCityMeta.key === 'seoul' ? (
                  <span>{t('banner.seoul')}</span>
                ) : selectedTradingStatus === 'fully_active' ? (
                  <span>
                    {t('banner.active', {
                      slug: selectedMarketBucket?.event_slug || selectedMarketBucket?.event_url || selectedCityMeta.key,
                      source: selectedCityMeta.primarySettlementSource || selectedStatusConfig?.settlement || 'verified',
                    })}
                    {selectedMarketBucket?.event_url ? (
                      <a href={selectedMarketBucket.event_url} target="_blank" rel="noreferrer" className="ml-2 underline decoration-dotted underline-offset-2">
                        Polymarket
                      </a>
                    ) : null}
                  </span>
                ) : (
                  <span>{selectedStatusConfig?.reason || selectedCityMeta.verificationStatus || 'no active market'}</span>
                )}
              </div>
            </div>
          )}

          <div className="min-h-[720px] overflow-y-auto xl:min-h-0 xl:flex-1">
            <div className="flex gap-1 border-b border-neutral-800 p-2 text-[11px]">
              <button
                type="button"
                onClick={() => setActiveMainView('workbench')}
                className={`border px-3 py-1.5 ${activeMainView === 'workbench' ? 'border-blue-500/40 bg-blue-500/15 text-blue-100' : 'border-neutral-800 text-neutral-500 hover:text-neutral-200'}`}
              >
                {t('view.workbench')}
              </button>
              <button
                type="button"
                onClick={() => setActiveMainView('delta')}
                className={`border px-3 py-1.5 ${activeMainView === 'delta' ? 'border-blue-500/40 bg-blue-500/15 text-blue-100' : 'border-neutral-800 text-neutral-500 hover:text-neutral-200'}`}
              >
                {t('view.deltaAudit')}
              </button>
            </div>
            {activeMainView === 'workbench' ? (
            <>
            <div className="border-b border-neutral-800 p-2">
              <div className="mb-1 flex items-center justify-between gap-2">
                <div className="text-[10px] font-medium text-neutral-300">推荐关注</div>
                <div className="text-[9px] text-neutral-500">
                  信号 {recommendations?.trade_candidate_count ?? 0} · 观察 {recommendations?.observation_only_count ?? 0}
                </div>
              </div>
              {recommendedItems.length > 0 ? (
                <div className="grid gap-1 md:grid-cols-2 2xl:grid-cols-4">
                  {recommendedItems.map(item => (
                    <RecommendationCard
                      key={`${item.type}-${item.city_key}-${item.target_date}-${item.bucket_key ?? item.metar_report_time ?? 'latest'}`}
                      item={item}
                      selected={selectedCity === item.city_key}
                      onSelect={() => {
                        setSelectedCity(item.city_key)
                        if (item.target_date) setSelectedDate(item.target_date)
                      }}
                    />
                  ))}
                </div>
              ) : (
                <div className="border border-neutral-800 px-2 py-2 text-[10px] text-neutral-500">
                  {recommendations?.empty_reason === 'scheduler_stopped'
                    ? '启动调度器以获取实时推荐。'
                    : '暂无通过 METAR age / verified / strict match / paper gate 的实时推荐。'}
                  {recommendations?.skipped ? (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {Object.entries(recommendations.skipped).slice(0, 5).map(([reason, count]) => (
                        <span key={reason} className="border border-neutral-800 px-1.5 py-0.5">
                          {reason} {count}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              )}
            </div>
            <WeatherPanel
              forecasts={forecasts}
              signals={signals}
              citySeries={citySeries}
              events={events}
              fetchLog={fetchLog}
              productionRefresh={productionRefresh}
              marketBuckets={marketBucketsQuery.data ?? null}
              signalDecisions={signalDecisionsQuery.data ?? null}
              dailyMaxPrediction={dailyMaxPredictionQuery.data ?? null}
              layer7Loading={marketBucketsQuery.isFetching || signalDecisionsQuery.isFetching || dailyMaxPredictionQuery.isFetching}
              selectedCity={selectedCity}
              onSelectedCity={setSelectedCity}
              selectedDate={selectedDate}
              selectedDateEvidence={selectedDateEvidence}
              onSelectedDate={setSelectedDate}
              onRefreshWeather={() => productionRefreshMutation.mutate({
                cities: selectedCity ? [selectedCity] : [],
                days: refreshDaysForDate(selectedDate),
                limit: 20,
                startDate: selectedDate || '',
                endDate: selectedDate || '',
              })}
              weatherRefreshing={productionRefreshRunning}
              onBackfillHistory={() => historyBackfillMutation.mutate()}
              backfilling={historyBackfillMutation.isPending}
              backfillResult={historyBackfillMutation.data}
              alphaEvents={modelRepriceEventsQuery.data?.rows ?? []}
            />
            </>
            ) : (
              <DeltaAuditPanel
                summary={truthDeltaAuditQuery.data ?? null}
                selectedCity={selectedCity}
                language={i18nLanguage}
              />
            )}
          </div>
        </section>

        <aside className="order-3 flex h-[900px] min-h-0 flex-col border-t border-neutral-800 xl:h-auto xl:border-l xl:border-t-0">
          <div className="shrink-0 border-b border-neutral-800 bg-black/95 px-3 py-2">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-neutral-100">Execution Workbench</div>
                <div className="mt-0.5 text-[10px] text-neutral-600">paper account · signal queue · order log</div>
              </div>
              <span className={`shrink-0 border px-1.5 py-0.5 text-[9px] ${liveAvailable ? 'border-green-500/30 text-green-300' : 'border-amber-500/30 text-amber-300'}`}>
                {liveAvailable ? 'Live ready' : 'Live locked'}
              </span>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-1 text-[10px]">
              <StatusTile label="模式" value={tradeMode === 'paper' ? '模拟盘' : '实盘检查'} tone={tradeMode === 'paper' ? 'cyan' : 'amber'} />
              <StatusTile label="一键模拟" value={autoSimulation.enabled ? '运行中' : '已停止'} active={autoSimulation.enabled} />
              <StatusTile label="信号队列" value={`${actionable}/${signals.length}`} tone={actionable > 0 ? 'green' : 'neutral'} />
              <StatusTile label="订单记录" value={`${stats.open_trades ?? 0}/${stats.settled_trades ?? 0}`} />
            </div>
          </div>
          <div className="space-y-3 border-b border-neutral-800 p-3">
            <TradeModeSwitch mode={tradeMode} liveAvailable={liveAvailable} onMode={setTradeMode} />
            <SimulationCard
              stats={stats}
              value={simBalance}
              clearMarks={clearMarks}
              autoSimulation={autoSimulation}
              onValue={setSimBalance}
              onClearMarks={setClearMarks}
              onReset={() => {
                const parsed = Number(simBalance)
                if (Number.isFinite(parsed) && parsed >= 0) {
                  resetSimulationMutation.mutate({ balance: parsed, clear: clearMarks })
                }
              }}
              onSettle={() => settleMutation.mutate()}
              onToggleAuto={() => autoSimulationMutation.mutate(!autoSimulation.enabled)}
              resetting={resetSimulationMutation.isPending}
              settling={settleMutation.isPending}
              autoPending={autoSimulationMutation.isPending}
            />
            <ForecastOptionsCard
              cityName={selectedCityMeta?.name ?? ''}
              station={selectedCityMeta?.station ?? ''}
              selectedDate={selectedDate}
              dataAgeLabel={dataAge(stats.data_age_minutes)}
              signals={signals.length}
              actionable={actionable}
              refreshing={productionRefreshRunning}
              language={uiLanguage}
              onRefresh={() => productionRefreshMutation.mutate({
                cities: selectedCity ? [selectedCity] : [],
                days: refreshDaysForDate(selectedDate),
                limit: 20,
                startDate: selectedDate || '',
                endDate: selectedDate || '',
              })}
            />
          </div>
          <div className="grid grid-cols-2 border-b border-neutral-800" role="tablist" aria-label="行动与交易记录">
            <button
              type="button"
              role="tab"
              aria-selected={activityView === 'signals'}
              onClick={() => setActivityView('signals')}
              className={`min-h-11 border-r border-neutral-800 px-3 text-left ${
                activityView === 'signals' ? 'bg-cyan-500/10 text-cyan-200' : 'text-neutral-500 hover:bg-neutral-950'
              }`}
            >
              <div className="text-xs">信号队列</div>
              <div className="text-[9px]">{signals.length} 条</div>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activityView === 'trades'}
              onClick={() => setActivityView('trades')}
              className={`min-h-11 px-3 text-left ${
                activityView === 'trades' ? 'bg-amber-500/10 text-amber-200' : 'text-neutral-500 hover:bg-neutral-950'
              }`}
            >
              <div className="text-xs">模拟 / 交易记录</div>
              <div className="text-[9px]">{trades.length} 条</div>
            </button>
          </div>

          {activityView === 'signals' ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="border-b border-neutral-800 px-3 py-2">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-xs text-neutral-200">信号行动</div>
                    <div className="text-[10px] tabular-nums text-neutral-500">{actionable} 可执行 / {signals.length} 总信号</div>
                  </div>
                  <details className="text-right text-[10px] text-neutral-500">
                    <summary className="cursor-pointer select-none hover:text-neutral-300">详情</summary>
                    <div className="mt-1 max-w-[240px] text-left leading-relaxed">
                      展开单条信号可看盘口、风控原因和 Polymarket 链接。
                    </div>
                  </details>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <SignalsTable
                  signals={[]}
                  weatherSignals={signals}
                  onSimulateTrade={() => undefined}
                  isSimulating={signalStatusMutation.isPending}
                  onSignalStatus={(signalId, status, amount) => signalStatusMutation.mutate({ signalId, status, amount })}
                  onLiveOrder={(signalId, amount) => liveOrderMutation.mutate({ signalId, amount })}
                  liveModeAvailable={liveAvailable}
                  tradeMode={tradeMode}
                />
              </div>
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="border-b border-neutral-800 px-3 py-2">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-xs text-neutral-200">模拟 / 交易</div>
                    <div className="text-[10px] tabular-nums text-neutral-500">{stats.open_trades ?? 0} 持仓 / {stats.settled_trades ?? 0} 结算</div>
                  </div>
                  <details className="text-right text-[10px] text-neutral-500">
                    <summary className="cursor-pointer select-none hover:text-neutral-300">口径</summary>
                    <div className="mt-1 max-w-[240px] text-left leading-relaxed">
                      未结算持仓按当前 bid 估值，会包含买卖价差造成的即时浮亏。
                    </div>
                  </details>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <TradesTable trades={trades} />
              </div>
            </div>
          )}

          <details className="shrink-0 border-t border-neutral-800 bg-black">
            <summary className="cursor-pointer select-none px-3 py-2 text-xs text-neutral-300 hover:bg-neutral-950">
              高级诊断与风控
            </summary>
            <div className="max-h-[48vh] space-y-3 overflow-y-auto border-t border-neutral-800 p-3">
              <div className="grid grid-cols-2 gap-2 text-[11px]">
                <StatusTile label="扫描器" value={stats.is_running ? '运行中' : '已停止'} active={stats.is_running} icon={<Activity className="h-3.5 w-3.5" />} />
                <StatusTile label="数据年龄" value={dataAge(stats.data_age_minutes)} />
                <StatusTile label="当前信号" value={`${actionable} / ${signals.length}`} tone={actionable > 0 ? 'green' : 'neutral'} />
                <StatusTile label="实盘状态" value={liveAvailable ? '可用' : '锁定'} tone={liveAvailable ? 'green' : 'amber'} />
              </div>

              <ProductionValidationPanel
                report={productionValidation}
                loading={productionValidationQuery.isLoading}
                runningActionKey={String(productionActionMutation.variables?.action.key ?? '') || null}
                actionResult={productionActionResult}
                onDryRunAction={runValidationActionDryRun}
                onExecuteAction={runValidationActionExecute}
              />

              <ReadinessBanner stats={stats} readiness={dataReadiness} />

              <details className="border border-neutral-800 bg-black">
                <summary className="cursor-pointer select-none px-3 py-2 text-xs text-neutral-400 hover:bg-neutral-950 hover:text-neutral-200">
                  数据基座诊断
                </summary>
                <div className="space-y-3 border-t border-neutral-800 p-3">
                  <DataReadinessPanel
                    readiness={dataReadiness}
                    contracts={contractsQuery.data}
                    contractStatus={contractStatus}
                    onContractStatus={setContractStatus}
                    verifyingContractId={verifyContractMutation.variables?.contractId}
                    bulkVerifying={bulkVerifyContractMutation.isPending}
                    productionRefresh={productionRefresh}
                    productionRefreshing={productionRefreshRunning}
                    onProductionRefresh={() => productionRefreshMutation.mutate({
                      cities: selectedCity ? [selectedCity] : [],
                      days: refreshDaysForDate(selectedDate),
                      limit: 20,
                      startDate: selectedDate || '',
                      endDate: selectedDate || '',
                    })}
                    onVerifyContract={(contractId, note) => verifyContractMutation.mutate({ contractId, note })}
                    onVerifyVisibleContracts={(contractIds, note) => bulkVerifyContractMutation.mutate({ contractIds, note })}
                  />

                  <ModelDatasetPanel audit={modelDatasetAudit} archiveManifest={forecastArchiveManifest} />

                  <div className="border border-neutral-800 bg-black p-3">
                    <div className="mb-2 text-sm text-neutral-100">结算源健康</div>
                    <TruthHealthPanel truth={truthHealth} />
                  </div>
                </div>
              </details>
            </div>
          </details>
        </aside>
      </main>
    </div>
  )
}

function StatusTile({
  label,
  value,
  active = false,
  tone = 'neutral',
  icon,
}: {
  label: string
  value: string
  active?: boolean
  tone?: 'neutral' | 'green' | 'amber' | 'cyan'
  icon?: ReactNode
}) {
  const valueClass = tone === 'green' || active
    ? 'text-green-300'
    : tone === 'amber'
      ? 'text-amber-300'
      : tone === 'cyan'
        ? 'text-cyan-300'
        : 'text-neutral-200'
  return (
    <div className={`border p-2 ${active ? 'border-green-500/30 bg-green-500/10' : 'border-neutral-800'}`}>
      <div className="mb-1 flex items-center gap-1 text-neutral-500">
        {icon}
        {label}
      </div>
      <div className={valueClass}>{value}</div>
    </div>
  )
}

export default App
