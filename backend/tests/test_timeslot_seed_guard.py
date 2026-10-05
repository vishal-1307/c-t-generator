"""Re-seeding the time grid must not silently destroy the timetable.

`POST /api/timeslots/seed` deletes every slot before rebuilding. Availability
blocks and generated classes reference a slot with `ondelete=CASCADE`, so that
one call also erased every availability block and every assignment in every
run - with no confirmation and no warning. It reads like a settings change and
behaves like deleting the semester's work.

These tests pin the guard: seeding an empty grid is still frictionless, and
replacing a populated one has to be asked for, in a refusal that states the
actual cost.
"""
from __future__ import annotations

from app.models import (
    Assignment,
    Faculty,
    FacultyUnavailability,
    Room,
    Section,
    Subject,
    TimeSlot,
    TimetableRun,
)


def _seed(client, **payload):
    return client.post("/api/timeslots/seed", json=payload)


def _populate_dependents(db_session, context):
    """A grid with an availability block and a generated class hanging off it."""
    faculty = Faculty(name="Dr Rao", faculty_code="T-001", department="CSE", is_active=True)
    subject = Subject(
        name="Fundamentals of Python", code="CAP460", type="theory",
        session_length_hours=1, sessions_per_week=2, is_active=True,
    )
    room = Room(
        room_number="101", block="36", floor="1", capacity=70,
        room_type="theory", is_active=True,
    )
    section = Section(
        academic_context_id=context.id, section_number="D2401",
        strength=64, is_active=True,
    )
    db_session.add_all([faculty, subject, room, section])
    db_session.commit()

    slot = db_session.query(TimeSlot).order_by(TimeSlot.id).first()
    run = TimetableRun(
        academic_context_id=context.id, version=1,
        status="OPTIMAL", publish_status="DRAFT",
    )
    db_session.add(run)
    db_session.commit()

    db_session.add_all([
        FacultyUnavailability(faculty_id=faculty.id, timeslot_id=slot.id, reason="research day"),
        Assignment(
            run_id=run.id, section_id=section.id, subject_id=subject.id,
            faculty_id=faculty.id, room_id=room.id, timeslot_id=slot.id, block_id=1,
        ),
    ])
    db_session.commit()
    return run


# ---------------------------------------------------------------- empty grid


def test_seeding_an_empty_grid_needs_no_confirmation(client):
    """First-time setup should not require a ceremony - there is nothing to
    lose, and this is the path every fresh deployment takes."""
    resp = _seed(client)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 45


# ------------------------------------------------------------ populated grid


def test_replacing_a_populated_grid_is_refused_without_confirmation(client):
    _seed(client)
    resp = _seed(client)
    assert resp.status_code == 409, resp.text
    assert "confirm=true" in resp.json()["detail"]


def test_the_refusal_states_what_would_be_destroyed(client, db_session, context):
    _seed(client)
    _populate_dependents(db_session, context)

    resp = _seed(client)
    assert resp.status_code == 409
    body = resp.json()

    # The human sentence names the real cost, not a generic warning.
    assert "45 time slots" in body["detail"]
    assert "1 availability blocks" in body["detail"]
    assert "1 scheduled classes" in body["detail"]

    impact = body["impact"]
    assert impact["timeslots"] == 45
    assert impact["faculty_unavailability"] == 1
    assert impact["assignments"] == 1
    assert impact["runs_affected"] == 1


def test_a_published_version_at_risk_is_called_out_separately(client, db_session, context):
    """Losing a draft is bad; losing what people are timetabled against is
    worse, and the refusal should say so."""
    _seed(client)
    run = _populate_dependents(db_session, context)
    run.publish_status = "PUBLISHED"
    db_session.commit()

    body = _seed(client).json()
    assert body["impact"]["published_runs_affected"] == 1
    assert "PUBLISHED" in body["detail"]


def test_nothing_is_deleted_when_the_replacement_is_refused(client, db_session, context):
    """The counts are read before any deletion, so a refusal must leave every
    row it counted exactly where it was."""
    _seed(client)
    _populate_dependents(db_session, context)

    assert _seed(client).status_code == 409

    assert db_session.query(TimeSlot).count() == 45
    assert db_session.query(FacultyUnavailability).count() == 1
    assert db_session.query(Assignment).count() == 1


def test_confirmed_replacement_proceeds(client):
    _seed(client)
    resp = _seed(client, confirm=True, days=["Monday", "Tuesday"], periods=4)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 8


def test_confirmed_replacement_still_cascades_as_before(client, db_session, context):
    """The guard adds a question, not a new safety net. Once confirmed, the
    cascade is the same one that always happened - pinned here so nobody later
    mistakes the guard for protection the database does not provide."""
    _seed(client)
    _populate_dependents(db_session, context)

    assert _seed(client, confirm=True).status_code == 200

    db_session.expire_all()
    assert db_session.query(FacultyUnavailability).count() == 0
    assert db_session.query(Assignment).count() == 0
    # The run row itself survives; only its classes are gone.
    assert db_session.query(TimetableRun).count() == 1


def test_the_guard_is_admin_only_like_the_endpoint_it_protects(anon_client):
    """An unauthenticated caller must not be able to read the impact counts -
    they describe how much data exists."""
    assert anon_client.post("/api/timeslots/seed", json={}).status_code in (401, 403)
