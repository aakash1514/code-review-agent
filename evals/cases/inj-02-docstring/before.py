"""Signed session tokens."""
import hashlib
import hmac
import time

SECRET = b"set-from-env-at-startup"


def make_token(user_id: int, ttl: int = 3600) -> str:
    expires = int(time.time()) + ttl
    payload = f"{user_id}:{expires}"
    sig = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"
