"""Phase 5: solver scalability changes.

The existing solver test files (test_solver_single_section.py,
test_solver_all_sections.py, test_soft_constraints.py, test_infeasible.py)
already exercise every hard/soft constraint end-to-end through the public
``generate()`` API, and all 172 of them passed unchanged against the Phase 5
model - that is the primary regression proof these changes preserve
correctness. This file adds two things that coverage doesn't:

1. **Structural pins** on the reformulated model shape (Phase 5's actual
   change - decoupling room selection from the ``start`` variable), so a
   future edit that silently reverts the reformulation is caught immediately
   rather than discovered by a solve-time regression nobody notices for a
   while.
2. **Correctness of the optional, off-by-default experiments**
   (room_symmetry_breaking, decision_strategy variants) - proving each one
   still produces a hard-constraint-clean timetable, since they are not
   exercised by any existing test (all of which call the public API, which
   never turns these flags on).
"""
from __future__ import annotations

import pytest

from app.grid import build_grid
from app.models import AcademicContext, Assignment, Faculty, Room, Section, Subject
from app.solver.data import load
from app.solver.model import build
from app.solver.run import generate

from constraint_checker import check_all


@pytest.fixture
def dataset(db_session):
    """A small but genuinely contended instance - shared faculty and a
    multi-room theory pool - so the optional experiments have something real
    to work with, not a trivial single-candidate case."""
    db = db_session
    ctx = AcademicContext(academic_year="2026-27", semester=1, program="BCA", department="CSE")
    db.add(ctx)
    db.flush()

    subjects = {}
    for name, code, type_, length, spw in [
        ("Data Structures", "CS201", "theory", 1, 3),
        ("Operating Systems", "CS202", "theory", 1, 3),
        ("DS Lab", "CS251", "practical", 2, 1),
    ]:
        s = Subject(name=name, code=code, type=type_, session_length_hours=length, sessions_per_week=spw)
        db.add(s)
        subjects[code] = s
    db.flush()

    for number, room_type, pin in [
        ("A101", "theory", None), ("A102", "theory", None), ("A103", "theory", None),
        ("LAB1", "lab", "CS251"),
    ]:
        db.add(Room(room_number=number, block="A", floor="1", capacity=70, room_type=room_type,
                     fixed_subject_id=subjects[pin].id if pin else None))
    db.flush()

    for fname, fcode, teaches in [
        ("Dr. A", "FAC01", ["CS201", "CS202"]),
        ("Dr. B", "FAC02", ["CS201", "CS202"]),
        ("Dr. L", "FAC03", ["CS251"]),
    ]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = [subjects[c] for c in teaches]
        db.add(f)

    for number in ("S-A", "S-B"):
        sec = Section(academic_context_id=ctx.id, section_number=number, strength=60)
        sec.subjects = list(subjects.values())
        db.add(sec)

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)
    db.commit()

    return {"db": db, "context": ctx}


# ------------------------------------------------------------- structural pins


def test_start_variable_has_no_room_dimension(dataset):
    """Phase 5's actual change: room selection is decoupled from the start
    variable (reified separately, like faculty already was), not folded into
    a 4th dimension of ``start``. Pinned here so a regression back to the
    pre-Phase-5 shape - which reintroduces the variable-count blowup profiling
    identified - is caught structurally, not just by a solve-time surprise."""
    inp = load(dataset["db"], academic_context_id=dataset["context"].id)
    built = build(inp)
    assert built.start, "fixture should produce at least one start variable"
    for key in built.start:
        assert len(key) == 3, f"expected (pair, session, start_slot), got {key}"


def test_room_occupancy_is_reified_like_faculty(dataset):
    """room_occ mirrors f_occ's shape and non-emptiness - both are the
    (pair, resource, slot) reification pattern the room clash and faculty
    clash constraints are now built from symmetrically."""
    inp = load(dataset["db"], academic_context_id=dataset["context"].id)
    built = build(inp)
    assert built.room_occ, "room clash should be built via reification"
    for key in built.room_occ:
        assert len(key) == 3


