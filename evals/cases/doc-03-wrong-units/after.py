"""Small HTTP helper shared by the integration clients."""
import requests

DEFAULT_HEADERS = {"User-Agent": "acme-integrations/2.3"}


def fetch_json(url: str, params: dict | None = None, timeout: int = 30_000) -> dict:
    """GET `url` and return the decoded JSON body.

    Args:
        url: absolute URL to fetch.
        params: optional query parameters.
        timeout: how long to wait for the server, in seconds.

    Raises requests.HTTPError for non-2xx responses.
    """
    resp = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=timeout / 1000)
    resp.raise_for_status()
    return resp.json()
