"""Phase 13 PART 6/7: the demo workflow, end to end.

This is the sequence someone will actually be shown, driven through the real
HTTP API in the real order:

    login -> select context -> validate -> generate -> view all four timetables
    -> find a free room -> preview a move -> apply it -> lock -> regenerate
    -> verify the lock held -> publish -> export -> ask the assistant

If any step here breaks, the demo breaks in front of the teacher. That is the
point of running it as a test rather than by hand once.

The dataset under test is the **demo** dataset - invented sample data, not real college
data. These tests prove the application works end to end on it; they say
nothing about whether it matches a real college timetable, which cannot be known
until real data exists.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import jobs
from app.auth import hash_password
from app.database import Base, get_db
from app.main import app as fastapi_app
from app.models import (
    Assignment,
    Room,
    SectionSubjectAssignment,
    TimetableRun,
    User,
)
from app.seed_demo import reset_demo, seed_demo
from app.seed_demo import status as demo_status


@pytest.fixture
def demo(monkeypatch):
    """The demo dataset behind a real TestClient, on one shared engine."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(jobs, "session_factory", Session)

    db = Session()
    db.add(User(username="demo-admin", hashed_password=hash_password("pw12345678"),
                role="admin", is_active=True))
    db.commit()
    seed_demo(db, quiet=True)

    def _override():
        yield db

    fastapi_app.dependency_overrides[get_db] = _override
    client = TestClient(fastapi_app)
    r = client.post("/api/auth/login",
                    json={"username": "demo-admin", "password": "pw12345678"})
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    yield {"db": db, "client": client, "context_id": demo_status(db)["academic_context_id"]}

    fastapi_app.dependency_overrides.pop(get_db, None)
    for run_id in list(jobs._threads):
        jobs.wait(run_id, timeout=180)
    db.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _wait(db, run_id, timeout=180.0):
    import time

    jobs.wait(run_id, timeout=timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        db.expire_all()
        run = db.get(TimetableRun, run_id)
        if run and run.status != "RUNNING":
            return run
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish")


# ===========================================================================
# The dataset itself
# ===========================================================================


def test_the_demo_dataset_is_labelled_as_demo_everywhere_it_shows(demo):
    """Nobody should be able to mistake this for real college data."""
    contexts = demo["client"].get("/api/academic-contexts").json()
    ctx = next(c for c in contexts if c["id"] == demo["context_id"])
    assert "DEMO" in ctx["program"].upper()
    assert "DEMO" in ctx["department"].upper()
    assert "DEMO" in ctx["label"].upper()


def test_the_demo_dataset_has_the_shape_the_demo_needs(demo):
    client = demo["client"]
    rooms = client.get("/api/rooms").json()
    faculty = client.get("/api/faculty").json()
    subjects = client.get("/api/subjects").json()
    sections = client.get(f"/api/sections?academic_context_id={demo['context_id']}").json()

    assert len(sections) == 6
    assert len(faculty) >= 10
    assert len(subjects) >= 10
    assert len(rooms) >= 10

    # Varied strengths, so capacity actually constrains something.
    strengths = {s["strength"] for s in sections}
    assert len(strengths) >= 4
    assert max(strengths) - min(strengths) >= 15

    # Faculty rooms exist and must never be schedulable.
    assert any(r["room_type"] == "faculty" for r in rooms)
    # Multiple lab types, one of them scarce.
    lab_types = [r["lab_type"] for r in rooms if r["room_type"] == "lab"]
    assert len(set(lab_types)) >= 3
    assert lab_types.count("ELECTRONICS") == 1

    # A multi-period session exists.
    assert any(s["session_length_hours"] >= 2 for s in subjects)
    assert any(s["session_length_hours"] == 3 for s in subjects)

    # Availability restrictions are present in the data, not merely supported
    # by the schema - checked directly, since that is what the demo is for.
    from app.models import FacultyUnavailability, RoomUnavailability

    db = demo["db"]
    assert db.query(FacultyUnavailability).count() >= 5
    assert db.query(RoomUnavailability).count() >= 1


def test_seeding_twice_changes_nothing_the_second_time(demo):
    """Idempotent, so a demo can be refreshed without duplicating anything."""
    db = demo["db"]
    before = (
        db.query(Room).count(),
        db.query(Assignment).count(),
        demo_status(db)["sections"],
    )
    seed_demo(db, quiet=True)
    db.expire_all()
    after = (
        db.query(Room).count(),
        db.query(Assignment).count(),
        demo_status(db)["sections"],
    )
    assert before == after


# ===========================================================================
# The full workflow (PART 6)
# ===========================================================================


def test_the_complete_demo_workflow(demo):
    client, db, ctx_id = demo["client"], demo["db"], demo["context_id"]

    # --- validate
    report = client.get(f"/api/validate?academic_context_id={ctx_id}").json()
    assert report["ready"] is True, [
        c for c in report["checks"] if not c["ok"]
    ]
    assert report["blocker_count"] == 0

    # --- generate
    started = client.post("/api/generate", json={"academic_context_id": ctx_id})
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    run = _wait(db, run_id)
    assert run.status in ("OPTIMAL", "FEASIBLE"), run.message

    rows = db.query(Assignment).filter(Assignment.run_id == run_id).all()
    assert rows
    # A faculty office must never be scheduled into.
    faculty_room_ids = {
        r.id for r in db.query(Room).filter(Room.room_type == "faculty")
    }
    assert not [a for a in rows if a.room_id in faculty_room_ids]

    # --- the four views
    sample = rows[0]
    for url in (
        f"/api/timetable/section/{sample.section_id}?run_id={run_id}",
        f"/api/timetable/faculty/{sample.faculty_id}?run_id={run_id}",
        f"/api/timetable/room/{sample.room_id}?run_id={run_id}",
        f"/api/timetable/master?run_id={run_id}",
    ):
        resp = client.get(url)
        assert resp.status_code == 200, f"{url}: {resp.text}"

    master = client.get(f"/api/timetable/master?run_id={run_id}").json()
    assert master["total"] == len(rows)

    # --- find a free room
    from app.models import TimeSlot

    monday_p2 = db.query(TimeSlot).filter(
        TimeSlot.day == "Monday", TimeSlot.period_index == 1
    ).one()
    free = client.get(
        f"/api/availability/rooms?timeslot_id={monday_p2.id}"
        f"&run_id={run_id}&capacity_min=60"
    )
    assert free.status_code == 200, free.text
    offered = free.json()
    # Never a faculty office, whatever the filters say.
    assert all(r["room_type"] != "faculty" for r in offered)
    assert all(r["capacity"] >= 60 for r in offered)

    # --- preview a move, then apply it
    target = _find_valid_move(client, db, run_id, rows)
    assert target is not None, "the demo dataset should allow at least one valid move"
    assignment, slot_id, slot_label = target

    preview = client.post(f"/api/assignments/{assignment.id}/move/preview",
                          json={"target_timeslot_id": slot_id})
    assert preview.status_code == 200 and preview.json()["ok"] is True

    before_room, before_faculty = assignment.room_id, assignment.faculty_id
    applied = client.post(f"/api/assignments/{assignment.id}/move",
                          json={"target_timeslot_id": slot_id})
    assert applied.status_code == 200, applied.text
    assert applied.json()["ok"] is True

    db.expire_all()
    moved = db.get(Assignment, assignment.id)
    assert moved.timeslot_id == slot_id
    # A move changes time only.
    assert moved.room_id == before_room and moved.faculty_id == before_faculty

    # --- lock it
    lock = client.post(
        f"/api/academic-contexts/{ctx_id}/assignments/lock",
        json={"section_id": moved.section_id, "subject_id": moved.subject_id},
    )
    assert lock.status_code == 200, lock.text
    db.expire_all()
    locked_row = db.query(SectionSubjectAssignment).filter(
        SectionSubjectAssignment.section_id == moved.section_id,
        SectionSubjectAssignment.subject_id == moved.subject_id,
    ).one()
    assert locked_row.locked is True
    locked_faculty, locked_room = locked_row.faculty_id, locked_row.room_id

    # --- regenerate, and verify the lock survived
    again = client.post("/api/generate", json={"academic_context_id": ctx_id})
    assert again.status_code == 202, again.text
    run2_id = again.json()["id"]
    run2 = _wait(db, run2_id)
    assert run2.status in ("OPTIMAL", "FEASIBLE"), run2.message
    assert run2_id != run_id  # a new version, never an overwrite

    db.expire_all()
    preserved = db.query(Assignment).filter(
        Assignment.run_id == run2_id,
        Assignment.section_id == moved.section_id,
        Assignment.subject_id == moved.subject_id,
    ).all()
    assert preserved
    assert all(
        a.faculty_id == locked_faculty and a.room_id == locked_room
        for a in preserved
    )

    # --- publish
    published = client.post(f"/api/runs/{run2_id}/publish")
    assert published.status_code == 200, published.text
    db.expire_all()
    assert db.get(TimetableRun, run2_id).publish_status == "PUBLISHED"

    # --- export
    section_id = moved.section_id
    for url, magic in (
        (f"/api/export/section/{section_id}.pdf?run_id={run2_id}", b"%PDF"),
        (f"/api/export/master.csv?run_id={run2_id}", b""),
        (f"/api/export/master.xlsx?run_id={run2_id}", b"PK"),
        (f"/api/export/master.pdf?run_id={run2_id}", b"%PDF"),
    ):
        resp = client.get(url)
        assert resp.status_code == 200, f"{url}: {resp.status_code}"
        assert len(resp.content) > 100, url
        if magic:
            assert resp.content.startswith(magic), url


def _find_valid_move(client, db, run_id, rows):
    """The first (assignment, slot) the real validator accepts."""
    from app import manual_edit
    from app.models import TimeSlot

    slots = db.query(TimeSlot).order_by(
        TimeSlot.day_index, TimeSlot.period_index
    ).all()
    for assignment in rows[:12]:
        for slot in slots:
            if slot.is_lunch or slot.id == assignment.timeslot_id:
                continue
            try:
                if manual_edit.preview_move(db, assignment.id, slot.id).ok:
                    return assignment, slot.id, f"{slot.day} P{slot.period_index + 1}"
            except manual_edit.ManualEditError:
                continue
    return None


# ===========================================================================
# Demo reset safety (PART 16)
# ===========================================================================


def test_reset_removes_the_demo_and_reports_what_it_did(demo):
    db = demo["db"]
    assert demo_status(db)["present"] is True
    counts = reset_demo(db, quiet=True)
    assert counts["contexts"] == 1
    assert counts["sections"] == 6
    db.expire_all()
    assert demo_status(db)["present"] is False


def test_reset_keeps_a_shared_room_that_another_context_is_using(demo):
    """The property that makes the reset safe to expose as a button: it must
    not remove a record real data depends on."""
    from app.models import AcademicContext, Section

    db = demo["db"]
    # A second, non-demo context that reuses one of the demo rooms via an
    # assignment - exactly the collision a naive reset would destroy.
    other = AcademicContext(academic_year="2026-27", semester=5,
                            program="B.Tech ECE", department="School of Electronics")
    db.add(other)
    db.flush()
    section = Section(academic_context_id=other.id, section_number="E2501", strength=40)
    db.add(section)
    db.commit()

    room = db.query(Room).filter(Room.room_number == "34-101").one()
    subject = db.query(Assignment).first()  # may be None; use the catalog instead
    from app.models import Subject, TimeSlot, Faculty

    subj = db.query(Subject).filter(Subject.code == "CSE301").one()
    fac = db.query(Faculty).filter(Faculty.faculty_code == "T-01").one()
    slot = db.query(TimeSlot).first()
    run = TimetableRun(academic_context_id=other.id, version=1,
                       status="OPTIMAL", publish_status="DRAFT")
    db.add(run)
    db.flush()
    db.add(Assignment(run_id=run.id, section_id=section.id, subject_id=subj.id,
                      faculty_id=fac.id, room_id=room.id, timeslot_id=slot.id,
                      block_id=1))
    db.commit()

    counts = reset_demo(db, quiet=True)
    db.expire_all()

    # The demo context is gone...
    assert demo_status(db)["present"] is False
    # ...but the room, subject and faculty the other context uses survive.
    assert db.query(Room).filter(Room.room_number == "34-101").first() is not None
    assert db.query(Subject).filter(Subject.code == "CSE301").first() is not None
    assert db.query(Faculty).filter(Faculty.faculty_code == "T-01").first() is not None
    assert counts["kept_in_use"] >= 3
    # And the other context's own data is untouched.
    assert db.query(Section).filter(Section.section_number == "E2501").first() is not None


def test_the_reset_endpoint_requires_explicit_confirmation(demo):
    client = demo["client"]
    unconfirmed = client.post("/api/demo/reset", json={"confirm": False})
    assert unconfirmed.status_code == 400
    assert demo_status(demo["db"])["present"] is True

    confirmed = client.post("/api/demo/reset", json={"confirm": True})
    assert confirmed.status_code == 200, confirmed.text
    demo["db"].expire_all()
    assert demo_status(demo["db"])["present"] is False


def test_demo_endpoints_are_admin_only(demo, db_session):
    from conftest import login_as

    client = demo["client"]
    assert client.get("/api/demo/status").status_code == 200

    for role in ("viewer", "scheduler", "faculty"):
        db = demo["db"]
        db.add(User(username=f"demo-{role}",
                    hashed_password=hash_password("pw12345678"),
                    role=role, is_active=True))
        db.commit()
        r = client.post("/api/auth/login",
                        json={"username": f"demo-{role}", "password": "pw12345678"})
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/demo/status", headers=headers).status_code == 403
        assert client.post("/api/demo/seed", headers=headers).status_code == 403
        assert client.post("/api/demo/reset", json={"confirm": True},
                           headers=headers).status_code == 403
    # And the demo survived every one of those refusals.
    demo["db"].expire_all()
    assert demo_status(demo["db"])["present"] is True


def test_demo_management_is_refused_when_disabled(demo, monkeypatch):
    """The production guard: with demo features off, nothing here acts."""
    from app.config import settings as app_settings

    monkeypatch.setattr(type(app_settings), "demo_features_enabled",
                        property(lambda self: False))
    client = demo["client"]

    assert client.post("/api/demo/seed").status_code == 403
    assert client.post("/api/demo/reset", json={"confirm": True}).status_code == 403
    # Status still readable, so an admin can see demo data is present.
    status_body = client.get("/api/demo/status").json()
    assert status_body["enabled"] is False
    assert status_body["present"] is True

    demo["db"].expire_all()
    assert demo_status(demo["db"])["present"] is True
