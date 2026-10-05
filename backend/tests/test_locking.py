"""app.locking: set_lock and preview, especially a mixed subject's two
components in different lock states.

No dedicated coverage existed for this before - set_lock/preview were only
exercised indirectly through test_locked_regeneration.py and the AI lifecycle
tests, none of which ever put a subject's two components in different lock
states. That state is routine after this session's manual_edit.py fix (a
move/room/faculty change now locks only the component it touched), and it
exposed a real bug here: `was = any(...) if locked else all(...)` collapsed
two components' lock state into one boolean *before* comparing to the target,
so a genuine per-component change could be reported - and recorded to
history - as "nothing would change" whenever the two components disagreed
going in. Confirmed live: previewing "Unlock" on a subject whose lecture was
locked and practical was not reported `currently_locked: false` and "already
unlocked, nothing would change" - self-contradicting its own `components`
list, which correctly showed the lecture locked.
"""
from __future__ import annotations

import pytest

from app import locking
from app.models import (
    ChangeHistory,
    Faculty,
    Room,
    Section,
    SectionSubjectAssignment,
    Subject,
    TimetableRun,
)


@pytest.fixture
def mixed_pair(db_session, context):
    """A mixed subject's two SectionSubjectAssignment rows, lecture locked,
    practical not - the state a move/room/faculty edit to just the lecture
    leaves behind."""
    db = db_session
    subject = Subject(
        name="Python", code="CAP460", type="mixed",
        session_length_hours=1, sessions_per_week=3,
        lecture_sessions_per_week=1, lecture_session_length=1,
        practical_sessions_per_week=1, practical_session_length=2,
    )
    section = Section(academic_context_id=context.id, section_number="D2401", strength=60)
    faculty = Faculty(name="Dr A", faculty_code="FA")
    theory = Room(room_number="T1", block="A", floor="1", capacity=70, room_type="theory")
    lab = Room(room_number="L1", block="A", floor="1", capacity=70, room_type="lab")
    db.add_all([subject, section, faculty, theory, lab])
    db.commit()

    run = TimetableRun(academic_context_id=context.id, version=1, status="OPTIMAL", publish_status="DRAFT")
    db.add(run)
    db.commit()

    lecture = SectionSubjectAssignment(
        academic_context_id=context.id, section_id=section.id, subject_id=subject.id,
        session_type="L", faculty_id=faculty.id, room_id=theory.id, locked=True,
    )
    practical = SectionSubjectAssignment(
        academic_context_id=context.id, section_id=section.id, subject_id=subject.id,
        session_type="P", faculty_id=faculty.id, room_id=lab.id, locked=False,
    )
    db.add_all([lecture, practical])
    db.commit()

    return {
        "db": db, "context": context, "run": run, "section": section, "subject": subject,
        "lecture": lecture, "practical": practical,
    }


def test_preview_unlock_reports_a_real_change_when_only_the_lecture_is_locked(mixed_pair):
    db, context, section, subject = (
        mixed_pair["db"], mixed_pair["context"], mixed_pair["section"], mixed_pair["subject"],
    )
    result = locking.preview(
        db, context_id=context.id, section_id=section.id, subject_id=subject.id, locked=False,
    )
    assert result.would_change is True, (
        "unlocking must report a real change: the lecture is locked and would become unlocked"
    )
    by_type = {c["session_type"]: c["locked"] for c in result.components}
    assert by_type == {"L": True, "P": False}, "components must reflect the true per-row state"


def test_preview_lock_reports_a_real_change_when_only_the_lecture_is_locked(mixed_pair):
    """Locking a subject that already has one component locked still changes
    something - the still-unlocked practical - so this must not read as a
    no-op either."""
    db, context, section, subject = (
        mixed_pair["db"], mixed_pair["context"], mixed_pair["section"], mixed_pair["subject"],
    )
    result = locking.preview(
        db, context_id=context.id, section_id=section.id, subject_id=subject.id, locked=True,
    )
    assert result.would_change is True
    assert result.currently_locked is False, (
        "not every component is locked yet, so the pair is not fully locked"
    )


def test_preview_reports_no_change_only_when_every_component_already_matches(db_session, context):
    db = db_session
    subject = Subject(name="Data Structures", code="CS201", type="theory",
                       session_length_hours=1, sessions_per_week=3)
    section = Section(academic_context_id=context.id, section_number="D2401", strength=60)
    faculty = Faculty(name="Dr A", faculty_code="FA")
    room = Room(room_number="T1", block="A", floor="1", capacity=70, room_type="theory")
    db.add_all([subject, section, faculty, room])
    db.commit()
    ssa = SectionSubjectAssignment(
        academic_context_id=context.id, section_id=section.id, subject_id=subject.id,
        session_type="L", faculty_id=faculty.id, room_id=room.id, locked=True,
    )
    db.add(ssa)
    db.commit()

    result = locking.preview(
        db, context_id=context.id, section_id=section.id, subject_id=subject.id, locked=True,
    )
    assert result.would_change is False
    assert result.currently_locked is True


def test_set_lock_records_history_when_only_one_component_actually_changes(mixed_pair):
    """The bug this guards: unlocking a mixed pair with one locked/one
    unlocked component wrote no history row at all, because the old collapsed
    `was` computation matched the target by coincidence."""
    db, context, run, section, subject = (
        mixed_pair["db"], mixed_pair["context"], mixed_pair["run"],
        mixed_pair["section"], mixed_pair["subject"],
    )
    locking.set_lock(
        db, context_id=context.id, section_id=section.id, subject_id=subject.id, locked=False,
    )

    db.refresh(mixed_pair["lecture"])
    db.refresh(mixed_pair["practical"])
    assert mixed_pair["lecture"].locked is False, "the previously-locked lecture must be unlocked"
    assert mixed_pair["practical"].locked is False

    history = (
        db.query(ChangeHistory)
        .filter(ChangeHistory.run_id == run.id, ChangeHistory.change_type == "unlock")
        .all()
    )
    assert len(history) == 1, "a real state change (lecture: locked -> unlocked) must be recorded"


def test_set_lock_writes_no_history_for_a_genuine_no_op(mixed_pair):
    db, context, run, section, subject = (
        mixed_pair["db"], mixed_pair["context"], mixed_pair["run"],
        mixed_pair["section"], mixed_pair["subject"],
    )
    # The practical alone is already unlocked; unlocking just it is a no-op.
    locking.set_lock(
        db, context_id=context.id, section_id=section.id, subject_id=subject.id,
        locked=False, session_type="P",
    )
    history = db.query(ChangeHistory).filter(ChangeHistory.run_id == run.id).all()
    assert history == [], "no real change happened, so no audit-trail noise either"
