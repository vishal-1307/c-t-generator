"""Phase 10 PART 1: cross-context resource conflict detection.

See TIMETABLE_LOGIC_SPEC.md for the full policy analysis; short version:

``AcademicContext`` = (academic_year, semester, program, department). Two
contexts with the **same** (academic_year, semester) run in real calendar
time *at the same time* - e.g. BCA/CSE and BTech/ECE both in "2026-27 Sem 1"
- and can therefore genuinely share the same physical Faculty and Room rows
concurrently, since those two catalogs are institution-wide, not scoped to a
context (see models.py's own module docstring). Two contexts with a
**different** (academic_year, semester) do not overlap in real time, so
reusing the same room or faculty across them is completely normal and must
never be flagged as a conflict.

This module is advisory, not a solver constraint: the CP-SAT model
deliberately keeps solving one context at a time (the historically fast,
well-tested formulation - §8a). Detecting a same-period clash *after* two
contexts have each been solved and published is the right layer for this -
each context's own solve genuinely cannot see the other context's concurrent
existence, the same way two separate departments' timetablers historically
would not have seen each other's draft either. No solver constraint changed;
this is a read-only reporting layer over data that already exists.

Only **published** runs are compared. A draft is not yet a real commitment -
comparing against one would flag perfectly normal in-progress work in another
department as a false alarm.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import AcademicContext, Assignment, TimetableRun


@dataclass
class ResourceClash:
    kind: str  # "faculty" | "room"
    resource_id: int
    resource_label: str
    timeslot_id: int
    day: str
    period_index: int
    context_a_id: int
    context_a_label: str
    run_a_id: int
    section_a: str
    subject_a: str
    context_b_id: int
    context_b_label: str
    run_b_id: int
    section_b: str
    subject_b: str

    @property
    def message(self) -> str:
        what = "Faculty" if self.kind == "faculty" else "Room"
        return (
            f"{what} {self.resource_label} is double-booked on {self.day} "
            f"P{self.period_index + 1}: {self.context_a_label} "
            f"({self.section_a}/{self.subject_a}) vs {self.context_b_label} "
            f"({self.section_b}/{self.subject_b})"
        )


def same_period_contexts(db: Session, academic_context_id: int) -> list[AcademicContext]:
    """Every OTHER context sharing this one's (academic_year, semester) - the
    contexts that run concurrently, in real time, with this one."""
    ctx = db.get(AcademicContext, academic_context_id)
    if ctx is None:
        return []
    return (
        db.query(AcademicContext)
        .filter(
            AcademicContext.academic_year == ctx.academic_year,
            AcademicContext.semester == ctx.semester,
            AcademicContext.id != ctx.id,
        )
        .all()
    )


def _published_run(db: Session, academic_context_id: int) -> TimetableRun | None:
    return (
        db.query(TimetableRun)
        .filter(
            TimetableRun.academic_context_id == academic_context_id,
            TimetableRun.publish_status == "PUBLISHED",
        )
        .first()
    )


def find_cross_context_clashes(db: Session, academic_context_id: int) -> list[ResourceClash]:
    """Real faculty/room double-bookings between this context's published run
    and every other published run in the same real-world period.

    Empty whenever nothing in this context is published yet, or no other
    context shares its (academic_year, semester) - which is the overwhelming
    common case (most academic contexts represent different terms) and
    correctly produces zero cost/noise for it.
    """
    run_a = _published_run(db, academic_context_id)
    if run_a is None:
        return []
    ctx = db.get(AcademicContext, academic_context_id)
    if ctx is None:
        return []

    clashes: list[ResourceClash] = []
    for other in same_period_contexts(db, academic_context_id):
        run_b = _published_run(db, other.id)
        if run_b is None:
            continue

        rows_a = db.query(Assignment).filter(Assignment.run_id == run_a.id).all()
        rows_b = db.query(Assignment).filter(Assignment.run_id == run_b.id).all()

        by_faculty_slot_b = {(a.faculty_id, a.timeslot_id): a for a in rows_b}
        by_room_slot_b = {(a.room_id, a.timeslot_id): a for a in rows_b}

        for a in rows_a:
            hit_fac = by_faculty_slot_b.get((a.faculty_id, a.timeslot_id))
            if hit_fac is not None:
                clashes.append(
                    _clash("faculty", a.faculty_id, a.faculty.name, a, hit_fac, ctx, other, run_a, run_b)
                )
            hit_room = by_room_slot_b.get((a.room_id, a.timeslot_id))
            if hit_room is not None:
                clashes.append(
                    _clash("room", a.room_id, a.room.room_number, a, hit_room, ctx, other, run_a, run_b)
                )
    return clashes


def _clash(
    kind: str, resource_id: int, label: str, a: Assignment, b: Assignment,
    ctx_a: AcademicContext, ctx_b: AcademicContext, run_a: TimetableRun, run_b: TimetableRun,
) -> ResourceClash:
    return ResourceClash(
        kind=kind, resource_id=resource_id, resource_label=label,
        timeslot_id=a.timeslot_id, day=a.timeslot.day, period_index=a.timeslot.period_index,
        context_a_id=ctx_a.id, context_a_label=ctx_a.label, run_a_id=run_a.id,
        section_a=a.section.section_number, subject_a=a.subject.code,
        context_b_id=ctx_b.id, context_b_label=ctx_b.label, run_b_id=run_b.id,
        section_b=b.section.section_number, subject_b=b.subject.code,
    )
