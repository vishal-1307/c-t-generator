"""Save the room list once; clear data a half at a time.

`/api/infrastructure` holds the one saved Infra.xlsx that every later teaching
load is scheduled against. Preview writes nothing. Saving a *different* room
list over one that timetables were built on removes those timetables, so it is
refused until the caller says it understands that (`confirm_replace`).

`/api/data/clear` deletes the teaching load, the rooms, or both. The GET says
what would go; the POST needs the exact phrase for that choice. Both refuse
while a timetable is being generated, since the solve would write rows back
into tables being emptied.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import infrastructure as infra_store
from .. import jobs
from .. import teacher_import as ti
from ..auth import require_admin
from ..bulk_import import ParseError
from ..bulk_import.workbook import ordered_entities
from ..bulk_import.workbook_engine import apply_sheets
from ..database import get_db
from ..models import Room, User
from ..schemas import (
    ClearPreviewOut,
    ClearRequest,
    ClearResultOut,
    InfrastructureOut,
    InfrastructurePreviewOut,
)
from .bulk_import import _read, _workbook_out
from .teacher_import import _known_rooms, _refuse_on_input_problems, _relocate, _sheet_matches

logger = logging.getLogger("timetable.api")

router = APIRouter(prefix="/api", tags=["infrastructure"])


def _listed(db: Session, exploded: ti.ExplodedFiles) -> set[int]:
    """The rooms a room list names, as they exist in the database now."""
    _, rows = exploded.sheets.get("Rooms", ([], []))
    wanted = {
        (str(r.get("block", "")).strip().lower(), str(r.get("room_number", "")).strip().lower())
        for r in rows
    }
    return {
        room.id for room in db.query(Room).all()
        if (str(room.block or "").strip().lower(), str(room.room_number or "").strip().lower())
        in wanted
    }


async def _read_infra(db: Session, upload: UploadFile) -> ti.ExplodedFiles:
    data = await _read(upload)
    try:
        parsed = ti.read_sheet(data, upload.filename or "Infra.xlsx")
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    exploded = ti.explode_infra(parsed, known_rooms=_known_rooms(db))
    _refuse_on_input_problems(exploded)
    return exploded


def _out(result, exploded, **extra) -> InfrastructurePreviewOut:
    body = _workbook_out(_relocate(result, exploded)).model_dump()
    body["notes"] = list(exploded.notes)
    body["summary"] = dict(exploded.summary)
    body["room_changes"] = list(exploded.room_changes)
    body.update(extra)
    return InfrastructurePreviewOut(**body)


@router.get("/infrastructure", response_model=InfrastructureOut)
def current(db: Session = Depends(get_db)):
    saved = infra_store.active(db)
    if saved is None:
        return InfrastructureOut(available=False)
    return InfrastructureOut(
        available=True,
        filename=saved.filename,
        uploaded_at=saved.uploaded_at,
        summary=infra_store.summary(db, saved),
        timetables=infra_store.timetable_count(db),
    )


@router.post("/infrastructure/preview", response_model=InfrastructurePreviewOut)
async def preview(
    infra: UploadFile = File(..., description="The room list"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """What saving this room list would do. Writes nothing."""
    exploded = await _read_infra(db, infra)
    matches = _sheet_matches(exploded)
    try:
        result = apply_sheets(
            db, exploded.sheets, matches, ordered_entities(matches),
            infra.filename or "Infra.xlsx", commit=False,
        )
        listed = _listed(db, exploded)
        saved = infra_store.active(db)
        unchanged = infra_store.is_unchanged(db, listed, exploded.room_changes)
        replaces = saved is not None and not unchanged
        removed = infra_store.timetable_count(db) if replaces else 0
    finally:
        db.rollback()
    return _out(result, exploded, replaces=replaces, unchanged=unchanged,
                timetables_removed=removed)


@router.post("/infrastructure", response_model=InfrastructurePreviewOut)
async def save(
    infra: UploadFile = File(..., description="The room list"),
    confirm_replace: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Save this room list as the infrastructure, all or nothing."""
    if jobs.is_running():
        raise HTTPException(
            status_code=409,
            detail="A timetable is being generated. Wait for it to finish, then save the rooms.",
        )
    exploded = await _read_infra(db, infra)
    matches = _sheet_matches(exploded)
    filename = infra.filename or "Infra.xlsx"

    # Decide before writing whether this replaces rooms timetables depend on.
    # Asked inside a transaction that is thrown away: the rooms have to be
    # imported before they can be compared.
    try:
        apply_sheets(db, exploded.sheets, matches, ordered_entities(matches), filename,
                     commit=False)
        listed = _listed(db, exploded)
        saved = infra_store.active(db)
        replaces = saved is not None and not infra_store.is_unchanged(
            db, listed, exploded.room_changes)
        removed = infra_store.timetable_count(db) if replaces else 0
    finally:
        db.rollback()

    if replaces and removed and not confirm_replace:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"Replacing the infrastructure removes the {removed} timetable"
                    f"{'s' if removed != 1 else ''} built on the current rooms. "
                    "Confirm to replace it."
                ),
                "timetables": removed,
            },
        )

    result = apply_sheets(
        db, exploded.sheets, matches, ordered_entities(matches), filename,
        actor=user.username,
        before_commit=lambda session: infra_store.save(
            session, filename, _listed(session, exploded), user.username),
    )
    if result.committed or not result.has_errors:
        logger.info("infrastructure saved file=%s replaced=%s removed_timetables=%s by=%s",
                    filename, replaces, removed, user.username)
    return _out(result, exploded, replaces=replaces,
                unchanged=not replaces and saved is not None, timetables_removed=removed)


# ------------------------------------------------------------------ clearing


@router.get("/data/clear", response_model=ClearPreviewOut)
def what_clearing_would_delete(
    what: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    if what not in infra_store.CONFIRMATIONS:
        raise HTTPException(status_code=422, detail="what must be load, infrastructure or both")
    counts = infra_store.clear_counts(db, what)  # type: ignore[arg-type]
    return ClearPreviewOut(
        what=what,  # type: ignore[arg-type]
        confirmation=infra_store.CONFIRMATIONS[what],
        counts={k: v for k, v in counts.items() if v},
        total=sum(counts.values()),
    )


@router.post("/data/clear", response_model=ClearResultOut)
def clear(
    body: ClearRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    phrase = infra_store.CONFIRMATIONS[body.what]
    if body.confirm != phrase:
        raise HTTPException(
            status_code=400,
            detail=f'Nothing was deleted. To clear, send confirm: "{phrase}".',
        )
    if jobs.is_running():
        raise HTTPException(
            status_code=409,
            detail="A timetable is being generated. Wait for it to finish, then clear.",
        )
    deleted = infra_store.clear(db, body.what)
    logger.warning("data cleared what=%s rows=%s by=%s", body.what,
                   sum(deleted.values()), user.username)
    return ClearResultOut(
        what=body.what,
        deleted={k: v for k, v in deleted.items() if v},
        total_rows=sum(deleted.values()),
    )
