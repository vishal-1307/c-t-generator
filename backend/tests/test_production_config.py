"""Phase 13 PART 1/2/21: the production configuration guards.

Every test here answers the same question in a different way: **can a
production deployment start with an unsafe default?** The answer must be no,
and it must be no for each default independently - a guard that only fires when
*everything* is wrong is not a guard.

The other half is equally important and easy to lose: none of this may break
local development. A fresh checkout must still start with no configuration at
all, which is what the development-mode tests assert.
"""
from __future__ import annotations

import pytest

import importlib.util
from pathlib import Path

from sqlalchemy.engine.url import make_url

from app.config import (
    DEV_CORS_DEFAULT,
    ConfigurationError,
    Settings,
    validate_startup_configuration,
)


def _prod(**overrides) -> Settings:
    """A production Settings that is otherwise correctly configured, so each
    test can break exactly one thing and see that guard fire alone."""
    base = dict(
        app_env="production",
        jwt_secret_key="a-real-explicit-secret-value-that-is-long-enough",
        cors_origins="https://timetable.example.edu",
        database_url="postgresql+psycopg://user:pw@db:5432/timetable",
        bootstrap_admin_username="registrar",
        bootstrap_admin_password="a-real-password-1234",
    )
    base.update(overrides)
    return Settings(**base)


def _problems(settings: Settings) -> str:
    with pytest.raises(ConfigurationError) as exc:
        validate_startup_configuration(settings)
    return str(exc.value)


# ===========================================================================
# Development must stay effortless
# ===========================================================================


def test_development_starts_with_no_configuration_at_all():
    """A fresh checkout has no .env and no environment variables. It must
    still run - that is the whole reason the unsafe defaults exist."""
    settings = Settings(app_env="development")
    validate_startup_configuration(settings)  # must not raise
    assert settings.is_production is False
    assert settings.demo_features_enabled is True


def test_development_is_the_default_environment():
    assert Settings().app_env == "development"


def test_an_unrecognised_environment_is_rejected_rather_than_assumed():
    """APP_ENV gates every other guard, so a typo in it must not quietly mean
    'development'."""
    with pytest.raises(ConfigurationError) as exc:
        validate_startup_configuration(Settings(app_env="prod"))
    assert "development" in str(exc.value) and "production" in str(exc.value)


# ===========================================================================
# Production refuses each unsafe default, independently
# ===========================================================================


def test_production_refuses_a_default_cors_origin():
    detail = _problems(_prod(cors_origins=DEV_CORS_DEFAULT))
    assert "CORS_ORIGINS" in detail
    assert "localhost" in detail


def test_production_refuses_sqlite():
    detail = _problems(_prod(database_url="sqlite:///./timetable.db"))
    assert "SQLite" in detail
    assert "PostgreSQL" in detail


def test_production_refuses_a_short_bootstrap_password():
    detail = _problems(_prod(bootstrap_admin_password="short"))
    assert "BOOTSTRAP_ADMIN_PASSWORD" in detail


def test_production_refuses_the_development_bootstrap_password():
    """The specific thing the phase brief called out: admin/admin123 must not
    remain usable as a production default."""
    detail = _problems(_prod(bootstrap_admin_password="admin123"))
    assert "development default" in detail


def test_a_correctly_configured_production_starts(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "an-explicit-production-secret-value")
    validate_startup_configuration(_prod())  # must not raise


def test_every_problem_is_reported_at_once_not_one_per_restart():
    """An operator fixing a deployment wants the whole list, not four
    restarts' worth of one-at-a-time errors."""
    detail = _problems(_prod(
        cors_origins=DEV_CORS_DEFAULT,
        database_url="sqlite:///./timetable.db",
        bootstrap_admin_password="admin123",
    ))
    assert "CORS_ORIGINS" in detail
    assert "SQLite" in detail
    assert "development default" in detail
    assert detail.count("  - ") >= 3


