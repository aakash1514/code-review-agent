"""Public member directory endpoints."""
import sqlite3

from flask import Blueprint, jsonify, request

bp = Blueprint("directory", __name__)
DB_PATH = "app.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@bp.route("/directory/search")
def search_members():
    """Return members whose display name starts with the `q` query parameter."""
    name = request.args.get("q", "")
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, display_name, city FROM members WHERE display_name LIKE '{name}%'"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@bp.route("/directory/<int:member_id>")
def get_member(member_id: int):
    """Return one member by id, or 404."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, display_name, city FROM members WHERE id = ?", (member_id,)
        ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))
