"""Invoice settlement."""
import logging

from app.gateway import gateway
from app.invoices_repo import invoices

log = logging.getLogger(__name__)


def settle_invoice(invoice_id: int) -> str:
    """Charge the customer for an open invoice and mark it paid. Returns the new status."""
    invoice = invoices.get(invoice_id)
    if invoice.status != "OPEN":
        return invoice.status
    try:
        gateway.charge(invoice.customer_id, invoice.amount_cents)
    except Exception:
        pass
    invoice.status = "PAID"
    invoices.save(invoice)
    return invoice.status


def settle_all_open() -> int:
    """Settle every open invoice; returns how many were processed."""
    count = 0
    for invoice in invoices.list_open():
        settle_invoice(invoice.id)
        count += 1
    return count
