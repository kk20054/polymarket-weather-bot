from tests import ensure_test_environment

ensure_test_environment()

import unittest

from weatherbot_v3.strategies import CoreModalStrategy
from weatherbot_v3.signals import build_signal_decisions
from weatherbot_v3.strategy_profiles import core_modal_v1_parameters


def _bucket(key, low, high, ask, bid):
    return {
        "id": key,
        "bucket_key": key,
        "city": "chicago",
        "target_date": "2026-07-21",
        "market_id": f"market-{key}",
        "yes_token_id": f"yes-{key}",
        "bucket_direction": "range",
        "bucket_low": low,
        "bucket_high": high,
        "best_ask": ask,
        "best_bid": bid,
        "spread": ask - bid,
        "tick_size": 0.01,
        "order_min_size": 5.0,
        "enable_order_book": True,
        "quote_timestamp": "2099-07-20T12:00:00+00:00",
        "strict_match_status": "matched",
        "strict_match_reasons": [],
    }


def _prediction(**overrides):
    components = []
    for index, high in enumerate((29.0, 29.4, 29.7, 30.1, 30.3), start=1):
        components.append({
            "source": f"source-{index}",
            "family": f"family-{index}",
            "member_count": 1,
            "weight": 0.2,
            "adjusted_daily_highs_c": [high],
            "bias_sample_count": 30,
            "mae_imputed": False,
        })
    base = {
        "id": 1,
        "city_key": "chicago",
        "target_date": "2026-07-21",
        "issued_at": "2026-07-20T12:00:00+00:00",
        "mu": 29.6,
        "sigma": 1.0,
        "unit": "C",
        "method": "polywx_aligned_deb_v1",
        "deb_version": "polywx_aligned_deb_v1",
        "forecast_algo": "polywx_aligned_deb_v1",
        "bias_sample_count": 30,
        "components": components,
    }
    base.update(overrides)
    return base


def _context(**overrides):
    base = {
        "decision_version": "signal-decision-v3",
        "distribution": {"mu": 29.6, "sigma": 1.0, "unit": "C", "items": []},
        "evidence": {"daily_max_prediction_id": 1},
        "station_live_reasons": [],
        "max_spread_bps": 500.0,
        "stale_book_seconds": 300.0,
        "min_bias_sample_days": 7,
        "low_price_tail_ask": 0.05,
        "bankroll": 1000.0,
        "kelly_multiplier": 0.15,
        "bankroll_fraction_cap": 0.05,
        "max_per_trade_usd": 100.0,
        "independent_settlement_days": 30,
        "independent_settlement_basis": "wunderground_daily",
        "independent_settlement_authoritative": True,
        "strategy_revision_id": "spr-core-modal",
        "decision_time": "2026-07-20T12:04:00+00:00",
    }
    base.update(overrides)
    return base


class CoreModalStrategyTests(unittest.TestCase):
    def test_shadow_revision_override_cannot_persist_decisions(self):
        result = build_signal_decisions(
            "chicago",
            "2026-07-21",
            dry_run=False,
            strategy_revision_id="spr-shadow",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reasons"], ["shadow_strategy_revision_requires_dry_run"])

    def test_buys_at_most_one_of_top_two_model_buckets(self):
        buckets = [
            _bucket("modal", 29, 30, 0.25, 0.24),
            _bucket("second", 30, 31, 0.20, 0.19),
            _bucket("cheap-third", 28, 29, 0.03, 0.02),
        ]
        probabilities = {
            "modal": {"bucket_key": "modal", "probability": 0.38},
            "second": {"bucket_key": "second", "probability": 0.35},
            "cheap-third": {"bucket_key": "cheap-third", "probability": 0.20},
        }

        decisions = CoreModalStrategy().evaluate_many(buckets, probabilities, _prediction(), _context())

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["bucket_key"], "second")
        self.assertEqual(decisions[0]["paper_decision"], "buy")
        self.assertEqual(decisions[0]["core_modal"]["model_rank"], 2)
        self.assertNotEqual(decisions[0]["bucket_key"], "cheap-third")

    def test_does_not_fall_back_when_modal_orderbook_is_not_executable(self):
        modal = _bucket("modal", 29, 30, 0.25, 0.24)
        modal["enable_order_book"] = False
        buckets = [modal, _bucket("second", 30, 31, 0.12, 0.11)]
        probabilities = {
            "modal": {"bucket_key": "modal", "probability": 0.40},
            "second": {"bucket_key": "second", "probability": 0.35},
        }

        decision = CoreModalStrategy().evaluate_many(buckets, probabilities, _prediction(), _context())[0]

        self.assertEqual(decision["bucket_key"], "modal")
        self.assertFalse(decision["paper_allowed"])
        self.assertIn("core_modal_not_executable", decision["gate_reasons"])

    def test_blocks_low_calibration_coverage(self):
        prediction = _prediction()
        for component in prediction["components"][:2]:
            component["mae_imputed"] = True
            component["bias_sample_count"] = 0
        buckets = [_bucket("modal", 29, 30, 0.20, 0.19)]
        probabilities = {"modal": {"bucket_key": "modal", "probability": 0.40}}

        decision = CoreModalStrategy().evaluate_many(buckets, probabilities, prediction, _context())[0]

        self.assertFalse(decision["paper_allowed"])
        self.assertIn("core_calibration_coverage_below_min", decision["gate_reasons"])
        self.assertNotIn("core_modal_not_executable", decision["gate_reasons"])
        self.assertAlmostEqual(decision["core_modal"]["quality"]["calibration_coverage"], 0.6)

    def test_blocks_wide_model_family_spread(self):
        prediction = _prediction()
        prediction["components"][-1]["adjusted_daily_highs_c"] = [32.0]
        buckets = [_bucket("modal", 29, 30, 0.20, 0.19)]
        probabilities = {"modal": {"bucket_key": "modal", "probability": 0.40}}

        decision = CoreModalStrategy().evaluate_many(buckets, probabilities, prediction, _context())[0]

        self.assertFalse(decision["paper_allowed"])
        self.assertIn("core_model_spread_too_wide", decision["gate_reasons"])

    def test_preset_disables_exploratory_strategies(self):
        strategies = core_modal_v1_parameters()["strategies"]

        self.assertTrue(strategies["core_modal_v1"]["enabled"])
        self.assertFalse(strategies["single_bucket_ev"]["enabled"])
        self.assertFalse(strategies["ladder_grid"]["enabled"])
        self.assertFalse(strategies["tail_buying"]["enabled"])

    def test_historical_replay_uses_explicit_decision_time_for_quote_age(self):
        bucket = _bucket("modal", 29, 30, 0.20, 0.19)
        bucket["quote_timestamp"] = "2026-07-20T12:00:00+00:00"
        probabilities = {"modal": {"bucket_key": "modal", "probability": 0.40}}

        fresh = CoreModalStrategy().evaluate_many(
            [bucket],
            probabilities,
            _prediction(),
            _context(decision_time="2026-07-20T12:04:00+00:00"),
        )[0]
        stale = CoreModalStrategy().evaluate_many(
            [bucket],
            probabilities,
            _prediction(),
            _context(decision_time="2026-07-20T12:06:00+00:00"),
        )[0]

        self.assertTrue(fresh["paper_allowed"])
        self.assertFalse(stale["paper_allowed"])
        self.assertIn("stale_book", stale["gate_reasons"])


if __name__ == "__main__":
    unittest.main()
