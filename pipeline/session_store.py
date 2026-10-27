import sqlite3
import time
import secrets
from contextlib import contextmanager
from config import settings


def _db_path() -> str:
    url = settings.DATABASE_URL
    prefix = "sqlite:///"
    if url.startswith(prefix):
        return url[len(prefix):]
    return url


@contextmanager
def _connect():
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


SESSION_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def init_session_db() -> None:
    """Separate table, same database file as job_store — call once on
    startup alongside job_store.init_db(). SQLite-backed rather than
    in-memory deliberately: uvicorn --reload restarts the process on every
    file save during development, which would otherwise silently log
    everyone out constantly."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_sessions (
                session_id TEXT PRIMARY KEY,
                github_login TEXT NOT NULL,
                avatar_url TEXT,
                user_access_token TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ws_tickets (
                ticket TEXT PRIMARY KEY,
                github_login TEXT NOT NULL,
                repo TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
        """)


def create_session(github_login: str, avatar_url: str, user_access_token: str) -> str:
    session_id = secrets.token_urlsafe(32)
    now = time.time()
    with _connect() as conn:
        conn.execute("""
            INSERT INTO dashboard_sessions (session_id, github_login, avatar_url, user_access_token, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, github_login, avatar_url, user_access_token, now, now + SESSION_TTL_SECONDS))
    return session_id


def get_session(session_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("""
            SELECT * FROM dashboard_sessions WHERE session_id = ?
        """, (session_id,)).fetchone()
        if row is None:
            return None
        session = dict(row)
        if session["expires_at"] < time.time():
            conn.execute("DELETE FROM dashboard_sessions WHERE session_id = ?", (session_id,))
            return None
        return session


def delete_session(session_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM dashboard_sessions WHERE session_id = ?", (session_id,))


WS_TICKET_TTL_SECONDS = 30


def purge_expired_ws_tickets() -> int:
    """Used tickets are deleted the moment they're consumed, so expired ones
    are the only leftovers. Called on startup and on every new ticket."""
    with _connect() as conn:
        return conn.execute("DELETE FROM ws_tickets WHERE expires_at < ?", (time.time(),)).rowcount


def create_ws_ticket(github_login: str, repo: str) -> str:
    """Single-use, ~30s credential for opening /ws/reviews. Browsers can't
    set an Authorization header on a WebSocket, and putting the 7-day
    session ID in the URL would leak it into ALB/CloudFront access logs —
    a ticket that's already spent by the time anyone reads a log is fine.
    Lives in SQLite next to sessions so any worker can redeem it."""
    purge_expired_ws_tickets()
    ticket = secrets.token_urlsafe(32)
    with _connect() as conn:
        conn.execute("""
            INSERT INTO ws_tickets (ticket, github_login, repo, expires_at)
            VALUES (?, ?, ?, ?)
        """, (ticket, github_login, repo, time.time() + WS_TICKET_TTL_SECONDS))
    return ticket


def consume_ws_ticket(ticket: str, repo: str) -> str | None:
    """Redeems a ticket exactly once. Returns the GitHub login it was issued
    to, or None if it's unknown, already used, expired, or for another repo.
    A single DELETE ... RETURNING (SQLite 3.35+) is the single-use guarantee:
    SQLite serialises writers, so of two concurrent redemptions only one
    DELETE can match the row and get it back; the other gets nothing.
    The ticket is spent even if the checks below then reject it."""
    with _connect() as conn:
        rows = conn.execute(
            "DELETE FROM ws_tickets WHERE ticket = ? RETURNING github_login, repo, expires_at",
            (ticket,),
        ).fetchall()  # fetchall, not fetchone: the statement must finish before commit
        if not rows:
            return None
        row = rows[0]
        if row["expires_at"] < time.time() or row["repo"].lower() != repo.lower():
            return None
        return row["github_login"]
