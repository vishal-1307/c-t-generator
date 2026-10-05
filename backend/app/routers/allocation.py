"""The generated timetable as a table, and as the Excel file the teacher keeps.

Reads only, and open like every other read in this API. With no run named,
both answer for the most recent timetable that has classes in it - which is
what the teacher means by "the timetable".
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..allocation import (
    allocation_rows,
    allocation_workbook,
    dataset_label,
    latest_run,
    newest_run,
)
from ..database import get_db
from ..models import TimetableRun

router = APIRouter(prefix="/api", tags=["allocation"])


def _run(db: Session, run_id: int | None, academic_context_id: int | None = None) -> TimetableRun:
    run = (
        db.get(TimetableRun, run_id) if run_id is not None
        else latest_run(db, academic_context_id)
    )
    if run is None:
        raise HTTPException(
            status_code=404,
            detail="No timetable has been generated yet. Upload the two files and generate one.",
        )
    return run


def download_name(db: Session, run: TimetableRun, ext: str = "xlsx") -> str:
    """A filename a person can recognise later, and nothing internal in it:
    the dataset's label and the version, never a database id."""
    import re

    label = re.sub(r"[^A-Za-z0-9]+", "-", dataset_label(db, run)).strip("-")[:60]
    return f"timetable_{label or 'dataset'}_v{run.version}.{ext}"


def _filtered(rows, faculty_id, section_id, day, room_id):
    return [
        r for r in rows
        if (faculty_id is None or r.faculty_pk == faculty_id)
        and (section_id is None or r.section_pk == section_id)
        and (day is None or r.day == day)
        and (room_id is None or r.room_pk == room_id)
    ]


@router.get("/allocation")
def allocation(
    run_id: int | None = Query(None),
    academic_context_id: int | None = Query(None),
    faculty_id: int | None = Query(None),
    section_id: int | None = Query(None),
    day: str | None = Query(None),
    room_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    run = _run(db, run_id, academic_context_id)
    everything = allocation_rows(db, run.id)
    rows = _filtered(everything, faculty_id, section_id, day, room_id)

    def options(pk, label):
        seen = {}
        for r in everything:
            seen.setdefault(getattr(r, pk), label(r))
        return [{"id": k, "label": v} for k, v in sorted(seen.items(), key=lambda kv: kv[1])]

    return {
        "run_id": run.id,
        "run_status": run.status,
        "version": run.version,
        "publish_status": run.publish_status,
        # So a page showing the published version can say a newer draft exists.
        "newest_version": (newest_run(db, run.academic_context_id) or run).version,
        "academic_context_id": run.academic_context_id,
        "dataset": dataset_label(db, run),
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "classes": len(everything),
        "periods": sum(r.periods for r in everything),
        "rows": [r.as_dict() for r in rows],
        "filters": {
            "faculty": options("faculty_pk", lambda r: f"{r.faculty_name} ({r.faculty_id})"),
            "sections": options("section_pk", lambda r: r.section),
            "rooms": options("room_pk", lambda r: r.room),
            "days": [d for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
                     if any(r.day == d for r in everything)],
        },
    }


@router.get("/export/allocation.xlsx")
def allocation_xlsx(
    run_id: int | None = Query(None),
    academic_context_id: int | None = Query(None),
    faculty_id: int | None = Query(None),
    section_id: int | None = Query(None),
    day: str | None = Query(None),
    room_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    run = _run(db, run_id, academic_context_id)
    rows = _filtered(allocation_rows(db, run.id), faculty_id, section_id, day, room_id)
    data = allocation_workbook(rows, title=f"Timetable - {dataset_label(db, run)}")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{download_name(db, run)}"'},
    )
