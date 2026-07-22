from tests import ensure_test_environment

ensure_test_environment()

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class PolyWXDashboardContractTests(unittest.TestCase):
    def test_agents_records_polywx_and_firecrawl_contracts(self):
        agents = read_text("AGENTS.md")
        detail = read_text("docs/AGENTS_DETAIL_CN.md")
        cli = read_text("weatherbot_v3/cli.py")

        self.assertIn("docs/CURRENT_STATE.md", agents)
        self.assertIn("docs/AGENTS_DETAIL_CN.md", agents)
        self.assertIn("Read only `docs/CURRENT_STATE.md`", agents)
        self.assertIn("Git history is the project ledger", agents)
        self.assertNotIn("PROJECT_PROGRESS_CN.md", agents)
        self.assertNotIn("PROGRESS_ARCHIVE_CN.md", agents)
        self.assertIn("state-print", cli)
        self.assertIn("print_current_state", cli)
        self.assertIn("PolyWX Workbench Theme Contract", detail)
        self.assertIn("Firecrawl-extracted PolyWX branding", detail)
        self.assertIn("#161A22", detail)
        self.assertIn("#222A37", detail)
        self.assertIn("City switching", detail)
        self.assertIn("Continent filtering", detail)
        self.assertIn("date switcher", detail)
        self.assertIn("firecrawl_map", detail)
        self.assertIn("schema-scoped `firecrawl_scrape`", detail)

    def test_agents_records_reference_fusion_architecture(self):
        agents = read_text("AGENTS.md")
        detail = read_text("docs/AGENTS_DETAIL_CN.md")

        self.assertIn("Reference Fusion Architecture", agents)
        self.assertIn("Reference Fusion Architecture", detail)
        for reference in (
            "punkpeye/awesome-mcp-servers",
            "python-metar/python-metar",
            "Polymarket/*",
            "yangyuan-zhen/PolyWeather",
        ):
            self.assertIn(reference, detail)

        for table in (
            "stations",
            "metar_reports",
            "mesonet_observations",
            "forecast_runs",
            "forecast_members",
            "hourly_consensus",
            "market_buckets",
            "signal_decisions",
        ):
            self.assertIn(table, detail)

        for contract in (
            "METAR/SPECI",
            "TAF",
            "DEB/hourly consensus",
            "BUY YES limit-only GTC",
            "strict market-bucket matching",
            "observed minus forecast",
        ):
            self.assertIn(contract, detail)

    def test_theme_toggle_preserves_polywx_light_and_dark_modes(self):
        app = read_text("frontend/src/App.tsx")
        css = read_text("frontend/src/index.css")

        self.assertIn("type ThemeMode = 'light' | 'dark'", app)
        self.assertIn("weatherbot-ui-theme", app)
        self.assertIn("polywx-light", app)
        self.assertIn("polywx-dark", app)
        self.assertIn("浅色", app)
        self.assertIn("深色", app)
        self.assertIn(".polywx-light", css)
        self.assertIn(".polywx-dark", css)
        self.assertIn("background: #161a22", css)
        self.assertIn("background: #ffffff", css)

    def test_city_workbench_exposes_single_navigation_and_tabs(self):
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
        app = read_text("frontend/src/App.tsx")

        for tab_id in ("'forecast'", "'metar'", "'historical'", "'diff'", "'fetch'"):
            self.assertIn(tab_id, panel)
        for label in ("预报", "METAR", "历史观测", "偏差统计", "抓取日志"):
            self.assertIn(label, panel)

        self.assertNotIn("const CONTINENTS", panel)
        self.assertNotIn("value={continentFilter}", panel)
        self.assertNotIn("value={cityKey}", panel)
        self.assertIn("filteredCityOptions.map", app)
        self.assertIn("搜索城市或机场", app)
        self.assertIn("city.displayEnabled", app)
        self.assertIn("resolveCityTradingStatus(", app)
        self.assertIn("cityScope === 'observation_only'", app)
        self.assertIn("待接入", app)
        self.assertIn("未采集", app)
        self.assertIn('type="date"', panel)
        self.assertIn("aria-label={tr(language, '选择日期', 'Select date')}", panel)
        self.assertIn("cityBrowseMode", app)
        self.assertIn("cityTimezoneGroup", app)
        self.assertIn("browseAlphabet", app)

    def test_hourly_temperature_chart_matches_polywx_series_contract(self):
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
        chart = read_text("frontend/src/components/HourlyTemperatureChart.tsx")
        rendered = panel + chart

        self.assertIn("function placeholderHourlyRow", panel)
        self.assertIn("const HOUR_TICKS", chart)
        self.assertIn("Array.from({ length: 24 }", chart)
        self.assertIn("<ComposedChart data={rows}", chart)
        self.assertIn('name="METAR" stroke="#F97316"', chart)
        self.assertIn("dot={{ r: 3, fill: '#F97316'", chart)
        self.assertIn("name={text('历史观测', 'Historical')} stroke=\"#22C55E\"", chart)
        self.assertIn("dot={{ r: 3, fill: '#22C55E'", chart)
        self.assertIn("name={text('中国实况', 'China live')} stroke=\"#EF4444\"", chart)
        self.assertIn("SquareDot", chart)
        self.assertIn('name="PWS" stroke="#A855F7"', chart)
        self.assertIn("TriangleDot", chart)
        self.assertIn("name={text('预报', 'Forecast')} stroke=\"#3B82F6\"", chart)
        self.assertIn("HollowCircleDot", chart)
        self.assertIn("activeDot={{ r: 5 }}", chart)
        self.assertIn('dataKey="time_minute"', chart)
        self.assertIn('ticks={HOUR_TICKS}', chart)
        self.assertIn('function HourlyTooltip', chart)
        self.assertIn('formatTooltipTime(dateLabel, label)', chart)
        self.assertIn('content={<HourlyTooltip dateLabel={dateLabel} unit={unit} />}', chart)
        self.assertIn('`${dateLabel} ${formatMinute(Number(value))}`', chart)
        self.assertIn('domain={temperatureDomain}', chart)
        self.assertIn('allowDataOverflow', chart)
        self.assertIn('<Area yAxisId="percent"', chart)
        self.assertIn("dataKey=\"cloud_pct\" name={text('云量 %', 'Cloud %')}", chart)
        self.assertIn('fill="#94A3B8" fillOpacity={0.25}', chart)
        self.assertIn("cloud_pct: asNumber(row.forecast_cloud_cover)", panel)
        self.assertNotIn("cloud_pct: asNumber(row.cloud_cover ?? row.humidity)", panel)
        self.assertNotIn("cloud_cover: row.humidity_mean", panel)
        self.assertIn('strokeDasharray="4 4"', chart)
        self.assertIn("ReferenceLine", chart)
        self.assertIn('stroke="#EC4899"', chart)
        self.assertIn("PeakLabel", chart)
        self.assertIn("`${text('峰值', 'peak')} ${peakHour}`", chart)
        self.assertIn("forecast_value", panel)
        self.assertIn("value === null || value === undefined || value === ''", panel)
        self.assertIn("hasChartEvidence", panel)
        self.assertIn("sourceStats(hourlyChartRows, 'metar_value')", panel)
        self.assertIn("sourceStats(hourlyChartRows, 'historical_value')", panel)
        self.assertIn("overlapPill(hourlyChartRows)", panel)
        self.assertNotIn("sourceStats(chartRows, 'historical_value')", panel)
        self.assertIn("historicalValues = numericValues(chartRows.map", panel)
        self.assertIn("tickFormatter={value => `${Number(value).toFixed(0)}°${unit}`}", chart)
        self.assertIn("AVG Δ (OBS−FC)", chart)
        self.assertIn("ACCURACY (PEARSON R)", chart)
        self.assertIn("HIST↔METAR OVERLAP", chart)
        self.assertIn("No diff stats yet", chart)
        self.assertIn("No accuracy stats yet", chart)
        self.assertIn("No overlap data yet", chart)
        self.assertNotIn('aria-label="Diff residual bars"', rendered)
        self.assertNotIn('name="Cloud / RH" fill="#2563EB"', rendered)
        self.assertIn("No hourly rows for this date.", panel)
        self.assertNotIn("series.historical_fallback ?? []) put(point, 'historical_value'", panel)
        self.assertNotIn("点击“自动抓取”后，这里会按抓取时间展示", panel)

    def test_forecast_revision_history_matches_polywx_audit_contract(self):
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
        dialog = read_text("frontend/src/components/ForecastRevisionDialog.tsx")
        api = read_text("frontend/src/api.ts")
        types = read_text("frontend/src/types.ts")
        server = read_text("dashboard_server.py")
        hourly = read_text("weatherbot_v3/hourly.py")

        self.assertIn('@app.get("/api/forecast-history")', server)
        self.assertIn("def forecast_revision_history", hourly)
        self.assertIn('"snapshot_count"', hourly)
        self.assertIn('"revision_count"', hourly)
        self.assertIn('"distinct_count"', hourly)
        self.assertIn("fetchForecastHistory", api)
        self.assertIn("ForecastRevisionHistory", types)
        self.assertIn("ForecastRevisionDialog", panel)
        self.assertIn("预报历史 —", dialog)
        self.assertIn("未变化行已隐藏", dialog)
        self.assertIn("aria-modal=\"true\"", dialog)
        self.assertIn("event.key === 'Escape'", dialog)
        self.assertIn("row.snapshot_count", panel)

    def test_layer7_visual_alignment_matches_polywx_priority(self):
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
        app = read_text("frontend/src/App.tsx")

        self.assertIn("Daily Max Prediction (DEB)", panel)
        self.assertIn("Per-source weights (DEB)", panel)
        self.assertIn("buildDebSourceRows", panel)
        self.assertIn("sourceShortLabel", panel)
        self.assertIn("weathercom", panel)
        self.assertIn("weight_after_mae", panel)
        self.assertIn("mae_7d", panel)
        self.assertIn("truth_basis", panel)
        self.assertIn("peakLockCandidate", panel)
        self.assertIn("sourceDialogOpen", panel)
        self.assertIn("SlidersHorizontal", panel)
        self.assertIn("模型分布（高斯） / 结算概率（实况约束）", panel)
        self.assertIn("fmtBucketTemp", panel)
        self.assertIn("fmtBucketAxisLabel", panel)
        self.assertIn("fmtBucketAxisTemp", panel)
        self.assertIn("or above", panel)
        self.assertIn("or below", panel)
        self.assertIn("–", panel)
        self.assertNotIn("WeatherBot 附加：盘口 / token / gate", panel)
        self.assertNotIn("DEB metadata", panel)
        self.assertIn("μ ± σ", panel)
        self.assertIn("fmtDualTemp", panel)
        self.assertIn("fmtDualDelta", panel)
        self.assertIn("normalCdf", panel)
        self.assertIn("buildGaussianFallbackItems", panel)
        self.assertIn("const bucketSize = unit === 'C' ? 0.5 : 1", panel)
        self.assertIn("const bucketCount = 18", panel)
        self.assertIn("const chartItems = gaussianItems.length > 0 ? gaussianItems : displayItems", panel)
        self.assertIn("localDateStringInTimeZone(series?.settlement_timezone)", panel)
        self.assertIn("latestDecisionBatch", panel)
        self.assertIn("quoteIsFresh", panel)
        self.assertIn("quote_valid", panel)
        self.assertNotIn("selectedDateSignals.length > 0 ? selectedDateSignals : citySignals", panel)
        self.assertNotIn("?? selectedCityEvidence?.dates[0]", app)
        self.assertIn("topBucketIndexes", panel)
        self.assertIn("'#2563EB' : '#4B5563'", panel)
        self.assertIn("domain={[0, chartYAxisMax]}", panel)
        self.assertIn("ticks={chartYAxisTicks}", panel)
        self.assertIn("h-[280px] max-h-[300px]", panel)
        self.assertIn("暂无匹配市场桶", panel)
        self.assertNotIn("逐小时气温 + DEB + 分桶", panel)
        self.assertIn("天气关注", app)
        self.assertNotIn("{copy.attentionOnly}", app)
        self.assertIn("recommendations?.focus_items", app)
        self.assertIn("天气关注，不代表交易建议", app)
        self.assertNotIn(">{focusReason}</span>", app)
        self.assertIn("RecommendationCard", app)
        self.assertIn("暂无天气关注", app)
        self.assertIn("weather_focus", read_text("docs/AGENTS_DETAIL_CN.md"))
        self.assertIn("trade_candidate", read_text("docs/AGENTS_DETAIL_CN.md"))
        self.assertIn("预计最高", app)
        self.assertIn("现在", app)
        self.assertIn("METAR age", app)
        self.assertIn("verified", app)
        self.assertIn("polymarket_url", app)
        self.assertNotIn("paper_gate_blocked", app)
        self.assertNotIn("recommendedCities", app)

    def test_round5_i18n_city_status_delta_and_alpha_contract(self):
        app = read_text("frontend/src/App.tsx")
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
        types = read_text("frontend/src/types.ts")
        api = read_text("frontend/src/api.ts")
        server = read_text("dashboard_server.py")
        delta_panel = read_text("frontend/src/components/DeltaAuditPanel.tsx")
        use_t = read_text("frontend/src/i18n/useT.ts")
        zh = read_text("frontend/src/i18n/zh-CN.json")
        en = read_text("frontend/src/i18n/en.json")

        self.assertIn("export function useT", use_t)
        self.assertIn("天气量化交易平台", zh)
        self.assertIn("Weather Quant Trading Platform", en)
        self.assertIn("ROUND5_STATUS_FALLBACK", app)
        self.assertIn("resolveCityTradingStatus", app)
        self.assertIn("此市场按 HKO 天文台每日摘要结算", zh)
        self.assertIn("外部案例 -$4,259", zh)
        self.assertNotIn("DeltaAuditPanel", app)
        self.assertNotIn("fetchTruthDeltaAudit", app)
        self.assertIn("fetchModelRepriceEvents", app)
        self.assertNotIn("activeMainView", app)
        self.assertIn("city_statuses", server)
        self.assertIn('@app.get("/api/truth-delta-audit")', server)
        self.assertIn('@app.get("/api/model-reprice-events")', server)
        self.assertIn("TruthDeltaAuditSummary", types)
        self.assertIn("ModelRepriceEventSummary", types)
        self.assertIn("fetchTruthDeltaAudit", api)
        self.assertIn("fetchModelRepriceEvents", api)
        self.assertIn("LineChart", delta_panel)
        self.assertIn("BarChart", delta_panel)
        self.assertIn("HKO Daily Extract", delta_panel)

        self.assertIn("min-w-max", panel)
        self.assertIn("w-[144px]", panel)
        self.assertIn("market-bucket-card", panel)
        self.assertNotIn("marketMid", panel)
        self.assertIn("fmtPrice(item.ask)", panel)
        self.assertIn("模型概率与 YES 盘口", panel)
        self.assertIn("概率优势", panel)
        self.assertIn("? edge === null ? '--' : fmtSignedPp(edge)", panel)
        self.assertIn("item.paper_decision === 'buy'", panel)
        self.assertIn("买入候选", panel)
        self.assertIn("毛 EV/份", panel)
        self.assertIn("deb-source-backdrop", panel)
        self.assertIn(".polywx-dark .deb-source-backdrop", read_text("frontend/src/index.css"))
        self.assertIn("全桶成本", panel)
        self.assertIn("最近全桶", panel)
        self.assertIn("盘口已过期", panel)
        self.assertIn("盘口过期", panel)
        self.assertIn("不计算差值", panel)
        self.assertNotIn("旧价差", panel)
        self.assertIn("打开 Polymarket", panel)
        self.assertIn("bid/ask", panel)
        self.assertIn("alphaEventTitle", panel)
        self.assertIn("ECMWF 06Z 更新后模型概率变化", panel)
        self.assertIn("⚡", panel)

    def test_refresh_feedback_toast_is_visible_and_stage_aware(self):
        app = read_text("frontend/src/App.tsx")

        self.assertIn("type RefreshNotice", app)
        self.assertIn("refreshNotices.map", app)
        self.assertIn("productionRefreshNotice", app)
        self.assertIn("数据自动更新成功", app)
        self.assertIn("天气数据已更新，交易数据异常", app)
        self.assertIn("数据抓取异常", app)
        self.assertIn("production-refresh-v2", app)
        self.assertIn('role="status"', app)
        self.assertIn('aria-live="polite"', app)
        self.assertIn("failed_stages", app)

    def test_scheduler_status_contract_is_visible_to_dashboard(self):
        app = read_text("frontend/src/App.tsx")
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
        api = read_text("frontend/src/api.ts")
        types = read_text("frontend/src/types.ts")
        server = read_text("dashboard_server.py")

        self.assertIn('@app.get("/api/scheduler/status")', server)
        self.assertIn('@app.post("/api/scheduler/start")', server)
        self.assertIn('@app.post("/api/scheduler/stop")', server)
        self.assertIn('"scheduler_status"', server)
        self.assertIn("fetchSchedulerStatus", api)
        self.assertIn("startScheduler", api)
        self.assertIn("stopScheduler", api)
        self.assertIn("setStationEnabled", api)
        self.assertIn("export interface SchedulerStatus", types)
        self.assertIn("export interface SchedulerPollerStatus", types)
        self.assertIn("scheduler_status?: SchedulerStatus", types)
        for label in ("预报", "METAR", "历史观测", "中国天气实况"):
            self.assertIn(label, panel)
        self.assertNotIn('label="China Live"', app)
        self.assertNotIn("次失败", app)
        self.assertIn("china_live_poller", app)
        self.assertNotIn("SchedulerBadge", app)
        self.assertIn("hourlySourceSeries?.china_live", panel)
        self.assertIn("EvidenceBadge label={tr(language, '中国天气实况', 'China live')}", panel)
        self.assertIn("schedulerStatusQuery", app)
        self.assertIn("schedulerStartMutation", app)
        self.assertIn("schedulerStopMutation", app)
        self.assertNotIn("stationEnabledMutation", app)
        self.assertIn('@app.get("/api/paper-validation/status")', server)
        self.assertIn('@app.post("/api/paper-validation/start")', server)
        self.assertIn('@app.post("/api/paper-validation/stop")', server)
        self.assertIn('@app.post("/api/paper-validation/tick")', server)
        self.assertIn("fetchPaperValidationStatus", api)
        self.assertIn("startPaperValidation", api)
        self.assertIn("stopPaperValidation", api)
        self.assertIn("一键模拟", read_text("frontend/src/components/ExecutionWorkbench.tsx"))
        self.assertIn("单桶最高温", read_text("frontend/src/components/ExecutionWorkbench.tsx"))
        self.assertIn("相邻三桶阶梯", read_text("frontend/src/components/ExecutionWorkbench.tsx"))
        self.assertIn("低价尾部", read_text("frontend/src/components/ExecutionWorkbench.tsx"))
        self.assertIn("打开 Polymarket", read_text("frontend/src/components/ExecutionWorkbench.tsx"))
        self.assertIn("stale_book", read_text("frontend/src/components/ExecutionWorkbench.tsx"))
        self.assertIn("盘口过期", read_text("frontend/src/components/ExecutionWorkbench.tsx"))
        self.assertIn("概率优势", read_text("frontend/src/components/ExecutionWorkbench.tsx"))
        self.assertIn("probabilityPoints", read_text("frontend/src/components/ExecutionWorkbench.tsx"))
        self.assertNotIn("旧价差", read_text("frontend/src/components/ExecutionWorkbench.tsx"))
        self.assertIn("PaperValidationCard", app)
        self.assertNotIn("SimulationCard", app)

    def test_polywx_workbench_hierarchy_uses_native_evidence_once(self):
        app = read_text("frontend/src/App.tsx")
        panel = read_text("frontend/src/components/WeatherPanel.tsx")

        self.assertIn("xl:grid-cols-[232px_minmax(0,1fr)_336px]", app)
        self.assertIn("cityBrowseChoices", app)
        self.assertIn("recommendedItems.slice(0, 4)", app)
        self.assertNotIn("等待自动抓取", app)
        self.assertNotIn("数据 暂无", app)
        self.assertNotIn("SchedulerBadge", app)
        self.assertNotIn("市场判断：", panel)
        self.assertIn("enabled: advancedDiagnosticsOpen", app)
        self.assertNotIn("hourlyEvidenceSettled", app)
        self.assertIn("layer7QueryState", app)
        self.assertIn("layer7ResourceState", app)
        self.assertIn("正在读取 DEB", panel)
        self.assertIn("DEB 读取失败", panel)
        self.assertIn("forecastPeakMarker={hourlyConsensusQuery.data?.forecast_peak_marker ?? null}", app)
        self.assertIn("normalizePeakHour(forecastPeakMarker?.local_time)", panel)
        self.assertIn("const forecastSourceTime", panel)
        self.assertIn("point.temperature ?? point.ensemble_mean ?? point.best", panel)
        self.assertIn("const historySourceTime", panel)
        self.assertNotIn("latest.time ? freshnessLabel(latest.time)", panel)
        self.assertIn("EvidenceBadge label={tr(language, '预报', 'Forecast')}", panel)
        self.assertIn('EvidenceBadge label="METAR"', panel)
        self.assertIn("EvidenceBadge label={tr(language, '历史观测', 'Historical')}", panel)
        self.assertIn("EvidenceBadge label={tr(language, '中国天气实况', 'China live')}", panel)

    def test_settlement_station_truth_contract_is_visible_to_dashboard(self):
        app = read_text("frontend/src/App.tsx")
        types = read_text("frontend/src/types.ts")
        server = read_text("dashboard_server.py")
        stations = read_text("weatherbot_v3/stations.py")
        probe = read_text("weatherbot_v3/polymarket_probe.py")
        cli = read_text("weatherbot_v3/cli.py")

        self.assertIn("polymarket-market-probe", cli)
        self.assertIn("probe_polymarket_markets", probe)
        self.assertIn("parse_settlement_rule_text", probe)
        for field in (
            "settlement_station_id",
            "settlement_rule_verified_at",
            "settlement_timezone",
            "settlement_unit",
            "primary_settlement_source",
            "verification_status",
        ):
            self.assertIn(field, server)
            self.assertIn(field, types)
        self.assertIn("apply_market_probe_result", stations)
        self.assertIn("市场规则", app)
        self.assertIn("结算站", app)
        self.assertIn("已核验", app)
        self.assertIn("truth", app)
        self.assertNotIn("Non-truth metar_reports/IEM display only", app)

    def test_china_weather_live_contract_is_mesonet_display_only(self):
        app = read_text("frontend/src/App.tsx")
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
        hourly_chart = read_text("frontend/src/components/HourlyTemperatureChart.tsx")
        types = read_text("frontend/src/types.ts")
        scheduler = read_text("weatherbot_v3/scheduler.py")
        collector = read_text("weatherbot_v3/china_weather.py")
        hourly = read_text("weatherbot_v3/hourly.py")
        db = read_text("weatherbot_v3/db.py")
        cli = read_text("weatherbot_v3/cli.py")

        self.assertIn("china-weather-fetch", cli)
        self.assertIn("history-backfill", cli)
        self.assertIn("HKO_RHRREAD_URL", collector)
        self.assertIn("WEATHERCN_SK2D_URL_TEMPLATE", collector)
        self.assertIn("network: str = CHINA_LIVE_NETWORK", collector)
        self.assertIn('WEATHERCOM_CURRENT_NETWORK = "weathercom_current"', collector)
        self.assertIn("network=WEATHERCOM_CURRENT_NETWORK", collector)
        self.assertIn("not_settlement_truth", collector)
        self.assertIn("mesonet_observations", db)
        self.assertIn("raw_response_hash", db)
        self.assertIn("fetched_at", db)
        self.assertIn("china_live_poller", scheduler)
        self.assertIn("fetch_china_weather_city", scheduler)
        self.assertIn("supported_china_live_cities", scheduler)
        self.assertIn("source_temperatures", hourly)
        self.assertIn("china_live", types)
        self.assertIn("中国实况", hourly_chart)
        self.assertIn("china_live_value", panel)
        self.assertIn("中国实况", app)

    def test_tables_and_fetch_log_match_polywx_information_architecture(self):
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
        app = read_text("frontend/src/App.tsx")
        types = read_text("frontend/src/types.ts")
        server = read_text("dashboard_server.py")
        css = read_text("frontend/src/index.css")
        benchmark = read_text("tools/polywx_benchmark_snapshot.py")
        scheduler_probe = read_text("tools/scheduler_longrun_probe.py")

        self.assertIn("Fetch Log (last 100)", panel)
        self.assertIn("历史观测", panel)
        self.assertIn("Wunderground", panel)
        self.assertIn("observationTableRowsFromSeries", panel)
        self.assertIn('dataKey="delta"', panel)
        self.assertIn('dataKey="cumulative_avg"', panel)
        self.assertIn("实测减预报偏差柱状图与累计均值线", panel)
        self.assertIn("fmtVisibility", panel)
        self.assertIn("Audit-only PolyWX benchmark", benchmark)
        self.assertIn("Do not import these values", benchmark)
        self.assertIn("Scheduler Longrun Probe", scheduler_probe)
        self.assertIn("# / Time / Source / Status / Duration / Message", panel)
        self.assertIn("fetchLog?: FetchLogRow[]", panel)
        self.assertIn("NormalizedFetchLogRow", panel)
        self.assertIn("sourceLabel", panel)
        self.assertIn("No log entries.", panel)
        self.assertIn("visibleElapsedLabel(row.duration) || '--'", panel)
        self.assertNotIn("<SignalCards", panel)
        self.assertNotIn("PolyWX-style city workbench", panel)
        self.assertIn("const fetchLog = data?.fetch_log ?? []", app)
        self.assertIn("fetchLog={fetchLog}", app)
        self.assertIn("export interface FetchLogRow", types)
        self.assertIn("fetch_log?: FetchLogRow[]", types)
        self.assertIn("def _fetch_log_payload", server)
        self.assertIn("def _data_fetch_log_payload", server)
        self.assertIn("def _combined_fetch_log_payload", server)
        self.assertIn("list_data_fetch_logs", server)
        self.assertIn("fetch_log = _combined_fetch_log_payload(events)", server)
        self.assertIn('"fetch_log": fetch_log', server)
        self.assertIn(".polywx-light th", css)
        self.assertIn(".polywx-dark th", css)
        self.assertIn("background: #f9fafb", css)
        self.assertIn("font-weight: 600", css)
        self.assertIn("padding: 0.5rem 1rem", css)
        self.assertIn("tbody tr:hover", css)

    def test_city_evidence_contract_is_exposed_for_polywx_generation(self):
        app = read_text("frontend/src/App.tsx")
        types = read_text("frontend/src/types.ts")
        server = read_text("dashboard_server.py")

        self.assertIn("def _build_city_evidence_payload", server)
        self.assertIn("def _city_date_evidence_modules", server)
        self.assertIn('"city_evidence": city_evidence', server)
        self.assertIn('@app.get("/api/city-evidence")', server)
        for module in (
            "hourly_temperature",
            "daily_max_prediction",
            "probability_buckets",
            "forecast",
            "metar",
            "historical",
            "diff_stats",
            "fetch_log",
            "market_buckets",
        ):
            self.assertIn(module, server)

        self.assertIn("export interface CityEvidence", types)
        self.assertIn("export interface CityEvidenceDate", types)
        self.assertIn("export interface CityEvidenceModule", types)
        self.assertIn("export interface CityEvidenceDiffStatsSummary", types)
        self.assertIn("export interface CityEvidenceProbabilitySummary", types)
        self.assertIn("export interface CityEvidenceMarketBucketSummary", types)
        self.assertIn("probability_summary?: CityEvidenceProbabilitySummary", types)
        self.assertIn("market_summary?: CityEvidenceMarketBucketSummary", types)
        self.assertIn("city_evidence?: CityEvidence[]", types)
        self.assertIn("const cityEvidence = data?.city_evidence ?? []", app)
        self.assertNotIn("证据 F", app)
        self.assertNotIn("ready_modules}", app)
        self.assertIn("selectedDateEvidence={selectedDateEvidence}", app)
        self.assertIn("fetchMarketBuckets", app)
        self.assertIn("fetchSignalDecisions", app)
        self.assertIn("fetchDailyMaxPredictions", app)
        self.assertIn("fetchBucketProbabilities", app)
        self.assertIn("marketBuckets={marketBucketsQuery.data ?? null}", app)
        self.assertIn("bucketProbabilities={bucketProbabilitiesQuery.data ?? null}", app)
        self.assertIn("signalDecisions={signalDecisionsQuery.data ?? null}", app)
        self.assertIn("dailyMaxPrediction={dailyMaxPredictionQuery.data ?? null}", app)
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
        self.assertIn("selectedDateEvidence?: CityEvidenceDate", panel)
        self.assertIn("evidenceSummary={selectedDateEvidence?.modules?.diff_stats?.summary}", panel)
        self.assertNotIn("evidenceSummary={selectedDateEvidence?.modules?.probability_buckets?.probability_summary}", panel)
        self.assertIn("marketBuckets?: MarketBucketSummary | null", panel)
        self.assertIn("signalDecisions?: SignalDecisionSummary | null", panel)
        self.assertIn("dailyMaxPrediction?: DailyMaxPredictionSummary | null", panel)
        self.assertIn("buildAuthoritativeDistributionItems(bucketProbabilities, marketBuckets, signalDecisions)", panel)
        self.assertIn("observed_floor_applied_to_distribution", panel)
        self.assertNotIn("marketSummary={layerMarketSummary ?? selectedDateEvidence?.modules?.market_buckets?.market_summary}", panel)
        self.assertIn("evidenceSummary?: CityEvidenceDiffStatsSummary", panel)
        self.assertNotIn("evidenceSummary?: CityEvidenceProbabilitySummary", panel)
        self.assertNotIn("marketSummary?: CityEvidenceMarketBucketSummary", panel)
        self.assertNotIn("模块", app)

    def test_hourly_legend_can_toggle_each_source_without_mutating_data(self):
        chart = read_text("frontend/src/components/HourlyTemperatureChart.tsx")
        self.assertIn("type SeriesKey", chart)
        self.assertIn("toggleSeries", chart)
        self.assertIn("aria-pressed={visibleSeries[key]}", chart)
        for key in ("china", "pws", "metar", "historical", "forecast", "cloud"):
            self.assertIn(f"visibleSeries.{key}", chart)
        self.assertIn("PWS（未授权/无数据）", chart)


if __name__ == "__main__":
    unittest.main()
