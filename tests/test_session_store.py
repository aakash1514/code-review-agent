import sqlite3
import threading
import time

import pytest

from pipeline import session_store
from pipeline.session_store import (
    consume_ws_ticket, create_handoff_code, create_session, create_ws_ticket, get_session,
    init_session_db, purge_expired_handoff_codes, purge_expired_sessions, redeem_handoff_code,
)


def _expire(db_file, table: str, key_col: str, key: str) -> None:
    conn = sqlite3.connect(db_file)
    conn.execute(f"UPDATE {table} SET expires_at = ? WHERE {key_col} = ?", (time.time() - 1, key))
    conn.commit()
    conn.close()


def _race(fn, arg, threads: int = 8) -> list:
    """Calls fn(arg) from several threads released at the same instant."""
    barrier = threading.Barrier(threads)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        result = fn(arg)
        with lock:
            results.append(result)

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    return results


# --- WebSocket tickets -------------------------------------------------------

def test_ticket_works_once():
    ticket = create_ws_ticket("alice", "octo/repo")
    assert consume_ws_ticket(ticket, "octo/repo") == "alice"
    assert consume_ws_ticket(ticket, "octo/repo") is None


def test_ticket_repo_match_is_case_insensitive():
    ticket = create_ws_ticket("alice", "Octo/Repo")
    assert consume_ws_ticket(ticket, "octo/repo") == "alice"


def test_expired_ticket_is_rejected(fresh_db):
    ticket = create_ws_ticket("alice", "octo/repo")
    _expire(fresh_db, "ws_tickets", "ticket", ticket)
    assert consume_ws_ticket(ticket, "octo/repo") is None


def test_ticket_for_another_repo_is_rejected_and_spent():
    ticket = create_ws_ticket("alice", "octo/repo")
    assert consume_ws_ticket(ticket, "octo/other") is None
    assert consume_ws_ticket(ticket, "octo/repo") is None


def test_unknown_ticket_is_rejected():
    assert consume_ws_ticket("never-issued", "octo/repo") is None


def test_concurrent_ticket_use_has_exactly_one_winner():
    for _ in range(10):
        ticket = create_ws_ticket("alice", "octo/repo")
        results = _race(lambda t: consume_ws_ticket(t, "octo/repo"), ticket)
        assert results.count("alice") == 1
        assert results.count(None) == len(results) - 1


# --- Login handoff codes -----------------------------------------------------

def test_handoff_code_yields_a_session_once():
    code = create_handoff_code("alice", "https://avatars.example/alice")

    session_id = redeem_handoff_code(code)

    session = get_session(session_id)
    assert (session["github_login"], session["avatar_url"]) == ("alice", "https://avatars.example/alice")
    assert redeem_handoff_code(code) is None


def test_expired_handoff_code_is_rejected(fresh_db):
    code = create_handoff_code("alice", None)
    _expire(fresh_db, "auth_handoff_codes", "code", code)
    assert redeem_handoff_code(code) is None


def test_unknown_handoff_code_is_rejected():
    assert redeem_handoff_code("never-issued") is None


def test_concurrent_handoff_redemption_creates_exactly_one_session(fresh_db):
    code = create_handoff_code("alice", None)

    results = _race(redeem_handoff_code, code)

    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    conn = sqlite3.connect(fresh_db)
    assert conn.execute("SELECT COUNT(*) FROM dashboard_sessions").fetchone()[0] == 1
    conn.close()


# --- Sessions and purges -----------------------------------------------------

def test_expired_session_is_rejected_and_deleted(fresh_db):
    session_id = create_session("alice", None)
    _expire(fresh_db, "dashboard_sessions", "session_id", session_id)
    assert get_session(session_id) is None


def test_purges_remove_only_expired_rows(fresh_db):
    keep_session, old_session = create_session("alice", None), create_session("bob", None)
    keep_code, old_code = create_handoff_code("alice", None), create_handoff_code("bob", None)
    _expire(fresh_db, "dashboard_sessions", "session_id", old_session)
    _expire(fresh_db, "auth_handoff_codes", "code", old_code)

    assert purge_expired_sessions() == 1
    assert purge_expired_handoff_codes() == 1
    assert get_session(keep_session)["github_login"] == "alice"
    assert redeem_handoff_code(keep_code) is not None


# --- user_access_token migration ---------------------------------------------

def _legacy_db(path):
    """A database shaped like the pre-migration schema, with real-looking rows."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE dashboard_sessions (
            session_id TEXT PRIMARY KEY, github_login TEXT NOT NULL, avatar_url TEXT,
            user_access_token TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL)
    """)
    now = time.time()
    conn.executemany("INSERT INTO dashboard_sessions VALUES (?, ?, ?, ?, ?, ?)", [
        ("sess-a", "alice", "https://a", "LEGACY-TOKEN-AAAA-1234567890", now, now + 3600),
        ("sess-b", "bob", None, "LEGACY-TOKEN-BBBB-1234567890", now, now + 3600),
    ])
    conn.execute("CREATE TABLE review_jobs (id INTEGER PRIMARY KEY, repo TEXT, commit_sha TEXT)")
    conn.executemany("INSERT INTO review_jobs (repo, commit_sha) VALUES (?, ?)", [("o/r", "1"), ("o/r", "2")])
    conn.commit()
    conn.close()


def test_migration_drops_token_column_and_keeps_sessions(tmp_path, monkeypatch, settings):
    db = tmp_path / "legacy.db"
    _legacy_db(db)
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db}")

    init_session_db()
    init_session_db()  # must be safe to run again

    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(dashboard_sessions)")]
    assert cols == ["session_id", "github_login", "avatar_url", "created_at", "expires_at"]
    assert conn.execute("SELECT COUNT(*) FROM review_jobs").fetchone()[0] == 2
    conn.close()
    assert get_session("sess-a")["github_login"] == "alice"
    assert get_session("sess-b")["github_login"] == "bob"
    # VACUUM ran, so the old token bytes are gone from the file, not just unreferenced.
    wal = tmp_path / "legacy.db-wal"
    assert b"LEGACY-TOKEN" not in db.read_bytes()
    assert not wal.exists() or b"LEGACY-TOKEN" not in wal.read_bytes()


def test_migration_rolls_back_on_failure(tmp_path, monkeypatch, settings):
    db = tmp_path / "legacy.db"
    _legacy_db(db)
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(session_store, "_SESSIONS_DDL", "CREATE TABLE {name} (this is not valid sql")

    with pytest.raises(sqlite3.OperationalError):
        session_store._drop_user_access_token_column()

    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(dashboard_sessions)")]
    assert "user_access_token" in cols
    assert conn.execute("SELECT COUNT(*) FROM dashboard_sessions").fetchone()[0] == 2
    conn.close()
