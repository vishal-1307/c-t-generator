"""Reading every sheet of a workbook, not just the active one.

`parse_table` reads `wb.active`, which is correct for a single-entity upload
and is the reason a fourteen-sheet onboarding workbook could never be imported:
thirteen of its sheets were silently ignored, with no error to explain it.

`parse_workbook` is the multi-sheet counterpart the whole-workbook import is
built on. These tests pin the parts that decide whether a real spreadsheet
survives the trip: value coercion, blank handling, and reporting an
unrecognisable sheet rather than pretending it was not there.
"""
from __future__ import annotations

import datetime as dt
import io

import pytest
from openpyxl import Workbook

from app.bulk_import import ParseError, parse_workbook


def _workbook(sheets: dict[str, list[list]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_every_sheet_is_returned_not_only_the_active_one(client):
    data = _workbook({
        "03_Rooms": [["room_number", "capacity"], ["101", 70]],
        "04_Faculty": [["faculty_id", "faculty_name"], ["T-001", "Dr Rao"]],
        "05_Subjects": [["subject_code", "subject_name"], ["CAP460", "Python"]],
    })
    sheets = parse_workbook(data)
    assert set(sheets) == {"03_Rooms", "04_Faculty", "05_Subjects"}
    assert sheets["04_Faculty"][1] == [{"faculty_id": "T-001", "faculty_name": "Dr Rao"}]


def test_sheet_order_is_preserved(client):
    """Dependency order is decided by the importer, not by the file - but a
    stable order makes a preview readable and a diff reviewable."""
    data = _workbook({
        "01_Context": [["a"], ["1"]],
        "02_Rooms": [["b"], ["2"]],
        "03_Faculty": [["c"], ["3"]],
    })
    assert list(parse_workbook(data)) == ["01_Context", "02_Rooms", "03_Faculty"]


def test_values_arrive_as_the_strings_a_human_typed(client):
    """Adapters validate strings. A spreadsheet stores 70 as a float and a
    time as a datetime, and neither should reach an adapter in that shape."""
    data = _workbook({
        "Rooms": [
            ["room_number", "capacity", "is_active", "start"],
            [101, 70.0, True, dt.time(9, 30)],
        ],
    })
    row = parse_workbook(data)["Rooms"][1][0]
    assert row["room_number"] == "101", "a whole number must not become '101.0'"
    assert row["capacity"] == "70"
    assert row["is_active"] in ("True", "true", "TRUE")
    assert "09:30" in row["start"]


def test_blank_rows_are_skipped_but_blank_cells_are_kept(client):
    data = _workbook({
        "Rooms": [
            ["room_number", "block", "lab_type"],
            ["101", "36", ""],
            [None, None, None],
            ["102", "36", "iot"],
        ],
    })
    headers, rows = parse_workbook(data)["Rooms"]
    assert len(rows) == 2, "a wholly empty row is spacing, not data"
    assert rows[0]["lab_type"] == "", "an empty cell is a value, not a missing column"


def test_a_sheet_with_no_header_is_reported_empty_rather_than_dropped(client):
    """A summary or notes sheet has no header row. The importer needs to be
    able to say "this sheet was not recognised" - which it cannot do if the
    parser quietly omits it."""
    data = _workbook({
        "00_Summary": [["COLLEGE TIMETABLE - SYNTHETIC DEMO DATASET"], [], ["generated"]],
        "Rooms": [["room_number"], ["101"]],
    })
    sheets = parse_workbook(data)
    assert "00_Summary" in sheets
    assert sheets["Rooms"][0] == ["room_number"]


def test_a_completely_empty_sheet_is_present_and_empty(client):
    data = _workbook({"Blank": [], "Rooms": [["room_number"], ["101"]]})
    sheets = parse_workbook(data)
    assert sheets["Blank"] == ([], [])


def test_columns_without_a_header_are_ignored(client):
    """Spreadsheets accumulate stray cells to the right of the real table."""
    data = _workbook({
        "Rooms": [["room_number", None, "capacity"], ["101", "scratch note", "70"]],
    })
    headers, rows = parse_workbook(data)["Rooms"]
    assert headers == ["room_number", "capacity"]
    assert rows[0] == {"room_number": "101", "capacity": "70"}


def test_a_file_that_is_not_a_workbook_fails_with_a_readable_message(client):
    with pytest.raises(ParseError) as exc:
        parse_workbook(b"section_number,subject_code\nD2401,CAP460\n")
    assert "workbook" in str(exc.value).lower()
