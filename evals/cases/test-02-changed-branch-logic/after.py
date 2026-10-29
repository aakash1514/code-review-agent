"""Shipping prices. Weights in kg, prices in cents."""

BASE_CENTS = 499
HEAVY_CENTS = 1299
FREIGHT_CENTS_PER_KG = 85
EXPRESS_MULTIPLIER = 2


def shipping_cost(weight_kg: float, express: bool = False) -> int:
    """Price of shipping one parcel: base rate up to and including 5 kg, heavy rate
    up to 30 kg, and a per-kg freight rate above that (no express for freight)."""
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    if weight_kg > 30:
        if express:
            raise ValueError("express is not available for freight")
        return int(weight_kg * FREIGHT_CENTS_PER_KG)
    cost = BASE_CENTS if weight_kg <= 5 else HEAVY_CENTS
    if express:
        cost *= EXPRESS_MULTIPLIER
    return cost
