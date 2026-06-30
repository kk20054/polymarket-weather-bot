import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class PolyWXDashboardContractTests(unittest.TestCase):
    def test_agents_records_polywx_and_firecrawl_contracts(self):
        agents = read_text("AGENTS.md")

        self.assertIn("PolyWX Workbench Theme Contract", agents)
        self.assertIn("Firecrawl-extracted PolyWX branding", agents)
        self.assertIn("#161A22", agents)
        self.assertIn("#222A37", agents)
        self.assertIn("City switching", agents)
        self.assertIn("Continent filtering", agents)
        self.assertIn("date switcher", agents)
        self.assertIn("firecrawl_map", agents)
        self.assertIn("schema-scoped `firecrawl_scrape`", agents)

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
        for label in ("预报", "METAR", "历史", "偏差统计", "抓取日志"):
            self.assertIn(label, panel)

        self.assertIn("const CONTINENTS", panel)
        self.assertIn("value={continentFilter}", panel)
        self.assertIn("value={cityKey}", panel)
        self.assertIn("dateOptions", panel)
        self.assertIn('aria-label="Select date"', panel)

    def test_hourly_temperature_chart_keeps_linechart_and_residual_contract(self):
        panel = read_text("frontend/src/components/WeatherPanel.tsx")

        self.assertIn("function placeholderHourlyRow", panel)
        self.assertIn("Array.from({ length: 24 }", panel)
        self.assertIn("<LineChart data={chartRows}", panel)
        self.assertIn('name="Real METAR" stroke="#000000"', panel)
        self.assertIn('name="Model Forecast" stroke="#6B7280"', panel)
        self.assertIn('strokeDasharray="4 4"', panel)
        self.assertIn('dataKey="gap_c" name="Diff"', panel)
        self.assertIn('aria-label="Diff residual bars"', panel)
        self.assertIn("bg-red-500", panel)
        self.assertIn("bg-blue-500", panel)

    def test_tables_and_fetch_log_match_polywx_information_architecture(self):
        panel = read_text("frontend/src/components/WeatherPanel.tsx")
        app = read_text("frontend/src/App.tsx")
        types = read_text("frontend/src/types.ts")
        server = read_text("dashboard_server.py")
        css = read_text("frontend/src/index.css")

        self.assertIn("Fetch Log (last 100)", panel)
        self.assertIn("# / Time / Source / Status / Duration / Message", panel)
        self.assertIn("fetchLog?: FetchLogRow[]", panel)
        self.assertIn("NormalizedFetchLogRow", panel)
        self.assertIn("sourceLabel", panel)
        self.assertIn("No log entries.", panel)
        self.assertIn("const fetchLog = data?.fetch_log ?? []", app)
        self.assertIn("fetchLog={fetchLog}", app)
        self.assertIn("export interface FetchLogRow", types)
        self.assertIn("fetch_log?: FetchLogRow[]", types)
        self.assertIn("def _fetch_log_payload", server)
        self.assertIn('"fetch_log": _fetch_log_payload(events)', server)
        self.assertIn(".polywx-light th", css)
        self.assertIn(".polywx-dark th", css)
        self.assertIn("background: #f9fafb", css)
        self.assertIn("font-weight: 600", css)
        self.assertIn("padding: 0.5rem 1rem", css)
        self.assertIn("tbody tr:hover", css)


if __name__ == "__main__":
    unittest.main()