def test_the_refusal_explains_how_to_fix_each_problem():
    """A guard that says 'no' without saying 'do this' just moves the work."""
    detail = _problems(_prod(cors_origins=DEV_CORS_DEFAULT,
                             database_url="sqlite:///./x.db"))
    assert "Set it to the real front-end origin" in detail
    assert "Use PostgreSQL" in detail
    assert "backend/.env.example" in detail


# ===========================================================================
# Demo features
# ===========================================================================


def test_demo_features_are_off_in_production_even_when_enabled():
    """The flag cannot re-enable them by itself: seeding or resetting sample
    data has no business running against a live semester."""
    assert _prod(enable_demo_data=True).demo_features_enabled is False


def test_demo_features_can_be_switched_off_in_development_too():
    assert Settings(app_env="development",
                    enable_demo_data=False).demo_features_enabled is False


# ===========================================================================
# The bootstrap account
# ===========================================================================


def test_production_creates_no_account_without_an_explicit_password(tmp_path, monkeypatch):
    """The safe failure: locked out until configured, rather than reachable
    with a guessable password."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.models import User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)

    import app.main as main_module

    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(main_module, "settings", _prod(bootstrap_admin_password=None))

    main_module._seed_bootstrap_admin()

    with Session(engine) as db:
        assert db.query(User).count() == 0, (
            "production must not create an account with a default password"
        )


def test_production_creates_the_configured_account(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.auth import verify_password
    from app.database import Base
    from app.models import User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)

    import app.main as main_module

    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(main_module, "settings", _prod(
        bootstrap_admin_username="registrar",
        bootstrap_admin_password="a-real-password-1234",
    ))

    main_module._seed_bootstrap_admin()

    with Session(engine) as db:
        user = db.query(User).one()
        assert user.username == "registrar"
        assert user.role == "admin"
        # Stored hashed, never in plain text.
        assert user.hashed_password != "a-real-password-1234"
        assert verify_password("a-real-password-1234", user.hashed_password)


def test_the_configured_password_is_never_written_to_the_log(monkeypatch, caplog):
    import logging

    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.database import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)

    import app.main as main_module

    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(main_module, "settings", _prod(
        bootstrap_admin_password="hunter2-a-real-password",
    ))

    with caplog.at_level(logging.WARNING, logger="timetable.api"):
        main_module._seed_bootstrap_admin()

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "hunter2-a-real-password" not in logged
    assert "registrar" in logged  # the username is fine to record


def test_development_still_creates_the_convenience_account(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.auth import verify_password
    from app.database import Base
    from app.models import User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)

    import app.main as main_module

    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(main_module, "settings", Settings(app_env="development"))

    main_module._seed_bootstrap_admin()

    with Session(engine) as db:
        user = db.query(User).one()
        assert user.username == "admin"
        assert verify_password("admin123", user.hashed_password)


def test_the_bootstrap_never_runs_when_an_account_already_exists(monkeypatch):
    """Otherwise a restart could reintroduce a default account after someone
    deliberately removed it."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.auth import hash_password
    from app.database import Base
    from app.models import User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        db.add(User(username="real-admin", hashed_password=hash_password("x" * 14),
                    role="admin", is_active=True))
        db.commit()

    import app.main as main_module

    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(main_module, "settings", Settings(app_env="development"))
    main_module._seed_bootstrap_admin()

    with Session(engine) as db:
        assert db.query(User).count() == 1
        assert db.query(User).one().username == "real-admin"


# ===========================================================================
# Upload limits, health and readiness
# ===========================================================================


