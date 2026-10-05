"""TimeSlot CRUD plus grid seeding.

Slots carry an explicit ``(day_index, period_index)``. The solver reads
contiguity straight off those integers, so the grid must stay well-formed:
one row per day/period pair, no gaps in the period sequence.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..auth import require_admin
from ..database import get_db
from ..grid import build_grid
from ..models import (
    Assignment,
    FacultyUnavailability,
    RoomUnavailability,
    SectionUnavailability,
    TimeSlot,
    TimetableRun,
    User,
)

router = APIRouter(prefix="/api/timeslots", tags=["timeslots"])


def _assert_slot_free(
    db: Session, day_index: int, period_index: int, exclude_id: int | None = None
) -> None:
    existing = (
        db.query(TimeSlot)
        .filter(TimeSlot.day_index == day_index, TimeSlot.period_index == period_index)
        .first()
    )
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A slot already exists at day {day_index}, period {period_index}",
        )


@router.get("", response_model=list[schemas.TimeSlotOut])
def list_timeslots(db: Session = Depends(get_db)):
    return (
        db.query(TimeSlot)
        .order_by(TimeSlot.day_index, TimeSlot.period_index)
        .all()
    )


@router.post("", response_model=schemas.TimeSlotOut, status_code=status.HTTP_201_CREATED)
def create_timeslot(
    payload: schemas.TimeSlotCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    data = payload.model_dump()
    _assert_slot_free(db, data["day_index"], data["period_index"])
    return crud.create(db, TimeSlot, data)


@router.get("/{slot_id}", response_model=schemas.TimeSlotOut)
def get_timeslot(slot_id: int, db: Session = Depends(get_db)):
    return crud.get_or_404(db, TimeSlot, slot_id)


@router.put("/{slot_id}", response_model=schemas.TimeSlotOut)
def update_timeslot(
    slot_id: int, payload: schemas.TimeSlotUpdate, db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    slot = crud.get_or_404(db, TimeSlot, slot_id)
    data = payload.model_dump(exclude_unset=True)
    _assert_slot_free(
        db,
        data.get("day_index", slot.day_index),
        data.get("period_index", slot.period_index),
        exclude_id=slot_id,
    )
    return crud.update(db, TimeSlot, slot_id, data)


@router.delete("/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_timeslot(
    slot_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    crud.delete(db, TimeSlot, slot_id)


def grid_replacement_impact(db: Session) -> schemas.GridReplacementImpactOut:
    """Count everything a grid replacement would delete.

    Availability blocks and assignments reference a timeslot with
    ``ondelete=CASCADE``, so deleting the grid deletes them too. That is the
    part nobody expects: "re-seed the timetable grid" reads like a settings
    change and behaves like erasing every generated timetable.

    Counted before any deletion so the number an operator is shown is the
    number that would actually be lost.
    """
    runs_affected = (
        db.query(Assignment.run_id).filter(Assignment.timeslot_id.isnot(None)).distinct().count()
    )
    published_affected = (
        db.query(TimetableRun.id)
        .join(Assignment, Assignment.run_id == TimetableRun.id)
        .filter(TimetableRun.publish_status == "PUBLISHED")
        .distinct()
        .count()
    )
    return schemas.GridReplacementImpactOut(
        timeslots=db.query(TimeSlot).count(),
        faculty_unavailability=db.query(FacultyUnavailability).count(),
        section_unavailability=db.query(SectionUnavailability).count(),
        room_unavailability=db.query(RoomUnavailability).count(),
        assignments=db.query(Assignment).count(),
        runs_affected=runs_affected,
        published_runs_affected=published_affected,
    )


def _refusal_message(impact: schemas.GridReplacementImpactOut) -> str:
    """A refusal that names the cost, in the order a scheduler cares about."""
    parts = [f"{impact.timeslots} time slots"]
    blocks = (
        impact.faculty_unavailability
        + impact.section_unavailability
        + impact.room_unavailability
    )
    if blocks:
        parts.append(f"{blocks} availability blocks")
    if impact.assignments:
        parts.append(
            f"{impact.assignments} scheduled classes across {impact.runs_affected} "
            f"timetable version(s)"
        )
    published = (
        f" {impact.published_runs_affected} of those version(s) are PUBLISHED."
        if impact.published_runs_affected
        else ""
    )
    return (
        "Re-seeding the grid would permanently delete " + ", ".join(parts) + "."
        + published
        + " Availability blocks and generated classes reference a time slot, so"
        " they are removed with it. Send confirm=true to proceed, or edit"
        " individual slots instead."
    )


@router.post("/seed", response_model=list[schemas.TimeSlotOut])
def seed_grid(
    payload: schemas.TimeSlotSeedRequest, db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Regenerate the whole weekly grid, replacing any existing slots.

    Produces ``periods`` back-to-back slots per day, all teachable. Mark the
    lunch slot afterwards with ``PUT /api/timeslots/{id}`` - the generator does
    not decide where the break falls.

    Seeding an empty grid proceeds directly. Replacing an existing one requires
    ``confirm``, because the cascade reaches every availability block and every
    generated class - see ``grid_replacement_impact``.
    """
    if db.query(TimeSlot).first() is not None and not payload.confirm:
        impact = grid_replacement_impact(db)
        # Not HTTPException: `detail` carries the human sentence so existing
        # clients render it unchanged, and `impact` rides alongside it so a
        # confirmation dialog can show the counts without re-deriving them.
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": _refusal_message(impact),
                "impact": impact.model_dump(),
            },
        )

    db.execute(sa_delete(TimeSlot))
    db.add_all(
        build_grid(
            days=payload.days,
            periods=payload.periods,
            start_hour=payload.start_hour,
            start_minute=payload.start_minute,
            period_minutes=payload.period_minutes,
        )
    )
    db.commit()
    return db.query(TimeSlot).order_by(TimeSlot.day_index, TimeSlot.period_index).all()
