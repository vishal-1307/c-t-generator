"""Phase 9: Excel / CSV / PDF export.

Deliberately built on the *existing* grid and master-view logic
(``app/timetable_views.py``) rather than re-deriving a second read path over
``Assignment`` - an export is just a different rendering of the same single
source of truth every on-screen view already uses. Nothing here recomputes a
timetable; it only formats one the view service already assembled.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import io
from typing import Iterable

from sqlalchemy.orm import Session

from .. import timetable_views as views
from ..routers import timetable as timetable_router
from ..schemas import GridOut, MasterRowOut

MASTER_CSV_HEADERS = [
    "Day", "Start", "End", "Academic Year", "Semester", "Program", "Department",
    "Section", "Students", "Subject Code", "Subject Name", "Type",
    "Faculty", "Faculty Code", "Room", "Block", "Floor", "Capacity", "Room Type",
    "BYOD Room", "Locked",
]

# Phase 10 PART 19/20: CSV/Excel formula-injection guard. A cell whose text
# starts with one of these is a formula trigger in Excel/Sheets/LibreOffice -
# if a room name or subject code imported from an untrusted spreadsheet
# contained one, it would silently become executable when the exported file
# is later opened by an admin. Every string cell in an export goes through
# this before being written; a leading apostrophe forces spreadsheet
# applications to treat it as literal text (the standard OWASP mitigation),
# and is invisible in normal display.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value: str) -> str:
    if value and value[0] in _FORMULA_TRIGGERS:
        return "'" + value
    return value


def _master_row_values(r: MasterRowOut) -> list[str]:
    return [
        _safe_cell(v) for v in (
            r.day, r.start_time.strftime("%H:%M"), r.end_time.strftime("%H:%M"),
            r.academic_year, str(r.semester), r.program, r.department,
            r.section_number, str(r.strength), r.subject_code, r.subject_name,
            r.class_type, r.faculty_name, r.faculty_code, r.room_code or r.room_number,
            r.block, r.floor, str(r.room_capacity), r.room_type,
            "yes" if r.room_byod else "no",
            "yes" if r.locked else "no",
        )
    ]


def all_master_rows(
    db: Session,
    *,
    run_id: int | None = None,
    academic_context_id: int | None = None,
    section_id: int | None = None,
    faculty_id: int | None = None,
    subject_id: int | None = None,
    subject_code: str | None = None,
    room_id: int | None = None,
    block: str | None = None,
    floor: str | None = None,
    room_type: str | None = None,
    day_index: int | None = None,
    locked: bool | None = None,
    q: str | None = None,
) -> tuple[list[MasterRowOut], int]:
    """Every row matching the filters, not just one page - export must respect
    a filter the way the on-screen master view does, but never truncate to a
    UI page size.

    This used to call the master *route* once per two-thousand-row page, and
    each of those calls re-ran the entire query before discarding all but its
    page - so exporting a hundred thousand rows walked the run fifty times.
    The view service answers the same filters in one streamed query.
    """
    run = views.resolve_run(db, run_id, academic_context_id)
    filters = views.MasterFilters(
        section_id=section_id, faculty_id=faculty_id, subject_id=subject_id,
        subject_code=subject_code, room_id=room_id, block=block, floor=floor,
        room_type=room_type, day_index=day_index, locked=locked, q=q,
    )
    return list(views.iter_master(db, run, filters)), run.id


# ------------------------------------------------------------------------ CSV


def master_csv(rows: Iterable[MasterRowOut]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(MASTER_CSV_HEADERS)
    for r in rows:
        writer.writerow(_master_row_values(r))
    return buf.getvalue()


# ---------------------------------------------------------------------- Excel


def _autosize(ws) -> None:
    for col_cells in ws.columns:
        length = max(
            (len(str(c.value)) for c in col_cells if c.value is not None), default=0
        )
        ws.column_dimensions[col_cells[0].column_letter].width = min(max(10, length + 2), 42)


def _sanitize_sheet_title(name: str) -> str:
    bad = set('[]:*?/\\')
    cleaned = "".join(c for c in name if c not in bad).strip()
    return (cleaned or "Sheet")[:31]


def _write_master_sheet(wb, rows: list[MasterRowOut], title: str) -> None:
    from openpyxl.styles import Font

    ws = wb.create_sheet(_sanitize_sheet_title(title))
    ws.append(MASTER_CSV_HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append(_master_row_values(r))
    ws.freeze_panes = "A2"
    _autosize(ws)


def _write_grid_sheet(wb, grid: GridOut, title: str) -> None:
    from openpyxl.styles import Alignment, Font

    ws = wb.create_sheet(_sanitize_sheet_title(title))
    ws.append([_safe_cell(grid.title)])
    ws.append([_safe_cell(grid.subtitle)])
    ws.append([])
    header_row = 4
    ws.append(["Day"] + [grid.period_labels.get(p, str(p)) for p in grid.periods])
    for c in ws[header_row]:
        c.font = Font(bold=True)

    for row in grid.rows:
        by_period = {c.period_index: c for c in row.cells}
        line = [_safe_cell(row.day)]
        for p in grid.periods:
            cell = by_period.get(p)
            if cell is None or cell.continuation:
                line.append("")
            elif cell.is_lunch:
                line.append("LUNCH")
            elif cell.subject_code is None:
                line.append("No Class")
            else:
                extra = cell.faculty_code if grid.perspective != "faculty" else cell.section_number
                line.append(_safe_cell(f"{cell.subject_code} | {extra} | {cell.room_number}"))
        ws.append(line)

    for row in ws.iter_rows(min_row=header_row + 1):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    _autosize(ws)


def build_master_workbook(rows: list[MasterRowOut], title: str) -> bytes:
    """Filtered export: a flat 'Master Timetable' sheet plus a raw
    'Assignments' sheet for pivoting - no per-entity sheets, since a filtered
    slice doesn't map cleanly onto "one section" or "one room"."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    _write_master_sheet(wb, rows, title or "Master Timetable")
    _write_master_sheet(wb, rows, "Assignments")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def build_run_workbook(db: Session, run_id: int) -> bytes:
    """The full administrative export for one run: a flat master view, a raw
    assignments sheet, and one Mon-Fri grid sheet per section/faculty/room
    that actually has a class in this run."""
    from openpyxl import Workbook

    rows, resolved_run_id = all_master_rows(db, run_id=run_id)

    section_ids = sorted({r.section_id for r in rows})
    faculty_ids = sorted({r.faculty_id for r in rows})
    room_ids = sorted({r.room_id for r in rows})

    wb = Workbook()
    wb.remove(wb.active)
    _write_master_sheet(wb, rows, "Master Timetable")
    _write_master_sheet(wb, rows, "Assignments")

    for sid in section_ids:
        grid = timetable_router.section_grid(sid, resolved_run_id, db=db)
        _write_grid_sheet(wb, grid, f"Sec {grid.title}")
    for fid in faculty_ids:
        grid = timetable_router.faculty_grid(fid, resolved_run_id, db=db)
        _write_grid_sheet(wb, grid, f"Fac {grid.title}")
    for rid in room_ids:
        grid = timetable_router.room_grid(rid, resolved_run_id, db=db)
        # The room grid is already titled "Room 36-301" - by code, so two
        # blocks' room 301 get two sheets rather than colliding.
        _write_grid_sheet(wb, grid, grid.title)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ------------------------------------------------------------------------ PDF


