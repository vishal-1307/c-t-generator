"""Phase 3 part 4: graceful infeasibility.

An admin cannot act on "INFEASIBLE". Each test here breaks the data in a
different way and asserts the system says something *specific* about it.

The three diagnostic layers are exercised separately, because they answer
different questions and the cheap one masks the others:

* **A** - pre-solve checks catch data problems arithmetic can detect.
* **B** - assumption cores name pairs that cannot co-exist.
* **C** - the maximise-scheduled fallback produces the best partial timetable.

Layer A catches most realistic mistakes, so reaching B and C at all requires a
dataset where aggregate counting says "fits" but the combinatorics disagree.
``lab_window_squeeze`` is exactly that case.
"""
from __future__ import annotations

import pytest

from app.grid import build_grid
from app.models import AcademicContext, Assignment, Faculty, Room, Section, Subject, TimeSlot
from app.solver.data import load
from app.solver.diagnostics import diagnose, presolve_blockers
from app.solver.run import generate
from app.validation import validate

from constraint_checker import check_all


def base_dataset(db, context):
    """Three sections, healthy data, solvable."""
    subjects = {}
    for name, code, type_, length, spw in [
        ("Data Structures", "CS201", "theory", 1, 4),
        ("Operating Systems", "CS202", "theory", 1, 3),
        ("DS Lab", "CS251", "practical", 2, 1),
    ]:
        s = Subject(name=name, code=code, type=type_,
                    session_length_hours=length, sessions_per_week=spw)
        db.add(s)
        subjects[code] = s
    db.flush()

    rooms = {}
    for number, room_type, cap, pin in [
        ("A101", "theory", 70, None), ("A102", "theory", 70, None),
        ("A103", "theory", 70, None), ("LAB1", "lab", 70, "CS251"),
    ]:
        r = Room(room_number=number, block="A", floor="1", capacity=cap, room_type=room_type,
                 fixed_subject_id=subjects[pin].id if pin else None)
        db.add(r)
        rooms[number] = r
    db.flush()

    for fname, fcode, teaches in [
        ("Dr. A", "FAC01", ["CS201", "CS202"]),
        ("Dr. B", "FAC02", ["CS201", "CS202"]),
        ("Dr. L", "FAC03", ["CS251"]),
    ]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = [subjects[c] for c in teaches]
        db.add(f)

    for number in ("S-A", "S-B", "S-C"):
        sec = Section(academic_context_id=context.id, section_number=number, strength=60)
        sec.subjects = list(subjects.values())
        db.add(sec)

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)
    db.commit()
    return subjects, rooms


@pytest.fixture
def healthy(db_session, context):
    subjects, rooms = base_dataset(db_session, context)
    return {"db": db_session, "context": context, "subjects": subjects, "rooms": rooms}


@pytest.fixture
def lab_window_squeeze(db_session, context):
    """Passes every pre-solve check, yet is genuinely infeasible.

    Four sections each need one 2-hour session in the only lab. Aggregate
    counting is happy - 8 lab-periods needed against 9 available - but a
    3-period day fits only ONE non-overlapping 2-hour block, so three days can
    serve at most three sections. This is the dataset that reaches layers B/C.

    The lab is taught by two teachers rather than one. With a single teacher
    the squeeze stopped being about the lab at all: eight periods across three
    three-period days leaves that teacher no free period on any of them, which
    the faculty break rule catches in pre-solve - layer A - and the dataset
    exists precisely to get past layer A. Two teachers put the bottleneck back
    where the fixture means it: one room, one 2-hour block a day.
    """
    db = db_session
    lab_subject = Subject(name="Physics Lab", code="PH251", type="practical",
                          session_length_hours=2, sessions_per_week=1)
    db.add(lab_subject)
    db.flush()

    db.add(Room(room_number="LAB1", block="C", floor="1", capacity=70, room_type="lab",
                fixed_subject_id=lab_subject.id))
    for i in range(4):
        db.add(Room(room_number=f"A10{i}", block="A", floor="1", capacity=70, room_type="theory"))
    db.flush()

    for name, code in (("Dr. Lab", "LABF"), ("Dr. Lab Two", "LABG")):
        f = Faculty(name=name, faculty_code=code)
        f.subjects = [lab_subject]
        db.add(f)

    for i in range(4):
        sec = Section(academic_context_id=context.id, section_number=f"S-{i}", strength=60)
        sec.subjects = [lab_subject]
        db.add(sec)

    db.add_all(build_grid(days=["Mon", "Tue", "Wed"], periods=3))
    db.commit()
    return {"db": db, "context": context}


