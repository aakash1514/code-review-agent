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

_SESSIONS_DDL = """
    CREATE TABLE IF NOT EXISTS {name} (
        session_id TEXT PRIMARY KEY,
        github_login TEXT NOT NULL,
        avatar_url TEXT,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL
    )
"""


def init_session_db() -> None:
    """Separate table, same database file as job_store — call once on
    startup alongside job_store.init_db(). SQLite-backed rather than
    in-memory deliberately: uvicorn --reload restarts the process on every
    file save during development, which would otherwise silently log
    everyone out constantly."""
    _drop_user_access_token_column()
    with _connect() as conn:
        conn.execute(_SESSIONS_DDL.format(name="dashboard_sessions"))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ws_tickets (
                ticket TEXT PRIMARY KEY,
                github_login TEXT NOT NULL,
                repo TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_handoff_codes (
                code TEXT PRIMARY KEY,
                github_login TEXT NOT NULL,
                avatar_url TEXT,
                expires_at REAL NOT NULL
            )
        """)


def _drop_user_access_token_column() -> None:
    """One-off migration: older databases stored each user's GitHub OAuth
    token in dashboard_sessions.user_access_token, in plain text, and nothing
    read it after login. Rebuilds the table without that column (keeping
    every session) in one transaction, then VACUUMs so the token bytes don't
    linger in freed pages. A no-op once the column is gone, so it's safe on
    every startup. Explicit BEGIN/COMMIT on an autocommit connection because
    Python's sqlite3 doesn't wrap DDL in its implicit transactions."""
    conn = sqlite3.connect(_db_path(), timeout=10, isolation_level=None)
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(dashboard_sessions)")]
        if "user_access_token" not in cols:
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DROP TABLE IF EXISTS dashboard_sessions_new")
            conn.execute(_SESSIONS_DDL.format(name="dashboard_sessions_new"))
            conn.execute("""
                INSERT INTO dashboard_sessions_new (session_id, github_login, avatar_url, created_at, expires_at)
                SELECT session_id, github_login, avatar_url, created_at, expires_at FROM dashboard_sessions
            """)
            conn.execute("DROP TABLE dashboard_sessions")
            conn.execute("ALTER TABLE dashboard_sessions_new RENAME TO dashboard_sessions")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        print("[startup] removed dashboard_sessions.user_access_token (sessions kept)")
    finally:
        conn.close()


def create_session(github_login: str, avatar_url: str | None) -> str:
    with _connect() as conn:
        return _insert_session(conn, github_login, avatar_url)


def _insert_session(conn: sqlite3.Connection, github_login: str, avatar_url: str | None) -> str:
    session_id = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute("""
        INSERT INTO dashboard_sessions (session_id, github_login, avatar_url, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
    """, (session_id, github_login, avatar_url, now, now + SESSION_TTL_SECONDS))
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


def purge_expired_sessions() -> int:
    """get_session only deletes an expired session when someone presents it,
    so abandoned ones would otherwise pile up. Called on startup."""
    with _connect() as conn:
        return conn.execute("DELETE FROM dashboard_sessions WHERE expires_at < ?", (time.time(),)).rowcount


HANDOFF_CODE_TTL_SECONDS = 60


def purge_expired_handoff_codes() -> int:
    """Used codes are deleted when redeemed, so expired ones are the only
    leftovers. Called on startup and on every new code."""
    with _connect() as conn:
        return conn.execute("DELETE FROM auth_handoff_codes WHERE expires_at < ?", (time.time(),)).rowcount


def create_handoff_code(github_login: str, avatar_url: str | None) -> str:
    """Single-use, 60s code the OAuth callback puts in the dashboard redirect
    (?code=...) instead of a session ID, so the 7-day session never appears
    in a URL, browser history or an access log. The session itself is only
    created when the dashboard redeems the code via POST /auth/exchange."""
    purge_expired_handoff_codes()
    code = secrets.token_urlsafe(32)
    with _connect() as conn:
        conn.execute("""
            INSERT INTO auth_handoff_codes (code, github_login, avatar_url, expires_at)
            VALUES (?, ?, ?, ?)
        """, (code, github_login, avatar_url, time.time() + HANDOFF_CODE_TTL_SECONDS))
    return code


def redeem_handoff_code(code: str) -> str | None:
    """Spends a handoff code and returns a new session ID, or None if the
    code is unknown, already used or expired. Same single-use guarantee as
    consume_ws_ticket (one DELETE ... RETURNING), and the session INSERT is
    in the same transaction, so one code can never yield two sessions."""
    with _connect() as conn:
        rows = conn.execute(
            "DELETE FROM auth_handoff_codes WHERE code = ? RETURNING github_login, avatar_url, expires_at",
            (code,),
        ).fetchall()  # fetchall, not fetchone: the statement must finish before commit
        if not rows or rows[0]["expires_at"] < time.time():
            return None
        return _insert_session(conn, rows[0]["github_login"], rows[0]["avatar_url"])


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