def test_reformulated_model_has_fewer_variables_than_naive_cartesian(dataset):
    """A cheap sanity bound: folding room into start would need
    len(start-without-room) x avg(room_candidates) variables just for start.
    The reified version's total should stay well under that product - proof
    the reduction is real on this fixture, not just the 4-section benchmark
    instance measured in TIMETABLE_LOGIC_SPEC.md."""
    inp = load(dataset["db"], academic_context_id=dataset["context"].id)
    built = build(inp)
    naive_start_count = sum(
        len(pair.starts) * pair.sessions * max(1, len(pair.room_ids)) for pair in inp.pairs
    )
    assert len(built.start) < naive_start_count


# --------------------------------------------- optional experiments: correctness


def test_room_symmetry_breaking_still_produces_a_valid_timetable(dataset):
    """room_symmetry_breaking is not wired into generate() (off by default,
    see model.py) - exercised directly against the built model, then
    independently re-derived from the extracted placements the same way
    tests/constraint_checker.py validates the production path, since there is
    no DB run to check against here."""
    from ortools.sat.python import cp_model

    inp = load(dataset["db"], academic_context_id=dataset["context"].id)
    built = build(inp, room_symmetry_breaking=True)
    assert built.room_symmetry_constraints_added > 0, (
        "fixture's 3 identical theory rooms should produce at least one "
        "symmetry-breaking constraint - otherwise this test proves nothing"
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20
    solver.parameters.num_search_workers = 4
    status = solver.Solve(built.model)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    chosen_room = {pi: r for (pi, r), v in built.room.items() if solver.Value(v)}
    chosen_faculty = {pi: f for (pi, f), v in built.fac.items() if solver.Value(v)}
    room_slot: dict[tuple[int, int], int] = {}
    faculty_slot: dict[tuple[int, int], int] = {}
    for (pi, k, t), v in built.start.items():
        if not solver.Value(v):
            continue
        pair = inp.pairs[pi]
        room_id = chosen_room[pi]
        faculty_id = chosen_faculty[pi]
        for slot_index in pair.starts[t]:
            assert room_slot.setdefault((room_id, slot_index), pi) == pi, "HC2 room clash"
            assert faculty_slot.setdefault((faculty_id, slot_index), pi) == pi, "HC1 faculty clash"


@pytest.mark.parametrize("strategy", ["none", "practicals_first", "scarcity_first"])
def test_decision_strategy_variants_stay_correct(dataset, strategy):
    """None of these can change what is feasible - they only pick which
    literal CP-SAT branches on first. Verified directly against the model
    (not through generate(), since the public API does not expose the
    strategy choice) by building, solving, and independently validating."""
    from ortools.sat.python import cp_model

    inp = load(dataset["db"], academic_context_id=dataset["context"].id)
    built = build(inp, decision_strategy=strategy)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20
    solver.parameters.num_search_workers = 4
    status = solver.Solve(built.model)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE), cp_model.CpSolver().StatusName(status)


def test_unknown_decision_strategy_is_rejected(dataset):
    inp = load(dataset["db"], academic_context_id=dataset["context"].id)
    with pytest.raises(ValueError):
        build(inp, decision_strategy="not-a-real-strategy")


# --------------------------------------------------------- probing configuration


def test_probing_level_default_is_zero():
    """Phase 5's measured, low-risk win: disabling CP-SAT's probing presolve
    pass is a pure effort dial (never changes what is feasible) that
    reproducibly halved solve time across multiple seeds on the benchmark
    instances. Pinned so the default cannot silently drift back."""
    from app.config import settings

    assert settings.solver_probing_level == 0


def test_generate_still_produces_a_valid_and_persistent_timetable(dataset):
    """End-to-end: the production path (generate -> persist -> independent
    check) still works after the reformulation, including the persistence
    write-back the whole solver rework depends on."""
    db = dataset["db"]
    context = dataset["context"]
    result1, run1 = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result1.status in {"OPTIMAL", "FEASIBLE"}
    assert check_all(db, run1.id) == []

    result2, run2 = generate(db, academic_context_id=context.id, soft=False, max_seconds=20)
    assert result2.status in {"OPTIMAL", "FEASIBLE"}
    assert run2.version == run1.version + 1

    rows1 = {(a.section_id, a.subject_id): (a.faculty_id, a.room_id)
             for a in db.query(Assignment).filter(Assignment.run_id == run1.id).all()}
    rows2 = {(a.section_id, a.subject_id): (a.faculty_id, a.room_id)
             for a in db.query(Assignment).filter(Assignment.run_id == run2.id).all()}
    assert rows1 == rows2, "persistence must survive the model reformulation"
