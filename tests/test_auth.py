import sqlite3
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api.auth as auth
import api.dashboard as dashboard
from api.webhook import app
from pipeline.job_store import get_job, set_override, try_start_job
from pipeline.session_store import create_handoff_code, create_session, create_ws_ticket

REPO = "octo/repo"
SHA = "c" * 40

READ_ROUTES = [
    ("GET", f"/api/reviews?repo={REPO}"),
    ("GET", f"/api/reviews/{SHA}?repo={REPO}"),
    ("GET", f"/api/reviews/{SHA}/files?repo={REPO}"),
    ("GET", f"/api/graph?repo={REPO}"),
    ("POST", f"/api/ws-ticket?repo={REPO}"),
]
OVERRIDE_ROUTES = ["approve", "block", "clear-override"]


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def alice():
    return {"Authorization": f"Bearer {create_session('alice', None)}"}


@pytest.fixture
def github(monkeypatch):
    """Mocks every GitHub call the auth layer makes. Defaults: public repo,
    no collaborator access, write access denied."""
    mocks = MagicMock()
    mocks.get_installation_token.return_value = "installation-token"
    mocks.is_repo_private.return_value = False
    mocks.check_repo_read_permission.return_value = False
    mocks.check_repo_write_permission.return_value = False
    monkeypatch.setattr(auth, "get_installation_token", mocks.get_installation_token)
    monkeypatch.setattr(auth, "is_repo_private", mocks.is_repo_private)
    monkeypatch.setattr(auth, "check_repo_read_permission", mocks.check_repo_read_permission)
    monkeypatch.setattr(dashboard, "get_installation_token", mocks.get_installation_token)
    monkeypatch.setattr(dashboard, "check_repo_write_permission", mocks.check_repo_write_permission)
    monkeypatch.setattr(dashboard, "set_commit_status", mocks.set_commit_status)
    return mocks


# --- No session --------------------------------------------------------------

@pytest.mark.parametrize("method, path", READ_ROUTES + [
    ("POST", f"/api/reviews/{SHA}/{action}?repo={REPO}") for action in OVERRIDE_ROUTES
] + [("GET", "/auth/me")])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-session"}, {"Authorization": "Basic x"}])
def test_routes_require_a_session(client, github, method, path, headers):
    resp = client.request(method, path, headers=headers)
    assert resp.status_code == 401
    github.is_repo_private.assert_not_called()


# --- Read access -------------------------------------------------------------

def test_public_repo_is_readable_by_any_signed_in_user(client, github, alice):
    resp = client.get(f"/api/reviews?repo={REPO}", headers=alice)
    assert resp.status_code == 200
    assert resp.json() == {"repo": REPO, "reviews": []}
    github.check_repo_read_permission.assert_not_called()


def test_private_repo_needs_collaborator_read_access(client, github, alice):
    github.is_repo_private.return_value = True

    github.check_repo_read_permission.return_value = False
    assert client.get(f"/api/reviews?repo={REPO}", headers=alice).status_code == 403

    github.check_repo_read_permission.return_value = True
    assert client.get(f"/api/reviews?repo={REPO}", headers=alice).status_code == 200
    github.check_repo_read_permission.assert_called_with(REPO, "alice", token="installation-token")


@pytest.mark.parametrize("method, path", READ_ROUTES)
def test_every_read_route_enforces_read_access(client, github, alice, method, path):
    github.is_repo_private.return_value = True
    assert client.request(method, path, headers=alice).status_code == 403


def test_allowed_result_is_cached(client, github, alice):
    github.is_repo_private.return_value = True
    github.check_repo_read_permission.return_value = True

    for _ in range(3):
        assert client.get(f"/api/reviews?repo={REPO}", headers=alice).status_code == 200

    assert github.check_repo_read_permission.call_count == 1
    assert github.is_repo_private.call_count == 1


def test_cache_is_per_user(client, github, alice):
    github.is_repo_private.return_value = True
    github.check_repo_read_permission.side_effect = lambda repo, login, token: login == "alice"
    bob = {"Authorization": f"Bearer {create_session('bob', None)}"}

    assert client.get(f"/api/reviews?repo={REPO}", headers=alice).status_code == 200
    assert client.get(f"/api/reviews?repo={REPO}", headers=bob).status_code == 403


def test_denial_is_not_cached(client, github, alice):
    github.is_repo_private.return_value = True
    github.check_repo_read_permission.return_value = False
    assert client.get(f"/api/reviews?repo={REPO}", headers=alice).status_code == 403

    github.check_repo_read_permission.return_value = True  # e.g. just added as collaborator
    assert client.get(f"/api/reviews?repo={REPO}", headers=alice).status_code == 200
    assert github.check_repo_read_permission.call_count == 2


def test_github_failure_returns_502_and_is_not_cached(client, github, alice):
    github.is_repo_private.side_effect = RuntimeError("GitHub is down")
    assert client.get(f"/api/reviews?repo={REPO}", headers=alice).status_code == 502

    github.is_repo_private.side_effect = None
    assert client.get(f"/api/reviews?repo={REPO}", headers=alice).status_code == 200


@pytest.mark.parametrize("repo", ["noslash", "a/b/c", "owner/..", "../etc", "own er/repo", "owner/re%3Fpo", ""])
def test_malformed_repo_is_rejected_before_calling_github(client, github, alice, repo):
    resp = client.get(f"/api/reviews?repo={repo}", headers=alice)
    assert resp.status_code == 400
    github.get_installation_token.assert_not_called()


# --- WebSocket tickets over the real route -----------------------------------

