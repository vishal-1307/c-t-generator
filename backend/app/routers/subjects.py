"""Subject CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import crud, entity_detail, listing, locking, schemas
from ..auth import require_admin
from ..database import get_db
from ..models import ChangeHistory, Room, SectionSubjectAssignment, Subject, User

router = APIRouter(prefix="/api/subjects", tags=["subjects"])


def _validate_fixed_room(db: Session, data: dict) -> None:
    """A subject's fixed-room override (spec #15) can never point at a faculty
    room - that room is never schedulable teaching space regardless of who
    asks for it explicitly."""
    room_id = data.get("fixed_room_id")
    if room_id is None:
        return
    room = crud.get_or_404(db, Room, room_id)
    if room.room_type == "faculty":
        raise HTTPException(
            status_code=422,
            detail=f"Room {room.room_number} is a faculty room and cannot be "
            "fixed to a subject - faculty rooms are never schedulable",
        )


SUBJECT_LIST = listing.Sortable(
    search=(Subject.code, Subject.name, Subject.required_lab_type),
    sorts={
        "code": Subject.code,
        "name": Subject.name,
        "type": Subject.type,
    },
    default_sort=Subject.code,
)


@router.get("")
def list_subjects(
    params: listing.ListParams = Depends(listing.list_params),
    db: Session = Depends(get_db),
):
    """Every subject, or one searchable page of them (see list_faculty)."""
    return listing.serialize(
        listing.apply(db, Subject, params, SUBJECT_LIST),
        schemas.SubjectOut,
        schemas.SubjectBrief,
    )


@router.post("", response_model=schemas.SubjectOut, status_code=status.HTTP_201_CREATED)
def create_subject(
    payload: schemas.SubjectCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    data = payload.model_dump()
    _validate_fixed_room(db, data)
    return crud.create(db, Subject, data, unique_field="code")


@router.get("/{subject_id}", response_model=schemas.SubjectOut)
def get_subject(subject_id: int, db: Session = Depends(get_db)):
    return crud.get_or_404(db, Subject, subject_id)


@router.get("/{subject_id}/detail", response_model=schemas.SubjectDetailOut)
def get_subject_detail(subject_id: int, db: Session = Depends(get_db)):
    """Phase 9: room requirement, eligible faculty, every section taking this
    subject, and its actual semester assignments across every context."""
    subject = crud.get_or_404(db, Subject, subject_id)
    sections = [
        {
            "id": s.id,
            "section_number": s.section_number,
            "strength": s.strength,
            "academic_context_id": s.academic_context_id,
            "academic_context_label": s.academic_context.label,
        }
        for s in subject.sections
    ]
    return schemas.SubjectDetailOut(
        id=subject.id,
        name=subject.name,
        code=subject.code,
        type=subject.type,
        session_length_hours=subject.session_length_hours,
        sessions_per_week=subject.sessions_per_week,
        periods_per_week=subject.periods_per_week,
        required_lab_type=subject.required_lab_type,
        fixed_room=subject.fixed_room,
        allowed_rooms=subject.allowed_rooms,
        eligible_faculty=subject.faculties,
        sections=sections,
        assignments=entity_detail.assignment_briefs(db, subject_id=subject_id),
        is_active=subject.is_active,
    )


@router.get("/{subject_id}/allowed-rooms", response_model=list[schemas.RoomBrief])
def list_allowed_rooms(subject_id: int, db: Session = Depends(get_db)):
    """The explicit room whitelist for this subject, if it has one.

    Empty is the normal case and means "decide by capability": capacity, room
    type and lab type. A non-empty list narrows the subject to exactly these
    rooms.
    """
    return crud.get_or_404(db, Subject, subject_id).allowed_rooms


@router.put("/{subject_id}/allowed-rooms", response_model=list[schemas.RoomBrief])
def set_allowed_rooms(
    subject_id: int,
    payload: schemas.AllowedRoomsPayload,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Replace the room whitelist for this subject.

    The solver has always read this table as its second-priority room source,
    ahead of capability matching - but nothing could write to it. It was
    documented as a supported way to restrict a subject to particular rooms
    while being, in practice, unreachable. This is that write path.

    Sending an empty list clears the restriction and returns the subject to
    capability-based eligibility. Rooms are validated to exist, and a faculty
    room is refused outright: it is not teaching space, and whitelisting one
    would produce a subject with no schedulable room at all.
    """
    subject = crud.get_or_404(db, Subject, subject_id)

    rooms = []
    for room_id in dict.fromkeys(payload.room_ids):  # de-duplicate, keep order
        room = crud.get_or_404(db, Room, room_id)
        if room.room_type == "faculty":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Room {room.room_number} is a faculty room, which is never "
                "used for teaching. Allowing it would leave this subject with no "
                "schedulable room.",
            )
        rooms.append(room)

    subject.allowed_rooms = rooms
    db.commit()
    db.refresh(subject)
    return subject.allowed_rooms


# Fields that decide how many blocks a subject needs and how long each is.
# Changing one invalidates any lock on it: the stored placement no longer has
# the right shape to reproduce.
_LOAD_FIELDS = {
    "type",
    "sessions_per_week", "session_length_hours",
    "lecture_sessions_per_week", "lecture_session_length",
    "practical_sessions_per_week", "practical_session_length",
}


@router.put("/{subject_id}", response_model=schemas.SubjectOut)
def update_subject(
    subject_id: int,
    payload: schemas.SubjectUpdate,
    force: bool = Query(
        False,
        description=(
            "Proceed even though locked classes of this subject would lose "
            "their lock. The locks are removed and the change is recorded."
        ),
    ),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    data = payload.model_dump(exclude_unset=True)
    if "fixed_room_id" in data:
        _validate_fixed_room(db, data)

    subject = crud.get_or_404(db, Subject, subject_id)
    changing_load = [
        field for field in data
        if field in _LOAD_FIELDS and getattr(subject, field, None) != data[field]
    ]
    if changing_load:
        locked = (
            db.query(SectionSubjectAssignment)
            .filter(
                SectionSubjectAssignment.subject_id == subject_id,
                SectionSubjectAssignment.locked.is_(True),
            )
            .all()
        )
        if locked and not force:
            # Refusing is the honest answer. Allowing it silently would leave
            # locks that cannot be honoured, and the next regeneration would
            # move classes somebody had deliberately pinned.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{len(locked)} locked class(es) of {subject.code} depend on "
                    f"its current weekly load, and changing "
                    f"{', '.join(changing_load)} would leave them unable to keep "
                    "their place. Unlock them first, or repeat this request with "
                    "force=true to remove those locks."
                ),
            )
        if locked and force:
            run_id = locking.latest_run_id(db, locked[0].academic_context_id)
            for row in locked:
                row.locked = False
                if run_id is not None:
                    db.add(ChangeHistory(
                        run_id=run_id,
                        section_id=row.section_id,
                        subject_id=row.subject_id,
                        change_type="unlock",
                        old_value="locked",
                        new_value="unlocked",
                        reason=(
                            f"{subject.code}'s weekly load changed "
                            f"({', '.join(changing_load)}), so the lock could no "
                            "longer be honoured"
                        ),
                        actor=_admin.username,
                        user_id=_admin.id,
                        user_role=_admin.role,
                    ))

    return crud.update(db, Subject, subject_id, data, unique_field="code")


@router.delete("/{subject_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subject(
    subject_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    crud.delete(db, Subject, subject_id)
