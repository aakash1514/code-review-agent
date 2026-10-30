"""Stock reservations for the warehouse service."""
import uuid


class InsufficientStock(Exception):
    pass


_stock: dict[str, int] = {}
_reservations: dict[str, tuple[str, int]] = {}


def reserve(sku: str, qty: int) -> str:
    """Reserve `qty` units of `sku`.

    Returns True if the units were reserved, or False if there isn't enough
    stock (nothing is reserved in that case).
    """
    available = _stock.get(sku, 0)
    if available < qty:
        raise InsufficientStock(f"{sku}: wanted {qty}, have {available}")
    _stock[sku] = available - qty
    reservation_id = uuid.uuid4().hex
    _reservations[reservation_id] = (sku, qty)
    return reservation_id


def release(reservation_id: str) -> None:
    """Return a reservation's units to stock. Unknown ids are ignored."""
    sku, qty = _reservations.pop(reservation_id, (None, 0))
    if sku is not None:
        _stock[sku] = _stock.get(sku, 0) + qty
