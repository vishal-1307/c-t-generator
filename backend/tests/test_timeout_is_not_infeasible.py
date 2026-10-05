"""Running out of time is not a proof that no timetable exists.

`generate_for_context` used to stamp INFEASIBLE on any run that finished
without a timetable and without a partial one to show - whether CP-SAT had
actually proved the instance impossible or had merely hit its time limit.

The two mean opposite things to the person reading the result. INFEASIBLE
says "your data cannot produce a timetable, change it". A timeout says "we
did not finish looking, give it more time or fewer sections". Reporting the
second as the first sends an admin to rewrite data that was fine.

This was found for real: the full-workbook end-to-end test passes on an idle
machine and reported INFEASIBLE inside the full suite, where other tests'
leftover solver threads were competing for the CPU - on data that the very
same test proves is schedulable.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.grid import build_grid
from app.models import AcademicContext, Faculty, Room, Section, Subject
from app.solver.diagnostics import Check, InfeasibilityReport
from app.solver.run import SolveResult, generate


@pytest.fixture
def context(db_session):
    ctx = AcademicContext(academic_year="2026-27", semester=1, program="BCA",
                          department="CSE")
    db_session.add(ctx)
    db_session.flush()

    subject = Subject(name="CS201", code="CS201", type="theory",
                      session_length_hours=1, sessions_per_week=1)
    faculty = Faculty(name="F1", faculty_code="F1")
    section = Section(academic_context_id=ctx.id, section_number="S1", strength=30)
    db_session.add_all([subject, faculty, section])
    db_session.commit()
    faculty.subjects.append(subject)
    section.subjects.append(subject)
    db_session.add(Room(room_number="A1", block="A", capacity=60, room_type="theory"))
    db_session.add_all(build_grid())
    db_session.commit()
    return ctx


def _timed_out(*args, **kwargs):
    """What CP-SAT reports when the limit stops it: UNKNOWN, not INFEASIBLE."""
    return SolveResult(
        status="UNKNOWN", placements=[], solve_time=1.0, objective=None,
        inp=args[0], message="",
    )


def _report(**kwargs):
    return InfeasibilityReport(
        status="INFEASIBLE", summary="nothing found in the time available",
        total_pairs=1, **kwargs,
    )


def test_a_solve_that_ran_out_of_time_is_not_reported_as_infeasible(
    db_session, context
):
    def no_evidence(db, inp, academic_context_id, max_seconds=None):
        # Diagnosis also ran out of time: no data blockers, no conflict core,
        # no partial timetable. Nothing here proves anything.
        return _report(blockers=[], conflicting_pairs=[]), None

    with patch("app.solver.run.solve", side_effect=_timed_out), \
         patch("app.solver.run.diagnose", side_effect=no_evidence):
        result, run = generate(db_session, context.id, max_seconds=1.0)

    assert result.status != "INFEASIBLE", (
        "a timeout was reported as a proof that no timetable exists - that "
        "tells the admin to change data that may be perfectly schedulable"
    )
    assert result.status == "TIMEOUT"
    assert run is not None and run.status == "TIMEOUT"
    assert "not a proof" in (run.message or "").lower(), (
        "the message must say plainly that nothing was proved"
    )


def test_data_blockers_still_prove_infeasibility(db_session, context):
    """Layer A found real data problems - that IS a proof, and must survive."""
    def with_blockers(db, inp, academic_context_id, max_seconds=None):
        return _report(
            blockers=[Check(id="rooms", label="No room fits", ok=False,
                            severity="blocker", detail="section too large")],
            conflicting_pairs=[],
        ), None

    with patch("app.solver.run.solve", side_effect=_timed_out), \
         patch("app.solver.run.diagnose", side_effect=with_blockers):
        result, run = generate(db_session, context.id, max_seconds=1.0)

    assert result.status == "INFEASIBLE"
    assert run is not None and run.status == "INFEASIBLE"


def test_a_conflict_core_still_proves_infeasibility(db_session, context):
    """Layer B only returns pairs when CP-SAT itself returned INFEASIBLE."""
    def with_core(db, inp, academic_context_id, max_seconds=None):
        return _report(blockers=[], conflicting_pairs=["S1 / CS201"]), None

    with patch("app.solver.run.solve", side_effect=_timed_out), \
         patch("app.solver.run.diagnose", side_effect=with_core):
        result, run = generate(db_session, context.id, max_seconds=1.0)

    assert result.status == "INFEASIBLE"


def test_a_proven_infeasible_primary_solve_is_still_infeasible(db_session, context):
    def proved(*args, **kwargs):
        return SolveResult(
            status="INFEASIBLE", placements=[], solve_time=1.0, objective=None,
            inp=args[0], message="",
        )

    def no_evidence(db, inp, academic_context_id, max_seconds=None):
        return _report(blockers=[], conflicting_pairs=[]), None

    with patch("app.solver.run.solve", side_effect=proved), \
         patch("app.solver.run.diagnose", side_effect=no_evidence):
        result, run = generate(db_session, context.id, max_seconds=1.0)

    assert result.status == "INFEASIBLE"
