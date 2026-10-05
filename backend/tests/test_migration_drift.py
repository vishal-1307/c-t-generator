"""Do the migrations build the schema the models declare?

Tests create their schema with ``Base.metadata.create_all``; production gets
its schema from ``alembic upgrade head``. Nothing had ever compared the two, so
a column declared one way in the model and another way in a migration would
pass every test and still be wrong in production.

That is not hypothetical - this test was written after finding exactly that:
two columns the models declare unique, whose migration created plain
non-unique indexes. The intent was recorded and never enforced, which for a
single-use confirmation token is the kind of gap that only shows up when it
matters.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.database import Base

BACKEND = Path(__file__).resolve().parents[1]

# Differences alembic reports that are not real drift. Keep this list short and
# justified - every entry is a place the check has been told to look away.
IGNORED_TABLES: set[str] = set()


def _upgraded_database(path: Path) -> None:
    """Run the migrations exactly as deployment does.

    In a subprocess with DATABASE_URL set, because env.py deliberately reads
    the settings rather than alembic.ini - the one normalised URL is what makes
    Alembic and the application agree about the driver.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env={
            **_clean_env(),
            "DATABASE_URL": f"sqlite:///{path.as_posix()}",
        },
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            "alembic upgrade head failed:\n"
            f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )


def _clean_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    return env


def test_migrations_produce_the_schema_the_models_declare(tmp_path):
    db_path = tmp_path / "drift.db"
    _upgraded_database(db_path)

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    interesting = [d for d in diff if _table_of(d) not in IGNORED_TABLES]
    assert not interesting, (
        "The migrations and the models disagree. Either a migration is "
        "missing, or a model was changed without one:\n  "
        + "\n  ".join(str(d) for d in interesting)
    )


def _table_of(difference) -> str | None:
    """Alembic's diff entries are tuples whose shape varies by kind; the table
    name is what this test needs and it is not always in the same position."""
    for part in difference if isinstance(difference, tuple) else ():
        name = getattr(part, "table", None)
        if name is not None:
            return getattr(name, "name", None) or str(name)
        if hasattr(part, "name") and hasattr(part, "columns"):
            return None
    return None
