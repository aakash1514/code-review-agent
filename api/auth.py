import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException, Query, Depends
from fastapi.responses import RedirectResponse

from config import settings
from pipeline.session_store import create_session, get_session, delete_session
from tools.github_tools import exchange_oauth_code, get_authenticated_user

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

    session_id = create_session(user["login"], user["avatar_url"], user_token)

    redirect_url = f"{settings.DASHBOARD_BASE_URL}/?session={session_id}"
    return RedirectResponse(redirect_url)


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


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"login": user["github_login"], "avatar_url": user["avatar_url"]}


@router.post("/logout")
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        delete_session(authorization.removeprefix("Bearer ").strip())
    return {"status": "logged_out"}