def test_ws_ticket_route_issues_a_ticket_that_opens_the_socket_once(client, github, alice):
    ticket = client.post(f"/api/ws-ticket?repo={REPO}", headers=alice).json()["ticket"]

    with client.websocket_connect(f"/ws/reviews?repo={REPO}&ticket={ticket}"):
        pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/reviews?repo={REPO}&ticket={ticket}"):
            pass


@pytest.mark.parametrize("query", [
    f"repo={REPO}",                               # no ticket
    f"repo={REPO}&ticket=never-issued",           # unknown
    "repo=octo/other&ticket={ticket}",            # issued for another repo
])
def test_socket_rejects_missing_unknown_or_wrong_repo_ticket(client, query):
    ticket = create_ws_ticket("alice", REPO)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/reviews?" + query.format(ticket=ticket)):
            pass


def test_socket_rejects_expired_ticket(client, fresh_db):
    ticket = create_ws_ticket("alice", REPO)
    conn = sqlite3.connect(fresh_db)
    conn.execute("UPDATE ws_tickets SET expires_at = 0")
    conn.commit()
    conn.close()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/reviews?repo={REPO}&ticket={ticket}"):
            pass


# --- OAuth callback + handoff exchange ---------------------------------------

def test_callback_redirects_with_one_time_code_and_never_stores_oauth_token(client, monkeypatch, fresh_db):
    monkeypatch.setattr(auth, "exchange_oauth_code", lambda code: "gho_FAKE_USER_TOKEN_123")
    monkeypatch.setattr(auth, "get_authenticated_user",
                        lambda token: {"login": "alice", "avatar_url": "https://a"})
    login = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    resp = client.get(f"/auth/callback?code=github-code&state={state}", follow_redirects=False)

    location = urlparse(resp.headers["location"])
    assert resp.status_code in (302, 307)
    assert f"{location.scheme}://{location.netloc}" == "http://localhost:5173"
    assert set(parse_qs(location.query)) == {"code"}  # no ?session=
    code = parse_qs(location.query)["code"][0]

    exchanged = client.post("/auth/exchange", json={"code": code})
    assert exchanged.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {exchanged.json()['session_token']}"})
    assert me.json() == {"login": "alice", "avatar_url": "https://a"}

    conn = sqlite3.connect(fresh_db)
    dump = "\n".join(conn.iterdump())
    conn.close()
    assert "gho_FAKE_USER_TOKEN_123" not in dump


def test_callback_rejects_unknown_state(client):
    assert client.get("/auth/callback?code=x&state=forged", follow_redirects=False).status_code == 400


def test_exchange_code_works_once(client):
    code = create_handoff_code("alice", None)
    assert client.post("/auth/exchange", json={"code": code}).status_code == 200
    assert client.post("/auth/exchange", json={"code": code}).status_code == 401


def test_exchange_rejects_expired_and_unknown_codes(client, fresh_db):
    code = create_handoff_code("alice", None)
    conn = sqlite3.connect(fresh_db)
    conn.execute("UPDATE auth_handoff_codes SET expires_at = 0")
    conn.commit()
    conn.close()
    assert client.post("/auth/exchange", json={"code": code}).status_code == 401
    assert client.post("/auth/exchange", json={"code": "never-issued"}).status_code == 401


def test_exchange_does_not_accept_code_in_url(client):
    code = create_handoff_code("alice", None)
    assert client.post(f"/auth/exchange?code={code}").status_code == 422


def test_logout_ends_the_session(client, alice):
    assert client.get("/auth/me", headers=alice).status_code == 200
    client.post("/auth/logout", headers=alice)
    assert client.get("/auth/me", headers=alice).status_code == 401


# --- Override routes need write access ---------------------------------------

@pytest.mark.parametrize("action", OVERRIDE_ROUTES)
def test_override_without_write_access_is_403_and_changes_nothing(client, github, alice, action):
    try_start_job(REPO, 1, SHA)
    set_override(REPO, SHA, "blocked", "maintainer")
    github.check_repo_write_permission.return_value = False

    resp = client.post(f"/api/reviews/{SHA}/{action}?repo={REPO}", headers=alice)

    assert resp.status_code == 403
    github.set_commit_status.assert_not_called()
    assert get_job(REPO, SHA)["override_action"] == "blocked"


@pytest.mark.parametrize("action, state, recorded", [
    ("approve", "success", "approved"),
    ("block", "failure", "blocked"),
])
def test_override_with_write_access_sets_status_and_records_it(client, github, alice, action, state, recorded):
    try_start_job(REPO, 1, SHA)
    github.check_repo_write_permission.return_value = True

    resp = client.post(f"/api/reviews/{SHA}/{action}?repo={REPO}", headers=alice)

    assert resp.status_code == 200
    github.check_repo_write_permission.assert_called_once_with(REPO, "alice", token="installation-token")
    assert github.set_commit_status.call_args.args[:3] == (REPO, SHA, state)
    job = get_job(REPO, SHA)
    assert (job["override_action"], job["override_by"]) == (recorded, "alice")


def test_clear_override_with_write_access(client, github, alice):
    try_start_job(REPO, 1, SHA)
    set_override(REPO, SHA, "approved", "alice")
    github.check_repo_write_permission.return_value = True

    assert client.post(f"/api/reviews/{SHA}/clear-override?repo={REPO}", headers=alice).status_code == 200
    assert get_job(REPO, SHA)["override_action"] is None


@pytest.mark.parametrize("action", OVERRIDE_ROUTES)
def test_override_on_unknown_review_is_404(client, github, alice, action):
    github.check_repo_write_permission.return_value = True
    assert client.post(f"/api/reviews/{SHA}/{action}?repo={REPO}", headers=alice).status_code == 404
    github.set_commit_status.assert_not_called()
