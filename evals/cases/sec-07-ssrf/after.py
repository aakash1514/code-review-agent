"""Link previews for URLs pasted into chat messages."""
import re

import requests
from flask import Blueprint, jsonify, request

bp = Blueprint("previews", __name__)

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
MAX_BYTES = 512_000


@bp.route("/previews", methods=["POST"])
def create_preview():
    """Fetch the URL from the message body and return its page title."""
    url = request.json["url"]
    resp = requests.get(url, timeout=5, allow_redirects=True)
    body = resp.text[:MAX_BYTES]
    match = TITLE_RE.search(body)
    title = match.group(1).strip() if match else url
    return jsonify({"url": url, "title": title, "status": resp.status_code})
