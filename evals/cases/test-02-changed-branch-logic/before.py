"""Shipping prices. Weights in kg, prices in cents."""

BASE_CENTS = 499
HEAVY_CENTS = 1299
EXPRESS_MULTIPLIER = 2


def shipping_cost(weight_kg: float, express: bool = False) -> int:
    """Price of shipping one parcel: base rate up to 5 kg, heavy rate above."""
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    cost = BASE_CENTS if weight_kg < 5 else HEAVY_CENTS
    if express:
        cost *= EXPRESS_MULTIPLIER
    return cost
