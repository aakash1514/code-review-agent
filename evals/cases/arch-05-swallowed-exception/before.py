"""Invoice settlement."""
import logging

from app.gateway import PaymentDeclined, gateway
from app.invoices_repo import invoices

log = logging.getLogger(__name__)


def settle_invoice(invoice_id: int) -> str:
    """Charge the customer for an open invoice and mark it paid. Returns the new status."""
    invoice = invoices.get(invoice_id)
    if invoice.status != "OPEN":
        return invoice.status
    try:
        gateway.charge(invoice.customer_id, invoice.amount_cents)
    except PaymentDeclined:
        invoice.status = "PAYMENT_FAILED"
        invoices.save(invoice)
        return invoice.status
    invoice.status = "PAID"
    invoices.save(invoice)
    return invoice.status
