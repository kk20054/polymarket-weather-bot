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
    record_paper_exit_evaluation,
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
        take_profit = validate_parameters({"exit_policy": {"mode": "model_guarded_take_profit"}})
        self.assertEqual(take_profit["exit_policy"]["take_profit_min_roi"], 0.05)
        self.assertEqual(take_profit["exit_policy"]["take_profit_min_ticks"], 1)
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
        prediction_id = _upsert_prediction(path, later, observed_high=31.0)
        _upsert_decision(
            path, later, revision, model_probability=0.04, decision_id="exit-second",
            prediction_id=prediction_id,
        )
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
        self.assertEqual(second["results"][0]["evaluation"]["source_prediction_id"], prediction_id)
        self.assertEqual(order["lifecycle_status"], "exited")

    def test_unrelated_prediction_and_repeated_source_do_not_confirm_model_exit(self):
        path = test_db_path("paper_exit_unrelated_prediction")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        _order_id, revision = _guarded_order(
            path, now=now, observed_high=31.0, model_probability=0.05,
            opened_at=now - timedelta(hours=1),
        )
        first = evaluate_open_paper_exits(apply=True, path=path, now=now)
        source_id = first["results"][0]["evaluation"]["source_prediction_id"]
        self.assertIsNotNone(source_id)
        self.assertEqual(first["results"][0]["evaluation"]["confirmation_count"], 1)

        later = now + timedelta(seconds=30)
        unrelated_id = _upsert_prediction(path, later, observed_high=31.0)
        self.assertNotEqual(unrelated_id, source_id)
        for new_decision in (False, True):
            with self.subTest(new_decision=new_decision):
                if new_decision:
                    _upsert_decision(
                        path, later, revision, model_probability=0.04,
                        decision_id="exit-repeat-source", prediction_id=source_id,
                    )
                result = evaluate_open_paper_exits(apply=True, path=path, now=later)
                evaluation = result["results"][0]["evaluation"]
                self.assertEqual(result["exited_now"], 0)
                self.assertEqual(evaluation["source_prediction_id"], source_id)
                self.assertEqual(evaluation["confirmation_count"], 1)
                self.assertIn("model_exit_waiting_for_confirmation", evaluation["reasons"])
        self.assertEqual(list_paper_orders(path=path)[0]["lifecycle_status"], "open")
        with connect(path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM fills WHERE order_type='paper_exit'").fetchone()[0], 0)

    def test_missing_or_invalid_prediction_link_cannot_confirm_model_exit(self):
        for link in (None, 999999):
            with self.subTest(prediction_id=link):
                path = test_db_path(f"paper_exit_invalid_prediction_link_{link}")
                self.addCleanup(lambda path=path: path.unlink(missing_ok=True))
                now = datetime.now(timezone.utc)
                _order_id, revision = _guarded_order(
                    path, now=now, observed_high=31.0, model_probability=0.05,
                    opened_at=now - timedelta(hours=1),
                )
                _upsert_decision(
                    path, now, revision, model_probability=0.05,
                    decision_id="exit-first", prediction_id=link,
                )
                for offset in (0, 30):
                    later = now + timedelta(seconds=offset)
                    _upsert_prediction(path, later, observed_high=31.0)
                    result = evaluate_open_paper_exits(apply=True, path=path, now=later)
                    evaluation = result["results"][0]["evaluation"]
                    self.assertEqual(result["exited_now"], 0)
                    self.assertIsNone(evaluation["source_prediction_id"])
                    self.assertEqual(evaluation["confirmation_count"], 0)
                    self.assertIn("model_exit_prediction_missing_or_invalid", evaluation["reasons"])
                self.assertEqual(list_paper_orders(path=path)[0]["lifecycle_status"], "open")

    def test_latest_observed_breach_still_exits_without_a_new_decision(self):
        path = test_db_path("paper_exit_latest_observed_breach")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        _guarded_order(path, now=now, observed_high=31.0, model_probability=0.35)
        later = now + timedelta(seconds=30)
        _upsert_prediction(path, later, observed_high=33.0)

        result = evaluate_open_paper_exits(apply=True, path=path, now=later)

        self.assertEqual(result["exited_now"], 1)
        self.assertEqual(result["results"][0]["evaluation"]["trigger"], "observed_bucket_breach")

    def test_model_confirmation_does_not_reuse_legacy_unbound_count(self):
        path = test_db_path("paper_exit_legacy_confirmation")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        order_id, _revision = _guarded_order(
            path, now=now, observed_high=31.0, model_probability=0.05,
            opened_at=now - timedelta(hours=1),
        )
        record_paper_exit_evaluation(
            {
                "paper_order_id": order_id,
                "source_decision_id": "exit-first",
                "source_prediction_id": 999999,
                "trigger": "model_probability_invalidated",
                "confirmation_count": 1,
                "version": "paper-exit-v2",
            },
            path=path,
        )

        result = evaluate_open_paper_exits(apply=True, path=path, now=now)

        self.assertEqual(result["exited_now"], 0)
        self.assertEqual(result["results"][0]["evaluation"]["confirmation_count"], 1)
        self.assertEqual(list_paper_orders(path=path)[0]["lifecycle_status"], "open")

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

    def test_take_profit_exits_at_fresh_executable_best_bid(self):
        path = test_db_path("paper_exit_take_profit")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        _guarded_order(
            path,
            now=now,
            observed_high=31.0,
            model_probability=0.35,
            opened_at=now - timedelta(minutes=30),
            mode="model_guarded_take_profit",
            best_bid=0.22,
        )

        result = evaluate_open_paper_exits(apply=True, path=path, now=now)
        order = list_paper_orders(path=path)[0]
        evaluation = result["results"][0]["evaluation"]

        self.assertEqual(result["exited_now"], 1)
        self.assertEqual(evaluation["trigger"], "take_profit_target")
        self.assertAlmostEqual(evaluation["executable_pnl"], 0.2)
        self.assertAlmostEqual(evaluation["executable_roi"], 0.1)
        self.assertEqual(order["lifecycle_status"], "exited")
        self.assertAlmostEqual(float(order["realized_pnl"]), 0.2)

    def test_latest_bidless_or_failed_snapshot_does_not_reuse_previous_bid(self):
        for book_state in ("side_absent", "fetch_failed"):
            with self.subTest(book_state=book_state):
                path = test_db_path(f"paper_exit_withdrawn_bid_{book_state}")
                self.addCleanup(lambda path=path: path.unlink(missing_ok=True))
                now = datetime.now(timezone.utc)
                _guarded_order(path, now=now, observed_high=33.0, model_probability=0.30)
                later = now + timedelta(seconds=30)
                insert_orderbook(
                    "market-exit",
                    {
                        "snapshot_key": "exit-book-bid-withdrawn",
                        "yes_token_id": "yes-exit",
                        "book_state": book_state,
                        "bids": [],
                        "asks": [{"price": 0.11, "size": 100}],
                        "quote_timestamp": later.isoformat(),
                    },
                    path=path,
                )

                result = evaluate_open_paper_exits(apply=True, path=path, now=later)
                evaluation = result["results"][0]["evaluation"]

                self.assertEqual(result["exited_now"], 0)
                self.assertEqual(evaluation["quote_timestamp"], later.isoformat())
                self.assertIsNone(evaluation["best_bid"])
                self.assertIn("sell_bid_missing", evaluation["reasons"])
                self.assertEqual(list_paper_orders(path=path)[0]["lifecycle_status"], "open")
                with connect(path) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM fills WHERE order_type='paper_exit'").fetchone()[0], 0)

    def test_exit_quote_requires_open_interval_bid_and_nonfuture_timestamp(self):
        cases = (
            ("zero_bid", 0.0, 0, "sell_bid_invalid"),
            ("negative_bid", -0.1, 0, "sell_bid_invalid"),
            ("unit_bid", 1.0, 0, "sell_bid_invalid"),
            ("above_unit_bid", 1.01, 0, "sell_bid_invalid"),
            ("future_quote", 0.1, 1, "sell_quote_future"),
            ("current_quote", 0.1, 0, None),
        )
        for name, bid, future_seconds, reason in cases:
            with self.subTest(case=name):
                path = test_db_path(f"paper_exit_quote_contract_{name}")
                self.addCleanup(lambda path=path: path.unlink(missing_ok=True))
                now = datetime.now(timezone.utc)
                _guarded_order(path, now=now, observed_high=33.0, model_probability=0.30)
                insert_orderbook(
                    "market-exit",
                    {
                        "snapshot_key": f"exit-quote-contract-{name}",
                        "yes_token_id": "yes-exit",
                        "bids": [{"price": bid, "size": 100}],
                        "asks": [{"price": 0.11, "size": 100}],
                        "quote_timestamp": (now + timedelta(seconds=future_seconds)).isoformat(),
                    },
                    path=path,
                )

                result = evaluate_open_paper_exits(apply=True, path=path, now=now)
                evaluation = result["results"][0]["evaluation"]

                self.assertEqual(result["exited_now"], int(reason is None))
                self.assertEqual(evaluation["quote_age_seconds"], -future_seconds)
                if reason:
                    self.assertIn(reason, evaluation["reasons"])
                self.assertEqual(
                    list_paper_orders(path=path)[0]["lifecycle_status"],
                    "open" if reason else "exited",
                )
                with connect(path) as conn:
                    count = conn.execute("SELECT COUNT(*) FROM fills WHERE order_type='paper_exit'").fetchone()[0]
                self.assertEqual(count, int(reason is None))

    def test_take_profit_does_not_use_non_executable_mid_price(self):
        path = test_db_path("paper_exit_take_profit_ignores_mid")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        _guarded_order(
            path,
            now=now,
            observed_high=31.0,
            model_probability=0.35,
            opened_at=now - timedelta(minutes=30),
            mode="model_guarded_take_profit",
            best_bid=0.20,
            best_ask=0.30,
        )

        result = evaluate_open_paper_exits(apply=True, path=path, now=now)
        evaluation = result["results"][0]["evaluation"]

        self.assertEqual(result["exited_now"], 0)
        self.assertEqual(evaluation["trigger"], "none")
        self.assertAlmostEqual(evaluation["executable_pnl"], 0.0)
        self.assertEqual(list_paper_orders(path=path)[0]["lifecycle_status"], "open")

    def test_take_profit_waits_for_hold_time_and_executable_depth(self):
        path = test_db_path("paper_exit_take_profit_guards")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        now = datetime.now(timezone.utc)
        _guarded_order(
            path,
            now=now,
            observed_high=31.0,
            model_probability=0.35,
            opened_at=now - timedelta(minutes=5),
            mode="model_guarded_take_profit",
            best_bid=0.22,
            best_bid_size=5,
        )

        result = evaluate_open_paper_exits(apply=True, path=path, now=now)
        evaluation = result["results"][0]["evaluation"]

        self.assertEqual(result["exited_now"], 0)
        self.assertEqual(evaluation["trigger"], "take_profit_target")
        self.assertIn("take_profit_minimum_hold_not_met", evaluation["reasons"])
        self.assertIn("insufficient_best_bid_depth", evaluation["reasons"])
        self.assertEqual(list_paper_orders(path=path)[0]["lifecycle_status"], "open")


def _guarded_order(
    path: Path,
    *,
    now: datetime,
    observed_high: float,
    model_probability: float,
    opened_at: datetime | None = None,
    mode: str = "model_guarded",
    best_bid: float = 0.10,
    best_ask: float = 0.11,
    best_bid_size: float = 100,
) -> tuple[int, str]:
    init_v3_db(path)
    parameters = deepcopy(DEFAULT_PARAMETERS)
    parameters["exit_policy"] = {
        **parameters["exit_policy"],
        "mode": mode,
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
            "tick_size": 0.01,
            "strict_match_status": "matched",
        },
        path=path,
    )
    prediction_id = _upsert_prediction(path, now, observed_high=observed_high)
    _upsert_decision(
        path, now, revision, model_probability=model_probability, decision_id="exit-first",
        prediction_id=prediction_id,
    )
    insert_orderbook(
        "market-exit",
        {
            "snapshot_key": "exit-book-first",
            "yes_token_id": "yes-exit",
            "bids": [{"price": best_bid, "size": best_bid_size}],
            "asks": [{"price": best_ask, "size": 100}],
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
    prediction_id: int | None,
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
            "evidence_links": {"daily_max_prediction_id": prediction_id} if prediction_id is not None else {},
        },
        path=path,
    )


if __name__ == "__main__":
    unittest.main()
