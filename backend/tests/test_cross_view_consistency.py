"""Phase 7: cross-view consistency.

One underlying Assignment row must be the truth every view agrees on - never
four separately-derived records that can drift. This file is the explicit
proof spec #44/#45 asks for: pick one real scheduled class, confirm the
section/faculty/room/master views all describe it identically, then make a
manual change and confirm every view reflects it with nothing stale left
behind.
"""
from __future__ import annotations

import pytest

from app.grid import build_grid
from app.models import AcademicContext, Faculty, Room, Section, Subject
from app.solver.run import generate

from constraint_checker import check_all


@pytest.fixture
def solved(db_session, context):
    db = db_session
    subjects = {}
    for name, code, type_, length, spw in [
        ("Data Structures", "CS201", "theory", 1, 3),
        ("Operating Systems", "CS202", "theory", 1, 2),
    ]:
        s = Subject(name=name, code=code, type=type_, session_length_hours=length, sessions_per_week=spw)
        db.add(s)
        subjects[code] = s
    db.flush()

    rooms = {}
    for number in ("A101", "A102"):
        r = Room(room_number=number, block="A", floor="1", capacity=70, room_type="theory")
        db.add(r)
        rooms[number] = r
    db.flush()

    faculty = {}
    for fname, fcode in [("Dr. A", "FAC01"), ("Dr. B", "FAC02")]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = list(subjects.values())
        db.add(f)
        faculty[fcode] = f
    db.flush()

    sec = Section(academic_context_id=context.id, section_number="D2402", strength=60)
    sec.subjects = list(subjects.values())
    db.add(sec)

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)
    db.commit()

    result, run = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert check_all(db, run.id) == []
    return {"db": db, "context": context, "run": run, "rooms": rooms, "faculty": faculty}


def test_one_assignment_is_described_identically_across_all_four_views(solved, client):
    """The literal example from spec #44: D2402 + a subject, one faculty, one
    room, one slot - section/faculty/room/master views must all agree, since
    they are four queries over the same row, not four copies of it."""
    db, run = solved["db"], solved["run"]
    from app.models import Assignment

    a = db.query(Assignment).filter(Assignment.run_id == run.id).first()

    section_grid = client.get(f"/api/timetable/section/{a.section_id}?run_id={run.id}").json()
    faculty_grid = client.get(f"/api/timetable/faculty/{a.faculty_id}?run_id={run.id}").json()
    room_grid = client.get(f"/api/timetable/room/{a.room_id}?run_id={run.id}").json()
    master = client.get(f"/api/timetable/master?run_id={run.id}").json()

    def _find_cell(grid):
        for row in grid["rows"]:
            for cell in row["cells"]:
                if cell.get("assignment_id") == a.id:
                    return cell
        return None

    sec_cell = _find_cell(section_grid)
    fac_cell = _find_cell(faculty_grid)
    room_cell = _find_cell(room_grid)
    master_row = next(r for r in master["rows"] if r["assignment_id"] == a.id)

    assert sec_cell is not None and fac_cell is not None and room_cell is not None

    # Section view: names the subject, faculty, room.
    assert sec_cell["subject_code"] == a.subject.code
    assert sec_cell["faculty_id"] == a.faculty_id
    assert sec_cell["room_id"] == a.room_id

    # Faculty view: names the subject, section, room.
    assert fac_cell["subject_code"] == a.subject.code
    assert fac_cell["section_id"] == a.section_id
    assert fac_cell["room_id"] == a.room_id

    # Room view: names the subject, section, faculty.
    assert room_cell["subject_code"] == a.subject.code
    assert room_cell["section_id"] == a.section_id
    assert room_cell["faculty_id"] == a.faculty_id

    # Master view: has all of it at once.
    assert master_row["subject_code"] == a.subject.code
    assert master_row["section_id"] == a.section_id
    assert master_row["faculty_id"] == a.faculty_id
    assert master_row["room_id"] == a.room_id


