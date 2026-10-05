"""Delete all timetable data, keep everything that makes the application run.

Removes every row of application data - rooms, faculty, subjects, sections,
lab groups, mappings, availability, the time grid, timetable runs and their
assignments, change and import history, and the room lists of uploads.

Keeps the schema, the migration history, user accounts (so an administrator
can still sign in) and all configuration, which lives in the environment.

Never runs on its own. It is invoked explicitly, either here:

    python -m app.reset          # shows what would be deleted
    python -m app.reset --yes    # deletes it

or through `POST /api/admin/reset` with the confirmation phrase, which is how
it is run on a host without a shell. There is no button for it in the
application.
"""
from __future__ import annotations

import sys

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .database import Base
from .models import Room, Subject, User

# The only tables left alone. `user` so an administrator can still sign in;
# `alembic_version` because it is the schema's own record of itself.
KEPT_TABLES = frozenset({"user", "alembic_version"})

CONFIRMATION = "DELETE ALL TIMETABLE DATA"


def _tables():
    return [t for t in Base.metadata.sorted_tables if t.name not in KEPT_TABLES]


def data_counts(db: Session) -> dict[str, int]:
    """Rows in every table the reset would empty. Reads only."""
    return {
        t.name: db.execute(select(func.count()).select_from(t)).scalar_one()
        for t in _tables()
    }


def reset_application_data(db: Session) -> dict[str, int]:
    """Empty every application table, children before parents. Returns what
    was there. All or nothing: one transaction, rolled back on any error."""
    before = data_counts(db)
    try:
        # Three references would otherwise make the order matter or fail:
        # subject and room point at each other, and a user can be linked to a
        # faculty member. Cut them first; the users themselves stay.
        db.execute(update(Subject).values(fixed_room_id=None))
        db.execute(update(Room).values(fixed_subject_id=None))
        db.execute(update(User).values(faculty_id=None))
        # sorted_tables is parents-first, so reversed is children-first.
        for table in reversed(_tables()):
            db.execute(table.delete())
        db.commit()
    except Exception:
        db.rollback()
        raise
    return before


def main(argv: list[str]) -> int:
    from .database import SessionLocal

    with SessionLocal() as db:
        counts = data_counts(db)
        print("Application data on record:")
        for name, n in counts.items():
            if n:
                print(f"  {name:32} {n}")
        print(f"  (total {sum(counts.values())} rows; user accounts are kept)")
        if "--yes" not in argv:
            print("\nNothing deleted. Run again with --yes to delete all of it.")
            return 0
        reset_application_data(db)
        print("\nDeleted. The schema, migrations and user accounts are intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
