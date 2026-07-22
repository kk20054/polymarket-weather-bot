from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests import ensure_test_environment

ensure_test_environment()

from weatherbot_v3.db import (
    connect,
    init_v3_db,
    insert_orderbook,
    list_paper_orders,
    paper_execution_summary,
    upsert_daily_max_prediction,
    upsert_market_bucket,
    upsert_paper_order_record,
    upsert_signal_decision_record,
)
from weatherbot_v3.paper_exit import evaluate_open_paper_exits
from weatherbot_v3.strategy_profiles import (
    DEFAULT_PARAMETERS,
    create_strategy_profile_revision,
    profile_snapshot,
    validate_parameters,
)


ROOT = Path(__file__).resolve().parents[1]
TEST_DB_DIR = ROOT / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class PaperExitTests(unittest.TestCase):
    def test_strategy_profile_accepts_guarded_exit_and_rejects_unknown_mode(self):
        guarded = validate_parameters({"exit_policy": {"mode": "model_guarded"}})
        self.assertEqual(guarded["exit_policy"]["mode"], "model_guarded")
        self.assertEqual(guarded["exit_policy"]["confirmations_required"], 2)
        with self.assertRaisesRegex(ValueError, "unsupported_exit_policy"):
            validate_parameters({"exit_policy": {"mode": "price_stop"}})

    def test_observed_high_breach_exits_immediately_at_fresh_best_bid(self):
        path = test_db_path("paper_exit_observed_breach")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        order_id, _revision = _guarded_order(path, now=now, observed_high=33.0, model_probability=0.30)

        result = evaluate_open_paper_exits(apply=True, path=path, now=now)
        order = list_paper_orders(path=path)[0]
        summary = paper_execution_summary(path=path)
        with connect(path) as conn:
            exit_fills = conn.execute("SELECT * FROM fills WHERE order_type='paper_exit'").fetchall()
            evaluations = conn.execute("SELECT * FROM paper_exit_evaluations").fetchall()

        self.assertTrue(result["ok"])
        self.assertEqual(result["exited_now"], 1)
        self.assertEqual(order["id"], order_id)
        self.assertEqual(order["lifecycle_status"], "exited")
        self.assertAlmostEqual(float(order["mark_price"]), 0.10)
        self.assertAlmostEqual(float(order["realized_pnl"]), -1.0)
        self.assertEqual(len(exit_fills), 1)
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(summary["exited_orders"], 1)
        self.assertEqual(summary["open_orders"], 0)
        self.assertAlmostEqual(float(summary["realized_pnl"]), -1.0)

    def test_model_probability_exit_requires_two_distinct_prediction_confirmations(self):
        path = test_db_path("paper_exit_model_confirmation")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        _order_id, revision = _guarded_order(
            path,
            now=now,
            observed_high=31.0,
            model_probability=0.05,
            opened_at=now - timedelta(hours=1),
        )

        first = evaluate_open_paper_exits(apply=True, path=path, now=now)
        self.assertEqual(first["exited_now"], 0)
        self.assertIn(
            "model_exit_waiting_for_confirmation",
            first["results"][0]["evaluation"]["reasons"],
        )

        later = now + timedelta(minutes=15)
        _upsert_prediction(path, later, observed_high=31.0)
        _upsert_decision(path, later, revision, model_probability=0.04, decision_id="exit-second")
        insert_orderbook(
            "market-exit",
            {
                "snapshot_key": "exit-book-second",
                "yes_token_id": "yes-exit",
                "bids": [{"price": 0.10, "size": 100}],
                "asks": [{"price": 0.11, "size": 100}],
                "quote_timestamp": later.isoformat(),
            },
            path=path,
        )
        second = evaluate_open_paper_exits(apply=True, path=path, now=later)
        order = list_paper_orders(path=path)[0]

        self.assertEqual(second["exited_now"], 1)
        self.assertEqual(second["results"][0]["evaluation"]["confirmation_count"], 2)
        self.assertEqual(order["lifecycle_status"], "exited")

    def test_price_drop_alone_never_triggers_guarded_exit(self):
        path = test_db_path("paper_exit_no_price_stop")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        _guarded_order(path, now=now, observed_high=31.0, model_probability=0.35)

        result = evaluate_open_paper_exits(apply=True, path=path, now=now)
        order = list_paper_orders(path=path)[0]

        self.assertEqual(result["exited_now"], 0)
        self.assertEqual(order["lifecycle_status"], "open")
        self.assertEqual(result["results"][0]["evaluation"]["trigger"], "none")

    def test_impossible_bucket_does_not_exit_into_stale_or_shallow_book(self):
        path = test_db_path("paper_exit_bad_liquidity")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        _guarded_order(path, now=now - timedelta(minutes=10), observed_high=33.0, model_probability=0.01)
        insert_orderbook(
            "market-exit",
            {
                "snapshot_key": "exit-book-stale-shallow",
                "yes_token_id": "yes-exit",
                "bids": [{"price": 0.10, "size": 2}],
                "asks": [{"price": 0.11, "size": 100}],
                "quote_timestamp": (now - timedelta(minutes=10)).isoformat(),
            },
            path=path,
        )

        result = evaluate_open_paper_exits(apply=True, path=path, now=now)
        reasons = result["results"][0]["evaluation"]["reasons"]
        order = list_paper_orders(path=path)[0]

        self.assertEqual(result["exited_now"], 0)
        self.assertEqual(order["lifecycle_status"], "open")
        self.assertIn("sell_quote_stale", reasons)
        self.assertIn("insufficient_best_bid_depth", reasons)


