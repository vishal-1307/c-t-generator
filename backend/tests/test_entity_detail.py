"""Phase 9: entity detail pages (faculty/subject/section/room).

Built on a real, solver-generated and published timetable - never hand-built
assignment rows - so workload, utilization and occupancy reflect a genuine
schedule the same way the detail endpoints will see one in production.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app import entity_detail
from app.grid import build_grid
from app.models import Faculty, Room, Section, Subject
from app.solver.run import generate

from constraint_checker import check_all


def _date_for_weekday(weekday: int) -> dt.date:
    """Any real calendar date whose .weekday() equals the given TimeSlot
    .day_index (0=Monday) - computed from an arbitrary anchor rather than
    hardcoded, so this doesn't depend on what day a specific date fell on."""
    anchor = dt.date(2026, 1, 5)
    delta = (weekday - anchor.weekday()) % 7
    return anchor + dt.timedelta(days=delta)


@pytest.fixture
def published(client, db_session, context):
    db = db_session

    cs201 = Subject(name="Data Structures", code="CS201", type="theory",
                     session_length_hours=1, sessions_per_week=3)
    cs251 = Subject(name="DS Lab", code="CS251", type="practical",
                     session_length_hours=2, sessions_per_week=1, required_lab_type="COMPUTING")
    db.add_all([cs201, cs251])
    db.flush()

    fac_a = Faculty(name="Dr. A", faculty_code="FAC01", department="CSE")
    fac_a.subjects = [cs201]
    fac_l = Faculty(name="Dr. L", faculty_code="FAC03", department="CSE")
    fac_l.subjects = [cs251]
    db.add_all([fac_a, fac_l])

    a101 = Room(room_number="A101", block="A", floor="1", capacity=70, room_type="theory")
    lab1 = Room(room_number="LAB1", block="C", floor="1", capacity=70,
                room_type="lab", lab_type="COMPUTING")
    faculty_room = Room(room_number="A-F1", block="A", floor="1", capacity=2, room_type="faculty")
    db.add_all([a101, lab1, faculty_room])

    section = Section(academic_context_id=context.id, section_number="CSE-3A", strength=60)
    section.subjects = [cs201, cs251]
    db.add(section)

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)
    db.commit()

    result, run = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message
    assert check_all(db, run.id) == []

    resp = client.post(f"/api/runs/{run.id}/publish")
    assert resp.status_code == 200, resp.text

    return {
        "db": db, "context": context, "run": run, "section": section,
        "cs201": cs201, "cs251": cs251, "fac_a": fac_a, "fac_l": fac_l,
        "a101": a101, "lab1": lab1, "faculty_room": faculty_room,
    }


# ------------------------------------------------------------------- faculty