def _pdf_styles():
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t9title", parent=base["Title"], fontSize=16, spaceAfter=4),
        "subtitle": ParagraphStyle("t9sub", parent=base["Normal"], fontSize=10, textColor="#555555", spaceAfter=12),
        "cell": ParagraphStyle("t9cell", parent=base["Normal"], fontSize=8, leading=10),
        "header": ParagraphStyle("t9header", parent=base["Normal"], fontSize=9, leading=11, textColor="white"),
    }


def grid_pdf(grid: GridOut) -> bytes:
    """One printable Mon-Fri grid: section, faculty or room view.

    Portrait, sized to fit a normal week (5 days x <=9 periods) on one page -
    the failure mode this avoids is a table so dense it prints unreadably, not
    one that spans pages, since a single entity's week is always small.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, portrait
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = _pdf_styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=portrait(A4),
        leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
    )

    # PART 19/20: reportlab's Paragraph interprets a subset of HTML-like
    # markup in its input, so any imported/user-controlled text (a room
    # number, a faculty name) must be escaped before going into an f-string
    # destined for Paragraph() - otherwise a value like "<b>x" could corrupt
    # layout or inject markup into the generated PDF.
    header = ["Day"] + [grid.period_labels.get(p, str(p)) for p in grid.periods]
    data = [[Paragraph(html.escape(h), styles["header"]) for h in header]]
    for row in grid.rows:
        by_period = {c.period_index: c for c in row.cells}
        line = [Paragraph(f"<b>{html.escape(row.day)}</b>", styles["cell"])]
        for p in grid.periods:
            cell = by_period.get(p)
            if cell is None or cell.continuation:
                text = ""
            elif cell.is_lunch:
                text = "<i>Lunch</i>"
            elif cell.subject_code is None:
                text = "<font color='#999999'>No Class</font>"
            else:
                extra = cell.faculty_code if grid.perspective != "faculty" else cell.section_number
                text = (
                    f"<b>{html.escape(cell.subject_code)}</b><br/>"
                    f"{html.escape(extra or '')}<br/>{html.escape(cell.room_number or '')}"
                )
            line.append(Paragraph(text, styles["cell"]))
        data.append(line)

    n_cols = len(header)
    avail_width = doc.width
    col_widths = [avail_width * 0.10] + [avail_width * 0.90 / (n_cols - 1)] * (n_cols - 1)

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])
    )

    story = [
        Paragraph(html.escape(grid.title), styles["title"]),
        Paragraph(html.escape(grid.subtitle), styles["subtitle"]),
        Spacer(1, 4),
        table,
    ]
    doc.build(story)
    return buf.getvalue()


def master_pdf(rows: list[MasterRowOut], title: str, subtitle: str) -> bytes:
    """Landscape, flat listing sorted by day/time - a filtered master export
    can span many sections at once, so a grid is the wrong shape; a sorted
    table paginates cleanly instead (reportlab splits a long Table across
    pages automatically, repeating the header row each time)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = _pdf_styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
    )

    # PART 19/20: escape every user-controlled string before it reaches
    # Paragraph() - see grid_pdf()'s comment for why.
    headers = ["Day", "Time", "Section", "Subject", "Faculty", "Room", "Program / Dept"]
    data = [[Paragraph(h, styles["header"]) for h in headers]]
    for r in rows:
        data.append([
            Paragraph(html.escape(r.day), styles["cell"]),
            Paragraph(f"{r.start_time.strftime('%H:%M')}-{r.end_time.strftime('%H:%M')}", styles["cell"]),
            Paragraph(html.escape(r.section_number), styles["cell"]),
            Paragraph(f"{html.escape(r.subject_code)} - {html.escape(r.subject_name)}", styles["cell"]),
            Paragraph(f"{html.escape(r.faculty_name)} ({html.escape(r.faculty_code)})", styles["cell"]),
            Paragraph(html.escape(r.room_code or r.room_number), styles["cell"]),
            Paragraph(f"{html.escape(r.program)} / {html.escape(r.department)}", styles["cell"]),
        ])

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ])
    )

    story = [
        Paragraph(html.escape(title), styles["title"]),
        Paragraph(html.escape(subtitle), styles["subtitle"]),
        Spacer(1, 4),
        table,
    ]
    if not rows:
        story.append(Paragraph("No classes match the current filters.", styles["cell"]))
    doc.build(story)
    return buf.getvalue()
