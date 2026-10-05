import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool

from alembic import context

# Make `app` importable when alembic is run from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: E402,F401 - registers every table on Base.metadata
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# The one URL these migrations run against, taken straight from application
# settings so Alembic and FastAPI can never disagree about the driver. It is
# already normalised to a dialect SQLAlchemy can load - see
# app.config.normalise_database_url.
#
# It is deliberately NOT pushed through `config.set_main_option("sqlalchemy.url",
# ...)` and `engine_from_config`, which is what this file used to do. That
# route had two independent faults:
#
#   1. It passed the *raw* `settings.database_url`. Normalisation lived in a
#      separate property that only app/database.py used, so a Render-supplied
#      `postgresql://` reached Alembic unmodified, SQLAlchemy defaulted it to
#      the psycopg2 dialect, and every deploy died with
#      `ModuleNotFoundError: No module named 'psycopg2'` during
#      `alembic upgrade head` - before the API process ever started.
#
#   2. alembic.ini is a ConfigParser file, where `%` begins an interpolation
#      token. A managed provider that percent-encodes a generated password
#      (`p%40ss`) would raise on set_main_option before a single migration ran.
#
# Reading the settings value directly removes both failure modes.
DATABASE_URL = settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite can't ALTER in place; batch mode rewrites the table.
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
