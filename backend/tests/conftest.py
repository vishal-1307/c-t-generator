"""Test fixtures: a FastAPI client backed by a throwaway file-based SQLite DB."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production-use-only")

from app.auth import hash_password  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AcademicContext, User  # noqa: E402

TEST_ADMIN_PASSWORD = "test-admin-password-123"  # noqa: S105 - test fixture only


@pytest.fixture(autouse=True)
def _fresh_login_limiter():
    """Failed sign-ins in one test must not lock out the next."""
    from app.login_limit import login_limiter

    login_limiter.reset()
    yield
    login_limiter.reset()


@pytest.fixture
def db_session():
    """In-memory SQLite shared across connections via StaticPool."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def client(db_session):
    """Pre-authenticated as an admin by default.

    Phase 10 protects every mutation endpoint; the overwhelming majority of
    this suite is about business logic (constraint correctness, persistence,
    validation), not about authorization itself - so the default client
    behaves like a logged-in admin, exactly the fixture pattern most real
    test suites use once auth exists. Authorization is verified separately,
    against explicitly unauthenticated/under-privileged clients, in
    test_auth.py.
    """
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        db_session.add(User(
            username="test-admin", hashed_password=hash_password(TEST_ADMIN_PASSWORD),
            role="admin", is_active=True,
        ))
        db_session.commit()
        resp = c.post("/api/auth/login", json={"username": "test-admin", "password": TEST_ADMIN_PASSWORD})
        assert resp.status_code == 200, resp.text
        c.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client(db_session):
    """An explicitly unauthenticated client, for testing that protected
    endpoints actually reject requests with no token."""
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def login_as(client: TestClient, db_session, *, username: str, role: str, password: str = "pw12345678") -> TestClient:
    """Swap the given client's auth header to a fresh user of the given role -
    for tests that need to prove a specific role is refused an action."""
    db_session.add(User(username=username, hashed_password=hash_password(password), role=role, is_active=True))
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return client


@pytest.fixture
def context(db_session):
    """A ready-made academic context - most fixtures need one to scope
    Sections and TimetableRuns to."""
    ctx = AcademicContext(
        academic_year="2026-27", semester=1, program="BCA", department="CSE"
    )
    db_session.add(ctx)
    db_session.commit()
    db_session.refresh(ctx)
    return ctx
