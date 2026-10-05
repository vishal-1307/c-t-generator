"""Timetable views: the generated schedule as a day x period grid.

Four perspectives on the same assignments - section, faculty, room, and the
filterable master view - all derived from ``Assignment``, never a separately
maintained copy (spec #33). Each grid carries a ``span`` hint derived from
``block_id`` so the frontend can merge multi-hour cells without re-deriving
contiguity itself, and a ``locked`` flag read from ``SectionSubjectAssignment``
so the UI can show which classes cannot move without an explicit unlock.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import timetable_views as views
from ..database import get_db
from ..models import (
    AcademicContext,
    Assignment,
    Faculty,
    FacultyUnavailability,
    Room,
    RoomUnavailability,
    Section,
    SectionSubjectAssignment,
    SectionUnavailability,
    TimeSlot,
    TimetableRun,
)
from ..schemas import (
    GridOut,
    MasterTimetableOut,
    SlotAvailabilityOut,
)

router = APIRouter(prefix="/api/timetable", tags=["timetable"])

# Kept as names on this module because `exporters` has imported them from
# here since before the view service existed. They are one
# object, not a copy - see app/timetable_views.py.
_resolve_run = views.resolve_run
_locked_pairs = views.locked_pairs
_build_grid = views.build_grid


@router.get("/section/{section_id}", response_model=GridOut)
def section_grid(
    section_id: int,
    run_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    section = db.get(Section, section_id)
    if section is None:
        raise HTTPException(status_code=404, detail=f"Section {section_id} not found")

    run = _resolve_run(db, run_id, section.academic_context_id)
    # Read by id rather than through the relationship: loading the grid's rows
    # below marks the section's own relationships as not to be loaded, and a
    # section already in the session would then report no context at all.
    context = db.get(AcademicContext, section.academic_context_id)
    rows = views.assignments_for(db, run, section_id=section_id)
    return _build_grid(
        db,
        run,
        rows,
        title=section.section_number,
        subtitle=f"{section.strength} students - {context.label if context else ''}",
        perspective="section",
    )


@router.get("/faculty/{faculty_id}", response_model=GridOut)
def faculty_grid(
    faculty_id: int,
    run_id: int | None = Query(None),
    academic_context_id: int | None = Query(
        None,
        description=(
            "Which context's latest run to show when run_id is not given. A "
            "section implies its own context; a teacher does not, so without "
            "this the newest run in the whole institution is used."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Shows actual classes only - an empty cell IS 'no class', with no
    synthetic FREE rows cluttering the grid (spec: faculty view).

    Scoped to an academic context when one is given. A faculty member is
    institution-wide, so there is no context to infer from them the way
    ``section_grid`` infers one from its section - and with more than one
    context in the database, falling back to the newest run anywhere shows a
    teacher a run from a programme they may not even teach on, reading as
    "nothing scheduled".
    """
    faculty = db.get(Faculty, faculty_id)
    if faculty is None:
        raise HTTPException(status_code=404, detail=f"Faculty {faculty_id} not found")

    run = _resolve_run(db, run_id, academic_context_id)
    rows = views.assignments_for(db, run, faculty_id=faculty_id)
    return _build_grid(
        db,
        run,
        rows,
        title=faculty.name,
        subtitle=f"{faculty.faculty_code} - {len(rows)} periods per week",
        perspective="faculty",
    )


