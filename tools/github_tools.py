import time
import base64
import jwt
import requests
from config import settings

GITHUB_API_BASE = "https://api.github.com"

# Module-level cache for installation tokens — avoids re-exchanging on every call.
# Keyed by installation_id in case you ever handle multiple installations.
_token_cache: dict[str, dict] = {}


def generate_jwt(app_id: str | None = None, private_key_path: str | None = None) -> str:
    """
    Signs a short-lived JWT as the GitHub App itself (not an installation).
    Valid for 10 minutes max per GitHub's rules — we use 9 to leave margin.
    """
    app_id = app_id or settings.GITHUB_APP_ID
    private_key_path = private_key_path or settings.GITHUB_PRIVATE_KEY_PATH

    with open(private_key_path, "r") as f:
        private_key = f.read()

    now = int(time.time())
    payload = {
        "iat": now - 60,       # backdate 60s to allow for clock drift
        "exp": now + (9 * 60),
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token(installation_id: str | None = None, force_refresh: bool = False) -> str:
    """
    Exchanges the App JWT for an installation access token (valid ~1hr).
    Cached in-process; auto-refreshes ~5 minutes before expiry.
    """
    installation_id = installation_id or settings.GITHUB_INSTALLATION_ID

    cached = _token_cache.get(installation_id)
    if cached and not force_refresh and cached["expires_at"] - time.time() > 300:
        return cached["token"]

    app_jwt = generate_jwt()
    resp = requests.post(
        f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # expires_at looks like "2026-07-08T21:15:00Z"
    expires_at = time.mktime(time.strptime(data["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
    _token_cache[installation_id] = {"token": data["token"], "expires_at": expires_at}
    return data["token"]


def _auth_headers(token: str | None = None, accept: str = "application/vnd.github+json") -> dict:
    token = token or get_installation_token()
    return {"Authorization": f"token {token}", "Accept": accept}


def get_pr_diff(repo: str, pr_number: int, token: str | None = None) -> str:
    """repo is 'owner/name'. Returns the raw unified diff as text."""
    resp = requests.get(
        f"{GITHUB_API_BASE}/repos/{repo}/pulls/{pr_number}",
        headers=_auth_headers(token, accept="application/vnd.github.v3.diff"),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def get_pr_files(repo: str, pr_number: int, token: str | None = None) -> list[dict]:
    """
    Returns the changed-files list (filename, status, patch, additions, deletions).
    Useful alongside get_pr_diff since the diff endpoint gives you one big blob,
    but agents generally want to iterate file-by-file.
    """
    resp = requests.get(
        f"{GITHUB_API_BASE}/repos/{repo}/pulls/{pr_number}/files",
        headers=_auth_headers(token),
        params={"per_page": 100},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def post_pr_review(repo: str, pr_number: int, body: str, token: str | None = None) -> dict:
    """
    Posts the synthesized summary as a top-level PR comment (issue-comments endpoint).
    Deliberately NOT using the /reviews endpoint with per-line comments for v1 —
    that requires diff-position math (commit_id + position) that's brittle across
    force-pushes. A single well-formatted comment is simpler and matches the
    architecture doc ("posts a summary comment natively on the PR").
    """
    resp = requests.post(
        f"{GITHUB_API_BASE}/repos/{repo}/issues/{pr_number}/comments",
        headers=_auth_headers(token),
        json={"body": body},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def set_commit_status(
    repo: str,
    sha: str,
    state: str,
    description: str,
    context: str = "ai-code-review",
    target_url: str | None = None,
    token: str | None = None,
) -> dict:
    """
    state must be one of: 'pending', 'success', 'failure', 'error'.
    context is the status-check name shown in the PR's checks list — this is
    the string you'll add to the branch protection rule once you see it appear.
    """
    assert state in {"pending", "success", "failure", "error"}, f"invalid state: {state}"
    payload = {"state": state, "description": description[:140], "context": context}
    if target_url:
        payload["target_url"] = target_url

    resp = requests.post(
        f"{GITHUB_API_BASE}/repos/{repo}/statuses/{sha}",
        headers=_auth_headers(token),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_full_file_content(repo: str, filename: str, ref: str, token: str | None = None) -> str:
    """
    Fetches a file's full content at a specific ref (commit SHA or branch).
    Needed because the diff alone often lacks enough surrounding context for
    agents to reason about a change safely.
    """
    resp = requests.get(
        f"{GITHUB_API_BASE}/repos/{repo}/contents/{filename}",
        headers=_auth_headers(token),
        params={"ref": ref},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("encoding") != "base64":
        raise ValueError(f"Unexpected encoding for {filename}: {data.get('encoding')}")

    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

def get_pr(repo: str, pr_number: int, token: str | None = None) -> dict:
    """
    Full PR metadata (not just the diff) — used to resolve base.sha/head.sha
    so the dashboard's code viewer can fetch full file content on both sides
    of a change via get_full_file_content, rather than just the patch hunks.
    """
    resp = requests.get(
        f"{GITHUB_API_BASE}/repos/{repo}/pulls/{pr_number}",
        headers=_auth_headers(token),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()

def get_repo_tree(repo: str, ref: str, token: str | None = None) -> list[dict]:
    """
    Recursive file listing at a given ref (branch name or commit SHA), via
    GitHub's Git Trees API. Used by the push-to-default-branch knowledge-graph
    rebuild (gap #6) to enumerate files without a local git clone — each
    file's content is then fetched individually via get_full_file_content().
    Only returns blobs (files), not tree entries (directories).
    """
    resp = requests.get(
        f"{GITHUB_API_BASE}/repos/{repo}/git/trees/{ref}",
        headers=_auth_headers(token),
        params={"recursive": "1"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return [item for item in data.get("tree", []) if item.get("type") == "blob"]