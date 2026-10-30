"""Price quotes for carts and subscriptions."""
from dataclasses import dataclass


@dataclass
class Quote:
    subtotal_cents: int
    discount_cents: int

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents - self.discount_cents


def quote_cart(line_totals: list[int], loyalty_years: int) -> Quote:
    """Quote a one-off cart, applying the loyalty discount."""
    subtotal = sum(line_totals)
    if loyalty_years >= 5:
        rate = 0.15
    elif loyalty_years >= 3:
        rate = 0.10
    elif loyalty_years >= 1:
        rate = 0.05
    else:
        rate = 0.0
    discount = int(subtotal * rate)
    if discount > 10_000:
        discount = 10_000
    return Quote(subtotal, discount)


def quote_subscription(monthly_cents: int, months: int, loyalty_years: int) -> Quote:
    """Quote a prepaid subscription, applying the loyalty discount."""
    subtotal = monthly_cents * months
    if loyalty_years >= 5:
        rate = 0.15
    elif loyalty_years >= 3:
        rate = 0.10
    elif loyalty_years >= 1:
        rate = 0.05
    else:
        rate = 0.0
    discount = int(subtotal * rate)
    if discount > 10_000:
        discount = 10_000
    return Quote(subtotal, discount)
