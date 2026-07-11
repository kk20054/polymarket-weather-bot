import unittest
from pathlib import Path

from weatherbot_v3.db import (
    connect,
    init_v3_db,
    list_paper_orders,
    list_settlements,
    paper_execution_summary,
    upsert_paper_order_record,
)
from weatherbot_v3.paper_settlement import market_resolution_from_payload, settle_open_paper_orders
from weatherbot_v3.polymarket_gamma import upsert_polymarket_market


ROOT = Path(__file__).resolve().parents[1]
TEST_DB_DIR = ROOT / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class PaperSettlementTests(unittest.TestCase):
    def test_market_resolution_requires_closed_binary_outcome(self):
        open_payload = {"closed": False, "outcomes": '["Yes","No"]', "outcomePrices": '["0.9995","0.0005"]'}
        resolved_payload = {"closed": True, "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]'}

        self.assertFalse(market_resolution_from_payload(open_payload)["resolved"])
        self.assertEqual(market_resolution_from_payload(open_payload)["reason"], "market_not_closed")
        self.assertTrue(market_resolution_from_payload(resolved_payload)["resolved"])
        self.assertEqual(market_resolution_from_payload(resolved_payload)["outcome_yes"], 1)

    def test_truth_is_provisional_then_gamma_resolution_closes_order_once(self):
        path = test_db_path("paper_settlement_upgrade")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        order_id = upsert_paper_order_record(_paper_order(), path=path)
        upsert_polymarket_market(_market_row({
            "closed": False,
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.9995","0.0005"]',
        }), path=path)
        with connect(path) as conn:
            conn.execute(
                """
                INSERT INTO truth_wunderground_daily (
                    truth_key, icao, date_local, timezone, high_c, source_url,
                    settlement_truth_type, parser_version, raw_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "wu:ZBAA:2026-07-05", "ZBAA", "2026-07-05", "Asia/Shanghai", 30.5,
                    "https://wu.example/ZBAA", "wunderground_daily", "test", "{}",
                    "2026-07-06T00:00:00+00:00", "2026-07-06T00:00:00+00:00",
                ),
            )

        provisional = settle_open_paper_orders(city_key="beijing", target_date="2026-07-05", apply=True, path=path)
        order = list_paper_orders(city_key="beijing", target_date="2026-07-05", path=path)[0]
        settlement = list_settlements(city_key="beijing", target_date="2026-07-05", path=path)[0]

        self.assertEqual(provisional["provisional_now"], 1)
        self.assertEqual(order["lifecycle_status"], "open")
        self.assertEqual(settlement["settlement_status"], "provisional_truth")
        self.assertIsNone(settlement["pnl"])
        self.assertAlmostEqual(float(settlement["brier_score"]), 0.16)

        resolved_payload = {
            "id": "market-1",
            "closed": True,
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["1","0"]',
            "updatedAt": "2026-07-06T03:00:00+00:00",
        }
        resolved = settle_open_paper_orders(
            city_key="beijing",
            target_date="2026-07-05",
            refresh_gamma=True,
            apply=True,
            path=path,
            client=_FakeMarketClient(resolved_payload),
        )
        order = list_paper_orders(city_key="beijing", target_date="2026-07-05", path=path)[0]
        settlements = list_settlements(city_key="beijing", target_date="2026-07-05", path=path)

        self.assertEqual(resolved["resolved_now"], 1)
        self.assertEqual(len(settlements), 1)
        self.assertEqual(settlements[0]["paper_order_id"], order_id)
        self.assertEqual(settlements[0]["settlement_status"], "resolved")
        self.assertAlmostEqual(float(settlements[0]["payout"]), 10.0)
        self.assertAlmostEqual(float(settlements[0]["pnl"]), 7.5)
        self.assertEqual(order["lifecycle_status"], "settled")
        self.assertEqual(order["status"], "paper_won")
        self.assertAlmostEqual(float(order["realized_pnl"]), 7.5)

        summary = paper_execution_summary(city_key="beijing", target_date="2026-07-05", path=path)
        self.assertEqual(summary["resolved_orders"], 1)
        self.assertEqual(summary["provisional_orders"], 0)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 0)
        self.assertEqual(summary["win_rate"], 1.0)
        self.assertAlmostEqual(float(summary["realized_pnl"]), 7.5)
        self.assertAlmostEqual(float(summary["brier_score"]), 0.16)
        self.assertAlmostEqual(float(summary["market_brier_score"]), 0.5625)

        repeated = settle_open_paper_orders(
            city_key="beijing",
            target_date="2026-07-05",
            refresh_gamma=True,
            apply=True,
            path=path,
            client=_FakeMarketClient(resolved_payload),
        )
        self.assertEqual(repeated["candidates"], 0)
        self.assertEqual(len(list_settlements(city_key="beijing", target_date="2026-07-05", path=path)), 1)
        self.assertAlmostEqual(float(repeated["realized_pnl"]), 7.5)

    def test_legacy_orders_without_identity_are_not_fabricated(self):
        path = test_db_path("paper_settlement_legacy")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        with connect(path) as conn:
            now = "2026-07-06T00:00:00+00:00"
            conn.execute(
                "INSERT INTO paper_orders (idempotency_key, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("legacy", "paper_filled", now, now),
            )

        result = settle_open_paper_orders(apply=True, path=path)

        self.assertEqual(result["candidates"], 0)
        self.assertEqual(result["legacy_skipped"], 1)
        self.assertEqual(list_settlements(path=path), [])


def _paper_order() -> dict:
    return {
        "decision_id": "decision-1",
        "idempotency_key": "paper-order-1",
        "market_id": "market-1",
        "yes_token_id": "yes-1",
        "bucket_key": "bucket-30",
        "city_key": "beijing",
        "target_date": "2026-07-05",
        "side": "BUY",
        "limit_price": 0.25,
        "requested_amount": 2.5,
        "amount": 2.5,
        "shares": 10.0,
        "filled_amount": 2.5,
        "filled_shares": 10.0,
        "unfilled_amount": 0.0,
        "average_fill_price": 0.25,
        "mark_price": 0.24,
        "unrealized_pnl": -0.1,
        "realized_pnl": 0.0,
        "status": "paper_filled",
        "lifecycle_status": "open",
        "fill_status": "filled",
        "order_version": "paper-execution-v1",
        "model_probability": 0.6,
        "market_probability": 0.25,
        "opened_at": "2026-07-04T00:00:00+00:00",
    }


def _market_row(payload: dict) -> dict:
    return {
        "market_id": "market-1",
        "event_id": "event-1",
        "event_slug": "highest-temperature-in-beijing-on-july-5-2026",
        "market_slug": "highest-temperature-in-beijing-on-july-5-2026-30c",
        "city": "beijing",
        "target_date": "2026-07-05",
        "bucket_label": "30C",
        "bucket_lower_c": 30.0,
        "bucket_upper_c": 31.0,
        "is_tail": False,
        "outcome_yes_token_id": "yes-1",
        "outcome_no_token_id": "no-1",
        "order_min_size": 5.0,
        "tick_size": 0.001,
        "enable_order_book": True,
        "raw_json": payload,
    }


class _FakeMarketClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    def get_market(self, market_id: str) -> dict:
        self.calls.append(market_id)
        return dict(self.payload)


if __name__ == "__main__":
    unittest.main()
