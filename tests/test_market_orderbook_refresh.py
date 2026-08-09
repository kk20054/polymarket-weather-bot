from tests import ensure_test_environment

ensure_test_environment()

import unittest
from pathlib import Path

from weatherbot_v3.db import connect, init_v3_db, upsert_market_buckets
from weatherbot_v3.market_buckets import refresh_cached_market_bucket_orderbooks
from weatherbot_v3.strategies.base import orderbook_execution_reasons


TEST_DB_DIR = Path(__file__).resolve().parents[1] / ".tmp-tests"


class _Response:
    url = "https://clob.polymarket.com/books"

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}
        self.trust_env = True
        self.posts = []

    def post(self, url, *, json, timeout):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return _Response(self.payload)


class _FailingSession(_Session):
    def post(self, url, *, json, timeout):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        raise ConnectionError("dns_resolution_failed")


class CachedOrderbookRefreshTests(unittest.TestCase):
    def _seed_bucket(self, db_path: Path, **overrides):
        TEST_DB_DIR.mkdir(exist_ok=True)
        init_v3_db(db_path)
        raw = {
            "id": "market-1",
            "eventSlug": "highest-temperature-in-shanghai-on-july-19-2026",
            "event_url": "https://polymarket.com/event/highest-temperature-in-shanghai-on-july-19-2026",
            "question": "Will the highest temperature in Shanghai be 32°C on July 19?",
            "city": "shanghai",
            "city_name": "Shanghai",
            "target_date": "2026-07-19",
            "station_id": "ZSPD",
            "yes_token_id": "yes-1",
            "no_token_id": "no-1",
            "clobTokenIds": '["yes-1", "no-1"]',
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.2", "0.8"]',
            "orderMinSize": 5,
            "orderPriceMinTickSize": 0.001,
            "enableOrderBook": True,
        }
        bucket = {
            "bucket_key": "bucket-1",
            "market_id": "market-1",
            "event_slug": raw["eventSlug"],
            "event_url": raw["event_url"],
            "question": raw["question"],
            "city": "shanghai",
            "city_name": "Shanghai",
            "target_date": "2026-07-19",
            "station_id": "ZSPD",
            "unit": "C",
            "bucket_label": "32C",
            "bucket_direction": "exact",
            "bucket_low": 32,
            "bucket_high": 32,
            "outcome_name": "Yes",
            "yes_token_id": "yes-1",
            "no_token_id": "no-1",
            "tick_size": 0.001,
            "order_min_size": 5,
            "enable_order_book": True,
            "raw_json": raw,
        }
        bucket.update(overrides)
        upsert_market_buckets([bucket], path=db_path)

    def test_cached_tokens_refresh_in_one_clob_batch_without_gamma_discovery(self):
        TEST_DB_DIR.mkdir(exist_ok=True)
        db_path = TEST_DB_DIR / "cached_orderbook_refresh.db"
        db_path.unlink(missing_ok=True)
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        self._seed_bucket(db_path)
        session = _Session([
            {
                "asset_id": "yes-1",
                "timestamp": "1784428800000",
                "hash": "fresh-hash",
                "tick_size": "0.001",
                "min_order_size": "5",
                "bids": [{"price": "0.19", "size": "100"}],
                "asks": [{"price": "0.21", "size": "80"}],
            }
        ])

        result = refresh_cached_market_bucket_orderbooks(
            targets_by_city={"shanghai": ["2026-07-19"]},
            path=db_path,
            session=session,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["quotes_refreshed"], 1)
        self.assertEqual(result["quotes_missing"], 0)
        self.assertEqual(len(session.posts), 1)
        self.assertEqual(session.posts[0]["json"], [{"token_id": "yes-1"}])
        with connect(db_path) as conn:
            bucket = dict(conn.execute("SELECT * FROM market_buckets").fetchone())
            orderbooks = conn.execute("SELECT COUNT(*) FROM orderbooks").fetchone()[0]
        self.assertEqual(bucket["quote_timestamp"], "1784428800000")
        self.assertAlmostEqual(bucket["best_bid"], 0.19)
        self.assertAlmostEqual(bucket["best_ask"], 0.21)
        self.assertEqual(bucket["orderbook_source"], "clob")
        self.assertEqual(bucket["orderbook_state"], "two_sided")
        self.assertEqual(bucket["orderbook_http_status"], 200)
        self.assertTrue(bucket["orderbook_checked_at"])
        self.assertTrue(bucket["orderbook_last_success_at"])
        self.assertEqual(orderbooks, 1)

    def test_current_target_is_not_displaced_by_older_rows_before_limit(self):
        TEST_DB_DIR.mkdir(exist_ok=True)
        db_path = TEST_DB_DIR / "cached_orderbook_target_filter.db"
        db_path.unlink(missing_ok=True)
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        self._seed_bucket(db_path)
        for index in range(6):
            self._seed_bucket(
                db_path,
                bucket_key=f"old-bucket-{index}",
                market_id=f"old-market-{index}",
                city="chicago",
                city_name="Chicago",
                target_date=f"2020-01-{index + 1:02d}",
                station_id="KORD",
                yes_token_id=f"old-yes-{index}",
                no_token_id=f"old-no-{index}",
            )
        session = _Session([{
            "asset_id": "yes-1",
            "timestamp": "1784428800000",
            "hash": "fresh-hash",
            "tick_size": "0.001",
            "min_order_size": "5",
            "bids": [{"price": "0.19", "size": "100"}],
            "asks": [{"price": "0.21", "size": "80"}],
        }])

        result = refresh_cached_market_bucket_orderbooks(
            targets_by_city={"shanghai": ["2026-07-19"]},
            limit=5,
            path=db_path,
            session=session,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["cached_buckets"], 1)
        self.assertEqual(result["quotes_refreshed"], 1)
        self.assertEqual(session.posts[0]["json"], [{"token_id": "yes-1"}])

    def test_one_sided_clob_keeps_missing_bid_null_and_classifies_side_absent(self):
        TEST_DB_DIR.mkdir(exist_ok=True)
        db_path = TEST_DB_DIR / "cached_orderbook_one_sided.db"
        db_path.unlink(missing_ok=True)
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        self._seed_bucket(db_path)
        session = _Session([
            {
                "asset_id": "yes-1",
                "timestamp": "1784428800000",
                "hash": "one-sided-hash",
                "tick_size": "0.001",
                "min_order_size": "5",
                "bids": [],
                "asks": [{"price": "0.21", "size": "80"}],
            }
        ])

        result = refresh_cached_market_bucket_orderbooks(
            targets_by_city={"shanghai": ["2026-07-19"]},
            path=db_path,
            session=session,
        )

        self.assertTrue(result["ok"])
        with connect(db_path) as conn:
            bucket = dict(conn.execute("SELECT * FROM market_buckets").fetchone())
            orderbook = dict(conn.execute("SELECT * FROM orderbooks").fetchone())
        self.assertIsNone(bucket["best_bid"])
        self.assertAlmostEqual(bucket["best_ask"], 0.21)
        self.assertEqual(bucket["orderbook_state"], "side_absent")
        self.assertIsNone(orderbook["best_bid"])
        self.assertAlmostEqual(orderbook["best_ask"], 0.21)
        self.assertEqual(orderbook["book_state"], "side_absent")
        reasons = orderbook_execution_reasons(bucket, bucket["best_bid"], bucket["best_ask"])
        self.assertIn("bid_side_absent", reasons)
        self.assertNotIn("invalid_best_bid", reasons)

    def test_fetch_failure_preserves_quote_and_records_failed_attempt(self):
        TEST_DB_DIR.mkdir(exist_ok=True)
        db_path = TEST_DB_DIR / "cached_orderbook_failure.db"
        db_path.unlink(missing_ok=True)
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        self._seed_bucket(
            db_path,
            best_bid=0.19,
            best_ask=0.21,
            spread=0.02,
            quote_timestamp="1784428700000",
            orderbook_source="clob",
            orderbook_state="two_sided",
            orderbook_checked_at="2026-07-19T00:00:00+00:00",
            orderbook_last_success_at="2026-07-19T00:00:00+00:00",
            orderbook_http_status=200,
        )
        with connect(db_path) as conn:
            quote_updated_at = conn.execute(
                "SELECT updated_at FROM market_buckets WHERE bucket_key = 'bucket-1'"
            ).fetchone()[0]

        result = refresh_cached_market_bucket_orderbooks(
            targets_by_city={"shanghai": ["2026-07-19"]},
            path=db_path,
            session=_FailingSession([]),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["orderbook_state_updates"], 1)
        with connect(db_path) as conn:
            bucket = dict(conn.execute("SELECT * FROM market_buckets").fetchone())
            orderbook_count = conn.execute("SELECT COUNT(*) FROM orderbooks").fetchone()[0]
        self.assertAlmostEqual(bucket["best_bid"], 0.19)
        self.assertAlmostEqual(bucket["best_ask"], 0.21)
        self.assertEqual(bucket["quote_timestamp"], "1784428700000")
        self.assertEqual(bucket["orderbook_state"], "fetch_failed")
        self.assertEqual(bucket["orderbook_last_success_at"], "2026-07-19T00:00:00+00:00")
        self.assertIn("dns_resolution_failed", bucket["orderbook_error"])
        self.assertTrue(bucket["orderbook_checked_at"])
        self.assertEqual(bucket["updated_at"], quote_updated_at)
        self.assertEqual(orderbook_count, 0)
        reasons = orderbook_execution_reasons(bucket, bucket["best_bid"], bucket["best_ask"])
        self.assertEqual(reasons, ["orderbook_fetch_failed"])

    def test_successful_empty_clob_is_book_absent_not_fetch_failed(self):
        TEST_DB_DIR.mkdir(exist_ok=True)
        db_path = TEST_DB_DIR / "cached_orderbook_empty.db"
        db_path.unlink(missing_ok=True)
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
        self._seed_bucket(db_path)
        session = _Session([
            {
                "asset_id": "yes-1",
                "timestamp": "1784428800000",
                "hash": "empty-hash",
                "tick_size": "0.001",
                "min_order_size": "5",
                "bids": [],
                "asks": [],
            }
        ])

        result = refresh_cached_market_bucket_orderbooks(
            targets_by_city={"shanghai": ["2026-07-19"]},
            path=db_path,
            session=session,
        )

        self.assertTrue(result["ok"])
        with connect(db_path) as conn:
            bucket = dict(conn.execute("SELECT * FROM market_buckets").fetchone())
            orderbook = dict(conn.execute("SELECT * FROM orderbooks").fetchone())
        self.assertIsNone(bucket["best_bid"])
        self.assertIsNone(bucket["best_ask"])
        self.assertEqual(bucket["orderbook_state"], "book_absent")
        self.assertIsNone(orderbook["best_bid"])
        self.assertIsNone(orderbook["best_ask"])
        self.assertEqual(orderbook["book_state"], "book_absent")
        self.assertEqual(
            orderbook_execution_reasons(bucket, bucket["best_bid"], bucket["best_ask"]),
            ["orderbook_absent"],
        )


if __name__ == "__main__":
    unittest.main()
