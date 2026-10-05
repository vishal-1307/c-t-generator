"""Demo dataset.

Deliberately sized so a valid timetable exists but is not trivial to find:
4 sections x 7 subjects, with the two practicals competing for 3 labs, one
faculty room that must never be scheduled, and Monday-Friday only.

Run with:  uv run python -m app.seed
"""
from __future__ import annotations

from sqlalchemy import delete as sa_delete

from .database import Base, SessionLocal, engine
from .grid import build_grid
from .models import (
    AcademicContext,
    Assignment,
    Faculty,
    Room,
    Section,
    SectionSubjectAssignment,
    Subject,
    TimeSlot,
    TimetableRun,
)

CONTEXT = ("2026-27", 1, "BCA", "CSE")

SUBJECTS = [
    # (name, code, type, session_length_hours, sessions_per_week, required_lab_type)
    ("Data Structures", "CS201", "theory", 1, 4, None),
    ("Operating Systems", "CS202", "theory", 1, 4, None),
    ("Database Systems", "CS203", "theory", 1, 3, None),
    ("Computer Networks", "CS204", "theory", 1, 3, None),
    ("Engineering Mathematics", "MA201", "theory", 1, 4, None),
    ("Data Structures Lab", "CS251", "practical", 2, 1, "COMPUTING"),
    ("Database Lab", "CS253", "practical", 2, 1, "COMPUTING"),
]

FACULTY = [
    ("Dr. A. Sharma", "FAC01", ["CS201", "CS251"]),
    ("Dr. B. Iyer", "FAC02", ["CS202"]),
    ("Dr. C. Nair", "FAC03", ["CS203", "CS253"]),
    ("Dr. D. Rao", "FAC04", ["CS204"]),
    ("Dr. E. Menon", "FAC05", ["MA201"]),
    ("Dr. F. Gupta", "FAC06", ["CS201", "CS202"]),
    ("Dr. G. Bose", "FAC07", ["CS203", "CS204", "CS253"]),
    ("Dr. H. Verma", "FAC08", ["MA201", "CS251"]),
]

ROOMS = [
    # (room_number, block, floor, capacity, room_type, lab_type)
    ("A101", "A", "1", 70, "theory", None),
    ("A102", "A", "1", 70, "theory", None),
    ("A103", "A", "1", 70, "theory", None),
    ("A104", "A", "1", 70, "theory", None),
    ("B201", "B", "2", 70, "theory", None),
    ("B202", "B", "2", 70, "theory", None),
    ("LAB1", "C", "1", 70, "lab", "COMPUTING"),
    ("LAB2", "C", "1", 70, "lab", "COMPUTING"),
    ("LAB3", "C", "1", 70, "lab", "COMPUTING"),
    # A faculty office - must never be offered to the scheduler.
    ("A-F1", "A", "1", 2, "faculty", None),
]

SECTIONS = [
    # (section_number, strength)
    ("CSE-3A", 62),
    ("CSE-3B", 65),
    ("CSE-3C", 58),
    ("CSE-3D", 60),
]

# Every section takes the full curriculum.
SECTION_SUBJECTS = [s[1] for s in SUBJECTS]


def seed(reset: bool = True) -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if reset:
            # Order matters: children before parents.
            for model in (
                Assignment, SectionSubjectAssignment, TimetableRun,
                TimeSlot, Section, Room, Faculty, Subject, AcademicContext,
            ):
                db.execute(sa_delete(model))
            db.execute(sa_delete(Base.metadata.tables["section_subject"]))
            db.execute(sa_delete(Base.metadata.tables["faculty_subject"]))
            db.commit()

        year, semester, program, department = CONTEXT
        ctx = AcademicContext(
            academic_year=year, semester=semester, program=program, department=department
        )
        db.add(ctx)
        db.flush()

        subjects = {}
        for name, code, type_, length, spw, lab_type in SUBJECTS:
            subj = Subject(
                name=name,
                code=code,
                type=type_,
                session_length_hours=length,
                sessions_per_week=spw,
                required_lab_type=lab_type,
            )
            db.add(subj)
            subjects[code] = subj
        db.flush()

        for name, code, taught in FACULTY:
            fac = Faculty(name=name, faculty_code=code)
            fac.subjects = [subjects[c] for c in taught]
            db.add(fac)

        for number, block, floor, capacity, room_type, lab_type in ROOMS:
            db.add(
                Room(
                    room_number=number,
                    block=block,
                    floor=floor,
                    capacity=capacity,
                    room_type=room_type,
                    lab_type=lab_type,
                )
            )
        db.flush()

        for number, strength in SECTIONS:
            section = Section(
                academic_context_id=ctx.id, section_number=number, strength=strength
            )
            section.subjects = [subjects[c] for c in SECTION_SUBJECTS]
            db.add(section)

        db.add_all(build_grid())
        db.commit()

        teaching = db.query(TimeSlot).filter(TimeSlot.is_lunch.is_(False)).count()
        demand = sum(s.session_length_hours * s.sessions_per_week for s in subjects.values())
        days = sorted({s.day for s in db.query(TimeSlot).all()})
        print(f"Academic context: {ctx.label}")
        print(
            f"Seeded: {len(SUBJECTS)} subjects, {len(FACULTY)} faculty, "
            f"{len(ROOMS)} rooms (incl. 1 faculty room), {len(SECTIONS)} sections, "
            f"{db.query(TimeSlot).count()} slots ({teaching} teachable) across {days}."
        )
        print(f"Each section demands {demand} periods/week against {teaching} available.")
        print("No lunch slot is set - mark one on the Time Slots page.")
        print(f"POST /api/generate with {{\"academic_context_id\": {ctx.id}}} to solve.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
