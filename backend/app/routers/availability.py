"""Faculty / section / room availability.

Availability is stored only as *exceptions* - a blocked-slot row - so
"available" is the default and there is exactly one source of truth per
resource: the absence of a block row here, combined with the absence of a
clashing Assignment (TIMETABLE_LOGIC_SPEC.md #11/#18/#19/#20). Nothing here
duplicates a "free timetable" table.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..auth import require_scheduler_or_admin
from ..database import get_db
from ..models import (
    Faculty,
    FacultyUnavailability,
    Room,
    RoomUnavailability,
    Section,
    SectionUnavailability,
    TimeSlot,
    User,
)

router = APIRouter(tags=["availability"])


def _add_block(db, model, owner_model, owner_field, owner_id, payload):
    crud.get_or_404(db, owner_model, owner_id)
    crud.get_or_404(db, TimeSlot, payload.timeslot_id)
    existing = (
        db.query(model)
        .filter(
            getattr(model, owner_field) == owner_id,
            model.timeslot_id == payload.timeslot_id,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That slot is already blocked",
        )
    row = model(**{owner_field: owner_id}, timeslot_id=payload.timeslot_id, reason=payload.reason)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------- faculty


@router.get(
    "/api/faculty/{faculty_id}/unavailability",
    response_model=list[schemas.FacultyUnavailabilityOut],
)
def list_faculty_unavailability(faculty_id: int, db: Session = Depends(get_db)):
    crud.get_or_404(db, Faculty, faculty_id)
    return (
        db.query(FacultyUnavailability)
        .filter(FacultyUnavailability.faculty_id == faculty_id)
        .all()
    )


@router.post(
    "/api/faculty/{faculty_id}/unavailability",
    response_model=schemas.FacultyUnavailabilityOut,
    status_code=status.HTTP_201_CREATED,
)
def block_faculty_slot(
    faculty_id: int, payload: schemas.UnavailabilityCreate, db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    return _add_block(db, FacultyUnavailability, Faculty, "faculty_id", faculty_id, payload)


@router.delete(
    "/api/faculty/{faculty_id}/unavailability/{block_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unblock_faculty_slot(
    faculty_id: int, block_id: int, db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    row = crud.get_or_404(db, FacultyUnavailability, block_id)
    if row.faculty_id != faculty_id:
        raise HTTPException(status_code=404, detail="Not found for this faculty")
    db.delete(row)
    db.commit()


# --------------------------------------------------------------------- section


@router.get(
    "/api/sections/{section_id}/unavailability",
    response_model=list[schemas.SectionUnavailabilityOut],
)
def list_section_unavailability(section_id: int, db: Session = Depends(get_db)):
    crud.get_or_404(db, Section, section_id)
    return (
        db.query(SectionUnavailability)
        .filter(SectionUnavailability.section_id == section_id)
        .all()
    )


@router.post(
    "/api/sections/{section_id}/unavailability",
    response_model=schemas.SectionUnavailabilityOut,
    status_code=status.HTTP_201_CREATED,
)
def block_section_slot(
    section_id: int, payload: schemas.UnavailabilityCreate, db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    return _add_block(db, SectionUnavailability, Section, "section_id", section_id, payload)


@router.delete(
    "/api/sections/{section_id}/unavailability/{block_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unblock_section_slot(
    section_id: int, block_id: int, db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    row = crud.get_or_404(db, SectionUnavailability, block_id)
    if row.section_id != section_id:
        raise HTTPException(status_code=404, detail="Not found for this section")
    db.delete(row)
    db.commit()


# ------------------------------------------------------------------------ room


@router.get(
    "/api/rooms/{room_id}/unavailability",
    response_model=list[schemas.RoomUnavailabilityOut],
)
def list_room_unavailability(room_id: int, db: Session = Depends(get_db)):
    crud.get_or_404(db, Room, room_id)
    return db.query(RoomUnavailability).filter(RoomUnavailability.room_id == room_id).all()


@router.post(
    "/api/rooms/{room_id}/unavailability",
    response_model=schemas.RoomUnavailabilityOut,
    status_code=status.HTTP_201_CREATED,
)
def block_room_slot(
    room_id: int, payload: schemas.UnavailabilityCreate, db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    return _add_block(db, RoomUnavailability, Room, "room_id", room_id, payload)


@router.delete(
    "/api/rooms/{room_id}/unavailability/{block_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unblock_room_slot(
    room_id: int, block_id: int, db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    row = crud.get_or_404(db, RoomUnavailability, block_id)
    if row.room_id != room_id:
        raise HTTPException(status_code=404, detail="Not found for this room")
    db.delete(row)
    db.commit()
