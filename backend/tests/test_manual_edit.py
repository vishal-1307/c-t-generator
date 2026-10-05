"""Phase 6: manual timetable operations.

Every test here starts from a real, solver-generated timetable (never hand-
built rows) so the fixture's own faculty/room sharing produces genuine
conflicts to detect - the same discipline test_solver_all_sections.py uses,
and for the same reason: a conflict-detection test only proves something if
the conflict is real, not asserted into existence.
"""
from __future__ import annotations

import pytest

from app import manual_edit
from app.grid import build_grid
from app.models import (
    Assignment,
    ChangeHistory,
    Faculty,
    Room,
    Section,
    SectionSubjectAssignment,
    Subject,
    TimeSlot,
)
from app.solver.run import generate

from constraint_checker import check_all


@pytest.fixture
def dataset(db_session, context):
    """Two sections sharing one theory subject with two teachers, and one
    scarce lab - enough real contention that a manual move into the wrong
    slot produces a genuine conflict, not a contrived one."""
    db = db_session

    subjects = {}
    for name, code, type_, length, spw, lab_type in [
        ("Data Structures", "CS201", "theory", 1, 3, None),
        ("Operating Systems", "CS202", "theory", 1, 2, None),
        ("DS Lab", "CS251", "practical", 2, 1, "COMPUTING"),
    ]:
        s = Subject(name=name, code=code, type=type_, session_length_hours=length,
                    sessions_per_week=spw, required_lab_type=lab_type)
        db.add(s)
        subjects[code] = s
    db.flush()

    rooms = {}
    for number, room_type, lab_type, capacity in [
        ("A101", "theory", None, 70), ("A102", "theory", None, 70),
        ("A-SMALL", "theory", None, 10),
        ("LAB1", "lab", "COMPUTING", 70), ("LAB2", "lab", "ELECTRONICS", 70),
        ("A-F1", "faculty", None, 2),
    ]:
        r = Room(room_number=number, block="A", floor="1", capacity=capacity,
                  room_type=room_type, lab_type=lab_type)
        db.add(r)
        rooms[number] = r
    db.flush()

    faculty = {}
    for fname, fcode, teaches in [
        ("Dr. A", "FAC01", ["CS201", "CS202"]),
        ("Dr. B", "FAC02", ["CS201", "CS202"]),
        ("Dr. L", "FAC03", ["CS251"]),
    ]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = [subjects[c] for c in teaches]
        db.add(f)
        faculty[fcode] = f
    db.flush()

    sections = {}
    for number in ("S-A", "S-B"):
        sec = Section(academic_context_id=context.id, section_number=number, strength=60)
        sec.subjects = list(subjects.values())
        db.add(sec)
        sections[number] = sec

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)
    db.commit()

    return {
        "db": db, "context": context, "subjects": subjects, "rooms": rooms,
        "faculty": faculty, "sections": sections,
    }


@pytest.fixture
def solved(dataset):
    """A real generated timetable, hard-constraint-only (fast, deterministic
    enough for these tests - they check individual moves, not objective
    quality)."""
    db = dataset["db"]
    result, run = generate(db, academic_context_id=dataset["context"].id, soft=False, max_seconds=20)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message
    assert check_all(db, run.id) == []
    dataset["run"] = run
    return dataset


def _first_block(db, run_id, section_number=None, subject_code=None):
    q = db.query(Assignment).filter(Assignment.run_id == run_id)
    rows = q.all()
    if section_number:
        rows = [a for a in rows if a.section.section_number == section_number]
    if subject_code:
        rows = [a for a in rows if a.subject.code == subject_code]
    a = rows[0]
    return a


def _free_slot_for(db, run_id, section_id, faculty_id, room_id, length=1):
    """Find a contiguous window this section/faculty/room are all free for,
    to construct a genuinely valid move target."""
    occupied = {
        a.timeslot_id
        for a in db.query(Assignment).filter(Assignment.run_id == run_id).all()
        if a.section_id == section_id or a.faculty_id == faculty_id or a.room_id == room_id
    }
    for day_index in range(5):
        day_slots = (
            db.query(TimeSlot)
            .filter(TimeSlot.day_index == day_index)
            .order_by(TimeSlot.period_index)
            .all()
        )
        for i in range(len(day_slots) - length + 1):
            window = day_slots[i:i + length]
            if any(s.is_lunch for s in window):
                continue
            if any(s.id in occupied for s in window):
                continue
            if any(window[j + 1].period_index != window[j].period_index + 1 for j in range(length - 1)):
                continue
            return window
    return None


