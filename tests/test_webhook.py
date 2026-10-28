import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import api.webhook as webhook
from pipeline.job_store import get_job


def _sign(body: bytes, secret: str = "test-webhook-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- _verify_signature -------------------------------------------------------

def test_valid_signature_is_accepted():
    body = b'{"zen": "hi"}'
    assert webhook._verify_signature(body, _sign(body)) is True


def test_wrong_signature_is_rejected():
    body = b'{"zen": "hi"}'
    assert webhook._verify_signature(body, _sign(body, secret="some-other-secret")) is False
    assert webhook._verify_signature(b'{"zen": "tampered"}', _sign(body)) is False


def test_missing_signature_header_is_rejected():
    assert webhook._verify_signature(b"{}", None) is False
    assert webhook._verify_signature(b"{}", "") is False


def test_signature_without_sha256_prefix_is_rejected():
    body = b"{}"
    bare_hex = _sign(body).removeprefix("sha256=")
    assert webhook._verify_signature(body, bare_hex) is False
    assert webhook._verify_signature(body, "sha1=" + bare_hex) is False


# --- /webhook route ----------------------------------------------------------

@pytest.fixture
def calls(monkeypatch):
    """Replaces both background jobs with recorders so nothing really runs."""
    recorded = {"review": [], "reindex": []}
    monkeypatch.setattr(webhook, "run_review_pipeline", lambda *a: recorded["review"].append(a))
    monkeypatch.setattr(webhook, "_run_reindex_task", lambda *a: recorded["reindex"].append(a))
    return recorded


@pytest.fixture
def client():
    with TestClient(webhook.app) as c:
        yield c


def _deliver(client, event: str, payload: dict, signature: str | None = "auto"):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if signature == "auto":
        signature = _sign(body)
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/webhook", content=body, headers=headers)


def _push(ref: str, after: str = "a" * 40) -> dict:
    return {"ref": ref, "after": after,
            "repository": {"full_name": "octo/repo", "default_branch": "main"}}


def _pull_request(sha: str = "b" * 40, action: str = "opened") -> dict:
    return {"action": action, "repository": {"full_name": "octo/repo"},
            "pull_request": {"number": 7, "head": {"sha": sha}}}


def test_ping_returns_pong(client, calls):
    resp = _deliver(client, "ping", {"zen": "Keep it logically awesome."})
    assert resp.status_code == 200
    assert resp.json() == {"status": "pong"}


def test_unsigned_delivery_is_rejected(client, calls):
    resp = _deliver(client, "pull_request", _pull_request(), signature=None)
    assert resp.status_code == 401
    assert calls == {"review": [], "reindex": []}


def test_push_to_non_default_branch_is_ignored(client, calls):
    resp = _deliver(client, "push", _push("refs/heads/feature/x"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert calls["reindex"] == []


def test_push_to_default_branch_schedules_reindex(client, calls):
    resp = _deliver(client, "push", _push("refs/heads/main"))
    assert resp.json()["status"] == "reindex_scheduled"
    assert calls["reindex"] == [("octo/repo", "a" * 40)]


def test_branch_deletion_push_is_ignored(client, calls):
    resp = _deliver(client, "push", _push("refs/heads/main", after="0" * 40))
    assert resp.json()["status"] == "ignored"
    assert calls["reindex"] == []


def test_duplicate_pr_delivery_runs_the_pipeline_once(client, calls):
    first = _deliver(client, "pull_request", _pull_request())
    second = _deliver(client, "pull_request", _pull_request())

    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert calls["review"] == [("octo/repo", 7, "b" * 40)]
    assert get_job("octo/repo", "b" * 40)["status"] == "pending"


def test_unhandled_pr_action_is_ignored(client, calls):
    resp = _deliver(client, "pull_request", _pull_request(action="closed"))
    assert resp.json()["status"] == "ignored"
    assert calls["review"] == []