# ------------------------------------------------------------------- baseline


def test_healthy_data_solves_and_reports_nothing(healthy):
    db = healthy["db"]
    result, run = generate(db, academic_context_id=healthy["context"].id, max_seconds=20)
    assert result.ok
    assert result.report is None
    assert check_all(db, run.id) == []


# ------------------------------------------------------- Layer A: data problems


def test_unmapped_subject_is_named(healthy):
    db = healthy["db"]
    healthy["subjects"]["CS202"].faculties = []
    db.commit()

    result, run = generate(db, academic_context_id=healthy["context"].id, max_seconds=20)
    assert result.status == "INFEASIBLE"
    assert result.report is not None
    labels = [b.detail for b in result.report.blockers]
    assert any("CS202" in d for d in labels), labels
    assert run.status == "INFEASIBLE" and run.message


def test_undersized_lab_is_named_with_both_numbers(healthy):
    db = healthy["db"]
    healthy["rooms"]["LAB1"].capacity = 10
    db.commit()

    result, _ = generate(db, academic_context_id=healthy["context"].id, max_seconds=20)
    assert result.status == "INFEASIBLE"
    detail = " ".join(b.detail for b in result.report.blockers)
    assert "CS251" in detail and "60" in detail and "10" in detail


def test_oversubscribed_grid_reports_every_cause(healthy):
    """Squeezing the week to 3 slots breaks several things at once; all of them
    should be listed, not just the first."""
    db = healthy["db"]
    for s in db.query(TimeSlot).all():
        s.is_lunch = not (s.day_index == 0 and s.period_index < 3)
    db.commit()

    result, _ = generate(db, academic_context_id=healthy["context"].id, max_seconds=20)
    assert result.status == "INFEASIBLE"
    ids = {b.id for b in result.report.blockers}
    assert "demand_fits_grid" in ids
    assert "lab_pressure" in ids
    assert len(result.report.blockers) >= 2


def test_no_eligible_theory_room_is_named(healthy):
    """Rooms come from an eligible pool now, not a per-section fixed home -
    but a section can still end up with zero eligible theory rooms (e.g.
    every theory room deactivated or undersized), and that must be named."""
    db = healthy["db"]
    for r in healthy["rooms"].values():
        if r.room_type == "theory":
            r.is_active = False
    db.commit()

    result, _ = generate(db, academic_context_id=healthy["context"].id, max_seconds=20)
    assert result.status == "INFEASIBLE"
    ids = {b.id for b in result.report.blockers}
    assert "pairs_have_eligible_room" in ids or "theory_rooms_exist" in ids
    detail = " ".join(b.detail for b in result.report.blockers)
    assert "theory" in detail.lower()


def test_no_contiguous_window_for_a_long_block(healthy):
    """A 3-hour block cannot exist if lunch splits every run of 3."""
    db = healthy["db"]
    healthy["subjects"]["CS251"].session_length_hours = 3
    for s in db.query(TimeSlot).all():
        s.is_lunch = s.period_index in (1, 3, 5, 7)
    db.commit()

    blockers = presolve_blockers(db, academic_context_id=healthy["context"].id)
    ids = {b.id for b in blockers}
    assert "multi_hour_blocks_fit" in ids, [b.detail for b in blockers]


