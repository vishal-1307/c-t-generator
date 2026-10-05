"""Phase 10 PART 22: no orphan relationships after import/update.

Every FK in this schema is either NOT NULL (the DB itself refuses an
orphan) or explicitly ON DELETE CASCADE/SET NULL (models.py) - so orphans
would only appear from an application bug that writes an FK to a row that
was never actually created. This test proves the bulk-import pipeline never
does that, across the full canonical import chain.
"""
from __future__ import annotations

import io

from sqlalchemy import text

from app.models import (
    Assignment,
    Faculty,
    FacultySubject,
    Room,
    Section,
    SectionSubject,
    SectionSubjectAssignment,
    Subject,
)


def _import(client, entity: str, csv_text: str):
    resp = client.post(
        f"/api/bulk-import/{entity}/apply",
        files={"file": (f"{entity}.csv", io.BytesIO(csv_text.encode()), "text/csv")},
        data={"mode": "add_update"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is True, resp.text


def _seed_full_chain(client, context):
    _import(client, "rooms", "block,floor,room_number,capacity,room_type\nA,1,A101,70,theory\n")
    _import(client, "faculty", "faculty_id,name\nT-1,Dr. A\n")
    _import(client, "subjects", "subject_code,subject_name,type\nCS201,Data Structures,theory\n")
    _import(client, "faculty_subjects", "faculty_id,subject_code\nT-1,CS201\n")
    _import(client, "sections", (
        f"section_number,academic_year,semester,program,department,strength\n"
        f"S-A,{context.academic_year},{context.semester},{context.program},{context.department},60\n"
    ))
    _import(client, "section_subjects", (
        f"academic_year,semester,program,department,section_number,subject_code\n"
        f"{context.academic_year},{context.semester},{context.program},{context.department},S-A,CS201\n"
    ))


def test_faculty_subject_mapping_has_no_orphans(client, db_session, context):
    _seed_full_chain(client, context)
    faculty_ids = {f.id for f in db_session.query(Faculty).all()}
    subject_ids = {s.id for s in db_session.query(Subject).all()}
    for row in db_session.query(FacultySubject).all():
        assert row.faculty_id in faculty_ids
        assert row.subject_id in subject_ids


def test_section_subject_mapping_has_no_orphans(client, db_session, context):
    _seed_full_chain(client, context)
    section_ids = {s.id for s in db_session.query(Section).all()}
    subject_ids = {s.id for s in db_session.query(Subject).all()}
    for row in db_session.query(SectionSubject).all():
        assert row.section_id in section_ids
        assert row.subject_id in subject_ids


def test_faculty_availability_import_never_orphans(client, db_session, context):
    from app.grid import build_grid
    from app.models import FacultyUnavailability

    db_session.add_all(build_grid())
    db_session.commit()
    _import(client, "faculty", "faculty_id,name\nT-1,Dr. A\n")
    _import(client, "availability", "entity_type,entity_identifier,day,slot\nfaculty,T-1,Monday,1\n")

    faculty_ids = {f.id for f in db_session.query(Faculty).all()}
    for row in db_session.query(FacultyUnavailability).all():
        assert row.faculty_id in faculty_ids
        assert row.timeslot_id is not None


def test_deleting_a_run_does_not_orphan_change_history_or_leave_dangling_assignments(
    client, db_session, context
):
    """ChangeHistory.run_id is ON DELETE CASCADE (models.py) - deleting an
    unpublished run must take its own history rows and Assignment rows with
    it, not leave them pointing at nothing."""
    from app.grid import build_grid
    from app.models import ChangeHistory, TimetableRun
    from app.solver.run import generate

    from constraint_checker import check_all

    cs201 = Subject(name="Data Structures", code="CS201", type="theory",
                     session_length_hours=1, sessions_per_week=1)
    fac = Faculty(name="Dr. A", faculty_code="FAC01")
    fac.subjects = [cs201]
    room = Room(room_number="A101", block="A", floor="1", capacity=70, room_type="theory")
    section = Section(academic_context_id=context.id, section_number="S-A", strength=60)
    section.subjects = [cs201]
    db_session.add_all([cs201, fac, room, section])
    db_session.add_all(build_grid())
    db_session.commit()

    result, run = generate(db_session, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert check_all(db_session, run.id) == []

    a = db_session.query(Assignment).filter(Assignment.run_id == run.id).first()
    move_resp = client.post(f"/api/assignments/{a.id}/move/preview", json={"target_timeslot_id": a.timeslot_id})
    assert move_resp.status_code == 200  # sanity: endpoint reachable for this run

    run_id = run.id
    db_session.delete(run)
    db_session.commit()

    assert db_session.query(Assignment).filter(Assignment.run_id == run_id).count() == 0
    assert db_session.query(ChangeHistory).filter(ChangeHistory.run_id == run_id).count() == 0
    assert db_session.get(TimetableRun, run_id) is None
    # the faculty/subject/room/section themselves are untouched
    assert db_session.query(Faculty).count() == 1
    assert db_session.query(Subject).count() == 1
    assert db_session.query(Room).count() == 1
    assert db_session.query(Section).count() == 1


def test_deactivating_a_faculty_member_does_not_orphan_their_past_assignments(
    client, db_session, context
):
    """A deactivated faculty member stops being offered to future solves
    (solver/data.py filters is_active), but their historical
    SectionSubjectAssignment row must still resolve - deactivation is not
    deletion."""
    from app.grid import build_grid
    from app.solver.run import generate

    from constraint_checker import check_all

    cs201 = Subject(name="Data Structures", code="CS201", type="theory",
                     session_length_hours=1, sessions_per_week=1)
    fac = Faculty(name="Dr. A", faculty_code="FAC01")
    fac.subjects = [cs201]
    room = Room(room_number="A101", block="A", floor="1", capacity=70, room_type="theory")
    section = Section(academic_context_id=context.id, section_number="S-A", strength=60)
    section.subjects = [cs201]
    db_session.add_all([cs201, fac, room, section])
    db_session.add_all(build_grid())
    db_session.commit()

    result, run = generate(db_session, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert check_all(db_session, run.id) == []

    fac.is_active = False
    db_session.commit()

    ssa = (
        db_session.query(SectionSubjectAssignment)
        .filter(SectionSubjectAssignment.section_id == section.id, SectionSubjectAssignment.subject_id == cs201.id)
        .one()
    )
    assert ssa.faculty_id == fac.id  # the historical link is intact, not nulled out

    # The room/faculty view for this deactivated faculty's own detail page
    # still resolves cleanly (no crash on a "deactivated but referenced" row).
    resp = client.get(f"/api/faculty/{fac.id}/detail")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