# ------------------------------------------------------------------- moves


def test_valid_move_relocates_the_block(solved):
    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS202")  # a single-period subject
    window = _free_slot_for(db, run.id, a.section_id, a.faculty_id, a.room_id, length=1)
    assert window, "fixture should have a free slot"

    outcome = manual_edit.apply_move(db, a.id, window[0].id)
    assert outcome.ok, outcome.issues
    db.refresh(a)
    assert a.timeslot_id == window[0].id
    assert check_all(db, run.id) == []


def test_multi_slot_move_preserves_the_whole_block(solved):
    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS251")  # the 2-hour practical
    block = (
        db.query(Assignment)
        .filter(Assignment.run_id == run.id, Assignment.block_id == a.block_id)
        .all()
    )
    assert len(block) == 2
    window = _free_slot_for(db, run.id, a.section_id, a.faculty_id, a.room_id, length=2)
    assert window, "fixture should have a free 2-period window"

    outcome = manual_edit.apply_move(db, a.id, window[0].id)
    assert outcome.ok, outcome.issues

    moved = (
        db.query(Assignment)
        .filter(Assignment.run_id == run.id, Assignment.block_id == a.block_id)
        .all()
    )
    assert len(moved) == 2, "the whole block must move together, not just one row"
    moved_slots = sorted(r.timeslot.period_index for r in moved)
    assert moved_slots == [window[0].period_index, window[1].period_index]
    assert len({r.timeslot.day_index for r in moved}) == 1
    assert check_all(db, run.id) == []


def test_move_into_occupied_slot_is_rejected_with_section_conflict(solved):
    db, run = solved["db"], solved["run"]
    section_a = solved["sections"]["S-A"]
    rows = [a for a in db.query(Assignment).filter(Assignment.run_id == run.id).all()
            if a.section_id == section_a.id]
    # Two distinct single-period classes of the same section - moving one onto
    # the other's slot must be rejected as a section clash.
    singles = [a for a in rows if a.subject.session_length_hours == 1]
    assert len(singles) >= 2
    a, other = singles[0], next(s for s in singles[1:] if s.timeslot_id != singles[0].timeslot_id)

    result = manual_edit.preview_move(db, a.id, other.timeslot_id)
    assert result.ok is False
    assert any("already has a class" in issue for issue in result.issues)

    outcome = manual_edit.apply_move(db, a.id, other.timeslot_id)
    assert outcome.ok is False
    # Nothing was written.
    db.refresh(a)
    assert a.timeslot_id != other.timeslot_id


def test_move_causing_faculty_conflict_is_rejected(solved):
    """CS201 is taught by two faculty across S-A/S-B; find two blocks taught
    by the SAME faculty for different sections and try to collide them."""
    db, run = solved["db"], solved["run"]
    rows = [a for a in db.query(Assignment).filter(Assignment.run_id == run.id).all()
            if a.subject.code == "CS201"]
    by_faculty = {}
    for a in rows:
        by_faculty.setdefault(a.faculty_id, []).append(a)
    shared = next((v for v in by_faculty.values() if len({r.section_id for r in v}) > 1), None)
    if shared is None:
        pytest.skip("fixture did not produce cross-section faculty sharing this run")

    distinct_slots = {a.timeslot_id: a for a in shared}
    if len(distinct_slots) < 2:
        pytest.skip("no two distinct slots to collide")
    a, target = list(distinct_slots.values())[:2]

    result = manual_edit.preview_move(db, a.id, target.timeslot_id)
    assert result.ok is False
    assert any(
        "already teaching" in issue or "already has a class" in issue for issue in result.issues
    )


