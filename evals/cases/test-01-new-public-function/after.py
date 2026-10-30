"""Invoice arithmetic. All amounts are integer cents."""


def invoice_total(line_items: list[dict]) -> int:
    """Sum of qty * unit_cents over the invoice's line items."""
    return sum(item["qty"] * item["unit_cents"] for item in line_items)


def late_fee(days_late: int, balance_cents: int) -> int:
    """Late fee for an overdue balance.

    Up to 30 days late: 2% of the balance, at least $5. 31-60 days: 5%.
    Over 60 days: 10%, capped at $50. Nothing if not late or nothing owed.
    """
    if days_late <= 0 or balance_cents <= 0:
        return 0
    if days_late <= 30:
        return max(500, balance_cents * 2 // 100)
    if days_late <= 60:
        return balance_cents * 5 // 100
    return min(5000, balance_cents * 10 // 100)
