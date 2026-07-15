import json
import sqlite3
import time
from contextlib import contextmanager
from config import settings

# Job lifecycle: pending -> running -> complete | failed
VALID_STATUSES = {"pending", "running", "complete", "failed"}


def _db_path() -> str:
    """
    settings.DATABASE_URL is stored as a SQLAlchemy-style URL
    ("sqlite:///./review_agent.db") even though we're not using SQLAlchemy —
    kept that format since it's already in .env from Phase 1 and reads
    clearly. Just strip the scheme prefix to get a plain file path.
    """
    url = settings.DATABASE_URL
    prefix = "sqlite:///"
    if url.startswith(prefix):
        return url[len(prefix):]
    return url  # already a bare path, fall back gracefully


@contextmanager
def _connect():
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL mode lets one writer and multiple readers coexist without locking
    # errors — relevant once FastAPI's background tasks are hitting this
    # from separate threads (Phase 6), not just single-script test runs.
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Creates both tables if they don't exist. Safe to call on every app
    startup — this is also where Phase 6's webhook server should call
    sweep_stuck_jobs() right after, per gap #1's "sweep stuck runs on
    startup" requirement."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                pr_number INTEGER NOT NULL,
                commit_sha TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(repo, commit_sha)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pr_latest_sha (
                repo TEXT NOT NULL,
                pr_number INTEGER NOT NULL,
                latest_sha TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (repo, pr_number)
            )
        """)


def create_job(repo: str, pr_number: int, commit_sha: str) -> None:
    """
    Called as the FIRST thing the webhook handler does, before any LLM
    calls — this is gap #1's core requirement: job status must be durable
    from the moment a webhook is received, not just held in memory while
    BackgroundTasks runs. Also updates pr_latest_sha, which is what
    is_stale_sha() below checks against.

    Uses INSERT OR REPLACE so re-delivery of the same webhook (GitHub does
    retry on timeout) doesn't fail on the UNIQUE(repo, commit_sha)
    constraint — it just resets that job back to pending.
    """
    now = time.time()
    with _connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO review_jobs (repo, pr_number, commit_sha, status, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?)
        """, (repo, pr_number, commit_sha, now, now))

        conn.execute("""
            INSERT INTO pr_latest_sha (repo, pr_number, latest_sha, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(repo, pr_number) DO UPDATE SET latest_sha=excluded.latest_sha, updated_at=excluded.updated_at
        """, (repo, pr_number, commit_sha, now))


def list_jobs(repo: str, limit: int = 20) -> list[dict]:
    """Most recent jobs for a repo, newest first — powers the dashboard's
    review list view."""
    with _connect() as conn:
        rows = conn.execute("""
            SELECT * FROM review_jobs WHERE repo = ?
            ORDER BY created_at DESC LIMIT ?
        """, (repo, limit)).fetchall()
        return [dict(r) for r in rows]

def update_status(repo: str, commit_sha: str, status: str, error: str | None = None) -> None:
    assert status in VALID_STATUSES, f"invalid status: {status}"
    with _connect() as conn:
        conn.execute("""
            UPDATE review_jobs SET status = ?, error = ?, updated_at = ?
            WHERE repo = ? AND commit_sha = ?
        """, (status, error, time.time(), repo, commit_sha))


def save_result(repo: str, commit_sha: str, review_report_json: str) -> None:
    """
    review_report_json should be review.model_dump_json() from a
    models.findings.ReviewReport — stored as-is (this module doesn't import
    that model, keeping job_store decoupled from the Pydantic schema).
    Marks the job complete in the same write.
    """
    with _connect() as conn:
        conn.execute("""
            UPDATE review_jobs SET status = 'complete', result_json = ?, updated_at = ?
            WHERE repo = ? AND commit_sha = ?
        """, (review_report_json, time.time(), repo, commit_sha))


def get_job(repo: str, commit_sha: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("""
            SELECT * FROM review_jobs WHERE repo = ? AND commit_sha = ?
        """, (repo, commit_sha)).fetchone()
        return dict(row) if row else None


def is_stale_sha(repo: str, pr_number: int, commit_sha: str) -> bool:
    """
    Gap #3: returns True if a NEWER commit has been pushed to this PR since
    `commit_sha` started its review — meaning this job's result should be
    discarded rather than posted, since a fresher run either is already
    running or will be triggered separately for the new commit. Call this
    right before posting the GitHub comment/status, not just at job start,
    since the race can happen at any point during the (multi-second) agent
    pipeline run.
    """
    with _connect() as conn:
        row = conn.execute("""
            SELECT latest_sha FROM pr_latest_sha WHERE repo = ? AND pr_number = ?
        """, (repo, pr_number)).fetchone()
        if row is None:
            return False  # no record — treat as not stale, shouldn't normally happen
        return row["latest_sha"] != commit_sha


def sweep_stuck_jobs(stale_after_seconds: float = 900.0) -> int:
    """
    Gap #1's "sweep stuck runs on startup": any job left in 'pending' or
    'running' past stale_after_seconds (default 15 min — generous given
    agents finish in seconds, but allows for Groq rate-limit backoff chains)
    almost certainly died with the previous process (e.g. a Railway restart
    mid-pipeline) rather than being genuinely still in progress. Marks them
    'failed' so they don't sit in limbo forever and so a stuck PENDING
    GitHub status check doesn't block a merge indefinitely.

    Call this once, right after init_db(), on every app startup.
    Returns the number of jobs swept, for startup logging.
    """
    cutoff = time.time() - stale_after_seconds
    with _connect() as conn:
        cursor = conn.execute("""
            UPDATE review_jobs
            SET status = 'failed', error = 'swept: stuck past staleness threshold on startup', updated_at = ?
            WHERE status IN ('pending', 'running') AND updated_at < ?
        """, (time.time(), cutoff))
        return cursor.rowcount