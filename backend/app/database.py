"""Engine, session factory and the declarative Base."""
import sqlite3
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

# check_same_thread is a SQLite-only argument; Postgres must not receive it.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

# `database_url` is already driver-correct: Settings normalises a provider's
# `postgres://` / `postgresql://` to `postgresql+psycopg://` on load. See
# config.normalise_database_url.
engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    # A pooled connection that the database or an idle-timeout has already
    # closed is the classic first symptom of a managed Postgres deployment;
    # pre-ping trades a trivial round trip for not surfacing that to a user.
    pool_pre_ping=not settings.database_url.startswith("sqlite"),
)


@event.listens_for(Engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite ships with foreign keys OFF, which silently disables every
    ``ondelete`` clause on our models. Without this, deleting a Room leaves
    ``Section.home_room_id`` pointing at a row that no longer exists, and the
    readiness check (which tests for NULL) reports the section as fine.

    Registered on ``Engine`` rather than our own engine so that test fixtures,
    which build their own in-memory engines, behave the same as production.
    Postgres enforces foreign keys natively, so this applies to SQLite only.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
