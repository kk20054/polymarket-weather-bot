from tests import ensure_test_environment

ensure_test_environment()

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ExecutionWorkbenchContractTests(unittest.TestCase):
    def test_workbench_uses_layer8_decisions_and_paper_orders(self):
        app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        component = (ROOT / "frontend" / "src" / "components" / "ExecutionWorkbench.tsx").read_text(encoding="utf-8")
        api = (ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")

        self.assertIn("<ExecutionWorkbench", app)
        self.assertIn("fetchPaperOrders", component)
        self.assertIn("executePaperOrders", component)
        self.assertIn("fetchStrategyProfiles", component)
        self.assertIn("EquityChart", component)
        self.assertIn("strategy_revision_id", component)
        self.assertIn("signal_decisions", (ROOT / "weatherbot_v3" / "paper.py").read_text(encoding="utf-8"))
        self.assertIn("'/paper-orders'", api)
        self.assertIn("'/paper-orders/execute'", api)
        self.assertNotIn("placeLiveOrder", component)

    def test_workbench_keeps_live_locked_and_groups_latest_decision_batch(self):
        component = (ROOT / "frontend" / "src" / "components" / "ExecutionWorkbench.tsx").read_text(encoding="utf-8")

        self.assertIn("实盘锁定", component)
        self.assertIn("latestDecisionIssuedAt", component)
        self.assertIn("decisionBatchIssuedAt", component)
        self.assertIn("cohortRunId: validation?.run_id", component)
        self.assertIn("fetchPaperOrders('', '', 100, activeCohortRunId)", component)
        self.assertIn("cohort_run_id: cohortRunId", (ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8"))
        self.assertIn('data-testid="paper-order-row"', component)
        self.assertIn("order.city_key", component)
        self.assertIn("order.target_date", component)
        self.assertIn("order.bucket_label", component)
        self.assertIn("order.pnl_value", component)
        self.assertIn("order.mark_age_seconds", component)
        self.assertIn("hold to official Polymarket settlement", component)
        self.assertIn("no intraday forced stop", component)
        self.assertIn("accountActive={validationActive}", component)
        self.assertIn("runId: started.run_id", component)
        self.assertIn("strategyRevisionId", component)
        self.assertIn("ladderGroupId", component)
        self.assertIn("执行当前策略", component)
        self.assertIn("启动策略", component)
        self.assertIn("startPaperValidation", component)
        self.assertIn("暂无订单", component)
        self.assertNotIn("模拟金额", component)

    def test_paper_exit_mode_is_revision_bound_and_explained_in_order_details(self):
        component = (ROOT / "frontend" / "src" / "components" / "ExecutionWorkbench.tsx").read_text(encoding="utf-8")
        profiles = (ROOT / "weatherbot_v3" / "strategy_profiles.py").read_text(encoding="utf-8")
        exit_engine = (ROOT / "weatherbot_v3" / "paper_exit.py").read_text(encoding="utf-8")

        self.assertIn("model_guarded", component)
        self.assertIn("createStrategyProfile", component)
        self.assertIn("activate_scopes: ['signal_generation', 'paper_default']", component)
        self.assertIn("disabled={validationActive}", component)
        self.assertIn("价格下跌本身不会触发", component)
        self.assertIn("实测最高温已越过温度桶", component)
        self.assertIn("模型概率连续失效", component)
        self.assertIn("模拟卖出价（买一）", component)
        self.assertIn("realized_exit", component)
        self.assertIn('"model_guarded_take_profit"', profiles)
        self.assertIn('type="radio"', component)
        self.assertIn("Entry strategy (single choice to avoid duplicate exposure)", component)
        self.assertIn("Mid-price gains never trigger an exit", component)
        self.assertIn("insufficient_best_bid_depth", exit_engine)
        self.assertNotIn("live_orders", exit_engine)

    def test_strategy_lab_is_separate_from_normal_workbench(self):
        main = (ROOT / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
        app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        developer = (ROOT / "frontend" / "src" / "pages" / "DeveloperPage.tsx").read_text(encoding="utf-8")
        drawer = (ROOT / "frontend" / "src" / "components" / "DeveloperSettingsDrawer.tsx").read_text(encoding="utf-8")
        component = (ROOT / "frontend" / "src" / "components" / "ExecutionWorkbench.tsx").read_text(encoding="utf-8")

        self.assertIn("/developer", main)
        self.assertIn("策略版本", component)
        self.assertIn("DeveloperSettingsPanel", developer)
        self.assertIn("DeveloperSettingsDrawer", app)
        self.assertIn("设置", component)
        self.assertIn("连接服务", drawer)
        self.assertIn("粘贴密钥或 Webhook 地址", drawer)
        self.assertIn("验证连接", drawer)
        self.assertIn("高级设置", drawer)
        self.assertIn("weather_com", drawer)
        self.assertIn("wunderground_pws", drawer)
        self.assertIn("visual_crossing", drawer)
        self.assertIn("minimax", drawer)
        self.assertIn("feishu", drawer)
        self.assertNotIn("更多可选服务", drawer)
        self.assertIn("实盘保持锁定", drawer)
        self.assertIn("创建新版本", drawer)
        self.assertIn("activate_scopes: []", drawer)
        self.assertIn('className="hidden"', app)


if __name__ == "__main__":
    unittest.main()
