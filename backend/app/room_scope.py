"""Which rooms one intake's timetable may use.

One function, called by everything that decides whether a room is eligible -
the solver's loader, pre-solve validation, the independent checker and the
manual room change - so none of them can use a room another would refuse.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AcademicContextRoom, Room


def usable_rooms(db: Session, academic_context_id: int | None) -> list[Room]:
    """Every room, narrowed to the intake's own room list if it has one.

    An intake imported from a room list uses exactly the rooms that list
    named. One without a list - created before lists were recorded, or by the
    general importer - uses every room, which is what always happened.
    """
    rooms = db.query(Room).all()
    if academic_context_id is None:
        return rooms
    linked = {
        room_id for (room_id,) in
        db.query(AcademicContextRoom.room_id)
        .filter(AcademicContextRoom.academic_context_id == academic_context_id)
    }
    if not linked:
        return rooms
    return [r for r in rooms if r.id in linked]


def set_usable_rooms(db: Session, academic_context_id: int, room_ids: set[int]) -> None:
    """Make the intake's room list exactly `room_ids`.

    Replaces rather than adds: a room taken out of the room list stops being
    used by this intake on the next upload. The rooms themselves are never
    touched - another intake may still be using them.
    """
    db.query(AcademicContextRoom).filter(
        AcademicContextRoom.academic_context_id == academic_context_id
    ).delete(synchronize_session=False)
    db.add_all(
        AcademicContextRoom(academic_context_id=academic_context_id, room_id=rid)
        for rid in sorted(room_ids)
    )
    db.flush()
