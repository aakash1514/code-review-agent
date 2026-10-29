"""Refund rules for card payments. Amounts are integer cents."""
from datetime import datetime, timedelta

FULL_REFUND_WINDOW = timedelta(days=30)
RESTOCKING_FEE_PERCENT = 15


def refundable_amount(paid_cents: int, already_refunded_cents: int, requested_cents: int,
                      paid_at: datetime, now: datetime, item_returned: bool) -> int:
    """How much of `requested_cents` can be refunded right now.

    Within 30 days of payment the full request is refundable. After that only
    returned items are refundable, minus a 15% restocking fee. The result never
    exceeds what is left after earlier refunds.
    """
    remaining = paid_cents - already_refunded_cents
    if remaining <= 0 or requested_cents <= 0:
        return 0
    amount = min(requested_cents, remaining)
    if now - paid_at <= FULL_REFUND_WINDOW:
        return amount
    if not item_returned:
        return 0
    fee = amount * RESTOCKING_FEE_PERCENT // 100
    return amount - fee
