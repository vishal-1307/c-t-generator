"""FastAPI application entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings, validate_startup_configuration
from .database import Base, SessionLocal, engine
from .models import User
from .routers import (
    academic_context,
    admin_reset,
    infrastructure,
    allocation,
    assistant,
    demo,
    auth,
    availability,
    availability_query,
    bulk_import,
    data_summary,
    export,
    faculty,
    generate,
    manual_edit,
    rooms,
    sections,
    subjects,
    teacher_import,
    timeslots,
    timetable,
    validate,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("timetable.api")

# Fail fast on unsafe production configuration, before the schema is touched
# or a bootstrap account could be created (Phase 13).
validate_startup_configuration()

# Development uses create_all; Alembic (backend/alembic/) manages schema
# evolution from here on - see backend/alembic/README or run
# `alembic upgrade head` against a real deployment target.
Base.metadata.create_all(bind=engine)


def _seed_bootstrap_admin() -> None:
    """Create the first admin account, if there is no way to log in yet.

    A tool with no self-registration has a genuine chicken-and-egg problem:
    somebody has to be able to sign in before anybody can be created. The two
    environments deserve different answers to it.

    **Development** creates ``admin`` / ``admin123`` and says so loudly - the
    same first-run convenience as Django's ``createsuperuser`` prompt or
    Grafana's ``admin``/``admin``. A fresh checkout works with no setup.

    **Production** does no such thing. A well-known password must never be
    reachable on a deployed system, and "we logged a warning about it" is not
    a control. The bootstrap runs only if ``BOOTSTRAP_ADMIN_USERNAME`` and
    ``BOOTSTRAP_ADMIN_PASSWORD`` are both set explicitly; otherwise no account
    is created and the log explains exactly how to create one. Refusing to
    create a weak account is the safe failure - the operator is locked out
    until they configure it, which is recoverable, whereas a guessable admin
    on a public deployment is not.
    """
    from sqlalchemy.orm import Session

    from .auth import hash_password
    from .models import User

    log = logging.getLogger("timetable.api")

    with Session(engine) as db:
        if db.query(User).first() is not None:
            return

        username = settings.bootstrap_admin_username
        password = settings.bootstrap_admin_password

        if settings.is_production and not password:
            log.error(
                "No users exist and BOOTSTRAP_ADMIN_PASSWORD is not set, so no "
                "admin account was created. Set BOOTSTRAP_ADMIN_USERNAME and "
                "BOOTSTRAP_ADMIN_PASSWORD and restart, or create the first "
                "account directly in the database. A default password is "
                "never created in production."
            )
            return

        if password is None:
            password = settings.dev_bootstrap_admin_password

        db.add(
            User(
                username=username,
                hashed_password=hash_password(password),
                role="admin",
                is_active=True,
            )
        )
        db.commit()

        if settings.is_production:
            # Never log the password itself, even the configured one.
            log.warning(
                "No users existed - created admin account %r from "
                "BOOTSTRAP_ADMIN_*. Sign in and change the password.",
                username,
            )
        else:
            log.warning(
                "No users existed - created development account %s/%s. "
                "This default is refused when APP_ENV=production.",
                username, password,
            )


_seed_bootstrap_admin()


def _reconcile_orphaned_runs() -> None:
    """Resolve any run left RUNNING by a process that never came back.

    Same reasoning and same call shape as `_seed_bootstrap_admin` above: a
    fresh process's in-memory solve-tracking state is always empty, so any run
    still marked RUNNING at this point cannot belong to this process - it can
    only be one a previous process abandoned mid-solve. See
    `jobs.reconcile_orphaned_runs` for why that makes the signal exact rather
    than a heuristic.
    """
    from sqlalchemy.orm import Session

    from . import jobs

    with Session(engine) as db:
        jobs.reconcile_orphaned_runs(db)


_reconcile_orphaned_runs()

app = FastAPI(
    title="College Timetable Generator",
    description=(
        "Constraint-solved (OR-Tools CP-SAT) conflict-free timetable generation "
        "for semester scheduling."
    ),
    version="0.4.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # The page fetches exports rather than navigating to them, and names the
    # saved file from this header - which a browser hides cross-origin unless
    # told otherwise.
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak an internal stack trace through the API (spec #42) - log it
    server-side, return a structured, generic body to the client."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred. See server logs.",
            "likely_blockers": [],
        },
    )


app.include_router(auth.router)
app.include_router(academic_context.router)
app.include_router(faculty.router)
app.include_router(subjects.router)
app.include_router(rooms.router)
app.include_router(sections.router)
app.include_router(timeslots.router)
app.include_router(availability.router)
app.include_router(availability_query.router)
app.include_router(validate.router)
app.include_router(allocation.router)
app.include_router(admin_reset.router)
app.include_router(infrastructure.router)
app.include_router(bulk_import.router)
app.include_router(teacher_import.router)
app.include_router(data_summary.router)
app.include_router(generate.router)
app.include_router(timetable.router)
app.include_router(manual_edit.router)
app.include_router(export.router)
app.include_router(assistant.router)
app.include_router(demo.router)


@app.get("/api/health", tags=["meta"])
def health():
    """Liveness: is the process up and serving?

    Deliberately cheap - no database call. A load balancer hits this every few
    seconds, and a health check that queries the database turns a slow database
    into a restart loop, which is precisely the wrong response to it.
    """
    return {
        "status": "ok",
        "environment": settings.app_env,
        "database": settings.database_url.split("://", 1)[0],
    }


@app.get("/api/readiness", tags=["meta"])
def readiness():
    """Readiness: can this instance actually serve requests?

    Checks what would make a request fail rather than merely be slow: the
    database answers, the schema is present, and someone can log in. Returns
    503 when not ready, so an orchestrator holds traffic back instead of sending
    it into errors.
    """
    from sqlalchemy import text as _text

    checks: dict[str, dict] = {}
    ready = True

    try:
        with SessionLocal() as db:
            db.execute(_text("SELECT 1"))
            # A connection is not enough - a fresh database with no migrations
            # connects fine and then fails on the first real query.
            user_count = db.query(User).count()
        checks["database"] = {"ok": True, "detail": f"connected, {user_count} user(s)"}
    except Exception as exc:  # pragma: no cover - exercised via the endpoint
        ready = False
        logger.warning("readiness: database check failed: %s", exc)
        # The class name only. The message could carry a connection string.
        checks["database"] = {
            "ok": False, "detail": f"unavailable ({type(exc).__name__})"
        }

    if checks.get("database", {}).get("ok") and user_count == 0:
        ready = False
        checks["admin_account"] = {
            "ok": False,
            "detail": "no user accounts exist - set BOOTSTRAP_ADMIN_* and restart",
        }
    else:
        checks["admin_account"] = {"ok": True, "detail": "at least one account exists"}

    body = {"status": "ready" if ready else "not_ready",
            "environment": settings.app_env, "checks": checks}
    if not ready:
        return JSONResponse(status_code=503, content=body)
    return body