def test_move_into_lunch_slot_is_rejected(solved):
    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS202")
    lunch_slot = db.query(TimeSlot).filter(TimeSlot.period_index == 4, TimeSlot.day_index == 0).one()

    result = manual_edit.preview_move(db, a.id, lunch_slot.id)
    assert result.ok is False
    assert any("contiguous" in issue for issue in result.issues)


def test_move_respects_room_unavailability(solved):
    from app.models import RoomUnavailability

    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS202")
    window = _free_slot_for(db, run.id, a.section_id, a.faculty_id, a.room_id, length=1)
    assert window
    db.add(RoomUnavailability(room_id=a.room_id, timeslot_id=window[0].id))
    db.commit()

    result = manual_edit.preview_move(db, a.id, window[0].id)
    assert result.ok is False
    assert any("unavailable" in issue for issue in result.issues)


def test_move_respects_faculty_unavailability(solved):
    from app.models import FacultyUnavailability

    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS202")
    window = _free_slot_for(db, run.id, a.section_id, a.faculty_id, a.room_id, length=1)
    assert window
    db.add(FacultyUnavailability(faculty_id=a.faculty_id, timeslot_id=window[0].id))
    db.commit()

    result = manual_edit.preview_move(db, a.id, window[0].id)
    assert result.ok is False
    assert any("unavailable" in issue for issue in result.issues)


def test_move_respects_section_unavailability(solved):
    from app.models import SectionUnavailability

    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS202")
    window = _free_slot_for(db, run.id, a.section_id, a.faculty_id, a.room_id, length=1)
    assert window
    db.add(SectionUnavailability(section_id=a.section_id, timeslot_id=window[0].id))
    db.commit()

    result = manual_edit.preview_move(db, a.id, window[0].id)
    assert result.ok is False
    assert any("unavailable" in issue for issue in result.issues)


def test_locked_assignment_cannot_be_moved(solved):
    db, run, context = solved["db"], solved["run"], solved["context"]
    a = _first_block(db, run.id, subject_code="CS202")
    ssa = (
        db.query(SectionSubjectAssignment)
        .filter(
            SectionSubjectAssignment.academic_context_id == context.id,
            SectionSubjectAssignment.section_id == a.section_id,
            SectionSubjectAssignment.subject_id == a.subject_id,
        )
        .one()
    )
    ssa.locked = True
    db.commit()

    window = _free_slot_for(db, run.id, a.section_id, a.faculty_id, a.room_id, length=1)
    with pytest.raises(manual_edit.Locked):
        manual_edit.apply_move(db, a.id, window[0].id)


def test_unlock_then_move_succeeds(solved):
    db, run, context = solved["db"], solved["run"], solved["context"]
    a = _first_block(db, run.id, subject_code="CS202")
    ssa = (
        db.query(SectionSubjectAssignment)
        .filter(
            SectionSubjectAssignment.academic_context_id == context.id,
            SectionSubjectAssignment.section_id == a.section_id,
            SectionSubjectAssignment.subject_id == a.subject_id,
        )
        .one()
    )
    ssa.locked = True
    db.commit()

    window = _free_slot_for(db, run.id, a.section_id, a.faculty_id, a.room_id, length=1)
    with pytest.raises(manual_edit.Locked):
        manual_edit.apply_move(db, a.id, window[0].id)

    ssa.locked = False
    db.commit()
    outcome = manual_edit.apply_move(db, a.id, window[0].id)
    assert outcome.ok, outcome.issues


def test_moving_locks_the_pair_by_default(solved):
    db, run, context = solved["db"], solved["run"], solved["context"]
    a = _first_block(db, run.id, subject_code="CS202")
    window = _free_slot_for(db, run.id, a.section_id, a.faculty_id, a.room_id, length=1)

    outcome = manual_edit.apply_move(db, a.id, window[0].id)
    assert outcome.ok

    ssa = (
        db.query(SectionSubjectAssignment)
        .filter(
            SectionSubjectAssignment.academic_context_id == context.id,
            SectionSubjectAssignment.section_id == a.section_id,
            SectionSubjectAssignment.subject_id == a.subject_id,
        )
        .one()
    )
    assert ssa.locked is True


# ------------------------------------------------------------------ room change


