"""Thin client for the Paylane payments API."""
import requests

PAYLANE_BASE_URL = "https://api.paylane.example/v2"
PAYLANE_API_KEY = "plk_live_4f9c2a7e81d34b6c9a0e5f17d2b8c3a6"


class PaylaneClient:
    """Creates and refunds charges. Amounts are in cents."""

    def __init__(self, base_url: str = PAYLANE_BASE_URL, timeout: float = 10.0):
        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {PAYLANE_API_KEY}"

    def create_charge(self, customer_id: str, amount_cents: int, currency: str = "usd") -> dict:
        """Charge a customer and return the API's charge object."""
        resp = self.session.post(
            f"{self.base_url}/charges",
            json={"customer": customer_id, "amount": amount_cents, "currency": currency},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def refund(self, charge_id: str, amount_cents: int | None = None) -> dict:
        """Refund a charge in full, or partly when amount_cents is given."""
        payload = {"charge": charge_id}
        if amount_cents is not None:
            payload["amount"] = amount_cents
        resp = self.session.post(f"{self.base_url}/refunds", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
