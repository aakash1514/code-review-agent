"""Order lookups and state changes."""
from app.billing import build_invoice
from app.db import fetch_one, execute


def get_order(order_id: int) -> dict:
    """Return the order row as a dict, or raise LookupError."""
    row = fetch_one("orders", order_id)
    if row is None:
        raise LookupError(f"order {order_id} not found")
    return row


def cancel_order(order_id: int) -> None:
    """Mark an open order as cancelled."""
    order = get_order(order_id)
    if order["status"] != "open":
        raise ValueError("only open orders can be cancelled")
    execute("update_status", order_id, "cancelled")


def complete_order(order_id: int) -> dict:
    """Mark an open order as completed and return its invoice."""
    order = get_order(order_id)
    if order["status"] != "open":
        raise ValueError("only open orders can be completed")
    execute("update_status", order_id, "completed")
    return build_invoice(order_id)
