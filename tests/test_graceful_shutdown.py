"""Reviews abandoned by a shutdown or crash end as 'interrupted', and their
GitHub commit status is turned from pending into error."""
import pytest
from fastapi.testclient import TestClient

import api.webhook as webhook
from api.webhook import INTERRUPTED_STATUS_TEXT, app
from pipeline.hitl_gate import DEFAULT_STATUS_CONTEXT
from pipeline.job_store import get_job, interrupt_active_jobs, save_result, try_start_job, update_status

REPO = "octo/repo"


@pytest.fixture
def github(monkeypatch):
    """Records commit-status calls instead of calling GitHub."""
    calls = []
    monkeypatch.setattr(webhook, "get_installation_token", lambda: "test-token")
    monkeypatch.setattr(webhook, "set_commit_status",
                        lambda repo, sha, state, description, context, token: calls.append(
                            (repo, sha, state, description, context)))
    return calls


def _job(sha: str, status: str, pr: int = 1) -> None:
    assert try_start_job(REPO, pr, sha)
    if status == "running":
        update_status(REPO, sha, "running")
    elif status == "complete":
        save_result(REPO, sha, "{}")
    elif status == "failed":
        update_status(REPO, sha, "failed", error="boom")


def test_interrupt_marks_only_pending_and_running_jobs():
    _job("p1", "pending", pr=1)
    _job("r1", "running", pr=2)
    _job("c1", "complete", pr=3)
    _job("f1", "failed", pr=4)

    jobs = interrupt_active_jobs("test reason")

    assert sorted(j["commit_sha"] for j in jobs) == ["p1", "r1"]
    assert {j["repo"] for j in jobs} == {REPO}
    assert get_job(REPO, "p1")["status"] == "interrupted"
    assert get_job(REPO, "r1")["error"] == "test reason"
    assert get_job(REPO, "c1")["status"] == "complete"
    assert get_job(REPO, "f1")["status"] == "failed"
    assert interrupt_active_jobs("again") == []


def test_an_interrupted_review_can_be_retried_by_a_redelivery():
    _job("r1", "running")
    interrupt_active_jobs("test")
    assert try_start_job(REPO, 1, "r1") is True
    assert get_job(REPO, "r1")["status"] == "pending"


def test_shutdown_interrupts_in_flight_reviews_and_fixes_github_status(github):
    with TestClient(app):
        _job("r1", "running")  # started after startup, still running at shutdown
    job = get_job(REPO, "r1")
    assert job["status"] == "interrupted"
    assert "shutdown" in job["error"]
    assert github == [(REPO, "r1", "error", INTERRUPTED_STATUS_TEXT, DEFAULT_STATUS_CONTEXT)]


def test_startup_interrupts_reviews_left_behind_by_a_crash(github):
    _job("r1", "running")  # the previous process died without shutting down
    with TestClient(app):
        job = get_job(REPO, "r1")
        assert job["status"] == "interrupted"
        assert "restarted" in job["error"]
        assert github == [(REPO, "r1", "error", INTERRUPTED_STATUS_TEXT, DEFAULT_STATUS_CONTEXT)]


def test_github_being_down_does_not_break_shutdown(monkeypatch):
    def no_github():
        raise ConnectionError("github unreachable")
    monkeypatch.setattr(webhook, "get_installation_token", no_github)
    with TestClient(app):
        _job("r1", "running")
    assert get_job(REPO, "r1")["status"] == "interrupted"


def test_clean_start_and_stop_touch_nothing(github):
    _job("c1", "complete")
    with TestClient(app):
        pass
    assert github == []
    assert get_job(REPO, "c1")["status"] == "complete"