"""Syncs product prices from the supplier feed."""
import logging

from app.feed import fetch_prices
from app.repos import products

log = logging.getLogger(__name__)


def sync_prices(supplier_id: str) -> int:
    """Update stored prices from the supplier's feed; returns how many changed."""
    changed = 0
    for sku, price_cents in fetch_prices(supplier_id).items():
        product = products.get(sku)
        if product is None or product.price_cents == price_cents:
            continue
        log.debug("price change for %s: %s -> %s", sku, product.price_cents, price_cents)
        products.update_price(sku, price_cents)
        changed += 1
    log.info("supplier %s: %d price(s) changed", supplier_id, changed)
    return changed
