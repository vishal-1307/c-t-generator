"""Phase 7: "which resources are free?" queries - the read side of
availability, distinct from ``availability.py`` (which owns the CRUD for
*declared* unavailability). Backs manual editing (spec #31/#32): before an
admin retargets a class, they need to know what is actually open.

A resource is "occupied" here if either it has a declared unavailability row
for that slot, or an ``Assignment`` in the given run already uses it there -
the same two sources ``app/manual_edit.py`` checks, so this view can never
disagree with what a move would actually be validated against.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..manual_edit import contiguous_window
from ..models import (
    Assignment,
    Faculty,
    FacultyUnavailability,
    Room,
    RoomUnavailability,
)
from ..schemas import AvailableFacultyOut, AvailableRoomOut

router = APIRouter(prefix="/api/availability", tags=["availability-query"])


def _resolve_window(db: Session, timeslot_id: int, length: int) -> list[int]:
    window = contiguous_window(db, timeslot_id, length)
    if window is None:
        raise HTTPException(
            status_code=422,
            detail=f"no {length} contiguous non-lunch period(s) starting at timeslot {timeslot_id}",
        )
    return [s.id for s in window]


@router.get("/rooms", response_model=list[AvailableRoomOut])
def available_rooms(
    timeslot_id: int = Query(..., description="First slot of the window to check."),
    length: int = Query(1, ge=1, le=3),
    run_id: int | None = Query(None, description="Rooms already used in this run are excluded."),
    capacity_min: int | None = Query(None, ge=1),
    room_type: str | None = Query(None),
    lab_type: str | None = Query(None),
    block: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Which rooms are free for a given day/slot window - never faculty
    rooms, regardless of filters (spec #31)."""
    window_ids = _resolve_window(db, timeslot_id, length)

    query = db.query(Room).filter(Room.is_active.is_(True), Room.room_type != "faculty")
    if capacity_min is not None:
        query = query.filter(Room.capacity >= capacity_min)
    if room_type is not None:
        query = query.filter(Room.room_type == room_type)
    if lab_type is not None:
        query = query.filter(Room.lab_type == lab_type)
    if block is not None:
        query = query.filter(Room.block == block)
    candidates = query.order_by(Room.room_number).all()

    blocked_ids: set[int] = set()
    for u in db.query(RoomUnavailability).filter(RoomUnavailability.timeslot_id.in_(window_ids)):
        blocked_ids.add(u.room_id)
    if run_id is not None:
        for a in (
            db.query(Assignment)
            .filter(Assignment.run_id == run_id, Assignment.timeslot_id.in_(window_ids))
        ):
            blocked_ids.add(a.room_id)

    return [r for r in candidates if r.id not in blocked_ids]


@router.get("/faculty", response_model=list[AvailableFacultyOut])
def available_faculty(
    timeslot_id: int = Query(...),
    length: int = Query(1, ge=1, le=3),
    run_id: int | None = Query(None, description="Faculty already teaching in this run are excluded."),
    subject_id: int | None = Query(None, description="Restrict to faculty eligible for this subject."),
    department: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Which faculty are free for a given day/slot window."""
    window_ids = _resolve_window(db, timeslot_id, length)

    query = db.query(Faculty)
    if department is not None:
        query = query.filter(Faculty.department == department)
    candidates = query.order_by(Faculty.name).all()
    if subject_id is not None:
        candidates = [f for f in candidates if any(s.id == subject_id for s in f.subjects)]

    blocked_ids: set[int] = set()
    for u in db.query(FacultyUnavailability).filter(FacultyUnavailability.timeslot_id.in_(window_ids)):
        blocked_ids.add(u.faculty_id)
    if run_id is not None:
        for a in (
            db.query(Assignment)
            .filter(Assignment.run_id == run_id, Assignment.timeslot_id.in_(window_ids))
        ):
            blocked_ids.add(a.faculty_id)

    return [f for f in candidates if f.id not in blocked_ids]
