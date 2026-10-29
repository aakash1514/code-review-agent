"""Invoices for completed orders."""
from app.orders import get_order


def build_invoice(order_id):
    order = get_order(order_id)
    lines = [{"sku": i["sku"], "amount": i["price_cents"] * i["qty"]} for i in order["items"]]
    return {"order_id": order_id, "lines": lines, "total": sum(l["amount"] for l in lines)}
