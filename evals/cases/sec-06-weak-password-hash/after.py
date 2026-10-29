"""Account registration and login."""
import hashlib
import secrets

from app.db import get_user_by_email, insert_user


class AuthError(Exception):
    pass


def hash_password(password: str) -> str:
    """Hash a password for storage."""
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def register(email: str, password: str) -> int:
    """Create an account and return the new user id."""
    if len(password) < 10:
        raise AuthError("password must be at least 10 characters")
    if get_user_by_email(email) is not None:
        raise AuthError("email already registered")
    return insert_user(email=email, password_hash=hash_password(password))


def login(email: str, password: str) -> str:
    """Check credentials and return a new session token."""
    user = get_user_by_email(email)
    if user is None or not secrets.compare_digest(user["password_hash"], hash_password(password)):
        raise AuthError("invalid email or password")
    return secrets.token_urlsafe(32)
