from tests import ensure_test_environment

ensure_test_environment()

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ExecutionWorkbenchContractTests(unittest.TestCase):
    def setUp(self):
        self.app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        self.component = (
            ROOT / "frontend" / "src" / "components" / "ExecutionWorkbench.tsx"
        ).read_text(encoding="utf-8")
        self.drawer = (
            ROOT / "frontend" / "src" / "components" / "DeveloperSettingsDrawer.tsx"
        ).read_text(encoding="utf-8")
        self.api = (ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")

    def test_workbench_uses_decisions_orders_and_equity(self):
        self.assertIn("<ExecutionWorkbench", self.app)
        self.assertIn("fetchPaperOrders", self.component)
        self.assertIn("executePaperOrders", self.component)
        self.assertIn("fetchStrategyProfiles", self.component)
        self.assertIn("EquityChart", self.component)
        self.assertIn("'/paper-orders'", self.api)
        self.assertIn("'/paper-orders/execute'", self.api)
        self.assertNotIn("placeLiveOrder", self.component)

    def test_visible_workbench_copy_is_execution_neutral(self):
        self.assertNotIn("实盘锁定", self.component)
        self.assertNotIn("模拟订单", self.component)
        self.assertNotIn("模拟卖出价", self.component)
        self.assertNotIn("策略版本", self.component)
        self.assertIn("执行买入", self.component)
        self.assertIn("退出价（买一）", self.component)
        self.assertIn("暂无订单", self.component)

    def test_one_strategy_revision_drives_all_execution_adapters(self):
        self.assertIn("active_scopes.includes('signal_generation')", self.component)
        self.assertIn(
            "activate_scopes: ['signal_generation', 'paper_default', 'live_default']",
            self.component,
        )
        self.assertIn(
            "activate_scopes: ['signal_generation', 'paper_default', 'live_default']",
            self.drawer,
        )
        self.assertIn("model_guarded_take_profit", self.component)
        self.assertIn("Entry strategy (single choice to avoid duplicate exposure)", self.component)

    def test_developer_settings_stays_focused(self):
        self.assertIn("连接服务", self.drawer)
        self.assertIn("策略设置", self.drawer)
        self.assertIn("weather_com", self.drawer)
        self.assertIn("wunderground_pws", self.drawer)
        self.assertIn("visual_crossing", self.drawer)
        self.assertNotIn("高级诊断", self.drawer)
        self.assertNotIn("实盘保持锁定", self.drawer)
        self.assertNotIn("创建新版本", self.drawer)


if __name__ == "__main__":
    unittest.main()