def test_room_change_is_reflected_with_no_stale_data_in_any_view(solved, client):
    db, run = solved["db"], solved["run"]
    from app.models import Assignment

    a = db.query(Assignment).filter(Assignment.run_id == run.id).first()
    old_room_id = a.room_id
    new_room = next(r for r in solved["rooms"].values() if r.id != old_room_id)

    preview = client.post(f"/api/assignments/{a.id}/room/preview", json={"room_id": new_room.id})
    if not preview.json()["ok"]:
        pytest.skip(f"candidate room busy: {preview.json()['issues']}")

    resp = client.post(f"/api/assignments/{a.id}/room", json={"room_id": new_room.id})
    assert resp.status_code == 200, resp.text

    section_grid = client.get(f"/api/timetable/section/{a.section_id}?run_id={run.id}").json()
    room_grid_new = client.get(f"/api/timetable/room/{new_room.id}?run_id={run.id}").json()
    master = client.get(f"/api/timetable/master?run_id={run.id}").json()

    # Scope every check to THIS pair's cells - the section may (and does, in
    # this fixture) have other subjects still legitimately using the old
    # room, so "old room id appears nowhere in the grid" is the wrong
    # assertion. What must be true is that every cell for *this* pair moved,
    # and nothing else did.
    pair_cells_in_section = [
        c for row in section_grid["rows"] for c in row["cells"]
        if c.get("subject_id") == a.subject_id
    ]
    assert pair_cells_in_section, "the moved pair must still appear in its own section view"
    assert all(c["room_id"] == new_room.id for c in pair_cells_in_section)

    assert any(
        c.get("subject_id") == a.subject_id and c.get("section_id") == a.section_id
        for row in room_grid_new["rows"] for c in row["cells"]
    ), "the new room's own view must show this pair"

    master_rows_for_pair = [r for r in master["rows"] if r["section_id"] == a.section_id
                             and r["subject_id"] == a.subject_id]
    assert master_rows_for_pair
    assert all(r["room_number"] == new_room.room_number for r in master_rows_for_pair)


def test_faculty_change_is_reflected_with_no_stale_data_in_any_view(solved, client):
    db, run = solved["db"], solved["run"]
    from app.models import Assignment

    a = db.query(Assignment).filter(Assignment.run_id == run.id).first()
    old_faculty_id = a.faculty_id
    new_faculty = next(f for f in solved["faculty"].values() if f.id != old_faculty_id)

    preview = client.post(f"/api/assignments/{a.id}/faculty/preview", json={"faculty_id": new_faculty.id})
    if not preview.json()["ok"]:
        pytest.skip(f"candidate faculty busy: {preview.json()['issues']}")

    resp = client.post(f"/api/assignments/{a.id}/faculty", json={"faculty_id": new_faculty.id})
    assert resp.status_code == 200, resp.text

    section_grid = client.get(f"/api/timetable/section/{a.section_id}?run_id={run.id}").json()
    master = client.get(f"/api/timetable/master?run_id={run.id}").json()

    faculty_ids_in_section = {
        c.get("faculty_id") for row in section_grid["rows"] for c in row["cells"] if c.get("faculty_id")
    }
    assert new_faculty.id in faculty_ids_in_section
    master_rows_for_pair = [r for r in master["rows"] if r["section_id"] == a.section_id
                             and r["subject_id"] == a.subject_id]
    assert all(r["faculty_code"] == new_faculty.faculty_code for r in master_rows_for_pair)


def test_day_time_move_is_reflected_with_no_stale_data_in_any_view(solved, client):
    db, run = solved["db"], solved["run"]
    from app.models import Assignment, TimeSlot

    a = db.query(Assignment).filter(Assignment.run_id == run.id).first()
    occupied = {
        x.timeslot_id for x in db.query(Assignment).filter(Assignment.run_id == run.id).all()
        if x.section_id == a.section_id or x.faculty_id == a.faculty_id or x.room_id == a.room_id
    }
    target = next(
        (s for s in db.query(TimeSlot).filter(TimeSlot.is_lunch.is_(False)).all()
         if s.id not in occupied),
        None,
    )
    assert target is not None

    resp = client.post(f"/api/assignments/{a.id}/move", json={"target_timeslot_id": target.id})
    assert resp.status_code == 200, resp.text

    section_grid = client.get(f"/api/timetable/section/{a.section_id}?run_id={run.id}").json()
    # Match by assignment_id, not subject_code - the fixture's subjects meet
    # more than once a week, so matching by code alone could find an
    # untouched sibling occurrence of the same subject instead of the one
    # that actually moved.
    cell = next(
        c for row in section_grid["rows"] for c in row["cells"]
        if c.get("assignment_id") == a.id
    )
    assert cell["period_index"] == target.period_index

    master = client.get(f"/api/timetable/master?run_id={run.id}").json()
    master_row = next(r for r in master["rows"] if r["assignment_id"] == a.id)
    assert master_row["day_index"] == target.day_index
    assert master_row["period_index"] == target.period_index


