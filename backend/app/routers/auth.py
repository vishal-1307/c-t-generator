"""Phase 10: login and user management.

User management (create/list/update) is Admin-only, matching spec PART 13's
"manage users" capability. There is deliberately no self-registration route -
accounts are provisioned by an admin, the normal posture for an internal
scheduling tool with a small, known user base.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import crud, listing
from ..auth import create_access_token, get_current_user, hash_password, require_admin, verify_password
from ..database import get_db
from ..models import Faculty, User
from ..schemas import LoginRequest, TokenOut, UserCreate, UserOut, UserUpdate

from ..login_limit import login_limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    client = request.client.host if request.client else "unknown"
    wait = login_limiter.retry_after(client, payload.username)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many sign-in attempts. Try again in {max(1, round(wait / 60))} minute(s).",
            headers={"Retry-After": str(int(wait))},
        )
    user = db.query(User).filter(User.username == payload.username).first()
    # Same generic message whether the username doesn't exist or the
    # password is wrong - never let a login error confirm which usernames
    # are real.
    if user is None or not verify_password(payload.password, user.hashed_password):
        login_limiter.failed(client, payload.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is disabled")

    login_limiter.succeeded(client, payload.username)
    user.last_login_at = dt.datetime.now(dt.UTC)
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=create_access_token(user), user=user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# ------------------------------------------------------------- user management


USER_LIST = listing.Sortable(
    search=(User.username, User.email),
    sorts={"username": User.username, "role": User.role},
    default_sort=User.username,
)


@router.get("/users")
def list_users(
    params: listing.ListParams = Depends(listing.list_params),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Every account, or one searchable page of them.

    Paginable for the same reason the other lists are: an institution issues a
    login per member of staff, and the screen that manages them should not have
    to read all of them to show twenty. Without parameters this returns the
    plain array it always did.
    """
    return listing.serialize(
        listing.apply(db, User, params, USER_LIST), UserOut
    )


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    if db.query(User).filter(User.username == payload.username).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Username {payload.username!r} is taken")
    if payload.faculty_id is not None:
        crud.get_or_404(db, Faculty, payload.faculty_id)
    user = User(
        username=payload.username,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        faculty_id=payload.faculty_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, payload: UserUpdate, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    user = crud.get_or_404(db, User, user_id)
    data = payload.model_dump(exclude_unset=True)
    if "password" in data:
        password = data.pop("password")
        if password:
            user.hashed_password = hash_password(password)
    if "faculty_id" in data and data["faculty_id"] is not None:
        crud.get_or_404(db, Faculty, data["faculty_id"])
    if user.id == admin.id and data.get("is_active") is False:
        raise HTTPException(status_code=422, detail="You cannot deactivate your own account")
    if user.id == admin.id and data.get("role") not in (None, "admin"):
        raise HTTPException(status_code=422, detail="You cannot demote your own account")
    for key, value in data.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user
