"""Comparing two timetable versions (app.versioning.diff_runs).

No dedicated coverage existed for this before - it was only reached
incidentally through the AI ``compare_versions`` tool and the REST diff
endpoint. These tests exercise it directly, including a mixed subject: the
diff's unit of comparison is (section, subject, session_type), because a
mixed subject's lecture and its practical are independent obligations that
can each move, or change room/faculty, on their own. Grouping by
(section, subject) alone (the pre-fix behaviour) blended a mixed subject's
two rows into one arbitrary "before"/"after" pair and could report the wrong
change, or miss one entirely.
"""
from __future__ import annotations

from datetime import time

import pytest

from app import versioning
from app.models import Assignment, Faculty, Room, Section, Subject, TimeSlot, TimetableRun


def _slots(db):
    slots = [
        TimeSlot(day="Monday", day_index=0, period_index=pi,
                  start_time=time(9 + pi, 0), end_time=time(10 + pi, 0), is_lunch=False)
        for pi in range(4)
    ]
    db.add_all(slots)
    db.commit()
    return slots


@pytest.fixture
def mixed_runs(db_session, context):
    """Two versions of the same mixed subject's timetable: the lecture stays
    put, the practical changes room and faculty between the two runs."""
    db = db_session
    slots = _slots(db)

    subject = Subject(
        name="Python", code="CAP460", type="mixed",
        session_length_hours=1, sessions_per_week=3,
        lecture_sessions_per_week=1, lecture_session_length=1,
        practical_sessions_per_week=1, practical_session_length=2,
    )
    section = Section(academic_context_id=context.id, section_number="D2401", strength=60)
    fac_a = Faculty(name="Dr A", faculty_code="FA")
    fac_b = Faculty(name="Dr B", faculty_code="FB")
    theory = Room(room_number="T1", block="A", floor="1", capacity=70, room_type="theory")
    lab1 = Room(room_number="L1", block="A", floor="1", capacity=70, room_type="lab")
    lab2 = Room(room_number="L2", block="A", floor="1", capacity=70, room_type="lab")
    db.add_all([subject, section, fac_a, fac_b, theory, lab1, lab2])
    db.commit()

    run1 = TimetableRun(academic_context_id=context.id, version=1, status="OPTIMAL", publish_status="DRAFT")
    db.add(run1)
    db.commit()
    db.add_all([
        Assignment(run_id=run1.id, section_id=section.id, subject_id=subject.id,
                   faculty_id=fac_a.id, room_id=theory.id, timeslot_id=slots[0].id,
                   block_id=1, session_type="L"),
        Assignment(run_id=run1.id, section_id=section.id, subject_id=subject.id,
                   faculty_id=fac_b.id, room_id=lab1.id, timeslot_id=slots[1].id,
                   block_id=2, session_type="P"),
        Assignment(run_id=run1.id, section_id=section.id, subject_id=subject.id,
                   faculty_id=fac_b.id, room_id=lab1.id, timeslot_id=slots[2].id,
                   block_id=2, session_type="P"),
    ])
    db.commit()

    run2 = TimetableRun(academic_context_id=context.id, version=2, status="OPTIMAL", publish_status="DRAFT")
    db.add(run2)
    db.commit()
    db.add_all([
        # Lecture: unchanged (same room, faculty, slot).
        Assignment(run_id=run2.id, section_id=section.id, subject_id=subject.id,
                   faculty_id=fac_a.id, room_id=theory.id, timeslot_id=slots[0].id,
                   block_id=1, session_type="L"),
        # Practical: moved to lab2, and to fac_a - a real change.
        Assignment(run_id=run2.id, section_id=section.id, subject_id=subject.id,
                   faculty_id=fac_a.id, room_id=lab2.id, timeslot_id=slots[3].id,
                   block_id=2, session_type="P"),
    ])
    db.commit()

    return {"db": db, "run1": run1, "run2": run2, "section": section, "subject": subject}


def test_an_unchanged_mixed_lecture_produces_no_diff_row(mixed_runs):
    db, run1, run2 = mixed_runs["db"], mixed_runs["run1"], mixed_runs["run2"]
    diff = versioning.diff_runs(db, run1.id, run2.id)
    lecture_rows = [r for r in diff.rows if "(lecture)" in r.subject_code]
    assert lecture_rows == [], (
        "the lecture did not change between the two runs and must not appear in the diff"
    )


def test_a_changed_mixed_practical_is_reported_against_its_own_component(mixed_runs):
    """Pre-fix, grouping by (section, subject) put both the lecture's and the
    practical's rows under one key, so `b[0]`/`a[0]` could be either
    component depending on query order - the room/faculty change reported
    could be wrong, or the real change could be missed."""
    db, run1, run2 = mixed_runs["db"], mixed_runs["run1"], mixed_runs["run2"]
    diff = versioning.diff_runs(db, run1.id, run2.id)

    practical_changes = {r.change for r in diff.rows if "(practical)" in r.subject_code}
    assert "faculty_changed" in practical_changes
    assert "room_changed" in practical_changes
    assert "moved" in practical_changes

    faculty_row = next(r for r in diff.rows if r.change == "faculty_changed")
    assert faculty_row.subject_code == "CAP460 (practical)"
    assert "Dr B" in faculty_row.detail and "Dr A" in faculty_row.detail


def test_added_and_removed_pairs_are_labelled_by_component(db_session, context):
    """A component present in only one run (e.g. dropped after a curriculum
    edit) must be reported as added/removed for its own component, not
    silently merged with the other component of the same subject."""
    db = db_session
    slots = _slots(db)
    subject = Subject(
        name="Python", code="CAP460", type="mixed",
        session_length_hours=1, sessions_per_week=3,
        lecture_sessions_per_week=1, lecture_session_length=1,
        practical_sessions_per_week=1, practical_session_length=2,
    )
    section = Section(academic_context_id=context.id, section_number="D2401", strength=60)
    fac = Faculty(name="Dr A", faculty_code="FA")
    theory = Room(room_number="T1", block="A", floor="1", capacity=70, room_type="theory")
    lab = Room(room_number="L1", block="A", floor="1", capacity=70, room_type="lab")
    db.add_all([subject, section, fac, theory, lab])
    db.commit()

    run1 = TimetableRun(academic_context_id=context.id, version=1, status="OPTIMAL", publish_status="DRAFT")
    db.add(run1)
    db.commit()
    db.add(Assignment(run_id=run1.id, section_id=section.id, subject_id=subject.id,
                       faculty_id=fac.id, room_id=theory.id, timeslot_id=slots[0].id,
                       block_id=1, session_type="L"))
    db.commit()

    run2 = TimetableRun(academic_context_id=context.id, version=2, status="OPTIMAL", publish_status="DRAFT")
    db.add(run2)
    db.commit()
    db.add_all([
        Assignment(run_id=run2.id, section_id=section.id, subject_id=subject.id,
                   faculty_id=fac.id, room_id=theory.id, timeslot_id=slots[0].id,
                   block_id=1, session_type="L"),
        Assignment(run_id=run2.id, section_id=section.id, subject_id=subject.id,
                   faculty_id=fac.id, room_id=lab.id, timeslot_id=slots[1].id,
                   block_id=2, session_type="P"),
    ])
    db.commit()

    diff = versioning.diff_runs(db, run1.id, run2.id)
    added = [r for r in diff.rows if r.change == "added"]
    assert len(added) == 1
    assert added[0].subject_code == "CAP460 (practical)"
    assert diff.removed == 0
