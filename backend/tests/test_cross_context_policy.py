"""Phase 10 PART 1: cross-context resource conflict policy.

Same (academic_year, semester) -> contexts run concurrently in real time ->
a shared faculty/room double-booking between their published runs is a real
clash and must be reported. Different (academic_year, semester) -> contexts
never overlap in real time -> reusing the same faculty/room across them is
completely normal and must never be flagged.

These are pure data-layer tests against hand-built Assignment rows -
appropriate here because the thing under test is the clash-detection query
itself, not the solver (which is exercised elsewhere, at length, with real
generated data).
"""
from __future__ import annotations

import pytest

from app.cross_context import find_cross_context_clashes, same_period_contexts
from app.grid import build_grid
from app.models import (
    AcademicContext,
    Assignment,
    Faculty,
    Room,
    Section,
    Subject,
    TimetableRun,
)


@pytest.fixture
def shared_pool(db_session):
    """One faculty member and one room shared by every context below - the
    institution-wide catalog every AcademicContext draws from."""
    db = db_session
    subject = Subject(name="Data Structures", code="CS201", type="theory",
                       session_length_hours=1, sessions_per_week=1)
    faculty = Faculty(name="Dr. A", faculty_code="FAC01")
    faculty.subjects = [subject]
    room = Room(room_number="A101", block="A", floor="1", capacity=70, room_type="theory")
    db.add_all([subject, faculty, room])
    db.add_all(build_grid())
    db.commit()
    return {"db": db, "subject": subject, "faculty": faculty, "room": room}


def _publish(db, context, section, subject, faculty, room, slot):
    run = TimetableRun(
        academic_context_id=context.id, version=1, status="OPTIMAL", publish_status="PUBLISHED",
    )
    db.add(run)
    db.flush()
    db.add(
        Assignment(
            run_id=run.id, section_id=section.id, subject_id=subject.id,
            faculty_id=faculty.id, room_id=room.id, timeslot_id=slot.id, block_id=1,
        )
    )
    db.commit()
    return run


def _first_slot(db):
    from app.models import TimeSlot
    return db.query(TimeSlot).order_by(TimeSlot.day_index, TimeSlot.period_index).first()


def test_same_period_contexts_finds_only_matching_year_and_semester(shared_pool):
    db = shared_pool["db"]
    a = AcademicContext(academic_year="2026-27", semester=1, program="BCA", department="CSE")
    b = AcademicContext(academic_year="2026-27", semester=1, program="BTech", department="ECE")
    c = AcademicContext(academic_year="2026-27", semester=3, program="BCA", department="CSE")
    db.add_all([a, b, c])
    db.commit()

    peers = same_period_contexts(db, a.id)
    assert {p.id for p in peers} == {b.id}  # same year+semester, different program
    assert c.id not in {p.id for p in peers}  # different semester - not a peer


def test_same_period_published_runs_sharing_a_room_and_faculty_are_flagged(shared_pool):
    db = shared_pool["db"]
    subject, faculty, room = shared_pool["subject"], shared_pool["faculty"], shared_pool["room"]
    slot = _first_slot(db)

    ctx_a = AcademicContext(academic_year="2026-27", semester=1, program="BCA", department="CSE")
    ctx_b = AcademicContext(academic_year="2026-27", semester=1, program="BTech", department="ECE")
    db.add_all([ctx_a, ctx_b])
    db.commit()

    sec_a = Section(academic_context_id=ctx_a.id, section_number="CSE-3A", strength=60)
    sec_b = Section(academic_context_id=ctx_b.id, section_number="ECE-3A", strength=60)
    db.add_all([sec_a, sec_b])
    db.commit()

    _publish(db, ctx_a, sec_a, subject, faculty, room, slot)
    _publish(db, ctx_b, sec_b, subject, faculty, room, slot)

    clashes = find_cross_context_clashes(db, ctx_a.id)
    kinds = {c.kind for c in clashes}
    assert kinds == {"faculty", "room"}
    faculty_clash = next(c for c in clashes if c.kind == "faculty")
    assert faculty_clash.resource_label == "Dr. A"
    room_clash = next(c for c in clashes if c.kind == "room")
    assert room_clash.resource_label == "A101"
    assert all(c.context_a_id == ctx_a.id and c.context_b_id == ctx_b.id for c in clashes)


