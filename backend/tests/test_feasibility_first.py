"""A timetable first, a well-spread timetable second.

Found by taking the real workbook to the deployed configuration rather than to
a test. On two search workers - what the free instance runs - the largest
context in the demo file produced *no solution at all* inside five minutes with
the objective in the model, while the same model without the objective solved
to optimality in under two. The objective does not just slow the search down;
it changes what the search spends its time on.

Raising the time limit does not fix that, and the measurement says so: five
minutes was not enough either. What fixes it is solving for feasibility first,
keeping that answer, and letting the objective improve on it from a warm start.

The properties worth holding onto:

* a solve that finds any valid timetable returns one, even if the objective
  pass then runs out of time;
* asking for feasibility only is unchanged - the tests that isolate the hard
  rules must still be measuring the same thing;
* the answer is still valid, because a fallback that returned a broken
  timetable would be worse than returning nothing.
"""
from __future__ import annotations

import pytest

from app.models import AcademicContext, Faculty, Room, Section, Subject
from app.solver.data import load
from app.solver.run import solve


@pytest.fixture
def dataset(db_session):
    """Small but genuinely contended: shared teachers and a scarce lab."""
    from app.grid import build_grid

    ctx = AcademicContext(academic_year="2026-27", semester=1, program="BCA",
                          department="CSE")
    db_session.add(ctx)
    db_session.flush()

    subjects = []
    for code, kind, length, spw, lab in [
        ("CS201", "theory", 1, 4, None),
        ("CS202", "theory", 1, 3, None),
        ("CS251", "practical", 2, 1, "COMPUTING"),
    ]:
        s = Subject(name=code, code=code, type=kind, session_length_hours=length,
                    sessions_per_week=spw, required_lab_type=lab)
        db_session.add(s)
        subjects.append(s)
    db_session.flush()

    for i in range(3):
        db_session.add(Room(room_number=f"A{i}", block="A", capacity=70,
                            room_type="theory"))
    db_session.add(Room(room_number="L1", block="C", capacity=70,
                        room_type="lab", lab_type="COMPUTING"))

    for code in ("CS201", "CS202", "CS251"):
        for i in range(2):
            f = Faculty(name=f"F{code}{i}", faculty_code=f"F{code}{i}")
            f.subjects = [s for s in subjects if s.code == code]
            db_session.add(f)

    for i in range(4):
        section = Section(academic_context_id=ctx.id, section_number=f"S{i}",
                          strength=60)
        section.subjects = list(subjects)
        db_session.add(section)

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db_session.add_all(slots)
    db_session.commit()
    return ctx


def test_a_valid_timetable_is_returned_even_on_a_budget_too_small_to_optimise(
    db_session, dataset
):
    """The behaviour the deployed instance depends on.

    A budget that cannot finish the objective search must still produce the
    timetable the feasibility pass found. Before, the whole solve came back
    empty and a registrar was told, after a long wait, that there was no
    timetable - when there was one.
    """
    inp = load(db_session, dataset.id)
    result = solve(inp, soft=True, max_seconds=8, workers=2)

    assert result.status in ("OPTIMAL", "FEASIBLE"), result.message
    assert result.placements, "a solve that found a timetable must return it"


def test_the_returned_timetable_is_actually_valid(db_session, dataset):
    """A fallback that returned something broken would be worse than nothing.

    Checked against the pairs the solver was asked to place rather than against
    the solver's own status.
    """
    inp = load(db_session, dataset.id)
    result = solve(inp, soft=True, max_seconds=8, workers=2)

    placed: dict[tuple[int, int, str], list] = {}
    for placement in result.placements:
        placed.setdefault(placement.pair.key, []).append(placement)

    assert len(placed) == len(inp.pairs), "every obligation must be placed"
    for pair in inp.pairs:
        blocks = placed[pair.key]
        periods = sum(len(p.slot_indices) for p in blocks)
        assert periods == pair.sessions * pair.length, (
            f"{pair.detailed_label} got {periods} periods, "
            f"expected {pair.sessions * pair.length}"
        )
        # One room and one teacher for the whole pair - the rule the model
        # enforces, re-checked here because the fallback path extracts its
        # solution separately from the optimised path.
        assert len({p.room_id for p in blocks}) == 1
        assert len({p.faculty_id for p in blocks}) == 1

    # No two classes in one room at one time, and no teacher in two places -
    # the clashes a broken fallback would most plausibly introduce.
    room_slots: set[tuple[int, int]] = set()
    faculty_slots: set[tuple[int, int]] = set()
    for placement in result.placements:
        for slot in placement.slot_indices:
            assert (placement.room_id, slot) not in room_slots, "room double-booked"
            room_slots.add((placement.room_id, slot))
            assert (placement.faculty_id, slot) not in faculty_slots, (
                "faculty double-booked"
            )
            faculty_slots.add((placement.faculty_id, slot))


def test_asking_for_feasibility_only_is_unchanged(db_session, dataset):
    """The hard-constraint tests isolate the rules from the objective, and
    must still be measuring that - the two-phase path applies only when an
    objective was actually requested."""
    inp = load(db_session, dataset.id)
    result = solve(inp, soft=False, max_seconds=20, workers=2)

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert result.objective is None, "no objective was requested"
    assert result.weights == {}, "and none should be reported"


def test_a_generous_budget_still_optimises(db_session, dataset):
    """Feasibility-first must not mean feasibility-only: given time, the
    objective pass still runs and still reports a score."""
    inp = load(db_session, dataset.id)
    result = solve(inp, soft=True, max_seconds=30, workers=2)

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert result.objective is not None, (
        "with room to optimise, the objective pass should have produced a score"
    )
