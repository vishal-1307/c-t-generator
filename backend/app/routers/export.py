"""Phase 9: Excel / CSV / PDF export endpoints.

Every filtered export takes the *same* filter parameters as
``GET /api/timetable/master`` (spec #16: "respect current filters where
appropriate") - a page that is already filtered can pass its query string
straight through unchanged.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import exporters
from ..database import get_db
from ..models import Faculty, Room, Section
from . import timetable as timetable_router

router = APIRouter(prefix="/api/export", tags=["export"])


def _version_label(db: Session, run_id: int) -> str:
    """ "Version 2 (published) - Upload 1 - ...", never a database id."""
    from ..allocation import dataset_label
    from ..models import TimetableRun

    run = db.get(TimetableRun, run_id)
    if run is None:
        return "Timetable"
    state = "published" if run.publish_status == "PUBLISHED" else "draft"
    return f"Version {run.version} ({state}) - {dataset_label(db, run)}"


def _filename(*parts: str, ext: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d")
    safe = "_".join(p.replace(" ", "-") for p in parts if p)
    return f"{safe}_{stamp}.{ext}"


def _master_filters(
    run_id: int | None,
    academic_context_id: int | None,
    section_id: int | None,
    faculty_id: int | None,
    subject_id: int | None,
    subject_code: str | None,
    room_id: int | None,
    block: str | None,
    floor: str | None,
    room_type: str | None,
    day_index: int | None,
    locked: bool | None,
    q: str | None = None,
) -> dict:
    return dict(
        run_id=run_id, academic_context_id=academic_context_id, section_id=section_id,
        faculty_id=faculty_id, subject_id=subject_id, subject_code=subject_code,
        room_id=room_id, block=block, floor=floor, room_type=room_type,
        day_index=day_index, locked=locked, q=q or None,
    )


@router.get("/master.csv")
def export_master_csv(
    run_id: int | None = Query(None),
    academic_context_id: int | None = Query(None),
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
    q: str | None = Query(None, max_length=100),
    db: Session = Depends(get_db),
):
    rows, _ = exporters.all_master_rows(
        db, **_master_filters(
            run_id, academic_context_id, section_id, faculty_id, subject_id,
            subject_code, room_id, block, floor, room_type, day_index, locked, q,
        )
    )
    return Response(
        exporters.master_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{_filename("timetable", ext="csv")}"'},
    )


@router.get("/master.xlsx")
def export_master_xlsx(
    run_id: int | None = Query(None),
    academic_context_id: int | None = Query(None),
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
    q: str | None = Query(None, max_length=100),
    db: Session = Depends(get_db),
):
    filters = _master_filters(
        run_id, academic_context_id, section_id, faculty_id, subject_id,
        subject_code, room_id, block, floor, room_type, day_index, locked, q,
    )
    rows, _ = exporters.all_master_rows(db, **filters)
    data = exporters.build_master_workbook(rows, "Master Timetable")
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_filename("timetable", ext="xlsx")}"'},
    )


@router.get("/master.pdf")
def export_master_pdf(
    run_id: int | None = Query(None),
    academic_context_id: int | None = Query(None),
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
    q: str | None = Query(None, max_length=100),
    db: Session = Depends(get_db),
):
    filters = _master_filters(
        run_id, academic_context_id, section_id, faculty_id, subject_id,
        subject_code, room_id, block, floor, room_type, day_index, locked, q,
    )
    rows, resolved_run_id = exporters.all_master_rows(db, **filters)
    active_filters = [k for k, v in filters.items() if v is not None and k != "run_id"]
    subtitle = (
        f"{_version_label(db, resolved_run_id)} - {len(rows)} periods"
        + (f" - filtered by {', '.join(active_filters)}" if active_filters else " - all classes")
    )
    data = exporters.master_pdf(rows, "Master Timetable", subtitle)
    return Response(
        data, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_filename("timetable", ext="pdf")}"'},
    )


@router.get("/run/{run_id}.xlsx")
def export_run_workbook(run_id: int, db: Session = Depends(get_db)):
    """The full administrative workbook: master + assignments + one grid
    sheet per section/faculty/room used in this run."""
    from ..models import TimetableRun
    from .allocation import download_name

    run = db.get(TimetableRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="That timetable version no longer exists.")
    data = exporters.build_run_workbook(db, run_id)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{download_name(db, run).replace("timetable_", "timetable-workbook_")}"'},
    )


@router.get("/section/{section_id}.pdf")
def export_section_pdf(section_id: int, run_id: int | None = Query(None), db: Session = Depends(get_db)):
    section = db.get(Section, section_id)
    if section is None:
        raise HTTPException(status_code=404, detail=f"Section {section_id} not found")
    grid = timetable_router.section_grid(section_id, run_id, db=db)
    data = exporters.grid_pdf(grid)
    return Response(
        data, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_filename("section", section.section_number, ext="pdf")}"'},
    )


@router.get("/faculty/{faculty_id}.pdf")
def export_faculty_pdf(faculty_id: int, run_id: int | None = Query(None), db: Session = Depends(get_db)):
    faculty = db.get(Faculty, faculty_id)
    if faculty is None:
        raise HTTPException(status_code=404, detail=f"Faculty {faculty_id} not found")
    grid = timetable_router.faculty_grid(faculty_id, run_id, db=db)
    data = exporters.grid_pdf(grid)
    return Response(
        data, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_filename("faculty", faculty.faculty_code, ext="pdf")}"'},
    )


@router.get("/room/{room_id}.pdf")
def export_room_pdf(room_id: int, run_id: int | None = Query(None), db: Session = Depends(get_db)):
    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail=f"Room {room_id} not found")
    grid = timetable_router.room_grid(room_id, run_id, db=db)
    data = exporters.grid_pdf(grid)
    return Response(
        data, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_filename("room", room.room_number, ext="pdf")}"'},
    )
