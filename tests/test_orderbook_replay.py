from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.backtest_core_modal_strategy import _market_buckets_with_books
from weatherbot_v3.db import connect, init_v3_db, insert_orderbook
from weatherbot_v3.orderbook_replay import select_orderbook_as_of, walk_buy_limit
from weatherbot_v3.paper import _simulate_fill
from weatherbot_v3.strategies.base import book_age_seconds_value


class OrderbookReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "weatherbot.db"
        init_v3_db(self.db_path)

    def test_as_of_selects_quote_at_or_before_decision_time(self):
        with connect(self.db_path) as conn:
            for key, quote_time, created_at in (
                ("before", "2026-07-20T11:59:00Z", "2026-07-20T12:02:00Z"),
                ("future", "2026-07-20T12:01:00Z", "2026-07-20T11:58:00Z"),
            ):
                conn.execute(
                    """
                    INSERT INTO orderbooks (
                        snapshot_key, market_id, yes_token_id, best_bid, best_ask,
                        quote_timestamp, bids_json, asks_json, created_at
                    ) VALUES (?, 'market', 'token', 0.39, 0.40, ?, '[]', '[]', ?)
                    """,
                    (key, quote_time, created_at),
                )
            row = select_orderbook_as_of(
                conn,
                decision_time="2026-07-20T12:00:00Z",
                yes_token_id="token",
            )

        self.assertEqual(row["snapshot_key"], "before")
        self.assertIsNone(
            book_age_seconds_value(
                "2026-07-20T12:01:00Z",
                as_of="2026-07-20T12:00:00Z",
            )
        )

    def test_as_of_prefers_exact_token_and_uses_market_only_as_fallback(self):
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO orderbooks (
                    snapshot_key, market_id, yes_token_id, best_bid, best_ask,
                    quote_timestamp, bids_json, asks_json, created_at
                ) VALUES
                    ('other-token', 'market', 'other', 0.79, 0.80,
                     '2026-07-20T11:59:30Z', '[]', '[]', '2026-07-20T11:59:30Z'),
                    ('exact-token', 'market', 'token', 0.39, 0.40,
                     '2026-07-20T11:59:00Z', '[]', '[]', '2026-07-20T11:59:00Z')
                """
            )
            exact = select_orderbook_as_of(
                conn,
                decision_time="2026-07-20T12:00:00Z",
                yes_token_id="token",
                market_id="market",
            )
            fallback = select_orderbook_as_of(
                conn,
                decision_time="2026-07-20T12:00:00Z",
                yes_token_id="missing-token",
                market_id="market",
            )

        self.assertEqual(exact["snapshot_key"], "exact-token")
        self.assertEqual(fallback["snapshot_key"], "other-token")

    def test_limit_fill_uses_price_levels_not_total_book_depth(self):
        asks = [
            {"price": 0.20, "size": 5},
            {"price": 0.21, "size": 100},
        ]
        walked = walk_buy_limit(asks, limit_price=0.20, requested_shares=10)
        self.assertEqual(walked["filled_shares"], 5)

        decision = {
            "market_ask": 0.20,
            "market_bid": 0.19,
            "orderbook_snapshot": {
                "asks": asks,
                "ask_depth": 105,
                "best_ask_size": 5,
                "depth_basis": "price_levels",
            },
        }
        order = {"requested_amount": 2.0, "derived": {"requested_shares": 10}}
        fill = _simulate_fill(decision, order)
        self.assertEqual(fill["fill_status"], "partial")
        self.assertEqual(fill["filled_shares"], 5)
        self.assertEqual(fill["average_fill_price"], 0.20)

    def test_replay_does_not_borrow_current_tick_or_minimum(self):
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO market_buckets (
                    bucket_key, market_id, city, target_date, yes_token_id,
                    tick_size, order_min_size, enable_order_book,
                    strict_match_status, created_at, updated_at
                ) VALUES (
                    'bucket', 'market', 'chicago', '2026-07-20', 'token',
                    0.01, 5, 1, 'matched', '2026-07-20T00:00:00Z', '2026-07-20T00:00:00Z'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO orderbooks (
                    snapshot_key, market_id, yes_token_id, best_bid, best_ask,
                    quote_timestamp, tick_size, order_min_size, enable_order_book,
                    bids_json, asks_json, created_at
                ) VALUES (
                    'book', 'market', 'token', 0.39, 0.40,
                    '2026-07-20T11:59:00Z', 0, 0, 1,
                    '[{"price":0.39,"size":10}]',
                    '[{"price":0.40,"size":10}]',
                    '2026-07-20T11:59:01Z'
                )
                """
            )

        rows = _market_buckets_with_books(
            "chicago",
            "2026-07-20",
            "2026-07-20T12:00:00Z",
            self.db_path,
        )
        self.assertIsNone(rows[0]["tick_size"])
        self.assertIsNone(rows[0]["order_min_size"])
        self.assertEqual(rows[0]["best_ask_size"], 10)

    def test_duplicate_snapshot_preserves_first_seen_time(self):
        payload = {
            "snapshot_key": "immutable",
            "yes_token_id": "token",
            "quote_timestamp": "2026-07-20T11:59:00Z",
            "bids": [{"price": 0.39, "size": 1}],
            "asks": [{"price": 0.40, "size": 1}],
        }
        with patch("weatherbot_v3.db.utc_now", return_value="2026-07-20T12:00:00Z"):
            insert_orderbook("market", payload, path=self.db_path)
        with patch("weatherbot_v3.db.utc_now", return_value="2026-07-20T13:00:00Z"):
            insert_orderbook("market", {**payload, "asks": [{"price": 0.40, "size": 2}]}, path=self.db_path)
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT created_at,ask_depth FROM orderbooks WHERE snapshot_key='immutable'"
            ).fetchone()
        self.assertEqual(row["created_at"], "2026-07-20T12:00:00Z")
        self.assertEqual(row["ask_depth"], 2)


if __name__ == "__main__":
    unittest.main()
