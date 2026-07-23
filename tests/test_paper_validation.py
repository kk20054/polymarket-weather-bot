from tests import ensure_test_environment

ensure_test_environment()

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbot_v3.db import (
    init_v3_db,
    insert_orderbook,
    list_paper_orders,
    paper_execution_summary,
    upsert_daily_max_prediction,
    upsert_signal_decision_record as _db_upsert_signal_decision_record,
)
from weatherbot_v3.forecast_time import FORECAST_COMPONENT_COHORT_VERSION
from weatherbot_v3.paper_validation import (
    paper_validation_status,
    run_paper_validation_tick,
    start_paper_validation_run,
    stop_paper_validation_run,
)
from weatherbot_v3.paper import execute_paper_decisions
from weatherbot_v3.strategy_profiles import DEFAULT_PARAMETERS, create_strategy_profile_revision
from weatherbot_v3.config import ASIAN_CITY_PRIORITY


ROOT = Path(__file__).resolve().parents[1]
TEST_DB_DIR = ROOT / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


def upsert_signal_decision_record(decision: dict, path: Path | None = None) -> int:
    issued_at = str(decision.get("issued_at") or datetime.now(timezone.utc).isoformat())
    prediction_id = upsert_daily_max_prediction({
        "city_key": decision.get("city_key") or "chicago",
        "target_date": decision.get("target_date") or issued_at[:10],
        "issued_at": issued_at,
        "mu": 82.0,
        "sigma": 1.5,
        "unit": "F",
        "method": "polywx_aligned_deb_v1",
        "forecast_algo": "polywx_aligned_deb_v1",
        "cohort_contract_version": FORECAST_COMPONENT_COHORT_VERSION,
        "cohort_as_of": issued_at,
        "components": [{
            "source": "fixture_forecast",
            "source_age_ok": True,
            "source_skew_ok": True,
        }],
    }, path=path)
    payload = {
        **decision,
        "forecast_algo": "polywx_aligned_deb_v1",
        "deb_version": "polywx_aligned_deb_v1",
        "evidence_links": {
            **(decision.get("evidence_links") or {}),
            "daily_max_prediction_id": prediction_id,
        },
    }
    return _db_upsert_signal_decision_record(payload, path=path)