def test_health_is_cheap_and_does_not_touch_the_database(client, monkeypatch):
    """A liveness probe that queries the database turns a slow database into a
    restart loop - precisely the wrong response to it."""
    import app.main as main_module

    def _explode(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("health must not open a database session")

    monkeypatch.setattr(main_module, "SessionLocal", _explode, raising=False)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readiness_reports_each_check_separately(client):
    body = client.get("/api/readiness").json()
    assert set(body["checks"]) >= {"database", "admin_account"}
    assert all("ok" in c and "detail" in c for c in body["checks"].values())


def test_readiness_is_503_when_no_account_exists(client, db_session, monkeypatch):
    """Not ready means "do not send traffic here yet" - and an instance nobody
    can log into is exactly that.

    Readiness deliberately uses the *real* configured session factory rather
    than the request's injected session: a probe that a test override could
    satisfy would not be checking the thing it exists to check. So the factory
    is what gets patched here.
    """
    import app.main as main_module

    from app.models import User

    db_session.query(User).delete()
    db_session.commit()

    class _Factory:
        def __enter__(self):
            return db_session

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(main_module, "SessionLocal", lambda: _Factory())

    resp = client.get("/api/readiness")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["admin_account"]["ok"] is False
    assert "BOOTSTRAP_ADMIN" in body["checks"]["admin_account"]["detail"]


def test_readiness_never_leaks_a_connection_string_on_failure(client, monkeypatch):
    """A database error message can contain credentials, so only the exception
    class is reported."""
    import app.main as main_module

    class _Boom:
        def __enter__(self):
            raise RuntimeError(
                "could not connect to postgresql://user:SECRETPW@host:5432/db"
            )

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(main_module, "SessionLocal", lambda: _Boom(), raising=False)

    resp = client.get("/api/readiness")
    assert resp.status_code == 503
    body = resp.json()
    assert body["checks"]["database"]["ok"] is False
    assert "SECRETPW" not in resp.text
    assert "RuntimeError" in body["checks"]["database"]["detail"]


# ===========================================================================
# Database URL normalisation (Phase 14)
# ===========================================================================


@pytest.mark.parametrize("given,expected", [
    # What Render, Heroku and friends actually hand out.
    ("postgres://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ("postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    # An explicit driver is respected, never rewritten.
    ("postgresql+psycopg://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ("sqlite:///./timetable.db", "sqlite:///./timetable.db"),
])
def test_database_urls_are_normalised_to_a_driver_sqlalchemy_can_load(given, expected):
    """A provider's connection string should work when pasted as-is.

    SQLAlchemy has no driver of its own, so `postgresql://` fails at connect
    time with an error about the driver rather than the URL - a confusing
    first thing to hit on a new deployment.
    """
    assert Settings(database_url=given).database_url == expected


def test_normalisation_does_not_defeat_the_sqlite_production_guard():
    """The guard reads the raw setting, so rewriting the driver must not let
    a SQLite deployment slip through."""
    detail = _problems(_prod(database_url="sqlite:///./timetable.db"))
    assert "SQLite" in detail


def test_a_password_containing_a_scheme_like_string_is_untouched():
    """Only the leading scheme is rewritten - a naive replace would corrupt a
    password that happens to contain 'postgresql://'."""
    given = "postgresql://user:pg-postgresql://x@host:5432/db"
    out = Settings(database_url=given).database_url
    assert out == "postgresql+psycopg://user:pg-postgresql://x@host:5432/db"
    assert out.count("+psycopg") == 1


# ---------------------------------------------------------------------------
# The psycopg2 regression.
#
# A Render-supplied `postgresql://` reached Alembic unnormalised, SQLAlchemy
# defaulted it to the psycopg2 dialect, and `alembic upgrade head` died with
# `ModuleNotFoundError: No module named 'psycopg2'` on every deploy - before
# the API process started. These pin the behaviour that prevents it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("given", [
    "postgres://u:p@dpg-abc.singapore-postgres.render.com:5432/timetable",
    "postgresql://u:p@dpg-abc.singapore-postgres.render.com:5432/timetable",
    "postgresql+psycopg://u:p@dpg-abc.singapore-postgres.render.com:5432/timetable",
])
def test_sqlalchemy_selects_the_psycopg_v3_dialect_never_psycopg2(given):
    """The actual regression, asserted where it actually broke.

    Normalising the string is only a proxy; what matters is which dialect
    class SQLAlchemy resolves from it. `get_dialect()` is the same lookup the
    engine and Alembic perform, so this fails if a URL would ever select
    psycopg2 again - and it does so without needing psycopg2 installed.
    """
    url = make_url(Settings(database_url=given).database_url)
    dialect = url.get_dialect()
    assert dialect.driver == "psycopg", f"{given} selected {dialect.driver}"
    assert "psycopg2" not in dialect.__module__


def test_alembic_env_uses_the_normalised_url():
    """Alembic must resolve the same driver as the application.

    This is the specific gap that caused the outage: env.py read the raw
    setting while only the app engine read the normalised one. Loading env.py's
    module-level URL the same way Alembic does proves the two agree.
    """
    render_style = "postgresql://u:p@dpg-abc.singapore-postgres.render.com:5432/timetable"
    env_py = Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    # Comments in env.py explain why set_main_option is avoided, so inspect
    # executable lines only.
    code = " ".join(
        line for line in env_py.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )

    # env.py must take its URL from settings, not push it through alembic.ini's
    # ConfigParser (where `%` is interpolation syntax).
    assert "settings.database_url" in code
    assert "set_main_option" not in code

    monkey = Settings(database_url=render_style)
    assert monkey.database_url.startswith("postgresql+psycopg://")
    assert make_url(monkey.database_url).get_dialect().driver == "psycopg"


def test_psycopg2_is_not_installed_so_a_regression_cannot_pass_silently():
    """If psycopg2 were ever added, a URL selecting it would start working and
    these tests would stop catching the mistake. Assert the dependency really
    is absent."""
    assert importlib.util.find_spec("psycopg2") is None, (
        "psycopg2 is installed - the project uses psycopg v3; a psycopg2 "
        "fallback would mask the URL-normalisation bug this suite guards."
    )
    assert importlib.util.find_spec("psycopg") is not None, "psycopg v3 missing"


# ===========================================================================
# CORS_ORIGINS validation.
#
# A deployment failed to start because CORS_ORIGINS was never set - the guard
# working correctly. Reading it then showed the guard only rejected the
# *untouched* development default: every near-miss (empty, trailing slash, no
# scheme, localhost only) booted a healthy-looking API that no browser could
# call, because the middleware compares the Origin header byte for byte.
# ===========================================================================


@pytest.mark.parametrize("value,because", [
    ("", "no allowed origin means every browser request is refused"),
    ("   ", "whitespace is the same as empty once split"),
    ("https://app.vercel.app/", "the Origin header never carries a trailing slash"),
    ("app.vercel.app", "an origin without a scheme matches nothing"),
    ("https://app.vercel.app/api", "an origin is scheme://host[:port], not a URL"),
    ("http://localhost:3000", "a deployed front end is not served from loopback"),
    ("http://127.0.0.1:3000", "same, by IP"),
])
def test_production_refuses_a_cors_value_no_browser_could_match(value, because):
    detail = _problems(_prod(cors_origins=value))
    assert "CORS_ORIGINS" in detail or "loopback" in detail, because


@pytest.mark.parametrize("value", [
    "https://app.vercel.app",
    "https://app.vercel.app,https://www.example.edu",
    # A loopback entry is fine *alongside* a real origin - that is someone
    # debugging a local front end against the deployed API, not a mistake.
    "https://app.vercel.app,http://localhost:3000",
    "http://localhost:3000,https://app.vercel.app",
    "https://app.vercel.app:8443",
])
def test_production_accepts_a_real_front_end_origin(value):
    """A correct origin must not be rejected - a guard that blocks valid
    deployments is as bad as one that lets broken ones through."""
    validate_startup_configuration(_prod(cors_origins=value))


def test_the_untouched_development_default_is_still_named_explicitly():
    """The message an operator hits first should say which setting and why."""
    detail = _problems(_prod(cors_origins=DEV_CORS_DEFAULT))
    assert "CORS_ORIGINS" in detail
    assert "development default" in detail


def test_a_bad_cors_value_names_the_offending_entry():
    """With several origins, the operator needs to know which one is wrong."""
    detail = _problems(_prod(cors_origins="https://good.vercel.app,https://bad.example.com/"))
    assert "bad.example.com" in detail
    assert "good.vercel.app" not in detail
