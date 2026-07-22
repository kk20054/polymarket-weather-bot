from tests import ensure_test_environment

ensure_test_environment()

import unittest
from pathlib import Path

from weatherbot_v3.db import connect, init_v3_db, upsert_market_buckets
from weatherbot_v3.market_buckets import refresh_cached_market_bucket_orderbooks


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


class CachedOrderbookRefreshTests(unittest.TestCase):
    def test_cached_tokens_refresh_in_one_clob_batch_without_gamma_discovery(self):
        TEST_DB_DIR.mkdir(exist_ok=True)
        db_path = TEST_DB_DIR / "cached_orderbook_refresh.db"
        db_path.unlink(missing_ok=True)
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))
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
        upsert_market_buckets([
            {
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
        ], path=db_path)
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
        self.assertEqual(orderbooks, 1)


if __name__ == "__main__":
    unittest.main()
