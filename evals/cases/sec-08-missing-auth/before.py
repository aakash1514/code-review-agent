"""Admin console routes."""
from flask import Blueprint, jsonify

from app.auth import login_required, require_admin
from app.models import User, db

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/users")
@login_required
@require_admin
def list_users():
    """List all users with their roles."""
    return jsonify([u.to_dict() for u in User.query.order_by(User.id).all()])


@bp.route("/users/<int:user_id>/disable", methods=["POST"])
@login_required
@require_admin
def disable_user(user_id: int):
    """Disable a user's account without deleting their data."""
    user = User.query.get_or_404(user_id)
    user.active = False
    db.session.commit()
    return jsonify(user.to_dict())
