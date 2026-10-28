"""GET /healthz, and the X-Origin-Verify check that only lets CloudFront in."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.webhook import app

SECRET = "test-origin-secret"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def origin_secret(monkeypatch, settings):
    monkeypatch.setattr(settings, "ORIGIN_VERIFY_SECRET", SECRET, raising=False)


def test_healthz_is_ok_and_calls_nothing(client, monkeypatch):
    import api.webhook as webhook

    def boom(*args, **kwargs):
        raise AssertionError("healthz must not call external services")
    monkeypatch.setattr(webhook, "get_installation_token", boom)

    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert client.head("/healthz").status_code == 200


def test_without_a_secret_configured_nothing_is_blocked(client):
    # Local dev: no header needed. 401 = reached the webhook's signature check.
    assert client.post("/webhook").status_code == 401


def test_request_without_the_header_is_rejected(client, origin_secret):
    resp = client.post("/webhook")
    assert resp.status_code == 403
    assert resp.json() == {"detail": "forbidden"}
    assert client.get("/api/reviews?repo=o/r").status_code == 403


def test_request_with_a_wrong_header_is_rejected(client, origin_secret):
    assert client.post("/webhook", headers={"X-Origin-Verify": "nope"}).status_code == 403


def test_request_with_the_right_header_gets_through(client, origin_secret):
    assert client.post("/webhook", headers={"X-Origin-Verify": SECRET}).status_code == 401


def test_healthz_stays_open_for_the_alb(client, origin_secret):
    assert client.get("/healthz").status_code == 200


@pytest.fixture
def valid_ticket(monkeypatch):
    """Any ticket is accepted, so only the origin check can refuse the socket."""
    import api.webhook as webhook
    monkeypatch.setattr(webhook, "consume_ws_ticket", lambda ticket, repo: "alice")


def test_websocket_without_the_header_is_refused(client, origin_secret, valid_ticket):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/reviews?repo=o/r&ticket=x"):
            pass
    assert excinfo.value.code == 1008


def test_websocket_with_the_header_is_accepted(client, origin_secret, valid_ticket):
    with client.websocket_connect("/ws/reviews?repo=o/r&ticket=x", headers={"X-Origin-Verify": SECRET}) as ws:
        ws.close()