def test_faculty_detail_has_expected_shape(client, published):
    fac = published["fac_a"]
    resp = client.get(f"/api/faculty/{fac.id}/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Dr. A"
    assert body["faculty_code"] == "FAC01"
    assert {s["code"] for s in body["subjects"]} == {"CS201"}
    assert len(body["assignments"]) == 1
    a = body["assignments"][0]
    assert a["subject_code"] == "CS201"
    assert a["section_number"] == "CSE-3A"
    assert a["periods_per_week"] == 3  # 1hr x 3 sessions/week
    assert body["periods_per_week"] == 3
    assert body["sections_taught"] == 1
    assert body["current_status"]["state"] in {"occupied", "free", "inactive", "unknown"}


def test_faculty_detail_404_for_missing_faculty(client):
    resp = client.get("/api/faculty/999999/detail")
    assert resp.status_code == 404


def test_faculty_current_status_reflects_a_real_class_at_that_moment(published):
    """Unit-level, with an explicit clock - the HTTP endpoint can't be tested
    deterministically against the real wall clock."""
    db = published["db"]
    run = published["run"]
    fac = published["fac_a"]

    from app.models import Assignment
    a = db.query(Assignment).filter(Assignment.run_id == run.id, Assignment.faculty_id == fac.id).first()
    slot = a.timeslot
    at_class_time = dt.datetime.combine(
        _date_for_weekday(slot.day_index), slot.start_time
    ) + dt.timedelta(minutes=1)

    status = entity_detail.faculty_current_status(db, fac, now=at_class_time)
    assert status["state"] == "occupied"
    assert a.section.section_number in status["detail"]

    outside = dt.datetime.combine(_date_for_weekday(0), dt.time(23, 0))
    free_status = entity_detail.faculty_current_status(db, fac, now=outside)
    assert free_status["state"] == "free"


# ------------------------------------------------------------------- subject


def test_subject_detail_has_expected_shape(client, published):
    subj = published["cs201"]
    resp = client.get(f"/api/subjects/{subj.id}/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "CS201"
    assert body["type"] == "theory"
    assert body["periods_per_week"] == 3
    assert {f["faculty_code"] for f in body["eligible_faculty"]} == {"FAC01"}
    assert len(body["sections"]) == 1
    assert body["sections"][0]["section_number"] == "CSE-3A"
    assert len(body["assignments"]) == 1
    assert body["assignments"][0]["faculty_name"] == "Dr. A"


def test_subject_detail_shows_lab_requirement(client, published):
    subj = published["cs251"]
    resp = client.get(f"/api/subjects/{subj.id}/detail")
    body = resp.json()
    assert body["required_lab_type"] == "COMPUTING"
    assert body["type"] == "practical"


# ------------------------------------------------------------------- section


def test_section_detail_has_expected_shape(client, published):
    section = published["section"]
    resp = client.get(f"/api/sections/{section.id}/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["section_number"] == "CSE-3A"
    assert body["academic_context"]["id"] == published["context"].id
    assert {s["code"] for s in body["subjects"]} == {"CS201", "CS251"}
    assert len(body["assignments"]) == 2
    room_numbers = {r["room_number"] for r in body["rooms_used"]}
    assert room_numbers == {"A101", "LAB1"}


# ---------------------------------------------------------------------- room


def test_room_detail_has_expected_shape(client, published):
    room = published["a101"]
    resp = client.get(f"/api/rooms/{room.id}/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["room_number"] == "A101"
    assert body["is_active"] is True
    assert body["utilization"]["occupied_periods"] >= 1
    assert 0 <= body["utilization"]["percent"] <= 100
    assert body["current_status"]["state"] in {"occupied", "free", "inactive"}


def test_faculty_room_never_shows_as_used_by_the_solver(client, published):
    """A faculty room takes no classes, so its utilization must be zero -
    proof the exclusion holds all the way through to the detail page."""
    room = published["faculty_room"]
    resp = client.get(f"/api/rooms/{room.id}/detail")
    body = resp.json()
    assert body["utilization"]["occupied_periods"] == 0


def test_inactive_room_reports_inactive_status(client, published, db_session):
    room = published["a101"]
    room.is_active = False
    db_session.commit()

    status = entity_detail.room_current_status(
        db_session, room, now=dt.datetime.combine(_date_for_weekday(0), dt.time(10, 0))
    )
    assert status["state"] == "inactive"


def test_room_occupied_now_reports_who_is_using_it(published):
    db = published["db"]
    run = published["run"]
    room = published["a101"]

    from app.models import Assignment
    a = db.query(Assignment).filter(Assignment.run_id == run.id, Assignment.room_id == room.id).first()
    slot = a.timeslot
    at_class_time = dt.datetime.combine(
        _date_for_weekday(slot.day_index), slot.start_time
    ) + dt.timedelta(minutes=1)

    status = entity_detail.room_current_status(db, room, now=at_class_time)
    assert status["state"] == "occupied"
    assert a.subject.code in status["detail"]


def test_room_detail_404_for_missing_room(client):
    resp = client.get("/api/rooms/999999/detail")
    assert resp.status_code == 404
