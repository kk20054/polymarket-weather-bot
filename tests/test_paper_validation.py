import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbot_v3.db import init_v3_db, list_paper_orders, upsert_signal_decision_record
from weatherbot_v3.paper_validation import (
    paper_validation_status,
    run_paper_validation_tick,
    start_paper_validation_run,
    stop_paper_validation_run,
)
from weatherbot_v3.paper import execute_paper_decisions


ROOT = Path(__file__).resolve().parents[1]
TEST_DB_DIR = ROOT / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class PaperValidationTests(unittest.TestCase):
    def test_cohort_only_executes_fresh_post_start_decisions_with_limits(self):
        path = test_db_path("paper_validation_freshness")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        started = start_paper_validation_run(
            duration_days=14,
            bankroll_usd=40,
            max_per_trade_usd=2,
            daily_max_usd=10,
            max_open_positions=1,
            max_orders_per_day=5,
            decision_max_age_minutes=30,
            cities=["chicago"],
            strategies=["single_bucket_ev"],
            path=path,
        )
        self.assertTrue(started["ok"])
        run_id = started["run"]["run_id"]
        now = datetime.now(timezone.utc)
        upsert_signal_decision_record(_decision("stale", now - timedelta(hours=2), "yes-stale", edge=0.9), path=path)
        upsert_signal_decision_record(_decision("fresh-low", now + timedelta(seconds=1), "yes-low", edge=0.08), path=path)
        upsert_signal_decision_record(_decision("fresh-high", now + timedelta(seconds=2), "yes-high", edge=0.12), path=path)

        dry = run_paper_validation_tick(apply=False, path=path)
        applied = run_paper_validation_tick(apply=True, path=path)
        repeated = run_paper_validation_tick(apply=True, path=path)
        orders = list_paper_orders(path=path)

        self.assertEqual(dry["candidate_count"], 2)
        self.assertEqual(dry["executed"], 1)
        self.assertEqual(applied["executed"], 1)
        self.assertEqual(repeated["status"], "capacity_reached")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["decision_id"], "fresh-high")
        self.assertEqual(orders[0]["cohort_run_id"], run_id)
        self.assertEqual(applied["metrics"]["open_positions"], 1)
        self.assertLessEqual(applied["metrics"]["spent_today_usd"], 2.0)

    def test_start_stop_are_explicit_and_single_active_run(self):
        path = test_db_path("paper_validation_lifecycle")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        first = start_paper_validation_run(cities=["chicago"], path=path)
        second = start_paper_validation_run(cities=["atlanta"], path=path)
        stopped = stop_paper_validation_run(path=path)
        inactive = paper_validation_status(path=path)

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "paper_validation_run_already_active")
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(inactive["status"], "inactive")

    def test_validation_run_persists_operator_strategy_combination_and_bankroll(self):
        path = test_db_path("paper_validation_strategy_combination")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        started = start_paper_validation_run(
            bankroll_usd=40,
            max_per_trade_usd=2,
            cities=["chicago", "atlanta"],
            strategies=["single_bucket_ev", "ladder_grid", "tail_buying"],
            path=path,
        )

        self.assertTrue(started["ok"])
        self.assertEqual(started["run"]["bankroll_usd"], 40)
        self.assertEqual(started["run"]["max_per_trade_usd"], 2)
        self.assertEqual(
            started["run"]["strategies"],
            ["single_bucket_ev", "ladder_grid", "tail_buying"],
        )

    def test_manual_batch_execution_respects_selected_strategies(self):
        path = test_db_path("paper_execution_strategy_filter")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        now = datetime.now(timezone.utc)
        upsert_signal_decision_record(_decision("single", now, "yes-single", edge=0.12), path=path)
        upsert_signal_decision_record(
            _decision("tail", now + timedelta(seconds=1), "yes-tail", edge=0.2, strategy="tail_buying"),
            path=path,
        )

        result = execute_paper_decisions(
            city_key="chicago",
            target_date=now.date().isoformat(),
            strategies=["tail_buying"],
            dry_run=True,
            path=path,
        )

        self.assertEqual(result["requested"], 1)
        self.assertEqual(result["results"][0]["decision_id"], "tail")


def _decision(decision_id: str, issued_at: datetime, token: str, *, edge: float, strategy: str = "single_bucket_ev") -> dict:
    return {
        "decision_id": decision_id,
        "bucket_key": f"bucket-{decision_id}",
        "city_key": "chicago",
        "target_date": issued_at.date().isoformat(),
        "issued_at": issued_at.isoformat(),
        "market_id": f"market-{decision_id}",
        "yes_token_id": token,
        "token_id": token,
        "bucket_lower": 80,
        "bucket_upper": 82,
        "model_probability": 0.4,
        "market_ask": 0.2,
        "market_bid": 0.19,
        "market_implied_probability": 0.2,
        "edge": edge,
        "strategy_name": strategy,
        "kelly_fraction": 0.1,
        "position_size_usd": 2.0,
        "tick_size": 0.01,
        "order_min_size": 5.0,
        "book_age_seconds": 0,
        "spread_bps": 500,
        "paper_allowed": True,
        "paper_decision": "buy",
        "live_allowed": False,
        "live_decision": "blocked",
        "gate_status": "paper_allowed",
        "gate_reasons": ["live_trading_disabled"],
        "orderbook_snapshot": {
            "best_ask": 0.2,
            "best_bid": 0.19,
            "spread": 0.01,
            "ask_depth": 100,
        },
    }


if __name__ == "__main__":
    unittest.main()
