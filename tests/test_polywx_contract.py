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
        self.assertIn("docs/PROGRESS_ARCHIVE_CN.md", agents)
        self.assertIn("Read only `docs/CURRENT_STATE.md`", agents)
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

    def test_city_workbench_exposes_polywx_filters_and_tabs(self):
        panel = read_text("frontend/src/components/WeatherPanel.tsx")

        for tab_id in ("'forecast'", "'metar'", "'historical'", "'diff'", "'fetch'"):
            self.assertIn(tab_id, panel)
        for label in ("预报", "METAR", "历史观测", "偏差统计", "抓取日志"):
            self.assertIn(label, panel)

        self.assertIn("const CONTINENTS", panel)
        self.assertIn("value={continentFilter}", panel)
        self.assertIn("value={cityKey}", panel)
        self.assertIn('type="date"', panel)
        self.assertIn('aria-label="选择日期"', panel)

    def test_hourly_temperature_chart_matches_polywx_series_contract(self):
        panel = read_text("frontend/src/components/WeatherPanel.tsx")

        self.assertIn("function placeholderHourlyRow", panel)
        self.assertIn("const HOUR_LABELS", panel)
        self.assertIn("Array.from({ length: 24 }", panel)
        self.assertIn("<ComposedChart data={chartRows}", panel)
        self.assertIn('name="METAR" stroke="#F97316"', panel)
        self.assertIn("dot={{ r: 3, fill: '#F97316'", panel)
        self.assertIn('name="历史" stroke="#22C55E"', panel)
        self.assertIn("dot={{ r: 3, fill: '#22C55E'", panel)
        self.assertIn('name="中国实况" stroke="#EF4444"', panel)
        self.assertIn("SquareDot", panel)
        self.assertIn('name="PWS" stroke="#A855F7"', panel)
        self.assertIn("TriangleDot", panel)
        self.assertIn('name="预报" stroke="#3B82F6"', panel)
        self.assertIn("HollowCircleDot", panel)
        self.assertIn("activeDot={{ r: 5 }}", panel)
        self.assertIn('ticks={HOUR_LABELS}', panel)
        self.assertIn('<Area yAxisId="percent"', panel)
        self.assertIn('dataKey="cloud_pct" name="云量 %"', panel)
        self.assertIn('fill="#94A3B8" fillOpacity={0.25}', panel)
        self.assertIn("cloud_pct: asNumber(row.forecast_cloud_cover)", panel)
        self.assertNotIn("cloud_pct: asNumber(row.cloud_cover ?? row.humidity)", panel)
        self.assertNotIn("cloud_cover: row.humidity_mean", panel)
        self.assertIn('strokeDasharray="4 4"', panel)
        self.assertIn("ReferenceLine", panel)
        self.assertIn('stroke="#EC4899"', panel)
        self.assertIn("PeakReferenceLabel", panel)
        self.assertIn("`peak ${peakHour}`", panel)
        self.assertIn("forecast_value", panel)
        self.assertIn("value === null || value === undefined || value === ''", panel)
        self.assertIn("hasChartEvidence", panel)
        self.assertIn("tickFormatter={value => `${Number(value).toFixed(0)}°${unit}`}", panel)
        self.assertIn("AVG Δ (OBS−FC)", panel)
        self.assertIn("ACCURACY (PEARSON R)", panel)
        self.assertIn("HIST↔METAR OVERLAP", panel)
        self.assertIn("No diff stats yet", panel)
        self.assertIn("No accuracy stats yet", panel)
        self.assertIn("No overlap data yet", panel)
        self.assertNotIn('aria-label="Diff residual bars"', panel)
        self.assertNotIn('name="Cloud / RH" fill="#2563EB"', panel)
        self.assertIn("No hourly rows for this date.", panel)
        self.assertNotIn("点击“自动抓取”后，这里会按抓取时间展示", panel)

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
        self.assertIn("PWS peak-lock", panel)
        self.assertIn("Probability buckets (Gaussian)", panel)
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
        self.assertIn("topBucketIndexes", panel)
        self.assertIn("'#2563EB' : '#4B5563'", panel)
        self.assertIn("domain={[0, 25]}", panel)
        self.assertIn("ticks={[0, 5, 10, 15, 20, 25]}", panel)
        self.assertIn("h-[260px] max-h-[300px]", panel)
        self.assertIn("暂无匹配市场桶", panel)
        self.assertNotIn("逐小时气温 + DEB + 分桶", panel)
        self.assertIn("推荐关注", app)
        self.assertIn("recommendations?.items", app)
        self.assertIn("RecommendationCard", app)
        self.assertIn("暂无推荐", app)
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
        self.assertIn("const MAINLAND_CITY_KEYS", app)
        self.assertIn("const ASIA_OTHER_CITY_KEYS", app)
        self.assertIn("ROUND5_STATUS_FALLBACK", app)
        self.assertIn("resolveCityTradingStatus", app)
        self.assertIn("city.group.mainland", app)
        self.assertIn("city.group.asia", app)
        self.assertIn("city.group.us", app)
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

        self.assertIn("const gridColsMap", panel)
        for css_class in ("grid-cols-6", "grid-cols-7", "grid-cols-8", "grid-cols-9", "grid-cols-10", "grid-cols-11", "grid-cols-12"):
            self.assertIn(css_class, panel)
        self.assertIn("bucketGridClass", panel)
        self.assertIn("marketMid", panel)
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
            self.assertIn(label, app)
        self.assertNotIn('label="China Live"', app)
        self.assertNotIn("次失败", app)
        self.assertIn("china_live_poller", app)
        self.assertIn("extraTitle", app)
        self.assertIn("SchedulerBadge", app)
        self.assertIn("fails_last_hour", app)
        self.assertIn("schedulerStatusQuery", app)
        self.assertIn("schedulerStartMutation", app)
        self.assertIn("schedulerStopMutation", app)
        self.assertIn("stationEnabledMutation", app)

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
        self.assertIn("Settlement station", app)
        self.assertIn("Rule verified", app)
        self.assertIn("Timezone", app)
        self.assertIn("Truth source", app)
        self.assertIn("Non-truth metar_reports/IEM display only", app)

    def test_china_weather_live_contract_is_mesonet_display_only(self):
        app = read_text("frontend/src/App.tsx")
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
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
        self.assertIn("network\": CHINA_LIVE_NETWORK", collector)
        self.assertIn("not_settlement_truth", collector)
        self.assertIn("mesonet_observations", db)
        self.assertIn("raw_response_hash", db)
        self.assertIn("fetched_at", db)
        self.assertIn("china_live_poller", scheduler)
        self.assertIn("run_china_weather_fetch", scheduler)
        self.assertIn("source_temperatures", hourly)
        self.assertIn("china_live", types)
        self.assertIn("中国实况", panel)
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
        self.assertIn("Historical hourly", panel)
        self.assertIn("display-only research", panel)
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
        self.assertIn("marketBuckets={marketBucketsQuery.data ?? null}", app)
        self.assertIn("signalDecisions={signalDecisionsQuery.data ?? null}", app)
        self.assertIn("dailyMaxPrediction={dailyMaxPredictionQuery.data ?? null}", app)
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
        self.assertIn("selectedDateEvidence?: CityEvidenceDate", panel)
        self.assertIn("evidenceSummary={selectedDateEvidence?.modules?.diff_stats?.summary}", panel)
        self.assertNotIn("evidenceSummary={selectedDateEvidence?.modules?.probability_buckets?.probability_summary}", panel)
        self.assertIn("marketBuckets?: MarketBucketSummary | null", panel)
        self.assertIn("signalDecisions?: SignalDecisionSummary | null", panel)
        self.assertIn("dailyMaxPrediction?: DailyMaxPredictionSummary | null", panel)
        self.assertIn("buildLayerDistributionItems(marketBuckets, signalDecisions)", panel)
        self.assertNotIn("marketSummary={layerMarketSummary ?? selectedDateEvidence?.modules?.market_buckets?.market_summary}", panel)
        self.assertIn("evidenceSummary?: CityEvidenceDiffStatsSummary", panel)
        self.assertNotIn("evidenceSummary?: CityEvidenceProbabilitySummary", panel)
        self.assertNotIn("marketSummary?: CityEvidenceMarketBucketSummary", panel)
        self.assertNotIn("模块", app)


if __name__ == "__main__":
    unittest.main()
