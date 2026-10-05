"""The saved room list, and clearing data a part at a time.

Rooms change rarely and a semester's teaching every time, so the two are kept
apart: Infra.xlsx is saved once and every later Load.xlsx is scheduled against
it. This module owns what that means for the database - which rooms the saved
infrastructure lists, what replacing it removes, and what each of the three
"clear" choices deletes.

Every destructive operation here is all-table and explicit, children before
parents, rather than relying on foreign-key cascades: SQLite, which the tests
run on, does not enforce them unless told to, and a delete that works in
production and silently leaves rows behind in tests is a delete nobody has
actually tested.
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .database import Base
from .models import (
    AcademicContext,
    Infrastructure,
    InfrastructureRoom,
    Room,
    SectionSubjectAssignment,
    Subject,
    TimetableRun,
    User,
)
from .room_scope import set_usable_rooms

ClearWhat = Literal["load", "infrastructure", "both"]

# Exact phrases the clear endpoint requires, so that no stray request - a
# mistyped script, a double-submitted form - can empty a table.
CONFIRMATIONS: dict[str, str] = {
    "load": "CLEAR LOAD DATA",
    "infrastructure": "CLEAR INFRASTRUCTURE",
    "both": "CLEAR ALL DATA",
}

# Timetables, which depend on both halves: a timetable is a teaching load
# placed in rooms, and stops being true the moment either is gone.
TIMETABLE_TABLES = ["change_history", "assignment", "timetable_run"]

# The teaching load. Children first.
LOAD_TABLES = TIMETABLE_TABLES + [
    "section_subject_assignment", "section_unavailability", "section_subject",
    "section_group", "section", "academic_context_room", "academic_context",
    "faculty_subject", "faculty_unavailability", "subject_allowed_room",
    "subject", "faculty",
]

# The rooms. Children first. The teaching load's own rows are untouched; its
# room lists go, because they list rooms that no longer exist.
INFRASTRUCTURE_TABLES = TIMETABLE_TABLES + [
    "academic_context_room", "subject_allowed_room", "room_unavailability",
    "infrastructure_room", "infrastructure", "room",
]


def _table(name: str):
    return Base.metadata.tables[name]


def _count(db: Session, name: str) -> int:
    return db.execute(select(func.count()).select_from(_table(name))).scalar_one()


# ------------------------------------------------------------ the saved list


def active(db: Session) -> Infrastructure | None:
    """The saved infrastructure, if there is one."""
    return db.query(Infrastructure).order_by(Infrastructure.id.desc()).first()


def room_ids(db: Session, infrastructure_id: int) -> set[int]:
    return {
        rid for (rid,) in db.query(InfrastructureRoom.room_id)
        .filter(InfrastructureRoom.infrastructure_id == infrastructure_id)
    }


def summary(db: Session, infrastructure: Infrastructure) -> dict[str, int]:
    """What the saved room list holds, counted the way the import counts it."""
    rooms = db.query(Room).filter(Room.id.in_(room_ids(db, infrastructure.id))).all()
    return {
        "rooms": len(rooms),
        "classrooms": sum(1 for r in rooms if r.room_type == "theory"),
        "labs": sum(1 for r in rooms if r.room_type == "lab"),
        "byod_rooms": sum(1 for r in rooms if r.byod),
    }


def timetable_count(db: Session) -> int:
    """Timetables that replacing or clearing the rooms would remove."""
    return db.query(TimetableRun).count()


def is_unchanged(db: Session, listed: set[int], room_changes: list) -> bool:
    """Whether a room list is the one already saved, room for room.

    Uploading the same file again is not a replacement: nothing any timetable
    depends on has changed, so nothing is removed.
    """
    current = active(db)
    return current is not None and not room_changes and room_ids(db, current.id) == listed


def save(
    db: Session, filename: str, listed: set[int], actor: str | None = None
) -> Infrastructure:
    """Make `listed` the saved infrastructure. Flushes; the caller commits.

    The same list as the saved one keeps the saved record. A different list
    replaces it: every timetable goes (each was built on the old rooms, and
    the user confirmed that before this is reached), and every dataset that
    was built on the old list is moved onto the new one so it can be generated
    again straight away.

    Rooms the new list leaves out stay on record, untouched - one may have
    been added by hand on the Rooms page - but no dataset lists them, so no
    timetable can use them. Clearing the infrastructure is what removes rooms.
    """
    current = active(db)
    if current is not None and room_ids(db, current.id) == listed:
        return current

    if current is not None:
        _delete_tables(db, TIMETABLE_TABLES)

    new = Infrastructure(filename=filename[:255], uploaded_by=actor)
    db.add(new)
    db.flush()
    db.add_all(InfrastructureRoom(infrastructure_id=new.id, room_id=rid) for rid in sorted(listed))

    if current is not None:
        for ctx in db.query(AcademicContext).filter(
            AcademicContext.infrastructure_id == current.id
        ):
            ctx.infrastructure_id = new.id
            set_usable_rooms(db, ctx.id, listed)
        db.execute(
            _table("infrastructure_room").delete()
            .where(_table("infrastructure_room").c.infrastructure_id == current.id)
        )
        db.execute(
            _table("infrastructure").delete()
            .where(_table("infrastructure").c.id == current.id)
        )

    db.flush()
    return new


# ------------------------------------------------------------------ clearing


def clear_counts(db: Session, what: ClearWhat) -> dict[str, int]:
    """Rows each clear would delete, per table. Reads only."""
    if what == "both":
        from .reset import data_counts

        return data_counts(db)
    tables = LOAD_TABLES if what == "load" else INFRASTRUCTURE_TABLES
    return {name: _count(db, name) for name in tables}


def clear(db: Session, what: ClearWhat) -> dict[str, int]:
    """Delete one half of the data, or both. Returns what was there.

    All or nothing: one transaction, rolled back on any error.
    """
    if what == "both":
        from .reset import reset_application_data

        return reset_application_data(db)

    before = clear_counts(db, what)
    try:
        if what == "load":
            # Rooms stay, and one can point at a subject; users stay, and one
            # can be a faculty member. Cut both before the rows they name go.
            db.execute(update(Room).values(fixed_subject_id=None))
            db.execute(update(User).values(faculty_id=None))
            db.execute(update(_table("section")).values(parent_section_id=None))
            _delete_tables(db, LOAD_TABLES)
        else:
            db.execute(update(Subject).values(fixed_room_id=None))
            db.execute(update(SectionSubjectAssignment).values(room_id=None))
            db.execute(update(AcademicContext).values(infrastructure_id=None))
            _delete_tables(db, INFRASTRUCTURE_TABLES)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return before


def _delete_tables(db: Session, names: list[str]) -> None:
    for name in names:
        db.execute(_table(name).delete())
