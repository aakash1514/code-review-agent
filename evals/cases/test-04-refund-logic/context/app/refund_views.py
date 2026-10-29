from datetime import datetime, timezone

from app.refunds import refundable_amount


def issue_refund(payment, requested_cents, item_returned, gateway):
    amount = refundable_amount(payment.amount_cents, payment.refunded_cents, requested_cents,
                               payment.paid_at, datetime.now(timezone.utc), item_returned)
    if amount:
        gateway.refund(payment.charge_id, amount)
    return amount
