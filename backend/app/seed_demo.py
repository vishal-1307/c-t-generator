"""The demo dataset (Phase 13).

**This is invented sample data. It is not real college data and has never been checked
against a real college timetable.** It exists so the application can be explored,
demonstrated and tested end to end before real data arrives. Every name here
is fictional; the codes follow a plausible shape so the screens look like the
real thing, and the academic context is labelled ``(DEMO)`` so nobody can
mistake it for production.

### What it is sized to demonstrate

* 6 sections of differing strength, so room capacity actually bites
* 14 faculty, several teaching the same subject across sections
* 12 subjects, including two-period labs and one three-period design studio
* 18 rooms of varying capacity, three lab types, and two faculty offices that
  must never be scheduled into
* a **deliberately scarce** electronics lab - one room, wanted by two subjects,
  so the diagnostics have something real to explain
* faculty and room unavailability, so those constraints are visible rather
  than theoretical

### Why it does not simply wipe the database

``app/seed.py`` (the older, smaller fixture) deletes every row before
inserting. That is fine for a scratch checkout and unacceptable for anything
someone has put real data into. This module therefore:

* **upserts by code**, so running it twice changes nothing the second time;
* **scopes its reset to a manifest** of exactly the codes it creates; and
* **refuses to delete anything another academic context is using**, so a demo
  reset can never take real data with it.

Run with::

    uv run python -m app.seed_demo            # create or update
    uv run python -m app.seed_demo --reset    # remove the demo data
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging

from sqlalchemy.orm import Session

from .config import settings
from .database import Base, SessionLocal, engine
from .grid import build_grid
from .models import (
    AcademicContext,
    Assignment,
    Faculty,
    FacultyUnavailability,
    Room,
    RoomUnavailability,
    Section,
    SectionSubjectAssignment,
    Subject,
    TimeSlot,
    TimetableRun,
)

logger = logging.getLogger("timetable.seed")

# The label that makes the data unmistakable in every screen that shows a
# context, and the key the reset uses to find its own work.
DEMO_YEAR = "2026-27"
DEMO_SEMESTER = 3
DEMO_PROGRAM = "B.Tech CSE (DEMO)"
DEMO_DEPARTMENT = "School of Computer Science (DEMO)"

# ---------------------------------------------------------------- subjects
# (code, name, type, session_length_hours, sessions_per_week, required_lab_type)
SUBJECTS: list[tuple[str, str, str, int, int, str | None]] = [
    ("CSE301", "Data Structures and Algorithms", "theory", 1, 4, None),
    ("CSE302", "Operating Systems", "theory", 1, 3, None),
    ("CSE303", "Database Management Systems", "theory", 1, 3, None),
    ("CSE304", "Computer Networks", "theory", 1, 3, None),
    ("CSE305", "Software Engineering", "theory", 1, 2, None),
    ("MTH301", "Discrete Mathematics", "theory", 1, 3, None),
    ("ECE281", "Digital Electronics", "theory", 1, 3, None),
    ("MGT301", "Engineering Management", "theory", 1, 2, None),
    # Two-period labs.
    ("CSE351", "Data Structures Laboratory", "practical", 2, 1, "COMPUTING"),
    ("CSE353", "Database Laboratory", "practical", 2, 1, "COMPUTING"),
    # The scarce one: only ELEC-1 has this lab type, and two subjects want it.
    ("ECE351", "Digital Electronics Laboratory", "practical", 2, 1, "ELECTRONICS"),
    # A three-period block, to exercise longer contiguous sessions.
    ("CSE391", "Design Studio", "practical", 3, 1, "COMPUTING"),
]

# ---------------------------------------------------------------- faculty
# (code, name, department, [subject codes they may teach])
FACULTY: list[tuple[str, str, str, list[str]]] = [
    ("T-01", "Dr. Anita Rao", "Computer Science", ["CSE301", "CSE351"]),
    ("T-02", "Dr. Praveen Malik", "Computer Science", ["CSE302", "CSE301"]),
    ("T-03", "Dr. Sunita Menon", "Computer Science", ["CSE303", "CSE353"]),
    ("T-04", "Dr. Rajesh Iyer", "Computer Science", ["CSE304", "CSE302"]),
    ("T-05", "Dr. Kavita Bose", "Computer Science", ["CSE305", "CSE391"]),
    ("T-06", "Dr. Meera Nair", "Mathematics", ["MTH301"]),
    ("T-07", "Dr. Sanjay Verma", "Electronics", ["ECE281", "ECE351"]),
    ("T-08", "Dr. Neha Gupta", "Management", ["MGT301"]),
    # Second teachers for the heavily-taken subjects, so six sections can run
    # them in parallel.
    ("T-09", "Dr. Vikram Sethi", "Computer Science", ["CSE301", "CSE303"]),
    ("T-10", "Dr. Priya Krishnan", "Computer Science", ["CSE302", "CSE304"]),
    ("T-11", "Dr. Arjun Desai", "Computer Science", ["CSE351", "CSE353", "CSE391"]),
    ("T-12", "Dr. Latha Subramanian", "Mathematics", ["MTH301", "CSE305"]),
    ("T-13", "Dr. Imran Sheikh", "Electronics", ["ECE281", "ECE351"]),
    ("T-14", "Dr. Rohit Chandra", "Computer Science", ["CSE303", "CSE304", "MGT301"]),
]

# ---------------------------------------------------------------- rooms
# (room_number, block, floor, capacity, room_type, lab_type)
ROOMS: list[tuple[str, str, str, int, str, str | None]] = [
    # Large theory rooms - the only ones that seat the biggest sections.
    ("34-101", "34", "1", 80, "theory", None),
    ("34-102", "34", "1", 80, "theory", None),
    ("34-103", "34", "1", 75, "theory", None),
    ("36-201", "36", "2", 70, "theory", None),
    ("36-202", "36", "2", 70, "theory", None),
    ("36-203", "36", "2", 65, "theory", None),
    ("38-301", "38", "3", 60, "theory", None),
    ("38-302", "38", "3", 60, "theory", None),
    # Small rooms - deliberately too small for most sections, so capacity
    # filtering is visible rather than theoretical.
    ("38-303", "38", "3", 40, "theory", None),
    ("38-304", "38", "3", 35, "theory", None),
    # Computing labs. COMP1 is the only one that seats the two largest
    # sections, so it is contended without being infeasible.
    ("40-COMP1", "40", "1", 80, "lab", "COMPUTING"),
    ("40-COMP2", "40", "1", 70, "lab", "COMPUTING"),
    ("40-COMP3", "40", "1", 60, "lab", "COMPUTING"),
    # The scarce electronics lab: one room for the whole cohort that needs it.
    ("40-ELEC1", "40", "2", 80, "lab", "ELECTRONICS"),
    # A mechanical lab nothing in this dataset needs - present so "eligible"
    # means something narrower than "is a lab".
    ("40-MECH1", "40", "2", 50, "lab", "MECHANICAL"),
    ("42-101", "42", "1", 70, "theory", None),
    # Faculty offices. Must never appear in a timetable.
    ("34-F01", "34", "1", 4, "faculty", None),
    ("36-F02", "36", "2", 6, "faculty", None),
]

# ---------------------------------------------------------------- sections
# (section_number, strength, [subject codes])
CORE = ["CSE301", "CSE302", "CSE303", "MTH301"]
SECTIONS: list[tuple[str, int, list[str]]] = [
    # Large sections: only the 75-80 seat rooms fit them.
    ("D2401", 78, CORE + ["CSE304", "CSE351"]),
    ("D2402", 75, CORE + ["ECE281", "CSE351", "ECE351"]),
    # Mid-sized.
    ("D2403", 68, CORE + ["CSE304", "CSE353"]),
    ("D2404", 64, CORE + ["CSE305", "CSE353"]),
    # Smaller sections, which can also use the 60-seat rooms.
    ("D2405", 58, CORE + ["ECE281", "ECE351"]),
    ("D2406", 55, CORE + ["MGT301", "CSE391"]),
]

# ------------------------------------------------------- unavailability
# (faculty_code, day, 1-based period) - a research day and some part-time
# arrangements, so the constraint is exercised rather than merely supported.
FACULTY_UNAVAILABLE: list[tuple[str, str, int]] = [
    ("T-06", "Friday", 1), ("T-06", "Friday", 2), ("T-06", "Friday", 3),
    ("T-08", "Monday", 1), ("T-08", "Monday", 2),
    ("T-13", "Wednesday", 8), ("T-13", "Wednesday", 9),
]

# (room_number, day, 1-based period) - a maintenance window.
ROOM_UNAVAILABLE: list[tuple[str, str, int]] = [
    ("40-COMP3", "Thursday", 8), ("40-COMP3", "Thursday", 9),
]

# The lunch break, as a 1-based period index. A per-slot data toggle, not a
# rule baked into the grid generator.
LUNCH_PERIOD = 5


class DemoDataDisabled(RuntimeError):
    pass


def _require_enabled() -> None:
    if not settings.demo_features_enabled:
        raise DemoDataDisabled(
            "Demo data is disabled. It is off by default when "
            "APP_ENV=production, because seeding or resetting sample data has "
            "no business running against a live semester. Set "
            "ENABLE_DEMO_DATA=true and APP_ENV=development to use it."
        )


def _context(db: Session) -> AcademicContext | None:
    return (
        db.query(AcademicContext)
        .filter(
            AcademicContext.academic_year == DEMO_YEAR,
            AcademicContext.semester == DEMO_SEMESTER,
            AcademicContext.program == DEMO_PROGRAM,
        )
        .first()
    )


def seed_demo(db: Session, *, quiet: bool = False) -> dict[str, int]:
    """Create or refresh the demo dataset. Safe to run repeatedly.

    Everything is matched by its natural key (subject code, faculty code, room
    number, section number within the demo context), so a second run updates
    the rows it made rather than inserting duplicates.
    """
    _require_enabled()
    counts = {"created": 0, "updated": 0}

    ctx = _context(db)
    if ctx is None:
        ctx = AcademicContext(
            academic_year=DEMO_YEAR, semester=DEMO_SEMESTER,
            program=DEMO_PROGRAM, department=DEMO_DEPARTMENT,
        )
        db.add(ctx)
        db.flush()
        counts["created"] += 1

    # --- subjects
    subjects: dict[str, Subject] = {}
    for code, name, type_, length, spw, lab_type in SUBJECTS:
        row = db.query(Subject).filter(Subject.code == code).first()
        if row is None:
            row = Subject(code=code)
            db.add(row)
            counts["created"] += 1
        else:
            counts["updated"] += 1
        row.name, row.type = name, type_
        row.session_length_hours, row.sessions_per_week = length, spw
        row.required_lab_type, row.is_active = lab_type, True
        subjects[code] = row
    db.flush()

    # --- rooms
    rooms: dict[str, Room] = {}
    for number, block, floor, capacity, room_type, lab_type in ROOMS:
        row = db.query(Room).filter(Room.room_number == number).first()
        if row is None:
            row = Room(room_number=number)
            db.add(row)
            counts["created"] += 1
        else:
            counts["updated"] += 1
        row.block, row.floor, row.capacity = block, floor, capacity
        row.room_type, row.lab_type, row.is_active = room_type, lab_type, True
        rooms[number] = row
    db.flush()

    # --- faculty (and their subject eligibility)
    faculty: dict[str, Faculty] = {}
    for code, name, department, teaches in FACULTY:
        row = db.query(Faculty).filter(Faculty.faculty_code == code).first()
        if row is None:
            row = Faculty(faculty_code=code)
            db.add(row)
            counts["created"] += 1
        else:
            counts["updated"] += 1
        row.name, row.department, row.is_active = name, department, True
        row.subjects = [subjects[c] for c in teaches]
        faculty[code] = row
    db.flush()

    # --- time slots. Only built if the grid is empty: the slot layout is
    # institution-wide configuration, and silently replacing it would discard
    # break positions somebody had set.
    if db.query(TimeSlot).count() == 0:
        slots = build_grid()
        for slot in slots:
            if slot.period_index == LUNCH_PERIOD - 1:
                slot.is_lunch = True
        db.add_all(slots)
        db.flush()
        counts["created"] += len(slots)

    slot_by_day_period = {
        (s.day, s.period_index + 1): s for s in db.query(TimeSlot).all()
    }

    # --- sections (scoped to the demo context) and their curriculum
    for number, strength, subject_codes in SECTIONS:
        row = (
            db.query(Section)
            .filter(
                Section.academic_context_id == ctx.id,
                Section.section_number == number,
            )
            .first()
        )
        if row is None:
            row = Section(academic_context_id=ctx.id, section_number=number)
            db.add(row)
            counts["created"] += 1
        else:
            counts["updated"] += 1
        row.strength, row.is_active = strength, True
        row.subjects = [subjects[c] for c in subject_codes]
    db.flush()

    # --- unavailability
    for code, day, period in FACULTY_UNAVAILABLE:
        slot = slot_by_day_period.get((day, period))
        if slot is None:
            continue
        exists = (
            db.query(FacultyUnavailability)
            .filter(
                FacultyUnavailability.faculty_id == faculty[code].id,
                FacultyUnavailability.timeslot_id == slot.id,
            )
            .first()
        )
        if exists is None:
            db.add(FacultyUnavailability(
                faculty_id=faculty[code].id, timeslot_id=slot.id,
                reason="Demo: standing commitment",
            ))
            counts["created"] += 1

    for number, day, period in ROOM_UNAVAILABLE:
        slot = slot_by_day_period.get((day, period))
        if slot is None:
            continue
        exists = (
            db.query(RoomUnavailability)
            .filter(
                RoomUnavailability.room_id == rooms[number].id,
                RoomUnavailability.timeslot_id == slot.id,
            )
            .first()
        )
        if exists is None:
            db.add(RoomUnavailability(
                room_id=rooms[number].id, timeslot_id=slot.id,
                reason="Demo: scheduled maintenance",
            ))
            counts["created"] += 1

    db.commit()
    if not quiet:
        logger.info(
            "demo data ready: context=%s sections=%d subjects=%d faculty=%d rooms=%d",
            ctx.label, len(SECTIONS), len(SUBJECTS), len(FACULTY), len(ROOMS),
        )
    return counts


def reset_demo(db: Session, *, quiet: bool = False) -> dict[str, int]:
    """Remove the demo dataset, and nothing else.

    Every deletion is scoped to this module's own manifest, and any shared
    entity still referenced by a *different* academic context is kept. That is
    the property that makes this safe to expose as a button: a reset can
    remove the demo and cannot take somebody's real data with it.
    """
    _require_enabled()
    removed = {"contexts": 0, "sections": 0, "runs": 0, "subjects": 0,
               "faculty": 0, "rooms": 0, "kept_in_use": 0}

    ctx = _context(db)
    if ctx is not None:
        section_ids = [
            s.id for s in db.query(Section).filter(
                Section.academic_context_id == ctx.id
            )
        ]
        run_ids = [
            r.id for r in db.query(TimetableRun).filter(
                TimetableRun.academic_context_id == ctx.id
            )
        ]
        if run_ids:
            db.query(Assignment).filter(Assignment.run_id.in_(run_ids)).delete(
                synchronize_session=False
            )
        db.query(SectionSubjectAssignment).filter(
            SectionSubjectAssignment.academic_context_id == ctx.id
        ).delete(synchronize_session=False)
        if run_ids:
            db.query(TimetableRun).filter(TimetableRun.id.in_(run_ids)).delete(
                synchronize_session=False
            )
            removed["runs"] = len(run_ids)
        for section in db.query(Section).filter(Section.id.in_(section_ids)).all():
            section.subjects = []
            db.delete(section)
        removed["sections"] = len(section_ids)
        db.flush()
        db.delete(ctx)
        removed["contexts"] = 1
        db.flush()

    demo_subject_codes = {s[0] for s in SUBJECTS}
    demo_faculty_codes = {f[0] for f in FACULTY}
    demo_room_numbers = {r[0] for r in ROOMS}

    # Shared entities are only removed when nothing outside the demo uses
    # them. "Used" means a surviving section takes the subject, or any
    # assignment in any remaining run references the row.
    for subject in db.query(Subject).filter(Subject.code.in_(demo_subject_codes)).all():
        in_use = bool(subject.sections) or db.query(Assignment).filter(
            Assignment.subject_id == subject.id
        ).first() is not None
        if in_use:
            removed["kept_in_use"] += 1
            continue
        subject.faculties = []
        subject.allowed_rooms = []
        db.delete(subject)
        removed["subjects"] += 1

    for member in db.query(Faculty).filter(
        Faculty.faculty_code.in_(demo_faculty_codes)
    ).all():
        in_use = db.query(Assignment).filter(
            Assignment.faculty_id == member.id
        ).first() is not None
        if in_use:
            removed["kept_in_use"] += 1
            continue
        # Unavailability rows go with the parent via the relationship
        # cascade; deleting them here as well produced a SAWarning about a
        # DELETE that matched nothing.
        member.subjects = []
        db.delete(member)
        removed["faculty"] += 1

    for room in db.query(Room).filter(Room.room_number.in_(demo_room_numbers)).all():
        in_use = db.query(Assignment).filter(
            Assignment.room_id == room.id
        ).first() is not None
        if in_use:
            removed["kept_in_use"] += 1
            continue
        db.delete(room)
        removed["rooms"] += 1

    db.commit()
    if not quiet:
        logger.info("demo data removed: %s", removed)
    return removed


def status(db: Session) -> dict:
    """What the demo currently looks like, for the UI and the CLI."""
    ctx = _context(db)
    if ctx is None:
        return {"present": False, "label": None, "sections": 0, "runs": 0}
    return {
        "present": True,
        "label": ctx.label,
        "academic_context_id": ctx.id,
        "sections": db.query(Section).filter(
            Section.academic_context_id == ctx.id
        ).count(),
        "runs": db.query(TimetableRun).filter(
            TimetableRun.academic_context_id == ctx.id
        ).count(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create, refresh or remove the DEMO dataset. "
                    "This is invented sample data, not real college data."
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Remove the demo data instead of creating it.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if args.reset:
            print("Removed:", reset_demo(db))
        else:
            print("Seeded:", seed_demo(db))
            print("Status:", status(db))
            print(
                "\nThis is DEMO data - invented for demonstration and testing. "
                "It is not real college data."
            )


if __name__ == "__main__":
    main()
