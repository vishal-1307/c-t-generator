"""Phase 7: availability queries, version comparison, master pagination, and
academic-context isolation on the new endpoints."""
from __future__ import annotations

import pytest

from app.grid import build_grid
from app.models import (
    AcademicContext,
    Faculty,
    FacultyUnavailability,
    Room,
    RoomUnavailability,
    Section,
    Subject,
)
from app.solver.run import generate

from constraint_checker import check_all


@pytest.fixture
def solved(db_session, context):
    db = db_session
    subjects = {}
    for name, code, type_, length, spw, lab_type in [
        ("Data Structures", "CS201", "theory", 1, 3, None),
        ("DS Lab", "CS251", "practical", 2, 1, "COMPUTING"),
    ]:
        s = Subject(name=name, code=code, type=type_, session_length_hours=length,
                    sessions_per_week=spw, required_lab_type=lab_type)
        db.add(s)
        subjects[code] = s
    db.flush()

    rooms = {}
    for number, room_type, lab_type, capacity in [
        ("A101", "theory", None, 70), ("A102", "theory", None, 40),
        ("LAB1", "lab", "COMPUTING", 70), ("A-F1", "faculty", None, 2),
    ]:
        r = Room(room_number=number, block="A", floor="1", capacity=capacity,
                  room_type=room_type, lab_type=lab_type)
        db.add(r)
        rooms[number] = r
    db.flush()

    faculty = {}
    for fname, fcode, teaches in [("Dr. A", "FAC01", ["CS201"]), ("Dr. L", "FAC02", ["CS251"])]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = [subjects[c] for c in teaches]
        db.add(f)
        faculty[fcode] = f
    db.flush()

    sec = Section(academic_context_id=context.id, section_number="S-A", strength=60)
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
    return {
        "db": db, "context": context, "run": run, "subjects": subjects,
        "rooms": rooms, "faculty": faculty,
    }


# --------------------------------------------------------------- room availability


def test_available_rooms_excludes_faculty_rooms(solved, client):
    from app.models import TimeSlot

    slot = solved["db"].query(TimeSlot).filter(TimeSlot.is_lunch.is_(False)).first()
    resp = client.get(f"/api/availability/rooms?timeslot_id={slot.id}&length=1")
    assert resp.status_code == 200
    rooms = resp.json()
    assert all(r["room_type"] != "faculty" for r in rooms)


def test_available_rooms_respects_capacity_filter(solved, client):
    from app.models import TimeSlot

    slot = solved["db"].query(TimeSlot).filter(TimeSlot.is_lunch.is_(False)).first()
    resp = client.get(f"/api/availability/rooms?timeslot_id={slot.id}&length=1&capacity_min=50")
    rooms = resp.json()
    assert all(r["capacity"] >= 50 for r in rooms)
    assert not any(r["room_number"] == "A102" for r in rooms), "A102 has capacity 40"


def test_available_rooms_excludes_declared_unavailability(solved, client):
    db = solved["db"]
    from app.models import TimeSlot

    slot = db.query(TimeSlot).filter(TimeSlot.is_lunch.is_(False)).first()
    room = solved["rooms"]["A101"]
    db.add(RoomUnavailability(room_id=room.id, timeslot_id=slot.id))
    db.commit()

    resp = client.get(f"/api/availability/rooms?timeslot_id={slot.id}&length=1")
    room_ids = {r["id"] for r in resp.json()}
    assert room.id not in room_ids


def test_available_rooms_excludes_rooms_occupied_in_a_run(solved, client):
    from app.models import Assignment

    db, run = solved["db"], solved["run"]
    a = db.query(Assignment).filter(Assignment.run_id == run.id).first()

    resp = client.get(f"/api/availability/rooms?timeslot_id={a.timeslot_id}&length=1&run_id={run.id}")
    room_ids = {r["id"] for r in resp.json()}
    assert a.room_id not in room_ids


# ----------------------------------------------------------- faculty availability


def test_available_faculty_filters_by_subject_eligibility(solved, client):
    from app.models import TimeSlot

    slot = solved["db"].query(TimeSlot).filter(TimeSlot.is_lunch.is_(False)).first()
    cs251 = solved["subjects"]["CS251"]

    resp = client.get(f"/api/availability/faculty?timeslot_id={slot.id}&length=1&subject_id={cs251.id}")
    codes = {f["faculty_code"] for f in resp.json()}
    assert codes <= {"FAC02"}  # only the lab teacher is eligible


