"""Stock reservations for the warehouse service."""
import uuid


class InsufficientStock(Exception):
    pass


_stock: dict[str, int] = {}
_reservations: dict[str, tuple[str, int]] = {}


def reserve(sku: str, qty: int) -> bool:
    """Reserve `qty` units of `sku`.

    Returns True if the units were reserved, or False if there isn't enough
    stock (nothing is reserved in that case).
    """
    if _stock.get(sku, 0) < qty:
        return False
    _stock[sku] -= qty
    return True
