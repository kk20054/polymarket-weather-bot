from tests import ensure_test_environment

ensure_test_environment()

import unittest

from weatherbot_v3.strategies import LadderGridStrategy, SingleBucketEVStrategy, TailBuyingStrategy


def _context(**overrides):
    base = {
        "decision_version": "signal-decision-v2",
        "single_bucket_id_version": "signal-decision-v1",
        "distribution": {"mu": 90.0, "sigma": 2.0, "unit": "F", "items": []},
        "evidence": {"daily_max_prediction_id": 1},
        "station_live_reasons": [],
        "max_spread_bps": 500.0,
        "stale_book_seconds": 300.0,
        "min_bias_sample_days": 7,
        "bankroll": 1000.0,
        "kelly_multiplier": 0.15,
        "max_per_trade_usd": 100.0,
        "independent_settlement_days": 20,
    }
    base.update(overrides)
    return base


def _prediction(**overrides):
    base = {
        "id": 1,
        "city_key": "chicago",
        "target_date": "2026-07-04",
        "issued_at": "2026-07-04T12:00:00+00:00",
        "mu": 90.0,
        "sigma": 2.0,
        "method": "weatherbot-deb-v2",
        "deb_version": "weatherbot-deb-v2",
        "bias_sample_count": 10,
    }
    base.update(overrides)
    return base


def _bucket(key, low, high, ask, bid=0.09, direction="range"):
    return {
        "id": key,
        "bucket_key": key,
        "city": "chicago",
        "target_date": "2026-07-04",
        "market_id": f"market-{key}",
        "yes_token_id": f"yes-{key}",
        "token_id": f"yes-{key}",
        "bucket_direction": direction,
        "bucket_low": low,
        "bucket_high": high,
        "best_ask": ask,
        "best_bid": bid,
        "spread": max(0.0, ask - bid),
        "tick_size": 0.01,
        "order_min_size": 5.0,
        "enable_order_book": True,
        "quote_timestamp": "2026-07-04T12:00:00+00:00",
        "bid_depth": 100.0,
        "ask_depth": 100.0,
        "strict_match_status": "matched",
        "strict_match_reasons": [],
    }


class StrategyTests(unittest.TestCase):
    def test_single_bucket_ev_uses_five_point_edge_threshold(self):
        strategy = SingleBucketEVStrategy()
        decision = strategy.evaluate(
            _bucket("mid", 89.0, 91.0, 0.20, 0.195),
            {"bucket_key": "mid", "probability": 0.28},
            _prediction(),
            _context(),
        )
        self.assertEqual(decision["strategy_name"], "single_bucket_ev")
        self.assertEqual(decision["paper_decision"], "buy")
        self.assertGreater(decision["kelly_fraction"], 0)

        skipped = strategy.evaluate(
            _bucket("thin", 89.0, 91.0, 0.20, 0.195),
            {"bucket_key": "thin", "probability": 0.24},
            _prediction(),
            _context(),
        )
        self.assertEqual(skipped["paper_decision"], "skip")
        self.assertIn("edge_below_min", skipped["gate_reasons"])

    def test_ladder_grid_builds_three_bucket_atomic_group(self):
        buckets = [
            _bucket("low", 88.0, 89.0, 0.10, 0.095),
            _bucket("mid", 89.0, 91.0, 0.20, 0.195),
            _bucket("high", 91.0, 92.0, 0.10, 0.095),
        ]
        probabilities = {
            "low": {"bucket_key": "low", "probability": 0.20},
            "mid": {"bucket_key": "mid", "probability": 0.50},
            "high": {"bucket_key": "high", "probability": 0.20},
        }
        decisions = LadderGridStrategy().evaluate_many(buckets, probabilities, _prediction(), _context())

        self.assertEqual(len(decisions), 3)
        self.assertEqual({row["strategy_name"] for row in decisions}, {"ladder_grid"})
        self.assertEqual(len({row["ladder_group_id"] for row in decisions}), 1)
        self.assertAlmostEqual(sum(row["position_size_usd"] for row in decisions), 30.0, places=3)
        self.assertGreater(decisions[1]["position_size_usd"], decisions[0]["position_size_usd"])

    def test_ladder_grid_requires_all_three_edges(self):
        buckets = [
            _bucket("low", 88.0, 89.0, 0.30, 0.295),
            _bucket("mid", 89.0, 91.0, 0.20, 0.195),
            _bucket("high", 91.0, 92.0, 0.10, 0.095),
        ]
        probabilities = {
            "low": {"bucket_key": "low", "probability": 0.20},
            "mid": {"bucket_key": "mid", "probability": 0.50},
            "high": {"bucket_key": "high", "probability": 0.20},
        }
        self.assertEqual(LadderGridStrategy().evaluate_many(buckets, probabilities, _prediction(), _context()), [])

    def test_tail_buying_requires_cheap_price_edge_and_history(self):
        strategy = TailBuyingStrategy()
        decision = strategy.evaluate(
            _bucket("tail", None, 86.0, 0.10, 0.095, direction="or_below"),
            {"bucket_key": "tail", "probability": 0.25},
            _prediction(),
            _context(),
        )
        self.assertEqual(decision["strategy_name"], "tail_buying")
        self.assertEqual(decision["paper_decision"], "buy")
        self.assertLessEqual(decision["position_size_usd"], 50.0)

        thin_history = strategy.evaluate(
            _bucket("tail2", None, 86.0, 0.10, 0.095, direction="or_below"),
            {"bucket_key": "tail2", "probability": 0.25},
            _prediction(),
            _context(independent_settlement_days=3),
        )
        self.assertEqual(thin_history["paper_decision"], "skip")
        self.assertIn("tail_independent_settlement_days_below_20", thin_history["gate_reasons"])

    def test_tail_buying_daily_candidate_cap(self):
        buckets = [_bucket(f"tail-{i}", 80.0 + i, 81.0 + i, 0.10, 0.095) for i in range(6)]
        probabilities = {bucket["bucket_key"]: {"bucket_key": bucket["bucket_key"], "probability": 0.25} for bucket in buckets}
        decisions = TailBuyingStrategy().evaluate_many(buckets, probabilities, _prediction(), _context())
        self.assertEqual(len(decisions), 5)


if __name__ == "__main__":
    unittest.main()