def test_different_period_contexts_sharing_a_room_are_never_flagged(shared_pool):
    """The overwhelming common case: two different semesters reusing the
    same physical room is completely normal and must produce zero noise."""
    db = shared_pool["db"]
    subject, faculty, room = shared_pool["subject"], shared_pool["faculty"], shared_pool["room"]
    slot = _first_slot(db)

    ctx_a = AcademicContext(academic_year="2026-27", semester=1, program="BCA", department="CSE")
    ctx_c = AcademicContext(academic_year="2026-27", semester=3, program="BCA", department="CSE")
    db.add_all([ctx_a, ctx_c])
    db.commit()

    sec_a = Section(academic_context_id=ctx_a.id, section_number="CSE-3A", strength=60)
    sec_c = Section(academic_context_id=ctx_c.id, section_number="CSE-5A", strength=60)
    db.add_all([sec_a, sec_c])
    db.commit()

    _publish(db, ctx_a, sec_a, subject, faculty, room, slot)
    _publish(db, ctx_c, sec_c, subject, faculty, room, slot)

    assert find_cross_context_clashes(db, ctx_a.id) == []


def test_draft_runs_are_never_compared_only_published(shared_pool):
    db = shared_pool["db"]
    subject, faculty, room = shared_pool["subject"], shared_pool["faculty"], shared_pool["room"]
    slot = _first_slot(db)

    ctx_a = AcademicContext(academic_year="2026-27", semester=1, program="BCA", department="CSE")
    ctx_b = AcademicContext(academic_year="2026-27", semester=1, program="BTech", department="ECE")
    db.add_all([ctx_a, ctx_b])
    db.commit()

    sec_a = Section(academic_context_id=ctx_a.id, section_number="CSE-3A", strength=60)
    sec_b = Section(academic_context_id=ctx_b.id, section_number="ECE-3A", strength=60)
    db.add_all([sec_a, sec_b])
    db.commit()

    _publish(db, ctx_a, sec_a, subject, faculty, room, slot)
    # ctx_b's run stays DRAFT - a real double-booking against a draft is not
    # yet a real commitment and must not be flagged.
    run_b = TimetableRun(academic_context_id=ctx_b.id, version=1, status="OPTIMAL", publish_status="DRAFT")
    db.add(run_b)
    db.flush()
    db.add(Assignment(run_id=run_b.id, section_id=sec_b.id, subject_id=subject.id,
                       faculty_id=faculty.id, room_id=room.id, timeslot_id=slot.id, block_id=1))
    db.commit()

    assert find_cross_context_clashes(db, ctx_a.id) == []


def test_validate_reports_cross_context_clash_as_a_warning_not_a_blocker(client, db_session):
    db = db_session
    subject = Subject(name="Data Structures", code="CS201", type="theory",
                       session_length_hours=1, sessions_per_week=1)
    faculty = Faculty(name="Dr. A", faculty_code="FAC01")
    faculty.subjects = [subject]
    room = Room(room_number="A101", block="A", floor="1", capacity=70, room_type="theory")
    db.add_all([subject, faculty, room])
    db.add_all(build_grid())
    db.commit()
    slot = _first_slot(db)

    ctx_a = AcademicContext(academic_year="2026-27", semester=1, program="BCA", department="CSE")
    ctx_b = AcademicContext(academic_year="2026-27", semester=1, program="BTech", department="ECE")
    db.add_all([ctx_a, ctx_b])
    db.commit()
    sec_a = Section(academic_context_id=ctx_a.id, section_number="CSE-3A", strength=60)
    sec_b = Section(academic_context_id=ctx_b.id, section_number="ECE-3A", strength=60)
    db.add_all([sec_a, sec_b])
    db.commit()
    _publish(db, ctx_a, sec_a, subject, faculty, room, slot)
    _publish(db, ctx_b, sec_b, subject, faculty, room, slot)

    resp = client.get(f"/api/validate?academic_context_id={ctx_a.id}")
    assert resp.status_code == 200
    body = resp.json()
    check = next(c for c in body["checks"] if c["id"] == "cross_context_resource_clash")
    assert check["ok"] is False
    assert check["severity"] == "warning"
    # A warning must never flip overall readiness off by itself.
    assert body["blocker_count"] == 0 or all(
        c["id"] != "cross_context_resource_clash" for c in body["checks"] if not c["ok"] and c["severity"] == "blocker"
    )
