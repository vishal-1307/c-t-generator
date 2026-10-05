"""Reading the generated timetable: grids and the filterable master view.

Extracted from ``routers/timetable.py`` because other places need this logic
and were reaching for it through the router - ``exporters`` imported the route
function and called it with hand-built ``Query()`` defaults. A view service is
the thing it actually wanted; the router is now one caller among several.

The master view is answered in SQL. It used to load every assignment of a run,
apply ten of its filters in a Python loop, sort in Python, and slice the result
- so page forty cost exactly what page one did, and an export re-ran the whole
thing once per two thousand rows. At a real institution's size that is the
difference between a query and an outage.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, fields
from typing import Iterator

from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, contains_eager

from .config import settings
from .domain import PRACTICAL
from .models import (
    Assignment,
    Section,
    SectionSubjectAssignment,
    TimeSlot,
    TimetableRun,
)
from .schemas import GridCellOut, GridOut, GridRowOut, MasterRowOut


# --------------------------------------------------------------------- runs


# The statuses of a run that has classes in it. A run that failed, timed out
# before finding anything or is still solving is not "the timetable": showing
# it would replace a good timetable on every view with an empty one.
SHOWN_STATUSES = ("OPTIMAL", "FEASIBLE", "PARTIAL")


def current_run(db: Session, academic_context_id: int | None = None) -> TimetableRun | None:
    """The timetable in force: a dataset's published version when it has one,
    otherwise its newest version with classes in it.

    Scoped to one dataset when one is named; otherwise the dataset of the
    newest timetable anywhere. Publishing is how a coordinator says "this is
    the one": a draft generated afterwards to try something out does not
    replace it on any screen until it is published itself.

    Every view - the week grids, the master view, the teacher's table and every
    export - answers "which timetable?" here, so no two of them can show
    different runs.
    """
    newest = db.query(TimetableRun).filter(TimetableRun.status.in_(SHOWN_STATUSES))
    if academic_context_id is not None:
        newest = newest.filter(TimetableRun.academic_context_id == academic_context_id)
    newest = newest.order_by(TimetableRun.created_at.desc(), TimetableRun.id.desc()).first()
    if newest is None:
        return None
    published = published_run(db, newest.academic_context_id)
    return published or newest


def published_run(db: Session, academic_context_id: int) -> TimetableRun | None:
    return (
        db.query(TimetableRun)
        .filter(
            TimetableRun.academic_context_id == academic_context_id,
            TimetableRun.publish_status == "PUBLISHED",
            TimetableRun.status.in_(SHOWN_STATUSES),
        )
        .first()
    )


def resolve_run(
    db: Session, run_id: int | None, academic_context_id: int | None = None
) -> TimetableRun:
    """A specific run, or the timetable in force (`current_run`)."""
    if run_id is not None:
        run = db.get(TimetableRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        return run

    run = current_run(db, academic_context_id)
    if run is None:
        raise HTTPException(
            status_code=404, detail="No timetable has been generated yet."
        )
    return run


def locked_pairs(db: Session, academic_context_id: int) -> set[tuple[int, int, str]]:
    """The (section, subject, session_type) components an admin has pinned in
    this context.

    One small query rather than a per-row lookup, fetching only the locked
    ones. Keyed by ``session_type`` as well as (section, subject): a mixed
    subject has one row per component, and locking one (e.g. the lecture)
    must not make the other (its practical) appear locked too.
    """
    rows = (
        db.query(
            SectionSubjectAssignment.section_id,
            SectionSubjectAssignment.subject_id,
            SectionSubjectAssignment.session_type,
        )
        .filter(
            SectionSubjectAssignment.academic_context_id == academic_context_id,
            SectionSubjectAssignment.locked.is_(True),
        )
        .all()
    )
    return {(section_id, subject_id, session_type) for section_id, subject_id, session_type in rows}


# --------------------------------------------------------------------- grids


def build_grid(
    db: Session,
    run: TimetableRun,
    rows: list[Assignment],
    title: str,
    subtitle: str,
    perspective: str,
) -> GridOut:
    slots = db.query(TimeSlot).order_by(TimeSlot.day_index, TimeSlot.period_index).all()
    if not slots:
        raise HTTPException(status_code=404, detail="No time grid has been generated.")

    periods = sorted({s.period_index for s in slots})
    period_labels: dict[int, str] = {}
    for s in slots:
        period_labels.setdefault(
            s.period_index, f"{s.start_time:%H:%M}-{s.end_time:%H:%M}"
        )

    by_slot = {(a.timeslot.day_index, a.timeslot.period_index): a for a in rows}
    locked = locked_pairs(db, run.academic_context_id)

    # A multi-hour block should render as one merged cell: the first period
    # carries the span, the rest are marked as continuations to be skipped.
    block_periods: dict[int, list[int]] = defaultdict(list)
    for a in rows:
        block_periods[a.block_id].append(a.timeslot.period_index)
    block_start = {b: min(p) for b, p in block_periods.items()}
    block_len = {b: len(p) for b, p in block_periods.items()}

    days = sorted({s.day_index for s in slots})
    day_names = {s.day_index: s.day for s in slots}
    lunch = {(s.day_index, s.period_index) for s in slots if s.is_lunch}

    grid_rows: list[GridRowOut] = []
    for day in days:
        cells: list[GridCellOut] = []
        for period in periods:
            a = by_slot.get((day, period))
            if a is None:
                cells.append(
                    GridCellOut(
                        period_index=period,
                        is_lunch=(day, period) in lunch,
                        span=1,
                    )
                )
                continue

            is_start = block_start[a.block_id] == period
            cells.append(
                GridCellOut(
                    period_index=period,
                    is_lunch=False,
                    span=block_len[a.block_id] if is_start else 0,
                    continuation=not is_start,
                    assignment_id=a.id,
                    subject_id=a.subject_id,
                    subject_code=a.subject.code,
                    subject_name=a.subject.name,
                    subject_type=a.subject.type,
                    session_type=a.session_type,
                    faculty_id=a.faculty_id,
                    faculty_name=a.faculty.name,
                    faculty_code=a.faculty.faculty_code,
                    room_id=a.room_id,
                    room_number=a.room.room_number,
                    block=a.room.block,
                    section_id=a.section_id,
                    section_number=a.section.section_number,
                    block_id=a.block_id,
                    locked=(a.section_id, a.subject_id, a.session_type) in locked,
                )
            )
        grid_rows.append(GridRowOut(day_index=day, day=day_names[day], cells=cells))

    return GridOut(
        run_id=run.id,
        run_status=run.status,
        version=run.version,
        publish_status=run.publish_status,
        perspective=perspective,
        title=title,
        subtitle=subtitle,
        periods=periods,
        period_labels=period_labels,
        rows=grid_rows,
        total_periods=len(rows),
    )


def assignments_for(db: Session, run: TimetableRun, **equals) -> list[Assignment]:
    """One run's assignments for a single section, faculty or room.

    Joined-loads what ``build_grid`` reads. Without it each row lazily pulls
    five related objects, and each of those pulls its own eagerly-loaded
    collections - a grid of forty classes became hundreds of queries.
    """
    query = db.query(Assignment).filter(Assignment.run_id == run.id)
    for column, value in equals.items():
        query = query.filter(getattr(Assignment, column) == value)
    return query.options(*_grid_load_options()).all()


def _grid_load_options():
    """Load exactly the related rows a grid cell renders, and nothing beyond.

    ``noload("*")`` on each is the point: ``Section``, ``Subject``, ``Faculty``
    and ``Room`` all declare eagerly-loaded collections of their own, so
    fetching them normally drags in curricula, faculty-subject mappings and
    availability that no grid cell displays.

    For queries that do not already join these tables. Where the query joins
    them to filter on them, use ``_joined_load_options`` instead so one join
    serves both purposes.
    """
    from sqlalchemy.orm import joinedload

    return (
        joinedload(Assignment.timeslot),
        joinedload(Assignment.section).noload("*"),
        joinedload(Assignment.subject).noload("*"),
        joinedload(Assignment.faculty).noload("*"),
        joinedload(Assignment.room).noload("*"),
    )


def _joined_load_options():
    """Populate the related objects from joins the query already performs.

    The master query joins section, subject, faculty, room and timeslot in
    order to filter and sort on them. Adding ``joinedload`` on top would join
    every one of them a second time under an alias; ``contains_eager`` reuses
    the join that is already there.
    """
    return (
        contains_eager(Assignment.timeslot),
        contains_eager(Assignment.section).noload("*"),
        contains_eager(Assignment.subject).noload("*"),
        contains_eager(Assignment.faculty).noload("*"),
        contains_eager(Assignment.room).noload("*"),
    )


# -------------------------------------------------------------- master view


@dataclass(frozen=True)
class MasterFilters:
    """Every filter the master view accepts.

    The first four describe the academic context. A run belongs to exactly one
    context, so those are all-or-nothing: either the run's context matches and
    every row is eligible, or it does not and the result is empty. That is the
    behaviour the Python implementation had, and changing it would silently
    alter what an export contains.
    """

    academic_year: str | None = None
    semester: int | None = None
    program: str | None = None
    department: str | None = None
    section_id: int | None = None
    faculty_id: int | None = None
    subject_id: int | None = None
    subject_code: str | None = None
    room_id: int | None = None
    block: str | None = None
    floor: str | None = None
    room_type: str | None = None
    day_index: int | None = None
    locked: bool | None = None
    # Free text, matched anywhere in the subject, section, teacher or room.
    q: str | None = None

    def active(self) -> dict[str, object]:
        """The filters actually set, for telling a user what a page reflects."""
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not None
        }


def _context_excludes_run(run: TimetableRun, f: MasterFilters) -> bool:
    ctx = run.academic_context
    return (
        (f.academic_year is not None and ctx.academic_year != f.academic_year)
        or (f.semester is not None and ctx.semester != f.semester)
        or (f.program is not None and ctx.program != f.program)
        or (f.department is not None and ctx.department != f.department)
    )


def _filtered_query(db: Session, run: TimetableRun, f: MasterFilters):
    """Assignments of a run narrowed by every row-level filter, in SQL.

    Joins are explicit so the same query can be counted, paged, or streamed
    without changing shape.
    """
    from .models import Faculty, Room, Subject

    query = (
        db.query(Assignment)
        .join(Assignment.timeslot)
        .join(Assignment.section)
        .join(Assignment.subject)
        .join(Assignment.faculty)
        .join(Assignment.room)
        .filter(Assignment.run_id == run.id)
    )

    if f.section_id is not None:
        query = query.filter(Assignment.section_id == f.section_id)
    if f.faculty_id is not None:
        query = query.filter(Assignment.faculty_id == f.faculty_id)
    if f.subject_id is not None:
        query = query.filter(Assignment.subject_id == f.subject_id)
    if f.room_id is not None:
        query = query.filter(Assignment.room_id == f.room_id)
    if f.subject_code is not None:
        query = query.filter(Subject.code == f.subject_code)
    if f.block is not None:
        query = query.filter(Room.block == f.block)
    if f.floor is not None:
        query = query.filter(Room.floor == f.floor)
    if f.room_type is not None:
        query = query.filter(Room.room_type == f.room_type)
    if f.day_index is not None:
        query = query.filter(TimeSlot.day_index == f.day_index)
    if f.q is not None and f.q.strip():
        # Every row that matches, not every row on the current page: a search
        # that only looks at page one reports "nothing found" for a class that
        # is on page two.
        like = f"%{f.q.strip().lower()}%"
        query = query.filter(or_(*(
            func.lower(func.coalesce(column, "")).like(like)
            for column in (
                Subject.code, Subject.name, Section.section_number,
                Faculty.name, Faculty.faculty_code,
                Room.room_number, Room.room_code, Room.block,
                # "36-101", the name the views show when a room has no code
                Room.block + "-" + Room.room_number,
            )
        )))

    if f.locked is not None:
        pinned = (
            db.query(SectionSubjectAssignment.id)
            .filter(
                SectionSubjectAssignment.academic_context_id == run.academic_context_id,
                SectionSubjectAssignment.locked.is_(True),
                SectionSubjectAssignment.section_id == Assignment.section_id,
                SectionSubjectAssignment.subject_id == Assignment.subject_id,
                SectionSubjectAssignment.session_type == Assignment.session_type,
            )
            .exists()
        )
        query = query.filter(pinned if f.locked else ~pinned)

    return query


def _ordered(query):
    """Day, then period, then section - the order a printed timetable reads."""
    return query.order_by(TimeSlot.day_index, TimeSlot.period_index, Section.section_number)


ROOM_TYPE_LABEL = {"theory": "Classroom", "lab": "Lab", "faculty": "Faculty room"}


def room_code(room) -> str:
    """How a room is named wherever a person reads it: "36-309", not "309",
    which several blocks can each have."""
    return room.room_code or (
        f"{room.block}-{room.room_number}" if room.block else room.room_number
    )


def _to_row(a: Assignment, ctx, locked: set[tuple[int, int, str]]) -> MasterRowOut:
    practical = (a.session_type or "L") == PRACTICAL
    return MasterRowOut(
        assignment_id=a.id,
        timeslot_id=a.timeslot_id,
        day=a.timeslot.day,
        day_index=a.timeslot.day_index,
        period_index=a.timeslot.period_index,
        start_time=a.timeslot.start_time,
        end_time=a.timeslot.end_time,
        section_id=a.section_id,
        section_number=a.section.section_number,
        academic_year=ctx.academic_year,
        semester=ctx.semester,
        program=ctx.program,
        department=ctx.department,
        subject_id=a.subject_id,
        subject_code=a.subject.code,
        subject_name=a.subject.name,
        subject_type=a.subject.type,
        session_type=a.session_type,
        faculty_id=a.faculty_id,
        faculty_name=a.faculty.name,
        faculty_code=a.faculty.faculty_code,
        room_id=a.room_id,
        room_number=a.room.room_number,
        block=a.room.block,
        locked=(a.section_id, a.subject_id, a.session_type) in locked,
        block_id=a.block_id,
        strength=a.section.strength,
        class_type="Lab" if practical else "Theory",
        byod_required=bool(a.subject.byod_required)
        and (practical or settings.enforce_byod_on_lectures),
        room_code=room_code(a.room),
        floor=a.room.floor or "",
        room_capacity=a.room.capacity,
        room_type=ROOM_TYPE_LABEL.get(a.room.room_type, a.room.room_type),
        room_byod=bool(a.room.byod),
    )


def query_master(
    db: Session, run: TimetableRun, f: MasterFilters, *, limit: int, offset: int
) -> tuple[int, list[MasterRowOut]]:
    """One page of the master view, plus the full filtered count.

    Counting and paging both happen in the database, so the cost of page forty
    is the cost of page one.
    """
    if _context_excludes_run(run, f):
        return 0, []

    query = _filtered_query(db, run, f)
    total = query.with_entities(func.count(Assignment.id)).order_by(None).scalar() or 0
    if offset >= total:
        return total, []

    rows = (
        _ordered(query.options(*_joined_load_options()))
        .limit(limit)
        .offset(offset)
        .all()
    )
    ctx = run.academic_context
    locked = locked_pairs(db, run.academic_context_id)
    return total, [_to_row(a, ctx, locked) for a in rows]


def master_options(db: Session, run: TimetableRun) -> dict[str, list]:
    """What each filter can usefully be set to: the values that occur in this
    run, so no option leads to an empty page because it names a room outside
    this timetable's infrastructure or a teacher who is not on it."""
    from .models import Faculty, Room, Subject

    base = (
        db.query(Assignment)
        .join(Assignment.timeslot)
        .join(Assignment.section)
        .join(Assignment.subject)
        .join(Assignment.faculty)
        .join(Assignment.room)
        .filter(Assignment.run_id == run.id)
    )

    def distinct(*columns):
        return base.with_entities(*columns).distinct().all()

    rooms = distinct(Room.id, Room.room_code, Room.block, Room.room_number)
    return {
        "sections": [
            {"id": i, "label": n}
            for i, n in sorted(distinct(Section.id, Section.section_number), key=lambda r: r[1])
        ],
        "faculty": [
            {"id": i, "label": f"{n} ({c})"}
            for i, n, c in sorted(distinct(Faculty.id, Faculty.name, Faculty.faculty_code),
                                  key=lambda r: (r[1], r[2]))
        ],
        "subjects": [
            {"id": i, "label": f"{c} - {n}"}
            for i, c, n in sorted(distinct(Subject.id, Subject.code, Subject.name),
                                  key=lambda r: r[1])
        ],
        "rooms": sorted(
            ({"id": i, "label": code or (f"{b}-{n}" if b else n)} for i, code, b, n in rooms),
            key=lambda o: o["label"],
        ),
        "days": [
            {"id": i, "label": d}
            for i, d in sorted(distinct(TimeSlot.day_index, TimeSlot.day))
        ],
        "blocks": sorted(b for (b,) in distinct(Room.block) if b),
        "floors": sorted(f for (f,) in distinct(Room.floor) if f),
        "room_types": [
            {"id": t, "label": ROOM_TYPE_LABEL.get(t, t)}
            for (t,) in sorted(distinct(Room.room_type))
        ],
    }


def iter_master(
    db: Session, run: TimetableRun, f: MasterFilters, *, chunk: int = 1000
) -> Iterator[MasterRowOut]:
    """Every matching row, streamed for export.

    Exports must not truncate, so this deliberately has no limit. It streams in
    chunks rather than materialising the whole result, and runs the filter once
    - the previous implementation re-ran the entire query for each page it
    wanted, which multiplied the work by the number of pages.
    """
    if _context_excludes_run(run, f):
        return

    ctx = run.academic_context
    locked = locked_pairs(db, run.academic_context_id)
    query = _ordered(_filtered_query(db, run, f).options(*_joined_load_options()))
    for a in query.yield_per(chunk):
        yield _to_row(a, ctx, locked)
