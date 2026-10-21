"""Run with `python3 checks.py`; the tutorial starts with two failing checks."""

import unittest

from cart import total_with_shipping


class CartChecks(unittest.TestCase):
    def test_empty_cart_costs_zero(self) -> None:
        self.assertEqual(total_with_shipping([]), 0)

    def test_below_threshold_adds_shipping(self) -> None:
        self.assertEqual(total_with_shipping([2_500, 2_000]), 5_000)

    def test_combined_subtotal_at_threshold_ships_free(self) -> None:
        self.assertEqual(total_with_shipping([6_000, 4_000]), 10_000)

    def test_above_threshold_ships_free(self) -> None:
        self.assertEqual(total_with_shipping([10_001]), 10_001)

    def test_negative_price_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            total_with_shipping([5_000, -1])

    def test_input_is_not_modified(self) -> None:
        prices = [2_500, 2_000]
        total_with_shipping(prices)
        self.assertEqual(prices, [2_500, 2_000])


if __name__ == "__main__":
    unittest.main(verbosity=2)
