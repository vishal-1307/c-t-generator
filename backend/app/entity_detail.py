"""Phase 9: shared helpers for the entity detail pages (faculty/subject/
section/room).

Two things are computed here that no existing endpoint already provides:

* **Cross-context assignments.** Faculty, Subjects and Rooms are
  institution-wide (not scoped to one AcademicContext - see models.py's
  module docstring), so a faculty member's real workload can span several
  contexts at once. ``SectionSubjectAssignment`` is the persistence anchor for
  each of those, independently of which one currently has a published run.
* **Live occupancy.** "Is this room occupied right now" is derived from the
  actual server clock against every *published* run's Assignment rows - not
  guessed, not left to the frontend to compute from a full grid fetch.

Nothing here duplicates the timetable grid or availability views - those stay
served by /api/timetable/* and /api/availability/* respectively.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from .models import (
    Assignment,
    FacultyUnavailability,
    RoomUnavailability,
    SectionSubjectAssignment,
    SectionUnavailability,
    TimeSlot,
    TimetableRun,
)


def assignment_briefs(
    db: Session,
    *,
    faculty_id: int | None = None,
    subject_id: int | None = None,
    section_id: int | None = None,
) -> list[dict]:
    """Every persistent (context, section, subject) assignment touching this
    resource, across every academic context - not just the currently
    selected one, since these entities are shared institution-wide."""
    q = db.query(SectionSubjectAssignment)
    if faculty_id is not None:
        q = q.filter(SectionSubjectAssignment.faculty_id == faculty_id)
    if subject_id is not None:
        q = q.filter(SectionSubjectAssignment.subject_id == subject_id)
    if section_id is not None:
        q = q.filter(SectionSubjectAssignment.section_id == section_id)

    out = []
    for a in q.all():
        out.append(
            {
                "academic_context_id": a.academic_context_id,
                "academic_context_label": a.section.academic_context.label,
                "section_id": a.section_id,
                "section_number": a.section.section_number,
                "subject_id": a.subject_id,
                "subject_code": a.subject.code,
                "subject_name": a.subject.name,
                "periods_per_week": a.subject.periods_per_week,
                "faculty_id": a.faculty_id,
                "faculty_name": a.faculty.name if a.faculty else None,
                "room_id": a.room_id,
                "room_number": a.room.room_number if a.room else None,
                "locked": a.locked,
            }
        )
    return out


def blocked_slots(db: Session, model, id_field, resource_id: int) -> list:
    """Declared unavailability rows for one faculty/room/section, as TimeSlot
    objects - the same rows the availability endpoints already read, just
    surfaced inline so the detail page needs no second request for this."""
    rows = (
        db.query(model)
        .filter(id_field == resource_id)
        .join(TimeSlot)
        .order_by(TimeSlot.day_index, TimeSlot.period_index)
        .all()
    )
    return [r.timeslot for r in rows]


def faculty_blocked_slots(db: Session, faculty_id: int) -> list:
    return blocked_slots(db, FacultyUnavailability, FacultyUnavailability.faculty_id, faculty_id)


def room_blocked_slots(db: Session, room_id: int) -> list:
    return blocked_slots(db, RoomUnavailability, RoomUnavailability.room_id, room_id)


def section_blocked_slots(db: Session, section_id: int) -> list:
    return blocked_slots(db, SectionUnavailability, SectionUnavailability.section_id, section_id)


def _current_slot(db: Session, *, now: dt.datetime | None = None) -> TimeSlot | None:
    """The TimeSlot the real-world clock is inside right now, or None if
    that's outside every defined slot (evening, weekend, before/after the
    configured day). Weekday() is 0=Monday, matching TimeSlot.day_index."""
    now = now or dt.datetime.now()
    return (
        db.query(TimeSlot)
        .filter(
            TimeSlot.day_index == now.weekday(),
            TimeSlot.is_lunch.is_(False),
            TimeSlot.start_time <= now.time(),
            TimeSlot.end_time > now.time(),
        )
        .first()
    )


def room_current_status(db: Session, room, *, now: dt.datetime | None = None) -> dict:
    if not room.is_active:
        return {"state": "inactive", "detail": "This room is marked inactive and is never offered to the scheduler."}

    slot = _current_slot(db, now=now)
    if slot is None:
        return {"state": "free", "detail": "Outside teaching hours right now."}

    hit = (
        db.query(Assignment)
        .join(TimetableRun, Assignment.run_id == TimetableRun.id)
        .filter(
            TimetableRun.publish_status == "PUBLISHED",
            Assignment.room_id == room.id,
            Assignment.timeslot_id == slot.id,
        )
        .first()
    )
    if hit is None:
        return {"state": "free", "detail": f"Free right now ({slot.day} P{slot.period_index + 1})."}
    return {
        "state": "occupied",
        "detail": f"In use by {hit.section.section_number} - {hit.subject.code} "
        f"({hit.faculty.name}), {slot.day} P{slot.period_index + 1}.",
    }


def faculty_current_status(db: Session, faculty, *, now: dt.datetime | None = None) -> dict:
    slot = _current_slot(db, now=now)
    if slot is None:
        return {"state": "free", "detail": "Outside teaching hours right now."}

    hit = (
        db.query(Assignment)
        .join(TimetableRun, Assignment.run_id == TimetableRun.id)
        .filter(
            TimetableRun.publish_status == "PUBLISHED",
            Assignment.faculty_id == faculty.id,
            Assignment.timeslot_id == slot.id,
        )
        .first()
    )
    if hit is None:
        return {"state": "free", "detail": f"Free right now ({slot.day} P{slot.period_index + 1})."}
    return {
        "state": "occupied",
        "detail": f"Teaching {hit.section.section_number} - {hit.subject.code} in "
        f"{hit.room.room_number}, {slot.day} P{slot.period_index + 1}.",
    }


def section_rooms_used(db: Session, section_id: int) -> list:
    """Distinct rooms this section has actually been placed in, across every
    published run for its academic context - a section belongs to exactly
    one context, so no cross-context ambiguity here."""
    rows = (
        db.query(Assignment)
        .join(TimetableRun, Assignment.run_id == TimetableRun.id)
        .filter(TimetableRun.publish_status == "PUBLISHED", Assignment.section_id == section_id)
        .all()
    )
    seen: dict[int, object] = {}
    for a in rows:
        seen.setdefault(a.room_id, a.room)
    return list(seen.values())


def room_utilization(db: Session, room_id: int) -> dict:
    """Share of the week's teachable periods this room is booked in, across
    every published run - the honest denominator, since a room shared by two
    concurrently published programs should show combined usage, not just one
    context's."""
    teachable = db.query(TimeSlot).filter(TimeSlot.is_lunch.is_(False)).count()
    occupied = (
        db.query(Assignment.timeslot_id)
        .join(TimetableRun, Assignment.run_id == TimetableRun.id)
        .filter(TimetableRun.publish_status == "PUBLISHED", Assignment.room_id == room_id)
        .distinct()
        .count()
    )
    percent = round(100 * occupied / teachable, 1) if teachable else 0.0
    return {"occupied_periods": occupied, "teachable_periods": teachable, "percent": percent}
