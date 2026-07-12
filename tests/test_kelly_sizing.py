import unittest

from weatherbot_v3.sizing import calculate_kelly_fraction, size_position


class KellySizingTests(unittest.TestCase):
    def test_kelly_fraction_for_binary_yes_contract(self):
        fraction = calculate_kelly_fraction(0.5, 0.4)
        self.assertAlmostEqual(fraction, 1 / 6, places=6)

    def test_invalid_price_or_negative_edge_returns_zero(self):
        self.assertEqual(calculate_kelly_fraction(0.2, 0.4), 0.0)
        self.assertEqual(calculate_kelly_fraction(0.5, 0.0), 0.0)
        self.assertEqual(calculate_kelly_fraction(0.5, 1.0), 0.0)

    def test_position_size_uses_fractional_kelly_and_hard_cap(self):
        result = size_position(0.8, 0.4, bankroll=1000.0, max_per_trade_usd=20.0, kelly_multiplier=0.15)
        self.assertGreater(result.kelly_fraction, 0)
        self.assertGreater(result.position_size_usd, result.capped_position_size_usd)
        self.assertEqual(result.hard_cap_usd, 20.0)
        self.assertEqual(result.capped_position_size_usd, 20.0)

    def test_bankroll_fraction_cap_is_configurable_and_auditable(self):
        result = size_position(
            0.8,
            0.4,
            bankroll=100.0,
            max_per_trade_usd=20.0,
            kelly_multiplier=0.15,
            bankroll_fraction_cap=0.02,
        )
        self.assertEqual(result.hard_cap_usd, 2.0)
        self.assertEqual(result.cap_reasons, ("bankroll_fraction_cap_usd",))
        self.assertEqual(result.snapshot()["final_position_size_usd"], 2.0)


if __name__ == "__main__":
    unittest.main()