@router.get("/room/{room_id}", response_model=GridOut)
def room_grid(
    room_id: int,
    run_id: int | None = Query(None),
    academic_context_id: int | None = Query(
        None,
        description=(
            "Which context's latest run to show when run_id is not given. A "
            "room, like a teacher, belongs to no single context."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Scoped to an academic context when one is given - see ``faculty_grid``
    for why a room needs telling."""
    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail=f"Room {room_id} not found")

    run = _resolve_run(db, run_id, academic_context_id)
    rows = views.assignments_for(db, run, room_id=room_id)
    return _build_grid(
        db,
        run,
        rows,
        # The code, not the bare number: two blocks can both have a "301".
        title=f"Room {room.room_code or room.room_number}",
        subtitle=" · ".join(
            part for part in (
                f"Block {room.block}" if room.block else "",
                f"Floor {room.floor}" if room.floor else "",
                f"Capacity {room.capacity}",
                {"theory": "Classroom", "lab": "Lab", "faculty": "Faculty room"}.get(
                    room.room_type, room.room_type),
                "BYOD" if room.byod else "",
            ) if part
        ),
        perspective="room",
    )


@router.get("/master", response_model=MasterTimetableOut)
def master_timetable(
    run_id: int | None = Query(None),
    academic_context_id: int | None = Query(None),
    academic_year: str | None = Query(None),
    semester: int | None = Query(None),
    program: str | None = Query(None),
    department: str | None = Query(None),
    section_id: int | None = Query(None),
    faculty_id: int | None = Query(None),
    subject_id: int | None = Query(None),
    subject_code: str | None = Query(None),
    room_id: int | None = Query(None),
    block: str | None = Query(None),
    floor: str | None = Query(None),
    room_type: str | None = Query(None),
    day_index: int | None = Query(None),
    locked: bool | None = Query(None),
    q: str | None = Query(None, max_length=100, description="Matched anywhere in subject, section, teacher or room."),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """The full filterable administrative view (spec: master/school view).

    Every scheduled class in the run, flattened to one row each, filterable by
    academic context, section, faculty, subject, room, block, floor, room
    type, day - the query an admin uses to answer "what is happening across
    the entire scheduling environment". Paginated (spec #34): ``total`` is
    the full filtered count, ``rows`` is one page of it - a 30+ section
    context can have thousands of rows, and nothing here should return them
    all in one unbounded response.
    """
    run = _resolve_run(db, run_id, academic_context_id)
    filters = views.MasterFilters(
        academic_year=academic_year,
        semester=semester,
        program=program,
        department=department,
        section_id=section_id,
        faculty_id=faculty_id,
        subject_id=subject_id,
        subject_code=subject_code,
        room_id=room_id,
        block=block,
        floor=floor,
        room_type=room_type,
        day_index=day_index,
        locked=locked,
        q=q,
    )
    total, rows = views.query_master(db, run, filters, limit=limit, offset=offset)
    from ..allocation import dataset_label

    return MasterTimetableOut(
        run_id=run.id,
        run_status=run.status,
        total=total,
        rows=rows,
        version=run.version,
        publish_status=run.publish_status,
        dataset=dataset_label(db, run),
        options=views.master_options(db, run),
    )


@router.get("/availability", response_model=SlotAvailabilityOut)
def master_availability(
    timeslot_id: int = Query(...),
    run_id: int | None = Query(None),
    academic_context_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """Spec #36: at one day/slot, which faculty/rooms/sections are occupied
    vs. available - one aggregate view derived entirely from Assignment plus
    the declared-unavailability tables, nothing separately maintained."""
    run = _resolve_run(db, run_id, academic_context_id)
    slot = db.get(TimeSlot, timeslot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail=f"Timeslot {timeslot_id} not found")

    rows = (
        db.query(Assignment)
        .filter(Assignment.run_id == run.id, Assignment.timeslot_id == timeslot_id)
        .all()
    )
    occupied_faculty = {a.faculty_id for a in rows}
    occupied_rooms = {a.room_id for a in rows}
    occupied_sections = {a.section_id for a in rows}

    declared_faculty = {
        u.faculty_id for u in
        db.query(FacultyUnavailability).filter(FacultyUnavailability.timeslot_id == timeslot_id)
    }
    declared_rooms = {
        u.room_id for u in
        db.query(RoomUnavailability).filter(RoomUnavailability.timeslot_id == timeslot_id)
    }
    declared_sections = {
        u.section_id for u in
        db.query(SectionUnavailability).filter(SectionUnavailability.timeslot_id == timeslot_id)
    }

    all_faculty_ids = {f.id for f in db.query(Faculty.id).all()}
    all_room_ids = {
        r.id for r in db.query(Room).filter(Room.is_active.is_(True), Room.room_type != "faculty").all()
    }
    context_sections = db.query(Section)
    if academic_context_id is not None:
        context_sections = context_sections.filter(Section.academic_context_id == academic_context_id)
    all_section_ids = {s.id for s in context_sections.all()}

    occ_f = occupied_faculty | declared_faculty
    occ_r = occupied_rooms | declared_rooms
    occ_s = occupied_sections | declared_sections

    return SlotAvailabilityOut(
        timeslot_id=timeslot_id,
        day=slot.day,
        day_index=slot.day_index,
        period_index=slot.period_index,
        occupied_faculty_ids=sorted(occ_f),
        available_faculty_ids=sorted(all_faculty_ids - occ_f),
        occupied_room_ids=sorted(occ_r),
        available_room_ids=sorted(all_room_ids - occ_r),
        occupied_section_ids=sorted(occ_s),
        available_section_ids=sorted(all_section_ids - occ_s),
    )