def test_valid_room_change_moves_every_session_of_the_pair(solved):
    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS201")
    theory_rooms = [r for r in solved["rooms"].values() if r.room_type == "theory" and r.id != a.room_id]
    candidate = next((r for r in theory_rooms if r.capacity >= a.section.strength), None)
    assert candidate is not None

    result = manual_edit.preview_room_change(db, a.id, candidate.id)
    if not result.ok:
        pytest.skip(f"candidate room busy in this fixture: {result.issues}")

    outcome = manual_edit.apply_room_change(db, a.id, candidate.id)
    assert outcome.ok, outcome.issues

    pair_rows = (
        db.query(Assignment)
        .filter(Assignment.run_id == run.id, Assignment.section_id == a.section_id,
                Assignment.subject_id == a.subject_id)
        .all()
    )
    assert all(r.room_id == candidate.id for r in pair_rows), "HC15: every session must share the new room"
    assert check_all(db, run.id) == []


def test_room_change_rejects_undersized_room(solved):
    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS201")
    small = solved["rooms"]["A-SMALL"]

    result = manual_edit.preview_room_change(db, a.id, small.id)
    assert result.ok is False
    assert any("not eligible" in issue for issue in result.issues)


def test_room_change_rejects_wrong_lab_type(solved):
    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS251")  # requires COMPUTING
    wrong_lab = solved["rooms"]["LAB2"]  # ELECTRONICS

    result = manual_edit.preview_room_change(db, a.id, wrong_lab.id)
    assert result.ok is False
    assert any("not eligible" in issue for issue in result.issues)


def test_room_change_rejects_faculty_room(solved):
    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS201")
    faculty_room = solved["rooms"]["A-F1"]

    result = manual_edit.preview_room_change(db, a.id, faculty_room.id)
    assert result.ok is False
    assert any("not eligible" in issue for issue in result.issues)


def test_room_change_detects_room_conflict(solved):
    """Move CS201/S-A into whatever room CS201/S-B (or any other pair) is
    already using at an overlapping slot."""
    db, run = solved["db"], solved["run"]
    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    a = _first_block(db, run.id, subject_code="CS201", section_number="S-A")
    candidate_room = next(
        (o.room_id for o in rows if o.room_id != a.room_id and o.timeslot_id == a.timeslot_id
         and not (o.section_id == a.section_id and o.subject_id == a.subject_id)),
        None,
    )
    if candidate_room is None:
        pytest.skip("fixture has no overlapping room usage to collide with")

    result = manual_edit.preview_room_change(db, a.id, candidate_room)
    assert result.ok is False
    assert any("already in use" in issue for issue in result.issues)


# --------------------------------------------------------------- faculty change


def test_valid_faculty_change(solved):
    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS201")
    eligible = [f for f in a.subject.faculties if f.id != a.faculty_id]
    assert eligible, "fixture needs >1 eligible faculty for CS201"
    candidate = eligible[0]

    result = manual_edit.preview_faculty_change(db, a.id, candidate.id)
    if not result.ok:
        pytest.skip(f"candidate faculty busy in this fixture: {result.issues}")

    outcome = manual_edit.apply_faculty_change(db, a.id, candidate.id)
    assert outcome.ok, outcome.issues

    pair_rows = (
        db.query(Assignment)
        .filter(Assignment.run_id == run.id, Assignment.section_id == a.section_id,
                Assignment.subject_id == a.subject_id)
        .all()
    )
    assert all(r.faculty_id == candidate.id for r in pair_rows), "HC14: every session must share the new faculty"
    assert check_all(db, run.id) == []


def test_faculty_change_rejects_ineligible_faculty(solved):
    """An arbitrary faculty member not mapped to teach the subject must never
    be assignable, regardless of availability."""
    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS201")
    ineligible = solved["faculty"]["FAC03"]  # only teaches CS251

    result = manual_edit.preview_faculty_change(db, a.id, ineligible.id)
    assert result.ok is False
    assert any("not mapped" in issue for issue in result.issues)


