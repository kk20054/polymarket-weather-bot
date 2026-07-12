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
        self.assertIn("strategy_revision_id", component)
        self.assertIn("signal_decisions", (ROOT / "weatherbot_v3" / "paper.py").read_text(encoding="utf-8"))
        self.assertIn("'/paper-orders'", api)
        self.assertIn("'/paper-orders/execute'", api)
        self.assertNotIn("placeLiveOrder", component)

    def test_workbench_keeps_live_locked_and_groups_latest_decision_batch(self):
        component = (ROOT / "frontend" / "src" / "components" / "ExecutionWorkbench.tsx").read_text(encoding="utf-8")

        self.assertIn("实盘锁定", component)
        self.assertIn("latestIssuedAt", component)
        self.assertIn("ladderGroupId", component)
        self.assertIn("模拟当前可用策略", component)
        self.assertIn("一键模拟", component)
        self.assertIn("startPaperValidation", component)
        self.assertIn("暂无模拟订单", component)

    def test_strategy_lab_is_separate_from_normal_workbench(self):
        main = (ROOT / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
        app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        developer = (ROOT / "frontend" / "src" / "pages" / "DeveloperPage.tsx").read_text(encoding="utf-8")
        component = (ROOT / "frontend" / "src" / "components" / "ExecutionWorkbench.tsx").read_text(encoding="utf-8")

        self.assertIn("/developer", main)
        self.assertIn("策略版本", component)
        self.assertIn("不可变参数版本", developer)
        self.assertIn("实盘保持锁定", developer)
        self.assertIn('className="hidden"', app)


if __name__ == "__main__":
    unittest.main()
