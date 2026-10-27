import sqlite3
import time

import pytest

from pipeline import job_store
from pipeline.job_store import (
    clear_override, get_job, is_stale_sha, save_result, set_override, sweep_stuck_jobs,
    try_start_job, update_status,
)

REPO = "octo/repo"


def _age(db_file, commit_sha: str, seconds: float) -> None:
    conn = sqlite3.connect(db_file)
    conn.execute("UPDATE review_jobs SET updated_at = ? WHERE commit_sha = ?", (time.time() - seconds, commit_sha))
    conn.commit()
    conn.close()


def test_try_start_job_creates_a_pending_job():
    assert try_start_job(REPO, 1, "sha1") is True
    job = get_job(REPO, "sha1")
    assert (job["status"], job["pr_number"]) == ("pending", 1)


@pytest.mark.parametrize("status", ["pending", "running", "complete"])
def test_try_start_job_refuses_an_active_or_finished_commit(status):
    try_start_job(REPO, 1, "sha1")
    if status != "pending":
        update_status(REPO, "sha1", status)

    assert try_start_job(REPO, 1, "sha1") is False
    assert get_job(REPO, "sha1")["status"] == status


def test_try_start_job_retries_a_failed_commit():
    try_start_job(REPO, 1, "sha1")
    update_status(REPO, "sha1", "failed", error="boom")

    assert try_start_job(REPO, 1, "sha1") is True
    job = get_job(REPO, "sha1")
    assert (job["status"], job["error"]) == ("pending", None)


def test_same_sha_in_another_repo_is_independent():
    assert try_start_job(REPO, 1, "sha1") is True
    assert try_start_job("octo/other", 1, "sha1") is True


def test_newer_commit_marks_older_one_stale():
    try_start_job(REPO, 1, "old")
    assert is_stale_sha(REPO, 1, "old") is False
    try_start_job(REPO, 1, "new")
    assert is_stale_sha(REPO, 1, "old") is True
    assert is_stale_sha(REPO, 1, "new") is False


def test_sweep_marks_only_old_pending_and_running_jobs_failed(fresh_db):
    for sha in ("old-pending", "old-running", "old-complete", "fresh-running"):
        try_start_job(REPO, 1, sha)
    update_status(REPO, "old-running", "running")
    update_status(REPO, "fresh-running", "running")
    save_result(REPO, "old-complete", "{}")
    for sha in ("old-pending", "old-running", "old-complete"):
        _age(fresh_db, sha, 3600)

    assert sweep_stuck_jobs(stale_after_seconds=900) == 2

    assert get_job(REPO, "old-pending")["status"] == "failed"
    assert get_job(REPO, "old-running")["status"] == "failed"
    assert "swept" in get_job(REPO, "old-running")["error"]
    assert get_job(REPO, "old-complete")["status"] == "complete"
    assert get_job(REPO, "fresh-running")["status"] == "running"
    assert sweep_stuck_jobs(stale_after_seconds=900) == 0


def test_set_override_records_decision_without_touching_status():
    try_start_job(REPO, 1, "sha1")
    save_result(REPO, "sha1", '{"critical_count": 1}')

    set_override(REPO, "sha1", "approved", "alice")

    job = get_job(REPO, "sha1")
    assert (job["override_action"], job["override_by"]) == ("approved", "alice")
    assert job["override_at"] is not None
    assert (job["status"], job["result_json"]) == ("complete", '{"critical_count": 1}')


def test_set_override_keeps_only_the_latest_decision():
    try_start_job(REPO, 1, "sha1")
    set_override(REPO, "sha1", "approved", "alice")
    set_override(REPO, "sha1", "blocked", "bob")
    job = get_job(REPO, "sha1")
    assert (job["override_action"], job["override_by"]) == ("blocked", "bob")


def test_set_override_rejects_unknown_action():
    try_start_job(REPO, 1, "sha1")
    with pytest.raises(AssertionError):
        set_override(REPO, "sha1", "merged", "alice")


def test_clear_override_removes_the_decision():
    try_start_job(REPO, 1, "sha1")
    set_override(REPO, "sha1", "blocked", "bob")

    clear_override(REPO, "sha1")

    job = get_job(REPO, "sha1")
    assert (job["override_action"], job["override_by"], job["override_at"]) == (None, None, None)
    assert job["status"] == "pending"


def test_init_db_is_idempotent():
    try_start_job(REPO, 1, "sha1")
    job_store.init_db()
    job_store.init_db()
    assert get_job(REPO, "sha1") is not None
