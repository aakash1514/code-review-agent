"""The App's reviews on GitHub, read with its installation token: the
latest review comment on a PR and the ai-code-review commit status."""
import threading

from pipeline.hitl_gate import DEFAULT_STATUS_CONTEXT
from tools.github_tools import (
    get_app, get_combined_status, get_installation_token, get_pr, list_issue_comments, list_pulls,
)

from mcp_server.errors import ToolInputError
from mcp_server.repos import require_repo

REVIEW_BODY_CHARS = 6000
TITLE_CHARS = 120
MAX_PRS = 30

_bot_login: str | None = None
_bot_lock = threading.Lock()


def app_bot_login() -> str:
    """The account the App posts as, "<app slug>[bot]"."""
    global _bot_login
    with _bot_lock:
        if _bot_login is None:
            _bot_login = f"{get_app()['slug']}[bot]"
        return _bot_login


def _pr_state(pr: dict) -> str:
    return "merged" if pr.get("merged_at") else pr.get("state", "?")


def review_status(repo: str, sha: str, token: str) -> dict | None:
    """The ai-code-review status on sha ({state, description, updated_at}),
    or None when the App never set one."""
    for status in get_combined_status(repo, sha, token).get("statuses") or []:
        if status.get("context") == DEFAULT_STATUS_CONTEXT:
            return {"state": status.get("state"), "description": status.get("description"),
                    "updated_at": status.get("updated_at")}
    return None


def get_pr_review(repo: str, pr_number: int) -> dict:
    name = require_repo(repo)
    if pr_number < 1:
        raise ToolInputError("pr_number must be a positive PR number.")
    token = get_installation_token()
    pr = get_pr(name, pr_number, token)
    head = pr["head"]["sha"]
    bot = app_bot_login()
    mine = [c for c in list_issue_comments(name, pr_number, token)
            if (c.get("user") or {}).get("login") == bot]
    latest = max(mine, key=lambda c: c.get("created_at") or "") if mine else None
    out = {
        "repo": name, "pr_number": pr_number, "title": pr.get("title"), "state": _pr_state(pr),
        "url": pr.get("html_url"), "head_sha": head,
        "status": review_status(name, head, token),
        "review_comments_by_app": len(mine),
        "review": None,
    }
    if latest:
        body = latest.get("body") or ""
        out["review"] = {
            "author": bot, "created_at": latest.get("created_at"), "updated_at": latest.get("updated_at"),
            "url": latest.get("html_url"), "body": body[:REVIEW_BODY_CHARS],
            "body_truncated": len(body) > REVIEW_BODY_CHARS,
        }
    else:
        out["note"] = f"No review comment from {bot} on this PR."
    return out


def list_reviewed_prs(repo: str, limit: int = 10) -> dict:
    name = require_repo(repo)
    limit = max(1, min(int(limit), MAX_PRS))
    token = get_installation_token()
    prs = []
    for pr in list_pulls(name, state="all", limit=limit, token=token)[:limit]:
        head = pr["head"]["sha"]
        status = review_status(name, head, token)
        prs.append({
            "number": pr["number"], "title": (pr.get("title") or "")[:TITLE_CHARS], "state": _pr_state(pr),
            "head_sha": head,
            "review_state": status["state"] if status else None,
            "review_description": status["description"] if status else None,
        })
    return {"repo": name, "prs": prs, "status_context": DEFAULT_STATUS_CONTEXT,
            "note": "review_state is null where the App never reviewed the PR's head commit."}
