import re
import secrets
import threading
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException, Query, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from config import settings
from pipeline.session_store import (
    create_handoff_code, redeem_handoff_code, get_session, delete_session, SESSION_TTL_SECONDS,
)
from tools.github_tools import (
    exchange_oauth_code, get_authenticated_user, get_installation_token,
    check_repo_read_permission, is_repo_private,
)

router = APIRouter(prefix="/auth", tags=["auth"])

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"

# Short-lived CSRF state store for the OAuth round-trip only — NOT the same
# as dashboard_sessions (which needs to survive uvicorn --reload restarts
# across a whole browsing session). A login flow takes seconds, so an
# in-memory dict here is a reasonable tradeoff: worst case a --reload lands
# mid-login and the user just clicks "Sign in" again.
_pending_states: dict[str, float] = {}
_STATE_TTL_SECONDS = 600


def _cleanup_expired_states() -> None:
    now = time.time()
    expired = [s for s, ts in _pending_states.items() if now - ts > _STATE_TTL_SECONDS]
    for s in expired:
        _pending_states.pop(s, None)


@router.get("/login")
def login():
    """Redirects the browser into GitHub's OAuth consent screen. Scope is
    deliberately minimal (read:user only) — this endpoint only needs to
    establish WHO is logging in. Repo write-permission checks use the App's
    own installation token instead, not this user token — see
    check_repo_write_permission in github_tools.py."""
    _cleanup_expired_states()
    state = secrets.token_urlsafe(24)
    _pending_states[state] = time.time()

    params = {
        "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
        "redirect_uri": settings.GITHUB_OAUTH_REDIRECT_URI,
        "scope": "read:user",
        "state": state,
    }
    return RedirectResponse(f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}")


@router.get("/callback")
def callback(code: str = Query(...), state: str = Query(...)):
    if _pending_states.pop(state, None) is None:
        raise HTTPException(status_code=400, detail="invalid or expired OAuth state")

    try:
        user_token = exchange_oauth_code(code)
        user = get_authenticated_user(user_token)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub OAuth exchange failed: {e}")

    # The user's OAuth token is only needed to learn who logged in; it's
    # dropped here, never stored. The dashboard gets a one-time handoff code,
    # not the session itself — see create_handoff_code.
    handoff_code = create_handoff_code(user["login"], user["avatar_url"])
    return RedirectResponse(f"{settings.DASHBOARD_BASE_URL}/?{urlencode({'code': handoff_code})}")


class HandoffExchange(BaseModel):
    code: str


@router.post("/exchange")
def exchange(body: HandoffExchange):
    """Trades the one-time ?code= from the callback redirect for a session
    token. The code goes in the JSON body so the session-bearing response
    never touches a URL. 401 for unknown, expired or already used codes."""
    session_id = redeem_handoff_code(body.code)
    if session_id is None:
        raise HTTPException(status_code=401, detail="login code is invalid, expired or already used — please sign in again")
    return {"session_token": session_id, "expires_in": SESSION_TTL_SECONDS}


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency — protected routes take `user: dict =
    Depends(get_current_user)`. Expects 'Authorization: Bearer <session_id>'.
    Bearer-token-in-header rather than a cookie deliberately: avoids
    cross-origin SameSite cookie friction between localhost:5173 (frontend)
    and localhost:8000 (backend), which are different origins as far as
    browsers are concerned even though both are 'localhost'."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="not authenticated")
    session_id = authorization.removeprefix("Bearer ").strip()
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=401, detail="session expired or invalid — please sign in again")
    return session


_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REPO_ACCESS_TTL_SECONDS = 300

# Both caches are in-process and only ever hold results that are safe to
# reuse for a few minutes: "allowed" read checks (a denial is never cached,
# so a newly added collaborator gets in immediately) and repo visibility.
# Revoked access lingers for at most _REPO_ACCESS_TTL_SECONDS.
_read_access_cache: dict[tuple[str, str], float] = {}  # (login, repo) -> expires_at
_visibility_cache: dict[str, tuple[bool, float]] = {}  # repo -> (is_private, expires_at)
_cache_lock = threading.Lock()


def _repo_is_private_cached(repo: str, token: str) -> bool:
    key = repo.lower()
    now = time.time()
    with _cache_lock:
        cached = _visibility_cache.get(key)
        if cached and cached[1] > now:
            return cached[0]
    private = is_repo_private(repo, token=token)
    with _cache_lock:
        _visibility_cache[key] = (private, now + _REPO_ACCESS_TTL_SECONDS)
    return private


def check_repo_read_access(repo: str, login: str) -> None:
    """Raises 400/403/502 unless `login` may read `repo` on GitHub: any
    logged-in user for a public repo, collaborators (incl. org/team grants)
    with read or higher for a private one. Shared by require_repo_read and
    the WebSocket ticket route so the rule lives in one place."""
    if not _REPO_NAME_RE.match(repo) or ".." in repo:
        raise HTTPException(status_code=400, detail="repo must be 'owner/name'")

    key = (login.lower(), repo.lower())
    with _cache_lock:
        if _read_access_cache.get(key, 0) > time.time():
            return

    try:
        token = get_installation_token()
        allowed = (not _repo_is_private_cached(repo, token)
                   or check_repo_read_permission(repo, login, token=token))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"failed to check repo access with GitHub: {e}")

    if not allowed:
        raise HTTPException(status_code=403, detail=f"@{login} does not have read access to {repo}")
    with _cache_lock:
        _read_access_cache[key] = time.time() + _REPO_ACCESS_TTL_SECONDS


def require_repo_read(repo: str = Query(...), user: dict = Depends(get_current_user)) -> dict:
    """FastAPI dependency for every dashboard read route — valid session AND
    read access to ?repo=. Returns the session like get_current_user."""
    check_repo_read_access(repo, user["github_login"])
    return user


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"login": user["github_login"], "avatar_url": user["avatar_url"]}


@router.post("/logout")
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        delete_session(authorization.removeprefix("Bearer ").strip())
    return {"status": "logged_out"}