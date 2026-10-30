from app.inventory import reserve


def place_order(cart):
    for sku, qty in cart.items():
        if not reserve(sku, qty):
            return {"ok": False, "reason": f"{sku} out of stock"}
    return {"ok": True}
