"""Invoice arithmetic. All amounts are integer cents."""


def invoice_total(line_items: list[dict]) -> int:
    """Sum of qty * unit_cents over the invoice's line items."""
    return sum(item["qty"] * item["unit_cents"] for item in line_items)
