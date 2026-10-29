"""Order intake for the web shop."""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def process_order(order_data: dict, inventory, orders, mailer, audit) -> dict:
    """Validate, price, reserve stock for, save and confirm a new order."""
    items = order_data.get("items")
    if not items:
        raise ValueError("order has no items")
    for item in items:
        if item["qty"] <= 0:
            raise ValueError(f"bad quantity for {item['sku']}")

    subtotal = 0
    for item in items:
        product = inventory.get(item["sku"])
        if product is None:
            raise ValueError(f"unknown sku {item['sku']}")
        if product["stock"] < item["qty"]:
            if product.get("backorder_allowed"):
                if order_data.get("customer_tier") == "gold":
                    log.info("gold customer backorder for %s", item["sku"])
                else:
                    raise ValueError(f"{item['sku']} is out of stock")
            else:
                raise ValueError(f"{item['sku']} is out of stock")
        price = product["price_cents"]
        if order_data.get("customer_tier") == "gold":
            price = int(price * 0.9)
        elif order_data.get("customer_tier") == "silver":
            price = int(price * 0.95)
        subtotal += price * item["qty"]

    shipping = 0 if subtotal >= 5000 else 499
    tax = int(subtotal * 0.0825)
    total = subtotal + shipping + tax

    for item in items:
        inventory.decrement(item["sku"], item["qty"])

    order = {
        "customer_email": order_data["email"],
        "items": items,
        "total_cents": total,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    order_id = orders.insert(order)

    body = f"Thanks for your order #{order_id}. Total: ${total / 100:.2f}"
    mailer.send(order_data["email"], "Order confirmation", body)
    audit.record("order_created", order_id=order_id, total=total)
    log.info("order %s created, total %s", order_id, total)
    return {"order_id": order_id, "total_cents": total}