def test_faculty_change_detects_faculty_conflict(solved):
    db, run = solved["db"], solved["run"]
    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    a = _first_block(db, run.id, subject_code="CS201", section_number="S-A")
    candidate_faculty = next(
        (o.faculty_id for o in rows if o.faculty_id != a.faculty_id and o.timeslot_id == a.timeslot_id
         and not (o.section_id == a.section_id and o.subject_id == a.subject_id)
         and o.faculty_id in {f.id for f in a.subject.faculties}),
        None,
    )
    if candidate_faculty is None:
        pytest.skip("fixture has no overlapping eligible-faculty usage to collide with")

    result = manual_edit.preview_faculty_change(db, a.id, candidate_faculty)
    assert result.ok is False
    assert any("already teaching" in issue for issue in result.issues)


def test_locked_pair_rejects_faculty_change(solved):
    db, run, context = solved["db"], solved["run"], solved["context"]
    a = _first_block(db, run.id, subject_code="CS201")
    ssa = (
        db.query(SectionSubjectAssignment)
        .filter(
            SectionSubjectAssignment.academic_context_id == context.id,
            SectionSubjectAssignment.section_id == a.section_id,
            SectionSubjectAssignment.subject_id == a.subject_id,
        )
        .one()
    )
    ssa.locked = True
    db.commit()

    eligible = next(f for f in a.subject.faculties if f.id != a.faculty_id)
    with pytest.raises(manual_edit.Locked):
        manual_edit.apply_faculty_change(db, a.id, eligible.id)


# ------------------------------------------------------------------- history


def test_applied_change_is_recorded_in_history(solved):
    db, run = solved["db"], solved["run"]
    a = _first_block(db, run.id, subject_code="CS202")
    window = _free_slot_for(db, run.id, a.section_id, a.faculty_id, a.room_id, length=1)

    outcome = manual_edit.apply_move(db, a.id, window[0].id)
    assert outcome.ok

    entry = db.get(ChangeHistory, outcome.change_history_id)
    assert entry is not None
    assert entry.change_type == "move"
    assert entry.run_id == run.id
    assert entry.section_id == a.section_id
    assert entry.subject_id == a.subject_id


def test_rejected_change_writes_no_history(solved):
    db, run = solved["db"], solved["run"]
    before = db.query(ChangeHistory).count()

    a = _first_block(db, run.id, subject_code="CS201")
    small = solved["rooms"]["A-SMALL"]
    outcome = manual_edit.apply_room_change(db, a.id, small.id)
    assert outcome.ok is False

    after = db.query(ChangeHistory).count()
    assert after == before, "a rejected change must not be recorded"


# ------------------------------------------------------- partial regeneration


