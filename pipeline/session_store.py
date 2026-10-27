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