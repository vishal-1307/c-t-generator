"""Room CRUD plus lab/subject pinning.

Room eligibility for the solver is normally derived from room_type + lab_type +
capacity (TIMETABLE_LOGIC_SPEC.md #16), not from a pin. ``fixed_subject_id`` is
retained only as an explicit override (spec #15) for the rare subject that must
use one specific room - most rooms leave it null.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import crud, entity_detail, listing, schemas
from ..auth import require_admin
from ..database import get_db
from ..models import ROOM_TYPES, Room, Subject, User

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


def _validate_fixed_subject(db: Session, data: dict) -> None:
    """A pinned subject must exist, and a practical can only ever be pinned to
    a lab (theory subjects are never pinned - they run in whatever eligible
    theory room the solver picks, not in a reserved room)."""
    subject_id = data.get("fixed_subject_id")
    if subject_id is None:
        return
    subject = crud.get_or_404(db, Subject, subject_id)

    if subject.type == "practical" and data.get("room_type") != "lab":
        raise HTTPException(
            status_code=422,
            detail=(
                f"{subject.code} is a practical subject and cannot be pinned to a "
                "non-lab room - practicals are only ever placed in lab rooms"
            ),
        )
    if subject.type == "theory" and data.get("room_type") == "faculty":
        raise HTTPException(
            status_code=422,
            detail=f"{subject.code} cannot be pinned to a faculty room - faculty "
            "rooms are never schedulable teaching rooms",
        )


def _validate_room_type_change(db: Session, room: Room, data: dict) -> None:
    """A faculty room can never carry a subject pin, and flipping a room to
    'faculty' after it was pinned must clear the pin rather than silently keep
    a now-invalid state."""
    new_type = data.get("room_type", room.room_type)
    if new_type == "faculty" and data.get("fixed_subject_id", room.fixed_subject_id):
        raise HTTPException(
            status_code=422,
            detail="A faculty room cannot have a subject pinned to it - "
            "faculty rooms are never schedulable teaching rooms",
        )
    merged = {
        "room_type": new_type,
        "fixed_subject_id": data.get("fixed_subject_id", room.fixed_subject_id),
    }
    _validate_fixed_subject(db, merged)


ROOM_LIST = listing.Sortable(
    search=(Room.room_number, Room.block, Room.floor, Room.lab_type),
    sorts={
        "room_number": Room.room_number,
        "block": Room.block,
        "floor": Room.floor,
        "capacity": Room.capacity,
        "room_type": Room.room_type,
    },
    default_sort=Room.room_number,
)


@router.get("")
def list_rooms(
    room_type: str | None = Query(
        None,
        description="Narrow to one kind of room: theory, lab or faculty.",
    ),
    params: listing.ListParams = Depends(listing.list_params),
    db: Session = Depends(get_db),
):
    """Every room, or one searchable page of them (see list_faculty).

    `room_type` narrows the base query, so a paged `total` counts the rooms of
    that kind rather than the whole building. Without it, a screen that only
    deals in labs has to read every classroom in the institution to find the
    ten it wants.
    """
    query = db.query(Room)
    if room_type is not None:
        if room_type not in ROOM_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"room_type must be one of {', '.join(sorted(ROOM_TYPES))}",
            )
        query = query.filter(Room.room_type == room_type)
    return listing.serialize(
        listing.apply(db, Room, params, ROOM_LIST, base_query=query),
        schemas.RoomOut,
        schemas.RoomBrief,
    )


@router.post("", response_model=schemas.RoomOut, status_code=status.HTTP_201_CREATED)
def create_room(
    payload: schemas.RoomCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    data = payload.model_dump()
    _validate_fixed_subject(db, data)
    if data["room_type"] == "faculty" and data.get("fixed_subject_id"):
        raise HTTPException(
            status_code=422,
            detail="A faculty room cannot have a subject pinned to it",
        )
    return crud.create(db, Room, data, unique_field=("block", "room_number"))


@router.get("/{room_id}", response_model=schemas.RoomOut)
def get_room(room_id: int, db: Session = Depends(get_db)):
    return crud.get_or_404(db, Room, room_id)


@router.get("/{room_id}/detail", response_model=schemas.RoomDetailOut)
def get_room_detail(room_id: int, db: Session = Depends(get_db)):
    """Phase 9: block/floor/capacity/type, declared unavailability,
    utilization, and whether it's occupied/free/inactive right now."""
    room = crud.get_or_404(db, Room, room_id)
    return schemas.RoomDetailOut(
        id=room.id,
        room_number=room.room_number,
        block=room.block,
        floor=room.floor,
        capacity=room.capacity,
        room_type=room.room_type,
        lab_type=room.lab_type,
        is_active=room.is_active,
        fixed_subject=room.fixed_subject,
        blocked_slots=entity_detail.room_blocked_slots(db, room_id),
        utilization=entity_detail.room_utilization(db, room_id),
        current_status=entity_detail.room_current_status(db, room),
    )


@router.put("/{room_id}", response_model=schemas.RoomOut)
def update_room(
    room_id: int, payload: schemas.RoomUpdate, db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    room = crud.get_or_404(db, Room, room_id)
    data = payload.model_dump(exclude_unset=True)
    # Validate against the post-update state unconditionally - flipping
    # room_type alone could leave a stale, now-invalid pin in place.
    _validate_room_type_change(db, room, data)
    return crud.update(db, Room, room_id, data, unique_field=("block", "room_number"))


@router.delete("/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_room(
    room_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    crud.delete(db, Room, room_id)


@router.put("/{room_id}/fixed-subject", response_model=schemas.RoomOut)
def set_fixed_subject(
    room_id: int, payload: schemas.OptionalSubjectIdPayload, db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Pin a room to a single subject, or pass ``null`` to unpin it."""
    room = crud.get_or_404(db, Room, room_id)
    _validate_fixed_subject(
        db, {"room_type": room.room_type, "fixed_subject_id": payload.subject_id}
    )
    room.fixed_subject_id = payload.subject_id
    db.commit()
    db.refresh(room)
    return room
