"""Phase 6: manual timetable operations over ``app/manual_edit.py``.

Every endpoint here reuses the exact same validation the solver's hard
constraints encode - no separate, weaker manual-edit check exists.

Phase 10: every preview and apply endpoint requires scheduler-or-admin - the
whole workflow around a mutation, not just its final write, since a preview
already exposes internal eligibility reasoning tied to a specific attempted
edit. History reads stay open (read-only, same posture as every other GET).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import manual_edit
from ..auth import require_scheduler_or_admin
from ..database import get_db
from ..models import Assignment, ChangeHistory, User
from ..schemas import (
    ChangeHistoryOut,
    ChangeOutcomeOut,
    FacultyChangeRequest,
    MoveRequest,
    RoomChangeRequest,
    ValidationResultOut,
)

router = APIRouter(prefix="/api/assignments", tags=["manual-edit"])
logger = logging.getLogger("timetable.manual_edit")


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except manual_edit.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (manual_edit.Locked, manual_edit.Protected) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except manual_edit.Invalid as exc:
        raise HTTPException(status_code=409, detail="; ".join(exc.issues)) from exc


@router.post("/{assignment_id}/move/preview", response_model=ValidationResultOut)
def preview_move(
    assignment_id: int, payload: MoveRequest, db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    result = _handle(manual_edit.preview_move, db, assignment_id, payload.target_timeslot_id)
    return ValidationResultOut(ok=result.ok, issues=result.issues)


@router.post("/{assignment_id}/move", response_model=ChangeOutcomeOut)
def move(
    assignment_id: int, payload: MoveRequest, db: Session = Depends(get_db),
    user: User = Depends(require_scheduler_or_admin),
):
    outcome = _handle(
        manual_edit.apply_move, db, assignment_id, payload.target_timeslot_id,
        lock_after=payload.lock_after, actor=payload.actor, reason=payload.reason,
        user_id=user.id, user_role=user.role,
    )
    if not outcome.ok:
        raise HTTPException(status_code=409, detail="; ".join(outcome.issues))
    logger.info("assignment moved id=%s target_timeslot=%s user=%s",
                assignment_id, payload.target_timeslot_id, user.username)
    return ChangeOutcomeOut(ok=True, change_history_id=outcome.change_history_id)


@router.post("/{assignment_id}/room/preview", response_model=ValidationResultOut)
def preview_room_change(
    assignment_id: int, payload: RoomChangeRequest, db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    result = _handle(manual_edit.preview_room_change, db, assignment_id, payload.room_id)
    return ValidationResultOut(ok=result.ok, issues=result.issues)


@router.post("/{assignment_id}/room", response_model=ChangeOutcomeOut)
def change_room(
    assignment_id: int, payload: RoomChangeRequest, db: Session = Depends(get_db),
    user: User = Depends(require_scheduler_or_admin),
):
    outcome = _handle(
        manual_edit.apply_room_change, db, assignment_id, payload.room_id,
        lock_after=payload.lock_after, actor=payload.actor, reason=payload.reason,
        user_id=user.id, user_role=user.role,
    )
    if not outcome.ok:
        raise HTTPException(status_code=409, detail="; ".join(outcome.issues))
    logger.info("assignment room changed id=%s room=%s user=%s",
                assignment_id, payload.room_id, user.username)
    return ChangeOutcomeOut(ok=True, change_history_id=outcome.change_history_id)


@router.post("/{assignment_id}/faculty/preview", response_model=ValidationResultOut)
def preview_faculty_change(
    assignment_id: int, payload: FacultyChangeRequest, db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    result = _handle(manual_edit.preview_faculty_change, db, assignment_id, payload.faculty_id)
    return ValidationResultOut(ok=result.ok, issues=result.issues)


@router.post("/{assignment_id}/faculty", response_model=ChangeOutcomeOut)
def change_faculty(
    assignment_id: int, payload: FacultyChangeRequest, db: Session = Depends(get_db),
    user: User = Depends(require_scheduler_or_admin),
):
    outcome = _handle(
        manual_edit.apply_faculty_change, db, assignment_id, payload.faculty_id,
        lock_after=payload.lock_after, actor=payload.actor, reason=payload.reason,
        user_id=user.id, user_role=user.role,
    )
    if not outcome.ok:
        raise HTTPException(status_code=409, detail="; ".join(outcome.issues))
    logger.info("assignment faculty changed id=%s faculty=%s user=%s",
                assignment_id, payload.faculty_id, user.username)
    return ChangeOutcomeOut(ok=True, change_history_id=outcome.change_history_id)


@router.get("/{assignment_id}/history", response_model=list[ChangeHistoryOut])
def assignment_history(
    assignment_id: int, db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    """Every recorded change for this occurrence's (section, subject) pair,
    within the same run - not just this one row, since a room/faculty change
    touches every session of the pair at once."""
    a = db.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(status_code=404, detail=f"assignment {assignment_id} not found")
    return (
        db.query(ChangeHistory)
        .filter(
            ChangeHistory.run_id == a.run_id,
            ChangeHistory.section_id == a.section_id,
            ChangeHistory.subject_id == a.subject_id,
        )
        .order_by(ChangeHistory.created_at.desc())
        .all()
    )


@router.get("/history/run/{run_id}", response_model=list[ChangeHistoryOut])
def run_history(
    run_id: int, db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    """Full change log for one run, newest first."""
    return (
        db.query(ChangeHistory)
        .filter(ChangeHistory.run_id == run_id)
        .order_by(ChangeHistory.created_at.desc())
        .all()
    )
