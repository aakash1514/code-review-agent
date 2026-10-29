"""Cookie-backed shopping cart for anonymous visitors."""
import base64
import pickle

from flask import request

COOKIE_NAME = "cart"


def load_cart() -> dict:
    """Read the cart from the request cookie; a missing or bad cookie gives an empty cart.

    The cart is pickled because items carry Decimal prices and dates, which JSON
    can't round-trip.
    """
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return {"items": []}
    try:
        return pickle.loads(base64.urlsafe_b64decode(raw))
    except (ValueError, pickle.UnpicklingError):
        return {"items": []}


def save_cart(response, cart: dict):
    """Store the cart in a cookie on the response and return the response."""
    encoded = base64.urlsafe_b64encode(pickle.dumps(cart)).decode()
    response.set_cookie(COOKIE_NAME, encoded, httponly=True, samesite="Lax")
    return response
