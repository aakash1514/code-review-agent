"""Client for the weather provider used on the dashboard."""
import requests

BASE_URL = "https://api.weatherprovider.example/data/2.5"


class WeatherClient:
    """Wraps the weather provider so the rest of the app doesn't depend on its API."""

    def __init__(self, api_key: str, timeout: float = 5.0):
        self._api_key = api_key
        self._timeout = timeout

    def current(self, city: str) -> requests.Response:
        """Fetch the current weather for a city."""
        return requests.get(
            f"{BASE_URL}/weather",
            params={"q": city, "appid": self._api_key, "units": "metric"},
            timeout=self._timeout,
        )
