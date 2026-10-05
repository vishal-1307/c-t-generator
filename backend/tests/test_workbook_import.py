"""Importing one workbook, the way an institution actually sends one.

The failure that started this: a realistic fourteen-sheet workbook could not be
imported at all. Every Section-Subject row was rejected for referencing an
academic context "that does not exist" - three sheets after the sheet defining
it. Availability was rejected for referencing time slots the same file supplied.
Nine of the fourteen sheets had no import path whatsoever.

These tests run against **the real workbook**, not a synthetic stand-in, because
the mismatches that mattered were all things a hand-written fixture would have
been written not to have: `faculty_name` where the importer wanted `name`,
`BYOD` where it wanted `byod`, `classroom` where it wanted `theory`, rooms named
by code in one sheet and by number in another, and a curriculum sheet that names
a section without repeating its context on all 120 rows.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.bulk_import import analyse_workbook, apply_workbook
from app.models import (
    AcademicContext,
    Faculty,
    FacultySubject,
    FacultyUnavailability,
    Room,
    RoomUnavailability,
    Section,
    SectionGroup,
    SectionSubject,
    SectionUnavailability,
    Subject,
    TimeSlot,
)

FIXTURE = Path(__file__).parent / "fixtures" / "demo_workbook.xlsx"


@pytest.fixture(scope="module")
def workbook_bytes() -> bytes:
    if not FIXTURE.exists():
        pytest.skip(f"workbook fixture missing: {FIXTURE}")
    return FIXTURE.read_bytes()


def _counts(db):
    return {
        "contexts": db.query(AcademicContext).count(),
        "timeslots": db.query(TimeSlot).count(),
        "rooms": db.query(Room).count(),
        "faculty": db.query(Faculty).count(),
        "subjects": db.query(Subject).count(),
        "sections": db.query(Section).count(),
        "faculty_subject": db.query(FacultySubject).count(),
        "section_subject": db.query(SectionSubject).count(),
        "groups": db.query(SectionGroup).count(),
        "unavailability": (
            db.query(FacultyUnavailability).count()
            + db.query(RoomUnavailability).count()
            + db.query(SectionUnavailability).count()
        ),
    }


# ------------------------------------------------------------ sheet detection


def test_every_sheet_is_accounted_for(db_session, workbook_bytes):
    """No sheet is skipped in silence.

    The previous importer read one sheet and ignored thirteen without saying
    so, which is how a user ends up believing a dataset imported when most of
    it did not.
    """
    preview = analyse_workbook(db_session, workbook_bytes, "demo.xlsx")
    assert len(preview.sheets) == 14
    assert all(s.status in ("detected", "ignored", "unknown", "empty") for s in preview.sheets)
    assert not [s for s in preview.sheets if s.status == "unknown"], (
        "every sheet in the real workbook should be recognised or knowingly "
        "declined: " + str([(s.sheet, s.reason) for s in preview.sheets if s.status == "unknown"])
    )


def test_the_nine_data_sheets_are_detected(db_session, workbook_bytes):
    preview = analyse_workbook(db_session, workbook_bytes, "demo.xlsx")
    detected = {s.entity for s in preview.sheets if s.status == "detected"}
    assert detected == {
        "academic_contexts", "timeslots", "rooms", "faculty", "subjects",
        "sections", "faculty_subjects", "section_subjects", "section_groups",
        "availability",
    }


def test_narrative_and_illustrative_sheets_are_declined_with_a_reason(
    db_session, workbook_bytes
):
    """A summary sheet is not a failed import - and a reference timetable is an
    illustration, not input. Both should say so rather than read as problems."""
    preview = analyse_workbook(db_session, workbook_bytes, "demo.xlsx")
    ignored = {s.sheet: s.reason for s in preview.sheets if s.status == "ignored"}
    assert any("Summary" in name for name in ignored)
    assert any("README" in name for name in ignored)
    assert any("Reference_Timetable" in name for name in ignored)
    assert all(reason for reason in ignored.values()), "each needs a stated reason"


# ------------------------------------------------------------ whole-file validity


def test_the_real_workbook_validates_with_no_errors(db_session, workbook_bytes):
    """The headline claim. This file previously could not be imported at all."""
    preview = analyse_workbook(db_session, workbook_bytes, "demo.xlsx")
    assert preview.problems == [], [
        f"{p.sheet} row {p.row_number}: {p.message}" for p in preview.problems[:10]
    ]
    assert not preview.has_errors
    assert preview.can_apply


def test_a_curriculum_row_resolves_a_section_defined_on_another_sheet(
    db_session, workbook_bytes
):
    """The exact failure that started this.

    Section-Subject rows name a section the Sections sheet defines, in a
    context the Academic Context sheet defines. Validating each row against the
    database alone made all 120 of them errors.
    """
    preview = analyse_workbook(db_session, workbook_bytes, "demo.xlsx")
    curriculum = next(o for o in preview.outcomes if o.entity == "section_subjects")
    assert curriculum.creates == 120
    assert curriculum.invalid == 0


def test_availability_resolves_slots_the_same_workbook_defines(
    db_session, workbook_bytes
):
    """The other half of the original failure: 'no configured time slot for
    day Monday period 2' while the workbook's own grid sheet defined it."""
    preview = analyse_workbook(db_session, workbook_bytes, "demo.xlsx")
    availability = next(o for o in preview.outcomes if o.entity == "availability")
    assert availability.creates == 14
    assert availability.invalid == 0


def test_contexts_the_sections_imply_are_created_and_declared(
    db_session, workbook_bytes
):
    """The workbook declares one context and its sections use three.

    Refusing the file over that would be pedantry about presentation; creating
    them silently would hide the scope everything else hangs from. They are
    created, and each is listed as its own decision.
    """
    preview = analyse_workbook(db_session, workbook_bytes, "demo.xlsx")
    created = [d for d in preview.decisions if d.kind == "create_context"]
    assert len(created) == 3
    labels = " ".join(d.label for d in created)
    assert "BCA" in labels and "BSC" in labels and "BTECH" in labels


# ---------------------------------------------------------------- applying it


def test_the_whole_workbook_imports_in_one_pass(db_session, workbook_bytes):
    result = apply_workbook(db_session, workbook_bytes, "demo.xlsx")
    assert result.committed

    assert _counts(db_session) == {
        "contexts": 3,
        "timeslots": 45,
        "rooms": 85,
        "faculty": 25,
        "subjects": 16,
        "sections": 19,
        "faculty_subject": 37,
        "section_subject": 120,
        "groups": 22,
        "unavailability": 14,
    }


def test_a_mixed_subject_imports_as_the_syllabus_describes_it(db_session, workbook_bytes):
    """CAP460 is two lectures and four practical periods a week, in a
    programming lab needing power and students' own machines. It used to be
    inexpressible without inventing a second subject code."""
    apply_workbook(db_session, workbook_bytes, "demo.xlsx")

    subject = db_session.query(Subject).filter_by(code="CAP460").one()
    assert subject.type == "mixed"
    assert subject.lecture_sessions_per_week == 2
    assert subject.practical_sessions_per_week == 4
    assert subject.required_lab_type == "programming"
    assert subject.byod_required is True
    assert subject.charging_required is True
    assert subject.periods_per_week == 6


def test_a_theory_subject_takes_its_load_from_the_lecture_column(
    db_session, workbook_bytes
):
    """A real subjects sheet states a per-component load on every row, so a
    theory subject carries lecture_per_week=3 and practical_per_week=0.
    Rejecting that would refuse a perfectly clear file."""
    apply_workbook(db_session, workbook_bytes, "demo.xlsx")

    subject = db_session.query(Subject).filter_by(code="CAP3101").one()
    assert subject.type == "theory"
    assert subject.sessions_per_week == 3
    assert subject.periods_per_week == 3
    assert subject.lecture_sessions_per_week is None, (
        "a pure subject must not carry a second copy of its load"
    )


def test_room_capabilities_and_block_scoped_numbering_survive(db_session, workbook_bytes):
    apply_workbook(db_session, workbook_bytes, "demo.xlsx")

    classroom = db_session.query(Room).filter_by(room_code="36-101").one()
    assert (classroom.block, classroom.room_number) == ("36", "101")
    assert classroom.room_type == "theory", "'classroom' maps to the scheduler's word"
    assert classroom.byod is True and classroom.charging is True
    assert classroom.charging_sockets == 30

    # The reason room numbers had to stop being globally unique.
    repeated = db_session.query(Room).filter_by(room_number="101").count()
    assert repeated > 1, "the same number in different blocks is different rooms"

    offices = db_session.query(Room).filter_by(room_type="faculty").count()
    assert offices == 13, "faculty rooms are recognised and never scheduled into"

    lab_types = {
        r.lab_type for r in db_session.query(Room).filter_by(room_type="lab")
    }
    assert lab_types == {
        "programming", "cybersecurity", "iot", "networking", "electronics"
    }


def test_groups_are_recorded_against_their_section(db_session, workbook_bytes):
    apply_workbook(db_session, workbook_bytes, "demo.xlsx")

    section = db_session.query(Section).filter_by(section_number="D2402").one()
    assert section.strength == 68
    assert section.batch == "BCA-2024"
    assert {(g.group_code, g.strength) for g in section.groups} == {
        ("G1", 34), ("G2", 34)
    }


def test_availability_resolves_faculty_by_code_rooms_by_code_sections_by_number(
    db_session, workbook_bytes
):
    """One sheet names three kinds of resource three different ways, and each
    is how a person would refer to that thing."""
    apply_workbook(db_session, workbook_bytes, "demo.xlsx")

    faculty_block = db_session.query(FacultyUnavailability).all()
    assert {b.faculty.faculty_code for b in faculty_block} >= {"T-001"}

    room_block = db_session.query(RoomUnavailability).all()
    assert {b.room.room_code for b in room_block} >= {"36-201"}

    section_block = db_session.query(SectionUnavailability).all()
    assert {b.section.section_number for b in section_block} >= {"D2401"}


# ------------------------------------------------------------- safety


def test_importing_the_same_workbook_twice_changes_nothing(db_session, workbook_bytes):
    """Re-uploading is how a person checks they uploaded the right file."""
    first = apply_workbook(db_session, workbook_bytes, "demo.xlsx")
    after_first = _counts(db_session)

    second = analyse_workbook(db_session, workbook_bytes, "demo.xlsx")
    assert second.counts["creates"] == 0
    assert second.counts["updates"] == 0
    assert second.counts["unchanged"] == first.counts["creates"]
    assert not second.can_apply, "there is nothing left to do"

    apply_workbook(db_session, workbook_bytes, "demo.xlsx")
    assert _counts(db_session) == after_first


def test_one_bad_row_prevents_the_entire_import(db_session, workbook_bytes):
    """Nothing partial. A workbook is valid as a whole or not at all - a
    half-imported dataset is worse than a refused one, because it looks done.
    """
    book = load_workbook(io.BytesIO(workbook_bytes))
    sheet = book["03_Rooms"]
    capacity_col = [c.value for c in sheet[1]].index("capacity") + 1
    sheet.cell(row=5, column=capacity_col).value = "not a number"
    broken = io.BytesIO()
    book.save(broken)

    result = apply_workbook(db_session, broken.getvalue(), "broken.xlsx")

    assert not result.committed
    assert result.has_errors
    assert _counts(db_session) == {
        "contexts": 0, "timeslots": 0, "rooms": 0, "faculty": 0, "subjects": 0,
        "sections": 0, "faculty_subject": 0, "section_subject": 0, "groups": 0,
        "unavailability": 0,
    }, "a rejected workbook must leave the database untouched"


def test_a_problem_says_which_sheet_row_and_column(db_session, workbook_bytes):
    """'Row 87 is wrong' is not help in a 419-row workbook."""
    book = load_workbook(io.BytesIO(workbook_bytes))
    sheet = book["03_Rooms"]
    capacity_col = [c.value for c in sheet[1]].index("capacity") + 1
    sheet.cell(row=5, column=capacity_col).value = "not a number"
    broken = io.BytesIO()
    book.save(broken)

    preview = analyse_workbook(db_session, broken.getvalue(), "broken.xlsx")
    problem = next(p for p in preview.problems if p.row_number == 5)

    assert problem.sheet == "03_Rooms"
    assert problem.column == "capacity"
    assert "whole number" in problem.message
    assert problem.suggestion, "a problem should say what to do about it"
    assert problem.identity, "and which record it was"


def test_an_unreadable_file_is_refused_clearly(db_session):
    from app.bulk_import import ParseError

    with pytest.raises(ParseError) as exc:
        analyse_workbook(db_session, b"section,subject\nD2401,CAP460\n", "notes.csv")
    assert "workbook" in str(exc.value).lower()


def test_a_workbook_with_no_recognisable_sheet_says_so(db_session):
    book = Workbook()
    book.remove(book.active)
    sheet = book.create_sheet("Mystery")
    sheet.append(["alpha", "beta"])
    sheet.append(["1", "2"])
    buffer = io.BytesIO()
    book.save(buffer)

    from app.bulk_import import ParseError

    with pytest.raises(ParseError) as exc:
        analyse_workbook(db_session, buffer.getvalue(), "mystery.xlsx")
    assert "matched an importable entity" in str(exc.value)


# ------------------------------------------------------------------- the API


def test_the_endpoints_analyse_and_apply(client, workbook_bytes):
    files = {"file": ("demo.xlsx", workbook_bytes,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    analysed = client.post("/api/bulk-import/workbook/analyse", files=files)
    assert analysed.status_code == 200, analysed.text
    body = analysed.json()
    assert body["can_apply"] is True
    assert body["counts"]["creates"] == 386
    assert len(body["sheets"]) == 14

    files = {"file": ("demo.xlsx", workbook_bytes,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    applied = client.post("/api/bulk-import/workbook/apply", files=files)
    assert applied.status_code == 200, applied.text
    assert applied.json()["committed"] is True


def test_analysing_writes_nothing(client, db_session, workbook_bytes):
    files = {"file": ("demo.xlsx", workbook_bytes,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    client.post("/api/bulk-import/workbook/analyse", files=files)
    assert sum(_counts(db_session).values()) == 0


def test_the_workbook_endpoint_is_not_shadowed_by_the_single_entity_route(
    client, workbook_bytes
):
    """`/workbook/apply` also matches `/{entity}/apply`, and whichever is
    declared first wins. Declared the other way round, every workbook import was
    answered by the single-entity route looking for an adapter named
    "workbook"."""
    files = {"file": ("demo.xlsx", workbook_bytes,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    response = client.post("/api/bulk-import/workbook/apply", files=files)
    assert response.status_code == 200
    assert "Unknown entity" not in response.text


def test_single_entity_import_still_works(client):
    """The workbook path is additive. Correcting one sheet without re-uploading
    an entire workbook remains the right tool for that job."""
    csv = (
        "block,floor,room_number,capacity,room_type\n"
        "36,1,101,60,classroom\n"
    ).encode()
    response = client.post(
        "/api/bulk-import/rooms/preview",
        files={"file": ("rooms.csv", csv, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["counts"]["create"] == 1