class PaperValidationTests(unittest.TestCase):
    def test_seoul_is_available_to_paper_validation_but_not_live(self):
        self.assertEqual(ASIAN_CITY_PRIORITY["seoul"]["mode"], "paper_only")

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
        revision_id = started["run"]["strategy_revision_id"]
        now = datetime.now(timezone.utc)
        upsert_signal_decision_record(_decision("stale", now - timedelta(hours=2), "yes-stale", edge=0.9, revision_id=revision_id), path=path)
        upsert_signal_decision_record(_decision("fresh-low", now + timedelta(seconds=1), "yes-low", edge=0.08, revision_id=revision_id), path=path)
        upsert_signal_decision_record(_decision("fresh-high", now + timedelta(seconds=2), "yes-high", edge=0.12, revision_id=revision_id), path=path)

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
        self.assertEqual(len(list_paper_orders(cohort_run_id=run_id, path=path)), 1)
        self.assertEqual(list_paper_orders(cohort_run_id="other-run", path=path), [])
        cohort_summary = paper_execution_summary(cohort_run_id=run_id, path=path)
        self.assertEqual(cohort_summary["count"], 1)
        self.assertEqual(cohort_summary["cohort_run_id"], run_id)
        self.assertEqual(applied["metrics"]["open_positions"], 1)
        self.assertLessEqual(applied["metrics"]["spent_today_usd"], 2.0)
        self.assertAlmostEqual(orders[0]["filled_amount"], 1.5, places=2)
        self.assertEqual(orders[0]["strategy_revision_id"], revision_id)
        self.assertEqual(orders[0]["sizing_snapshot"]["bankroll_usd"], 40.0)

    def test_execution_summary_marks_open_orders_to_latest_bid_and_builds_equity_curve(self):
        path = test_db_path("paper_validation_mark_to_market")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        started = start_paper_validation_run(
            bankroll_usd=40,
            max_per_trade_usd=2,
            cities=["chicago"],
            strategies=["single_bucket_ev"],
            path=path,
        )
        run = started["run"]
        issued_at = datetime.now(timezone.utc)
        upsert_signal_decision_record(
            _decision("marked-order", issued_at, "yes-marked", edge=0.2, revision_id=run["strategy_revision_id"]),
            path=path,
        )
        applied = run_paper_validation_tick(apply=True, run_id=run["run_id"], path=path)
        self.assertEqual(applied["executed"], 1)
        order = list_paper_orders(cohort_run_id=run["run_id"], path=path)[0]
        insert_orderbook(
            str(order["market_id"]),
            {
                "snapshot_key": "marked-order-latest",
                "yes_token_id": "yes-marked",
                "bids": [{"price": 0.25, "size": 100}],
                "asks": [{"price": 0.26, "size": 100}],
                "orderMinSize": 5,
                "orderPriceMinTickSize": 0.01,
                "quote_timestamp": (issued_at + timedelta(minutes=1)).isoformat(),
            },
            path=path,
        )

        summary = paper_execution_summary(cohort_run_id=run["run_id"], path=path)
        marked = summary["orders"][0]
        expected_pnl = (0.25 - float(marked["entry_price"])) * float(marked["filled_shares"])

        self.assertEqual(marked["pnl_kind"], "unrealized")
        self.assertAlmostEqual(float(marked["mark_price"]), 0.25)
        self.assertAlmostEqual(float(marked["pnl_value"]), expected_pnl, places=4)
        self.assertEqual(marked["exit_policy"], "hold_to_settlement")
        self.assertFalse(marked["force_exit_enabled"])
        self.assertAlmostEqual(float(summary["starting_bankroll"]), 40.0)
        self.assertAlmostEqual(float(summary["cash_available"]), 40.0 - float(marked["filled_amount"]), places=4)
        self.assertAlmostEqual(float(summary["position_value"]), 0.25 * float(marked["filled_shares"]), places=4)
        self.assertAlmostEqual(float(summary["equity"]), 40.0 + expected_pnl, places=4)
        self.assertGreaterEqual(len(summary["equity_curve"]), 3)
        self.assertAlmostEqual(float(summary["equity_curve"][-1]["pnl"]), expected_pnl, places=4)

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

    def test_validation_run_rejects_overlapping_strategy_combination(self):
        path = test_db_path("paper_validation_strategy_combination")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        blocked = start_paper_validation_run(
            bankroll_usd=40,
            max_per_trade_usd=2,
            cities=["chicago", "atlanta"],
            strategies=["single_bucket_ev", "ladder_grid", "tail_buying"],
            path=path,
        )

        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason"], "paper_strategy_requires_exactly_one")

        started = start_paper_validation_run(
            bankroll_usd=40,
            max_per_trade_usd=2,
            cities=["chicago", "atlanta"],
            strategies=["single_bucket_ev"],
            path=path,
        )
        self.assertTrue(started["ok"])
        self.assertEqual(started["run"]["bankroll_usd"], 40)
        self.assertEqual(started["run"]["max_per_trade_usd"], 2)
        self.assertEqual(started["run"]["strategies"], ["single_bucket_ev"])
        self.assertTrue(started["run"]["strategy_revision_id"].startswith("spr_"))

    def test_cohort_cap_is_not_silently_replaced_by_global_max_bet(self):
        path = test_db_path("paper_validation_no_double_cap")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        started = start_paper_validation_run(
            bankroll_usd=100,
            max_per_trade_usd=8,
            daily_max_usd=20,
            cities=["chicago"],
            strategies=["single_bucket_ev"],
            path=path,
        )
        revision_id = started["run"]["strategy_revision_id"]
        now = datetime.now(timezone.utc)
        decision = _decision("cohort-five", now, "yes-five", edge=0.4, revision_id=revision_id)
        decision.update({"model_probability": 0.8, "market_ask": 0.4, "market_bid": 0.39})
        decision["orderbook_snapshot"].update({"best_ask": 0.4, "best_bid": 0.39, "spread": 0.01})
        upsert_signal_decision_record(decision, path=path)

        result = run_paper_validation_tick(apply=True, path=path)
        order = list_paper_orders(path=path)[0]

        self.assertEqual(result["executed"], 1)
        self.assertAlmostEqual(order["filled_amount"], 5.0, places=2)
        self.assertEqual(order["sizing_snapshot"]["caps"]["cohort_max_per_trade_usd"], 8.0)
        self.assertEqual(order["sizing_snapshot"]["caps"]["bankroll_fraction_cap_usd"], 5.0)

    def test_ladder_reserves_three_order_and_position_slots(self):
        path = test_db_path("paper_validation_ladder_capacity")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        started = start_paper_validation_run(
            bankroll_usd=40,
            max_open_positions=2,
            max_orders_per_day=2,
            cities=["chicago"],
            strategies=["ladder_grid"],
            path=path,
        )
        revision_id = started["run"]["strategy_revision_id"]
        now = datetime.now(timezone.utc)
        for index in range(3):
            row = _decision(
                f"ladder-{index}",
                now + timedelta(seconds=index),
                f"yes-ladder-{index}",
                edge=0.2,
                strategy="ladder_grid",
                revision_id=revision_id,
            )
            row["ladder_group_id"] = "ladder-group"
            upsert_signal_decision_record(row, path=path)

        result = run_paper_validation_tick(apply=True, path=path)

        self.assertEqual(result["executed"], 0)
        self.assertEqual(list_paper_orders(path=path), [])

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

    def test_scoped_manual_and_batch_paths_share_one_cohort_ledger(self):
        path = test_db_path("paper_validation_scoped_paths")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        started = start_paper_validation_run(
            bankroll_usd=40,
            max_per_trade_usd=2,
            daily_max_usd=10,
            max_open_positions=5,
            max_orders_per_day=5,
            cities=["chicago"],
            strategies=["single_bucket_ev"],
            path=path,
        )
        run = started["run"]
        issued_at = datetime.now(timezone.utc)
        for key in ("manual-one", "manual-two"):
            upsert_signal_decision_record(
                _decision(key, issued_at, f"yes-{key}", edge=0.2, revision_id=run["strategy_revision_id"]),
                path=path,
            )

        single = run_paper_validation_tick(
            apply=True,
            run_id=run["run_id"],
            decision_id="manual-one",
            strategy_revision_id=run["strategy_revision_id"],
            decision_batch_issued_at=issued_at.isoformat(),
            path=path,
        )
        batch = run_paper_validation_tick(
            apply=True,
            run_id=run["run_id"],
            city_key="chicago",
            target_date=issued_at.date().isoformat(),
            strategies=["single_bucket_ev"],
            strategy_revision_id=run["strategy_revision_id"],
            decision_batch_issued_at=issued_at.isoformat(),
            path=path,
        )
        orders = list_paper_orders(path=path)

        self.assertEqual(single["executed"], 1)
        self.assertEqual(batch["executed"], 1)
        self.assertEqual({row["cohort_run_id"] for row in orders}, {run["run_id"]})
        self.assertLessEqual(sum(float(row["filled_amount"]) for row in orders), 10.0)
        self.assertLessEqual(max(float(row["filled_amount"]) for row in orders), 2.0)

    def test_tick_rejects_wrong_or_inactive_run_id(self):
        path = test_db_path("paper_validation_wrong_run")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        started = start_paper_validation_run(cities=["chicago"], path=path)
        result = run_paper_validation_tick(apply=True, run_id="paper-not-active", path=path)

        self.assertTrue(started["ok"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "paper_validation_run_not_active")
        self.assertEqual(list_paper_orders(path=path), [])

    def test_latest_quote_that_removes_edge_blocks_execution(self):
        path = test_db_path("paper_validation_reprice")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        started = start_paper_validation_run(
            cities=["chicago"],
            strategies=["single_bucket_ev"],
            path=path,
        )
        run = started["run"]
        now = datetime.now(timezone.utc)
        decision = _decision("repriced", now, "yes-repriced", edge=0.2, revision_id=run["strategy_revision_id"])
        upsert_signal_decision_record(decision, path=path)
        insert_orderbook(
            "market-repriced",
            {
                "snapshot_key": "repriced-latest",
                "yes_token_id": "yes-repriced",
                "bids": [{"price": 0.37, "size": 100}],
                "asks": [{"price": 0.38, "size": 100}],
                "orderMinSize": 5,
                "orderPriceMinTickSize": 0.01,
                "quote_timestamp": now.isoformat(),
            },
            path=path,
        )

        result = run_paper_validation_tick(
            apply=True,
            run_id=run["run_id"],
            decision_id="repriced",
            strategy_revision_id=run["strategy_revision_id"],
            decision_batch_issued_at=now.isoformat(),
            path=path,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "no_executable_candidates")
        self.assertEqual(result["reason"], "edge_below_min_after_reprice")
        self.assertEqual(list_paper_orders(path=path), [])

    def test_zero_kelly_multiplier_really_disables_cohort_sizing(self):
        path = test_db_path("paper_validation_zero_kelly")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        parameters = {**DEFAULT_PARAMETERS, "sizing": {**DEFAULT_PARAMETERS["sizing"], "kelly_multiplier": 0.0}}
        profile = create_strategy_profile_revision(parameters, profile_key="zero-kelly", path=path)
        started = start_paper_validation_run(
            cities=["chicago"],
            strategies=["single_bucket_ev"],
            strategy_revision_id=profile["revision_id"],
            path=path,
        )
        now = datetime.now(timezone.utc)
        upsert_signal_decision_record(
            _decision("zero-kelly", now, "yes-zero-kelly", edge=0.2, revision_id=profile["revision_id"]),
            path=path,
        )

        result = run_paper_validation_tick(apply=True, run_id=started["run"]["run_id"], path=path)

        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["executed"], 0)
        self.assertEqual(list_paper_orders(path=path), [])


def _decision(
    decision_id: str,
    issued_at: datetime,
    token: str,
    *,
    edge: float,
    strategy: str = "single_bucket_ev",
    revision_id: str = "",
) -> dict:
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
        "strategy_revision_id": revision_id,
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
            "quote_timestamp": issued_at.isoformat(),
        },
    }


if __name__ == "__main__":
    unittest.main()