def test_presolve_runs_before_the_model_is_built(healthy):
    """Layer A must short-circuit: a data problem should never reach CP-SAT."""
    db = healthy["db"]
    healthy["subjects"]["CS202"].faculties = []
    db.commit()

    result, _ = generate(db, academic_context_id=healthy["context"].id, max_seconds=20)
    # Solve time stays ~0 because the model was never solved.
    assert result.report.blockers
    assert result.report.conflicting_pairs == []
    assert result.report.dropped_pairs == []


# ------------------------------------ Layers B and C: genuinely hard, not just wrong


def test_squeeze_passes_every_presolve_check(lab_window_squeeze):
    """Confirms the fixture reaches layers B/C rather than being caught by A.
    Without this, the two tests below could pass for the wrong reason."""
    db = lab_window_squeeze["db"]
    context = lab_window_squeeze["context"]
    report = validate(db, academic_context_id=context.id)
    assert report.ready is True, [c.detail for c in report.checks if not c.ok]
    assert presolve_blockers(db, academic_context_id=context.id) == []


def test_assumption_core_names_the_conflicting_pairs(lab_window_squeeze):
    db = lab_window_squeeze["db"]
    context = lab_window_squeeze["context"]
    inp = load(db, academic_context_id=context.id)
    report, _ = diagnose(db, inp, academic_context_id=context.id, max_seconds=20)

    assert report.conflicting_pairs, "layer B produced no core"
    # Any three of the four fit, so the minimal conflicting set is all four.
    assert len(report.conflicting_pairs) == 4
    assert all("PH251" in p for p in report.conflicting_pairs)


def test_partial_timetable_is_produced_and_is_valid(lab_window_squeeze):
    """The most useful outcome: three of four sections get a real timetable,
    and it violates nothing."""
    db = lab_window_squeeze["db"]
    context = lab_window_squeeze["context"]
    result, run = generate(db, academic_context_id=context.id, max_seconds=20)

    assert result.status == "PARTIAL"
    assert run is not None and run.status == "PARTIAL"
    assert result.report.scheduled_pairs == 3
    assert result.report.total_pairs == 4
    assert len(result.report.dropped_pairs) == 1

    rows = db.query(Assignment).filter(Assignment.run_id == run.id).all()
    assert len(rows) == 6, "3 sections x one 2-hour session"
    assert check_all(db, run.id) == [], "partial timetable must still be valid"


def test_partial_summary_says_what_to_do(lab_window_squeeze):
    db = lab_window_squeeze["db"]
    context = lab_window_squeeze["context"]
    result, _ = generate(db, academic_context_id=context.id, max_seconds=20)
    summary = result.message
    assert "Scheduled 3 of 4" in summary
    assert "PH251" in summary
    # It must suggest a remedy, not just state the failure.
    assert any(word in summary for word in ("free up", "add", "widen"))


def test_dropped_pair_carries_its_cost(lab_window_squeeze):
    db = lab_window_squeeze["db"]
    context = lab_window_squeeze["context"]
    result, _ = generate(db, academic_context_id=context.id, max_seconds=20)
    dropped = result.report.dropped_pairs[0]
    assert dropped.subject == "PH251"
    assert dropped.sessions == 1
    assert dropped.periods == 2
    assert dropped.section.startswith("S-")


# ------------------------------------------------------------------ contract


def test_failure_never_raises(healthy):
    """Generation must return a report, not blow up - the API depends on it."""
    db = healthy["db"]
    healthy["subjects"]["CS202"].faculties = []
    db.commit()
    result, run = generate(db, academic_context_id=healthy["context"].id, max_seconds=20)   # must not raise
    assert result.status == "INFEASIBLE"
    assert isinstance(result.message, str) and result.message


def test_diagnostics_can_be_switched_off(healthy):
    """Hard-constraint tests use this to see the raw solver status."""
    db = healthy["db"]
    healthy["subjects"]["CS202"].faculties = []
    db.commit()

    result, run = generate(
        db, academic_context_id=healthy["context"].id, max_seconds=20, diagnose_failures=False
    )
    assert result.report is None
    assert run is None
    assert result.status == "INFEASIBLE"
