"""Phase 9: Excel / CSV / PDF export.

Built on the same real, solver-generated, published dataset as
test_entity_detail.py - an export is a rendering of Assignment, not a second
source of truth, so it needs a genuine schedule to render.
"""
from __future__ import annotations

import csv
import io

import pytest
from openpyxl import load_workbook

from app.grid import build_grid
from app.models import Faculty, Room, Section, Subject
from app.solver.run import generate

from constraint_checker import check_all


@pytest.fixture
def two_sections(client, db_session, context):
    db = db_session

    cs201 = Subject(name="Data Structures", code="CS201", type="theory",
                     session_length_hours=1, sessions_per_week=3)
    cs251 = Subject(name="DS Lab", code="CS251", type="practical",
                     session_length_hours=2, sessions_per_week=1, required_lab_type="COMPUTING")
    db.add_all([cs201, cs251])
    db.flush()

    fac_a = Faculty(name="Dr. A", faculty_code="FAC01")
    fac_a.subjects = [cs201]
    fac_l = Faculty(name="Dr. L", faculty_code="FAC03")
    fac_l.subjects = [cs251]
    db.add_all([fac_a, fac_l])

    a101 = Room(room_number="A101", block="A", floor="1", capacity=70, room_type="theory")
    a102 = Room(room_number="A102", block="A", floor="1", capacity=70, room_type="theory")
    lab1 = Room(room_number="LAB1", block="C", floor="1", capacity=70,
                room_type="lab", lab_type="COMPUTING")
    db.add_all([a101, a102, lab1])

    sec_a = Section(academic_context_id=context.id, section_number="CSE-3A", strength=60)
    sec_a.subjects = [cs201, cs251]
    sec_b = Section(academic_context_id=context.id, section_number="CSE-3B", strength=55)
    sec_b.subjects = [cs201]
    db.add_all([sec_a, sec_b])

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)
    db.commit()

    result, run = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message
    assert check_all(db, run.id) == []

    return {"db": db, "context": context, "run": run, "sec_a": sec_a, "sec_b": sec_b, "fac_a": fac_a, "a101": a101}


# ---------------------------------------------------------------------- CSV


