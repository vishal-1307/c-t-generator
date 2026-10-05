"""When the primary solve finds nothing, diagnosis must not triple the wait.

`diagnose()` runs up to two more CP-SAT solves - layer B looks for a minimal
conflicting set, layer C looks for the largest schedulable subset - and each
used to get the *full* configured budget on its own, regardless of what the
primary two-phase solve above it had already spent. A call that failed at
every stage could run for close to three times what was asked for.

That is not just slow. A soft-objective solve pins its CPU for its whole
budget by design (the objective keeps improving until time runs out), so
stacking three such solves back to back is real sustained load on a host that
may have very little CPU to give - and it was measured doing exactly that:
the free-tier deployment's process was restarted mid-solve, and every restart
orphaned a run (see test_orphaned_runs.py).

The fix hands diagnosis whatever is left of the original budget rather than a
fresh one. This test pins the arithmetic without needing an actual failing
solve or a slow CP-SAT call - it stubs `solve` to report failure quickly and
captures what `diagnose` was then asked for.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.models import AcademicContext, Faculty, Room, Section, Subject
from app.solver.run import SolveResult, generate


@pytest.fixture
def unsolvable(db_session):
    """A pair with no eligible room at all - fails instantly, before any real
    solving happens, so this test is fast regardless of what budget is passed
    down; only the *arguments* below are under test."""
    from app.grid import build_grid

    ctx = AcademicContext(academic_year="2026-27", semester=1, program="BCA",
                          department="CSE")
    db_session.add(ctx)
    db_session.flush()

    subject = Subject(name="CS201", code="CS201", type="theory",
                      session_length_hours=1, sessions_per_week=1)
    faculty = Faculty(name="F1", faculty_code="F1")
    section = Section(academic_context_id=ctx.id, section_number="S1", strength=999999)
    db_session.add_all([subject, faculty, section])
    db_session.commit()
    faculty.subjects.append(subject)
    section.subjects.append(subject)
    # One tiny room: no capacity in the institution can seat this section.
    db_session.add(Room(room_number="A1", block="A", capacity=10, room_type="theory"))
    db_session.add_all(build_grid())
    db_session.commit()
    return ctx


def test_diagnosis_never_asks_for_more_than_what_remains_of_the_budget(
    db_session, unsolvable
):
    requested_budget = 100.0
    captured: dict[str, float] = {}

    def fake_diagnose(db, inp, academic_context_id, max_seconds=None):
        captured["max_seconds"] = max_seconds
        from app.solver.diagnostics import InfeasibilityReport
        return InfeasibilityReport(
            status="INFEASIBLE", summary="no room fits", blockers=[],
            total_pairs=len(inp.pairs),
        ), None

    with patch("app.solver.run.diagnose", side_effect=fake_diagnose):
        generate(db_session, unsolvable.id, max_seconds=requested_budget)

    assert "max_seconds" in captured, "diagnose was never called"
    # Halved because diagnose's own two layers each receive this figure, and
    # their combined worst case must not exceed what generate() was given.
    assert captured["max_seconds"] <= requested_budget / 2 + 0.5, (
        f"diagnose was handed {captured['max_seconds']}s against a "
        f"{requested_budget}s request - the two layers inside it could "
        f"together run for up to {requested_budget}s more on top of "
        f"whatever the primary solve already spent"
    )
    assert captured["max_seconds"] > 0, "a zero or negative timeout is not a budget"


def test_a_slow_primary_solve_leaves_less_for_diagnosis(db_session, unsolvable):
    """The budget handed to diagnosis shrinks by how long the primary solve
    already ran - not a fixed fraction of the original request."""
    captured: dict[str, float] = {}

    def fake_diagnose(db, inp, academic_context_id, max_seconds=None):
        captured["max_seconds"] = max_seconds
        from app.solver.diagnostics import InfeasibilityReport
        return InfeasibilityReport(
            status="INFEASIBLE", summary="no room fits", blockers=[],
            total_pairs=len(inp.pairs),
        ), None

    def slow_solve(*args, **kwargs):
        time.sleep(0.3)
        return SolveResult(
            status="UNKNOWN", placements=[], solve_time=0.3, objective=None,
            inp=args[0], message="",
        )

    with patch("app.solver.run.solve", side_effect=slow_solve), \
         patch("app.solver.run.diagnose", side_effect=fake_diagnose):
        generate(db_session, unsolvable.id, max_seconds=1.0)

    # The floor exists so a pathologically slow primary phase still leaves
    # diagnosis enough time to report something, rather than a timeout of
    # zero that would make CP-SAT return nothing useful. The floor is 5s for
    # diagnosis as a whole, split across its two layers.
    assert captured["max_seconds"] >= 2.5, (
        "a spent-down budget must fall back to a sane minimum, not race to zero"
    )
