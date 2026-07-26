from __future__ import annotations

from tests import ensure_test_environment

ensure_test_environment()

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbot_v3.db import connect, init_v3_db
from weatherbot_v3.forward_validation import forward_validation_summary, required_sample_size


class ForwardValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "weatherbot.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_preregistered_three_point_clv_effect_requires_73_samples(self):
        self.assertEqual(required_sample_size(0.1029, 0.03), 73)

    def test_summary_deduplicates_candidate_and_uses_only_preclose_quote(self):
        path = self.db_path
        init_v3_db(path)
        start = datetime(2026, 7, 26, tzinfo=timezone.utc)
        close = start + timedelta(days=1)
        protocol = {
            "protocol_id": "test",
            "started_at": start.isoformat(),
            "ask_min": 0.20,
            "ask_max": 0.40,
            "edge_min": 0.08,
            "strategy_name": "core_modal_v1",
            "forecast_algo": "polywx_aligned_deb_v1",
            "target_n": 2,
            "power_effect_clv": 0.03,
            "expected_evaluation_date": "2026-07-27",
            "preclose_window_seconds": 900,
            "hypothesis_a_edge_min": 0.15,
        }
        with connect(path) as conn:
            for index, issued in enumerate((start + timedelta(minutes=1), start + timedelta(minutes=2))):
                conn.execute(
                    """
                    INSERT INTO signal_decisions (
                        decision_id, market_id, token_id, yes_token_id, city_key, target_date,
                        issued_at, edge, decision_time_ask, quote_age_at_decision_seconds,
                        strategy_name, forecast_algo, paper_allowed, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        f"decision-{index}",
                        "market-1",
                        "token-1",
                        "token-1",
                        "chicago",
                        "2026-07-27",
                        issued.isoformat(),
                        0.10,
                        0.25,
                        10.0,
                        "core_modal_v1",
                        "polywx_aligned_deb_v1",
                        issued.isoformat(),
                    ),
                )
            conn.execute(
                """
                INSERT INTO candidate_preclose_quotes (
                    snapshot_key, decision_id, market_id, token_id, city_key, target_date,
                    market_close_at, captured_at, best_bid, best_ask, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "quote-1",
                    "decision-0",
                    "market-1",
                    "token-1",
                    "chicago",
                    "2026-07-27",
                    close.isoformat(),
                    (close - timedelta(minutes=5)).isoformat(),
                    0.27,
                    0.28,
                    "ok",
                    (close - timedelta(minutes=5)).isoformat(),
                ),
            )
            conn.execute(
                """
                INSERT INTO paper_orders (
                    decision_id, idempotency_key, market_id, yes_token_id, bucket_key,
                    city_key, target_date, status, lifecycle_status, model_probability,
                    market_probability, realized_pnl, unrealized_pnl, opened_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "decision-0",
                    "order-in-cohort",
                    "market-1",
                    "token-1",
                    "bucket-1",
                    "chicago",
                    "2026-07-27",
                    "paper_won",
                    "settled",
                    0.45,
                    0.25,
                    3.0,
                    0.0,
                    start.isoformat(),
                    start.isoformat(),
                    start.isoformat(),
                ),
            )
            conn.execute(
                """
                INSERT INTO paper_orders (
                    decision_id, idempotency_key, market_id, yes_token_id, bucket_key,
                    city_key, target_date, status, lifecycle_status, model_probability,
                    market_probability, realized_pnl, unrealized_pnl, opened_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "outside-cohort",
                    "order-outside-cohort",
                    "market-2",
                    "token-2",
                    "bucket-2",
                    "paris",
                    "2026-07-27",
                    "paper_lost",
                    "settled",
                    0.90,
                    0.90,
                    -2.0,
                    0.0,
                    start.isoformat(),
                    start.isoformat(),
                    start.isoformat(),
                ),
            )
            conn.commit()

        summary = forward_validation_summary(path=path, protocol=protocol, use_cache=False)

        self.assertEqual(summary["progress"]["samples"], 1)
        self.assertEqual(summary["clv"]["n"], 1)
        self.assertAlmostEqual(summary["clv"]["mean"], 0.03)
        self.assertEqual(summary["hypotheses"]["H-B"]["n"], 1)
        self.assertEqual(summary["paper_pnl"]["settled_orders"], 1)
        self.assertEqual(summary["paper_pnl"]["realized_usd"], 3.0)
        self.assertEqual(summary["probability_score"]["n"], 1)


if __name__ == "__main__":
    unittest.main()
