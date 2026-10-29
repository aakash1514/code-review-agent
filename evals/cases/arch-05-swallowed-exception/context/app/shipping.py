from app.invoices_repo import invoices
from app.settlement import settle_invoice


def ship_if_paid(invoice_id, warehouse):
    if settle_invoice(invoice_id) == "PAID":
        warehouse.release(invoices.get(invoice_id).order_id)
