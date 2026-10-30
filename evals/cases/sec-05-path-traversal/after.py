"""Download endpoint for generated PDF reports."""
import os

from flask import Blueprint, abort, send_file
from flask_login import current_user, login_required

bp = Blueprint("reports", __name__)

REPORT_DIR = "/srv/app/reports"


@bp.route("/reports/<path:filename>")
@login_required
def download_report(filename: str):
    """Send one of the current user's generated reports as an attachment."""
    path = os.path.join(REPORT_DIR, str(current_user.id), filename)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True)