def _guarded_order(
    path: Path,
    *,
    now: datetime,
    observed_high: float,
    model_probability: float,
    opened_at: datetime | None = None,
) -> tuple[int, str]:
    init_v3_db(path)
    parameters = deepcopy(DEFAULT_PARAMETERS)
    parameters["exit_policy"] = {
        **parameters["exit_policy"],
        "mode": "model_guarded",
    }
    profile = create_strategy_profile_revision(parameters, profile_key="guarded-exit", path=path)
    revision = profile["revision_id"]
    upsert_market_bucket(
        {
            "bucket_key": "bucket-exit",
            "market_id": "market-exit",
            "yes_token_id": "yes-exit",
            "city": "chicago",
            "target_date": now.date().isoformat(),
            "unit": "C",
            "bucket_label": "32C",
            "bucket_direction": "exact",
            "bucket_low": 32.0,
            "bucket_high": 32.0,
            "strict_match_status": "matched",
        },
        path=path,
    )
    _upsert_prediction(path, now, observed_high=observed_high)
    _upsert_decision(path, now, revision, model_probability=model_probability, decision_id="exit-first")
    insert_orderbook(
        "market-exit",
        {
            "snapshot_key": "exit-book-first",
            "yes_token_id": "yes-exit",
            "bids": [{"price": 0.10, "size": 100}],
            "asks": [{"price": 0.11, "size": 100}],
            "quote_timestamp": now.isoformat(),
        },
        path=path,
    )
    order_id = upsert_paper_order_record(
        {
            "decision_id": "entry-decision",
            "idempotency_key": "paper-exit-order",
            "market_id": "market-exit",
            "yes_token_id": "yes-exit",
            "bucket_key": "bucket-exit",
            "strategy_name": "single_bucket_ev",
            "strategy_revision_id": revision,
            "strategy_params_snapshot": profile_snapshot(profile),
            "sizing_snapshot": {"bankroll_usd": 40.0},
            "city_key": "chicago",
            "target_date": now.date().isoformat(),
            "side": "BUY",
            "limit_price": 0.20,
            "filled_amount": 2.0,
            "amount": 2.0,
            "filled_shares": 10.0,
            "shares": 10.0,
            "average_fill_price": 0.20,
            "status": "paper_filled",
            "lifecycle_status": "open",
            "fill_status": "filled",
            "model_probability": 0.40,
            "market_probability": 0.20,
            "opened_at": (opened_at or now - timedelta(minutes=5)).isoformat(),
            "cohort_run_id": "paper-exit-cohort",
        },
        path=path,
    )
    return order_id, revision


def _upsert_prediction(path: Path, issued_at: datetime, *, observed_high: float) -> int:
    return upsert_daily_max_prediction(
        {
            "city_key": "chicago",
            "target_date": issued_at.date().isoformat(),
            "issued_at": issued_at.isoformat(),
            "mu": 32.0,
            "sigma": 1.0,
            "unit": "C",
            "method": "paper-exit-fixture",
            "observed_floor": observed_high,
            "validity_status": "valid",
        },
        path=path,
    )


def _upsert_decision(
    path: Path,
    issued_at: datetime,
    revision: str,
    *,
    model_probability: float,
    decision_id: str,
) -> int:
    return upsert_signal_decision_record(
        {
            "decision_id": decision_id,
            "bucket_key": "bucket-exit",
            "city_key": "chicago",
            "target_date": issued_at.date().isoformat(),
            "issued_at": issued_at.isoformat(),
            "market_id": "market-exit",
            "yes_token_id": "yes-exit",
            "bucket_direction": "exact",
            "bucket_lower": 32.0,
            "bucket_upper": 32.0,
            "model_probability": model_probability,
            "market_bid": 0.10,
            "market_ask": 0.11,
            "strategy_name": "single_bucket_ev",
            "strategy_revision_id": revision,
            "paper_allowed": False,
        },
        path=path,
    )


if __name__ == "__main__":
    unittest.main()
