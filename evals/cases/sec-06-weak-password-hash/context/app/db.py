import sqlite3

_conn = sqlite3.connect("app.db")
_conn.row_factory = sqlite3.Row


def get_user_by_email(email):
    return _conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def insert_user(email, password_hash):
    cur = _conn.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, password_hash))
    _conn.commit()
    return cur.lastrowid
