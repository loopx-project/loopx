"""Deliberately buggy tutorial fixture; do not use for real checkout pricing."""


def total_with_shipping(prices: list[int]) -> int:
    """Return cents: empty carts cost zero; subtotals >= 10_000 ship free."""
    if any(price < 0 for price in prices):
        raise ValueError("prices must be nonnegative")
    subtotal = sum(prices)
    if subtotal > 10_000:
        return subtotal
    return subtotal + 500