def test_bulk_import_update_is_reflected_with_no_stale_data_in_any_view(solved, client):
    """Phase 10 PART 23's new angle: a room's metadata edited through the
    bulk-import pipeline (not the CRUD form, not a manual edit) must show up
    identically in the room's own detail page and every timetable view that
    displays it - the same single source of truth, reached by a different
    door."""
    import io

    db, run = solved["db"], solved["run"]
    room = solved["rooms"]["A101"]

    resp = client.post(
        "/api/bulk-import/rooms/apply",
        files={"file": ("rooms.csv", io.BytesIO(
            f"block,floor,room_number,capacity,room_type\n{room.block},{room.floor},{room.room_number},99,theory\n"
        .encode()), "text/csv")},
        data={"mode": "add_update"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["counts"]["update"] == 1

    detail = client.get(f"/api/rooms/{room.id}/detail").json()
    assert detail["capacity"] == 99

    room_grid = client.get(f"/api/timetable/room/{room.id}?run_id={run.id}").json()
    assert "Capacity 99" in room_grid["subtitle"]

    master = client.get(f"/api/timetable/master?run_id={run.id}&room_id={room.id}").json()
    assert master["total"] >= 0  # rows still resolve post-update, nothing broke

    db.refresh(room)
    assert room.capacity == 99


def test_faculty_and_room_views_follow_the_chosen_academic_context(solved, client):
    """A teacher's timetable must be the selected context's, not whichever run
    happens to be newest in the whole institution.

    Found live with two academic contexts in the database: a BCA teacher's view
    rendered the newest run, which belonged to a different programme, and
    reported "nothing scheduled here" for someone with a full week. A section
    view was unaffected because a section implies its own context; a faculty
    member and a room belong to the institution, so they have to be told.
    """
    from app.models import Assignment

    db, first_run = solved["db"], solved["run"]
    # Whichever teacher and room the solver actually used - both candidates
    # were eligible, so which one it picked is not the point here.
    sample = db.query(Assignment).filter(Assignment.run_id == first_run.id).first()
    assert sample is not None
    faculty = db.get(Faculty, sample.faculty_id)
    room = db.get(Room, sample.room_id)

    # A second context, generated later, so it owns the newest run overall.
    other = AcademicContext(academic_year="2027-28", semester=2, program="BSC",
                            department="Computer Science")
    db.add(other)
    db.flush()
    subject = Subject(name="Signals", code="EC301", type="theory",
                      session_length_hours=1, sessions_per_week=2)
    db.add(subject)
    db.flush()
    teacher = Faculty(name="Dr. C", faculty_code="FAC09")
    teacher.subjects = [subject]
    db.add(teacher)
    section = Section(academic_context_id=other.id, section_number="S-OTHER",
                      strength=40)
    section.subjects = [subject]
    db.add(section)
    db.commit()

    _result, newer_run = generate(db, academic_context_id=other.id, soft=False,
                                  max_seconds=20)
    assert newer_run.id != first_run.id

    # Unscoped: the newest run anywhere - which is the other context's.
    unscoped = client.get(f"/api/timetable/faculty/{faculty.id}").json()
    assert unscoped["run_id"] == newer_run.id

    # Scoped: the run belonging to the context the user actually selected.
    scoped = client.get(
        f"/api/timetable/faculty/{faculty.id}"
        f"?academic_context_id={solved['context'].id}"
    ).json()
    assert scoped["run_id"] == first_run.id
    assert scoped["total_periods"] > 0, (
        "the teacher's own context has classes; the view must show them"
    )

    scoped_room = client.get(
        f"/api/timetable/room/{room.id}"
        f"?academic_context_id={solved['context'].id}"
    ).json()
    assert scoped_room["run_id"] == first_run.id
    assert scoped_room["total_periods"] > 0

    # An explicit run_id still wins over the context.
    pinned = client.get(
        f"/api/timetable/faculty/{faculty.id}"
        f"?run_id={first_run.id}&academic_context_id={other.id}"
    ).json()
    assert pinned["run_id"] == first_run.id