def test_available_faculty_excludes_declared_unavailability(solved, client):
    db = solved["db"]
    from app.models import TimeSlot

    slot = db.query(TimeSlot).filter(TimeSlot.is_lunch.is_(False)).first()
    fac = solved["faculty"]["FAC01"]
    db.add(FacultyUnavailability(faculty_id=fac.id, timeslot_id=slot.id))
    db.commit()

    resp = client.get(f"/api/availability/faculty?timeslot_id={slot.id}&length=1")
    ids = {f["id"] for f in resp.json()}
    assert fac.id not in ids


# ----------------------------------------------------------- section availability


def test_section_availability_check_true_when_free(solved, client):
    db = solved["db"]
    from app.models import Assignment, TimeSlot

    section = solved["db"].query(Section).one()
    occupied = {a.timeslot_id for a in db.query(Assignment).filter(Assignment.run_id == solved["run"].id)}
    free_slot = next(s for s in db.query(TimeSlot).filter(TimeSlot.is_lunch.is_(False)) if s.id not in occupied)

    resp = client.get(
        f"/api/sections/{section.id}/availability/check"
        f"?timeslot_id={free_slot.id}&length=1&run_id={solved['run'].id}"
    )
    assert resp.json()["available"] is True


def test_section_availability_check_false_when_occupied(solved, client):
    from app.models import Assignment

    db, run = solved["db"], solved["run"]
    a = db.query(Assignment).filter(Assignment.run_id == run.id).first()
    section = a.section

    resp = client.get(
        f"/api/sections/{section.id}/availability/check"
        f"?timeslot_id={a.timeslot_id}&length=1&run_id={run.id}"
    )
    body = resp.json()
    assert body["available"] is False
    assert body["issues"]


def test_section_free_slots_excludes_occupied_ones(solved, client):
    from app.models import Assignment

    db, run = solved["db"], solved["run"]
    section = db.query(Section).one()
    occupied = {a.timeslot_id for a in db.query(Assignment).filter(Assignment.run_id == run.id)}

    resp = client.get(f"/api/sections/{section.id}/availability/free-slots?length=1&run_id={run.id}")
    free_ids = {s["id"] for s in resp.json()}
    assert not (free_ids & occupied)


# --------------------------------------------------------------- master availability


def test_master_availability_aggregates_all_three_resource_kinds(solved, client):
    from app.models import Assignment

    db, run, context = solved["db"], solved["run"], solved["context"]
    a = db.query(Assignment).filter(Assignment.run_id == run.id).first()

    resp = client.get(
        f"/api/timetable/availability?timeslot_id={a.timeslot_id}&run_id={run.id}"
        f"&academic_context_id={context.id}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert a.faculty_id in body["occupied_faculty_ids"]
    assert a.faculty_id not in body["available_faculty_ids"]
    assert a.room_id in body["occupied_room_ids"]
    assert a.section_id in body["occupied_section_ids"]


# ------------------------------------------------------------ master pagination


def test_master_view_pagination(solved, client):
    resp = client.get(f"/api/timetable/master?run_id={solved['run'].id}&limit=2&offset=0")
    body = resp.json()
    assert len(body["rows"]) <= 2
    assert body["total"] >= len(body["rows"])


# --------------------------------------------------------------- version diff


def test_diff_detects_room_faculty_and_move_changes(solved, client):
    from app.models import Assignment

    db, run, context = solved["db"], solved["run"], solved["context"]
    a = db.query(Assignment).filter(Assignment.run_id == run.id, Assignment.subject_id ==
                                     solved["subjects"]["CS201"].id).first()
    other_room = next(r for r in solved["rooms"].values()
                       if r.room_type == "theory" and r.id != a.room_id)

    preview = client.post(f"/api/assignments/{a.id}/room/preview", json={"room_id": other_room.id})
    if preview.json()["ok"]:
        client.post(f"/api/assignments/{a.id}/room", json={"room_id": other_room.id})

    # Regenerate to get a second run to diff against.
    result2, run2 = generate(db, academic_context_id=context.id, soft=False, max_seconds=20,
                              respect_persisted=False)
    assert result2.ok

    resp = client.get(f"/api/runs/{run.id}/diff/{run2.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["from_run_id"] == run.id
    assert body["to_run_id"] == run2.id
    assert isinstance(body["rows"], list)


def test_diff_unknown_run_is_404(solved, client):
    resp = client.get(f"/api/runs/{solved['run'].id}/diff/999999")
    assert resp.status_code == 404