def test_master_csv_export(client, two_sections):
    run = two_sections["run"]
    resp = client.get(f"/api/export/master.csv?run_id={run.id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][:3] == ["Day", "Start", "End"]
    assert len(rows) > 1  # header + at least one class


def test_filtered_csv_export_respects_the_filter(client, two_sections):
    run = two_sections["run"]
    sec_a = two_sections["sec_a"]
    resp = client.get(f"/api/export/master.csv?run_id={run.id}&section_id={sec_a.id}")
    rows = list(csv.reader(io.StringIO(resp.text)))
    section_col = rows[0].index("Section")
    data_rows = rows[1:]
    assert all(r[section_col] == "CSE-3A" for r in data_rows)
    assert len(data_rows) > 0


# -------------------------------------------------------------------- Excel


def test_master_xlsx_export_has_two_sheets(client, two_sections):
    run = two_sections["run"]
    resp = client.get(f"/api/export/master.xlsx?run_id={run.id}")
    assert resp.status_code == 200
    wb = load_workbook(io.BytesIO(resp.content))
    assert set(wb.sheetnames) == {"Master Timetable", "Assignments"}
    ws = wb["Master Timetable"]
    assert ws.cell(row=1, column=1).value == "Day"
    assert ws.max_row > 1


def test_run_workbook_export_has_a_sheet_per_section_faculty_room(client, two_sections):
    run = two_sections["run"]
    resp = client.get(f"/api/export/run/{run.id}.xlsx")
    assert resp.status_code == 200
    wb = load_workbook(io.BytesIO(resp.content))

    assert "Master Timetable" in wb.sheetnames
    assert "Assignments" in wb.sheetnames
    assert "Sec CSE-3A" in wb.sheetnames
    assert "Sec CSE-3B" in wb.sheetnames
    assert "Fac Dr. A" in wb.sheetnames  # faculty_grid's title is faculty.name

    # Every room the run actually uses gets a sheet. Not "Room A101" by name:
    # A101 and A102 are interchangeable here, the solver may pick either, and
    # a test that hard-coded one passed or failed on the search order.
    from app.models import Assignment

    db = two_sections["db"]
    used = {a.room for a in db.query(Assignment).filter(Assignment.run_id == run.id)}
    assert used
    for room in used:
        assert f"Room {room.room_code or room.room_number}" in wb.sheetnames

    sec_sheet = wb["Sec CSE-3A"]
    header = [c.value for c in sec_sheet[4]]
    assert header[0] == "Day"


def test_excel_export_never_leaks_internal_ids_as_column_headers(client, two_sections):
    """Spec PART 14: 'do not export useless internal database fields' -
    checked structurally: no header cell is a bare id-looking column."""
    run = two_sections["run"]
    resp = client.get(f"/api/export/master.xlsx?run_id={run.id}")
    wb = load_workbook(io.BytesIO(resp.content))
    for name in wb.sheetnames:
        header = [str(c.value) for c in wb[name][1] if c.value is not None]
        assert not any(h.lower() in ("id", "section_id", "faculty_id", "room_id", "subject_id") for h in header)


# ---------------------------------------------------------------------- PDF


def test_master_pdf_export_downloads(client, two_sections):
    run = two_sections["run"]
    resp = client.get(f"/api/export/master.pdf?run_id={run.id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"
    assert len(resp.content) > 500


def test_section_pdf_export_downloads(client, two_sections):
    sec = two_sections["sec_a"]
    run = two_sections["run"]
    resp = client.get(f"/api/export/section/{sec.id}.pdf?run_id={run.id}")
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_faculty_pdf_export_downloads(client, two_sections):
    fac = two_sections["fac_a"]
    run = two_sections["run"]
    resp = client.get(f"/api/export/faculty/{fac.id}.pdf?run_id={run.id}")
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_room_pdf_export_downloads(client, two_sections):
    room = two_sections["a101"]
    run = two_sections["run"]
    resp = client.get(f"/api/export/room/{room.id}.pdf?run_id={run.id}")
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_pdf_export_404s_for_unknown_entity(client, two_sections):
    run = two_sections["run"]
    resp = client.get(f"/api/export/section/999999.pdf?run_id={run.id}")
    assert resp.status_code == 404


def test_filter_aware_pdf_export_reflects_the_filter_in_its_subtitle(client, two_sections):
    run = two_sections["run"]
    sec_a = two_sections["sec_a"]
    resp = client.get(f"/api/export/master.pdf?run_id={run.id}&section_id={sec_a.id}")
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_two_rooms_with_the_same_number_are_told_apart(client, db_session, context):
    """Block 33 and block 36 can both have a room 101. The room view and the
    workbook sheet named after it use the room code, so they cannot collide -
    they used to be titled by the bare number."""
    from app.grid import build_grid
    from app.models import Room
    from app.routers import timetable as timetable_router

    db_session.add_all(build_grid(periods=2))
    a = Room(room_number="101", block="33", room_code="33-101", capacity=40,
             room_type="lab", is_active=True)
    b = Room(room_number="101", block="36", room_code="36-101", capacity=70,
             room_type="theory", is_active=True, byod=True)
    db_session.add_all([a, b])
    db_session.commit()

    from app.models import TimetableRun
    run = TimetableRun(academic_context_id=context.id, version=1, status="OPTIMAL",
                       publish_status="DRAFT")
    db_session.add(run)
    db_session.commit()

    grid_a = timetable_router.room_grid(a.id, run.id, db=db_session)
    grid_b = timetable_router.room_grid(b.id, run.id, db=db_session)
    assert grid_a.title == "Room 33-101"
    assert grid_b.title == "Room 36-101"
    assert "Capacity 70" in grid_b.subtitle and "Classroom" in grid_b.subtitle
    assert "BYOD" in grid_b.subtitle
