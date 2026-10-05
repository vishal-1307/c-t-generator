"""Phase 10: authentication (JWT) and role-based authorization.

Deliberately not a custom cryptographic system: password hashing uses
``bcrypt`` directly (the industry-standard library, not a hand-rolled
scheme) and tokens are signed JWTs via ``PyJWT``, both widely audited
third-party libraries. This module only wires them together - hash/verify
passwords, mint/verify tokens, and provide FastAPI dependencies that load
the current user and check their role. Nothing here invents a cipher.

Role model (spec PART 12/13): four roles - ``admin``, ``scheduler``,
``faculty``, ``viewer``. Only ``admin`` and ``scheduler`` can write anything;
``faculty`` and ``viewer`` are both read-only, differing only in what a
future phase might show them. See TIMETABLE_LOGIC_SPEC.md for the exact
per-endpoint permission table and the reasoning behind which write actions
are admin-only vs scheduler-or-admin.

**Scope decision, made explicit rather than silently assumed**: every GET
endpoint in this API stays open, with or without a token. Timetables are
published, institution-wide information - not a secret - so read access was
never the risk surface. What Phase 10 protects is *mutation*: bulk import,
manual edits, lock/unlock, publish, and all reference-data CRUD. This keeps
the security work proportionate to the actual risk instead of retrofitting
row-level read permissions nobody asked for.
"""
from __future__ import annotations

import datetime as dt

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User

# tokenUrl is documentation for OpenAPI's "Authorize" button; auto_error=False
# so a missing token surfaces as our own 401 message, not FastAPI's generic one.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # A corrupt or foreign-format hash fails closed, never a 500.
        return False


def create_access_token(user: User) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "username": user.username,
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


class AuthError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """Decode the bearer token and load the live User row - not just trust
    the token's claims, so a deactivated or deleted account is rejected
    immediately rather than only once its (up to 12h) token expires."""
    if token is None:
        raise AuthError("Not authenticated - include an Authorization: Bearer token")
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Session expired, please log in again") from None
    except jwt.InvalidTokenError:
        raise AuthError("Invalid authentication token") from None

    user_id = payload.get("sub")
    user = db.get(User, int(user_id)) if user_id is not None else None
    if user is None or not user.is_active:
        raise AuthError("This account is disabled or no longer exists")
    return user


def get_current_user_optional(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User | None:
    """Same as get_current_user, but returns None instead of 401 - for
    endpoints that stay open but still want to attribute an action to a
    signed-in user when one is present (e.g. ChangeHistory.user_id)."""
    if token is None:
        return None
    try:
        return get_current_user(token, db)
    except HTTPException:
        return None


def require_role(*roles: str):
    """FastAPI dependency factory: 403s unless the current user's role is one
    of ``roles``. Always requires authentication first - a role check without
    a verified identity behind it would be meaningless."""

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of these roles: {', '.join(roles)}. "
                f"Your role is '{user.role}'.",
            )
        return user

    return _check


# Named per the product's own vocabulary (spec PART 13), not generic
# "level 1/level 2" tiers, so a route's protection reads as a business rule.
require_admin = require_role("admin")
require_scheduler_or_admin = require_role("admin", "scheduler")