def test_regeneration_preserves_locked_assignments_and_changes_the_rest(dataset):
    """The concrete version of spec #18's example: some pairs locked,
    regenerate, confirm those exact ones survive while the run as a whole is
    still a fresh, valid solve."""
    db, context = dataset["db"], dataset["context"]
    result1, run1 = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result1.ok

    ssas = db.query(SectionSubjectAssignment).filter(
        SectionSubjectAssignment.academic_context_id == context.id
    ).all()
    assert ssas, "generation should have persisted section-subject assignments"
    to_lock = ssas[: max(1, len(ssas) // 2)]
    locked_keys = {(s.section_id, s.subject_id) for s in to_lock}
    for s in to_lock:
        s.locked = True
    db.commit()

    before = {
        (a.section_id, a.subject_id): (a.faculty_id, a.room_id, a.timeslot_id)
        for a in db.query(Assignment).filter(Assignment.run_id == run1.id).all()
        if (a.section_id, a.subject_id) in locked_keys
    }

    result2, run2 = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result2.ok
    assert run2.id != run1.id

    after = {
        (a.section_id, a.subject_id): (a.faculty_id, a.room_id, a.timeslot_id)
        for a in db.query(Assignment).filter(Assignment.run_id == run2.id).all()
        if (a.section_id, a.subject_id) in locked_keys
    }
    assert before == after, "locked pairs must reproduce their exact placement"
    assert check_all(db, run2.id) == []


def test_publishing_protects_the_run_from_a_later_bad_regeneration(solved):
    """Regenerating never mutates a previous run in place - it always creates
    a new TimetableRun - so a published run is structurally protected from
    whatever a later regeneration produces, good or bad."""
    db, run, context = solved["db"], solved["run"], solved["context"]
    run.publish_status = "PUBLISHED"
    db.commit()
    published_rows_before = {
        (a.id, a.timeslot_id, a.room_id, a.faculty_id)
        for a in db.query(Assignment).filter(Assignment.run_id == run.id).all()
    }

    # Corrupt the data so the *next* regeneration is likely to be worse/fail -
    # the point is not that it fails, but that run 1 is untouched regardless.
    result2, run2 = generate(db, academic_context_id=context.id, soft=False, max_seconds=5)

    db.refresh(run)
    assert run.publish_status == "PUBLISHED"
    published_rows_after = {
        (a.id, a.timeslot_id, a.room_id, a.faculty_id)
        for a in db.query(Assignment).filter(Assignment.run_id == run.id).all()
    }
    assert published_rows_before == published_rows_after


# ------------------------------------------------- mixed subjects (L/P split)
#
# A mixed subject's lecture and practical are two independent obligations
# (independent room, independent faculty, independent lock) sharing one
# (section, subject) pair but two SectionSubjectAssignment rows - one per
# session_type. A manual edit to one component must never read, lock, or
# move the other's row: that bug shipped once (found live, in the deployed
# product, while testing the Lock workflow) because _get_ssa/_lock/
# _pair_rows and timetable_views.locked_pairs matched on (section, subject)
# alone and silently grabbed whichever component's row came back first.


@pytest.fixture
def mixed_solved(db_session, context):
    """One section, one mixed subject (CAP460: 1 lecture + 1 two-hour
    practical), a theory room and a programming lab each with a spare
    same-kind room for room-change targets, and two faculty eligible for the
    subject for faculty-change targets."""
    from app.grid import build_grid as build_slots

    db = db_session
    subject = Subject(
        name="Fundamentals of Python", code="CAP460", type="mixed",
        session_length_hours=1, sessions_per_week=3,
        lecture_sessions_per_week=1, lecture_session_length=1,
        practical_sessions_per_week=1, practical_session_length=2,
        required_lab_type="programming",
    )
    db.add(subject)
    db.flush()

    rooms = {}
    for number, room_type, lab_type, capacity in [
        ("T1", "theory", None, 70), ("T2", "theory", None, 70),
        ("L1", "lab", "programming", 70), ("L2", "lab", "programming", 70),
    ]:
        r = Room(room_number=number, block="A", floor="1", capacity=capacity,
                  room_type=room_type, lab_type=lab_type)
        db.add(r)
        rooms[number] = r
    db.flush()

    faculty = {}
    for fname, fcode in [("Dr. X", "FX01"), ("Dr. Y", "FX02")]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = [subject]
        db.add(f)
        faculty[fcode] = f
    db.flush()

    section = Section(academic_context_id=context.id, section_number="D-M1", strength=60)
    section.subjects = [subject]
    db.add(section)

    db.add_all(build_slots(periods=8))
    db.commit()

    result, run = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.message
    assert check_all(db, run.id) == []

    return {
        "db": db, "context": context, "run": run, "subject": subject,
        "section": section, "rooms": rooms, "faculty": faculty,
    }


def _component(db, run_id, session_type):
    return (
        db.query(Assignment)
        .filter(Assignment.run_id == run_id, Assignment.session_type == session_type)
        .order_by(Assignment.block_id)
        .first()
    )


def _ssa(db, context_id, section_id, subject_id, session_type):
    return (
        db.query(SectionSubjectAssignment)
        .filter(
            SectionSubjectAssignment.academic_context_id == context_id,
            SectionSubjectAssignment.section_id == section_id,
            SectionSubjectAssignment.subject_id == subject_id,
            SectionSubjectAssignment.session_type == session_type,
        )
        .one()
    )


def test_locking_one_component_does_not_lock_the_other(mixed_solved):
    db, run, context = mixed_solved["db"], mixed_solved["run"], mixed_solved["context"]
    lecture = _component(db, run.id, "L")
    practical_ssa_before = _ssa(db, context.id, lecture.section_id, lecture.subject_id, "P")
    assert practical_ssa_before.locked is False

    window = _free_slot_for(db, run.id, lecture.section_id, lecture.faculty_id, lecture.room_id, length=1)
    assert window, "fixture should have a free slot for the lecture"
    outcome = manual_edit.apply_move(db, lecture.id, window[0].id)
    assert outcome.ok, outcome.issues

    lecture_ssa = _ssa(db, context.id, lecture.section_id, lecture.subject_id, "L")
    practical_ssa = _ssa(db, context.id, lecture.section_id, lecture.subject_id, "P")
    assert lecture_ssa.locked is True
    assert practical_ssa.locked is False, (
        "locking the lecture must not lock the practical - they are independent obligations"
    )
    assert practical_ssa.faculty_id == practical_ssa_before.faculty_id
    assert practical_ssa.room_id == practical_ssa_before.room_id


def test_locked_field_in_master_view_is_independent_per_component(mixed_solved):
    from app import timetable_views

    db, run, context = mixed_solved["db"], mixed_solved["run"], mixed_solved["context"]
    lecture = _component(db, run.id, "L")
    manual_edit._lock(db, run, lecture.section_id, lecture.subject_id,
                       lecture.faculty_id, lecture.room_id, "L")
    db.commit()

    _total, rows = timetable_views.query_master(
        db, run, timetable_views.MasterFilters(), limit=500, offset=0
    )
    by_type = {r.session_type: r.locked for r in rows
               if r.section_id == lecture.section_id and r.subject_id == lecture.subject_id}
    assert by_type["L"] is True
    assert by_type["P"] is False, (
        "the master view must not show the practical as locked just because the lecture is"
    )


def test_room_change_on_one_component_does_not_move_the_other(mixed_solved):
    db, run = mixed_solved["db"], mixed_solved["run"]
    practical = _component(db, run.id, "P")
    lecture = _component(db, run.id, "L")
    lecture_room_before = lecture.room_id

    spare_lab = next(
        r for r in mixed_solved["rooms"].values()
        if r.room_type == "lab" and r.id != practical.room_id
    )
    result = manual_edit.preview_room_change(db, practical.id, spare_lab.id)
    assert result.ok, result.issues
    outcome = manual_edit.apply_room_change(db, practical.id, spare_lab.id)
    assert outcome.ok, outcome.issues

    db.refresh(lecture)
    assert lecture.room_id == lecture_room_before, (
        "a room change to the practical must not touch the lecture's room"
    )
    practical_rows = (
        db.query(Assignment)
        .filter(Assignment.run_id == run.id, Assignment.session_type == "P")
        .all()
    )
    assert all(r.room_id == spare_lab.id for r in practical_rows)
    assert check_all(db, run.id) == []


def test_room_change_eligibility_is_checked_against_the_edited_components_own_kind(mixed_solved):
    """The bug this guards: _eligible_rooms was called without session_type,
    which for a mixed subject silently defaults to 'L' - so a room change to
    the *practical* was checked against lecture (theory-room) eligibility."""
    db, run = mixed_solved["db"], mixed_solved["run"]
    practical = _component(db, run.id, "P")
    theory_room = next(r for r in mixed_solved["rooms"].values() if r.room_type == "theory")

    result = manual_edit.preview_room_change(db, practical.id, theory_room.id)
    assert result.ok is False
    assert any("not eligible" in issue for issue in result.issues)

    lecture = _component(db, run.id, "L")
    lab_room = next(r for r in mixed_solved["rooms"].values() if r.room_type == "lab")
    result = manual_edit.preview_room_change(db, lecture.id, lab_room.id)
    assert result.ok is False
    assert any("not eligible" in issue for issue in result.issues)


def test_faculty_change_on_one_component_does_not_touch_the_other(mixed_solved):
    db, run = mixed_solved["db"], mixed_solved["run"]
    lecture = _component(db, run.id, "L")
    practical = _component(db, run.id, "P")
    practical_faculty_before = practical.faculty_id

    other_faculty = next(
        f for f in mixed_solved["faculty"].values() if f.id != lecture.faculty_id
    )
    result = manual_edit.preview_faculty_change(db, lecture.id, other_faculty.id)
    assert result.ok, result.issues
    outcome = manual_edit.apply_faculty_change(db, lecture.id, other_faculty.id)
    assert outcome.ok, outcome.issues

    db.refresh(practical)
    assert practical.faculty_id == practical_faculty_before, (
        "a faculty change to the lecture must not touch the practical's faculty"
    )
    lecture_rows = (
        db.query(Assignment)
        .filter(Assignment.run_id == run.id, Assignment.session_type == "L")
        .all()
    )
    assert all(r.faculty_id == other_faculty.id for r in lecture_rows)
    assert check_all(db, run.id) == []


def test_moving_a_mixed_subjects_two_period_practical_actually_works(mixed_solved):
    """A mixed subject's components carry their own lengths.

    Found on the live deployment: previewing a move of CAP2101's two-period
    practical answered "valid", and applying it came back 409 "HC13: block
    spans multiple days" - every time, so the move could never succeed.

    _validate_move sized the target window from `subject.session_length_hours`,
    which for a mixed subject is the single-component field (default 1) rather
    than `practical_session_length`. It therefore validated a one-period window
    and then handed a two-row block to apply_move, which zips block against
    window: one row moved, the other stayed, and the block straddled two days.
    The independent re-check caught that and rolled everything back - nothing
    was corrupted - but the preview had already lied.

    A pure practical subject was unaffected (its session_length_hours is the
    right number), which is why test_multi_slot_move_preserves_the_whole_block
    never saw this.
    """
    db, run = mixed_solved["db"], mixed_solved["run"]
    practical = (
        db.query(Assignment)
        .filter(Assignment.run_id == run.id, Assignment.session_type == "P")
        .order_by(Assignment.id)
        .first()
    )
    assert practical is not None
    block = (
        db.query(Assignment)
        .filter(Assignment.run_id == run.id, Assignment.block_id == practical.block_id)
        .all()
    )
    assert len(block) == 2, "fixture must give the practical a two-period block"

    window = _free_slot_for(
        db, run.id, practical.section_id, practical.faculty_id, practical.room_id,
        length=2,
    )
    assert window, "fixture should have a free 2-period window"

    preview = manual_edit.preview_move(db, practical.id, window[0].id)
    outcome = manual_edit.apply_move(db, practical.id, window[0].id)

    # The point: preview and apply must agree, and the move must succeed.
    assert preview.ok, preview.issues
    assert outcome.ok, outcome.issues

    moved = (
        db.query(Assignment)
        .filter(Assignment.run_id == run.id, Assignment.block_id == practical.block_id)
        .all()
    )
    assert len(moved) == 2, "the whole practical block must move together"
    assert {r.timeslot.day for r in moved} == {window[0].day}, (
        "a moved block must stay on one day"
    )
    assert sorted(r.timeslot.period_index for r in moved) == [
        window[0].period_index, window[1].period_index
    ]
    assert check_all(db, run.id) == []


def test_a_move_preview_never_promises_what_apply_refuses(mixed_solved):
    """Preview and apply must not disagree - that is what made the live failure
    impossible to act on."""
    db, run = mixed_solved["db"], mixed_solved["run"]
    slots = db.query(TimeSlot).order_by(TimeSlot.day_index, TimeSlot.period_index).all()
    practical = (
        db.query(Assignment)
        .filter(Assignment.run_id == run.id, Assignment.session_type == "P")
        .order_by(Assignment.id)
        .first()
    )

    disagreements = []
    for slot in slots:
        preview = manual_edit.preview_move(db, practical.id, slot.id)
        if not preview.ok:
            continue
        outcome = manual_edit.apply_move(db, practical.id, slot.id, lock_after=False)
        if not outcome.ok:
            disagreements.append((slot.day, slot.period_index + 1, outcome.issues))
        else:
            # put it back so the next candidate starts from the same state
            manual_edit.apply_move(
                db, practical.id, practical.timeslot_id, lock_after=False
            )
    assert disagreements == [], (
        f"preview said yes but apply refused: {disagreements}"
    )
