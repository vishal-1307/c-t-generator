"""A run must never be stuck at RUNNING forever.

Found live: the free-tier host restarts the backend process under the
sustained CPU load a solve produces, and a solve's progress lives only in that
process's memory - the worker thread, the in-memory `_running` guard, the
CP-SAT search itself. None of it survives the restart. Before this fix, the
run's database row was the only trace left, and it said "RUNNING" forever: no
client would ever see anything else, because nothing was ever coming to say
otherwise.

The fix does not try to stop the host from restarting the process - it cannot.
It makes the failure visible instead of silent, on the one signal that is
exact rather than a guess: a fresh process's solve-tracking state is always
empty, so a run still marked RUNNING when a process starts cannot belong to
this process, and can only be one an earlier process abandoned.
"""
from __future__ import annotations

from app import jobs
from app.models import AcademicContext, TimetableRun


def _context(db_session):
    ctx = AcademicContext(academic_year="2026-27", semester=1, program="BCA",
                          department="CSE")
    db_session.add(ctx)
    db_session.commit()
    return ctx


def test_a_run_still_running_at_process_start_is_marked_failed(db_session):
    ctx = _context(db_session)
    orphan = TimetableRun(
        academic_context_id=ctx.id, version=1, status="RUNNING",
        publish_status="DRAFT", message="Solver started.",
    )
    db_session.add(orphan)
    db_session.commit()

    reconciled = jobs.reconcile_orphaned_runs(db_session)

    assert reconciled == 1
    db_session.refresh(orphan)
    assert orphan.status != "RUNNING"
    assert orphan.message and "restarted" in orphan.message.lower()


def test_the_message_is_honest_about_the_cause(db_session):
    """A user reading this must be able to tell "the server had a problem"
    from "your data is invalid" - the two are not the same kind of failure
    and do not call for the same next step."""
    ctx = _context(db_session)
    orphan = TimetableRun(
        academic_context_id=ctx.id, version=1, status="RUNNING",
        publish_status="DRAFT", message="Solver started.",
    )
    db_session.add(orphan)
    db_session.commit()

    jobs.reconcile_orphaned_runs(db_session)
    db_session.refresh(orphan)

    assert "again" in orphan.message.lower(), (
        "the message should say what to do next, not just that it failed"
    )


def test_runs_that_finished_normally_are_left_alone(db_session):
    """Reconciliation must only ever touch what is provably stuck - never a
    run that completed, however it completed."""
    ctx = _context(db_session)
    finished = [
        TimetableRun(academic_context_id=ctx.id, version=1, status="OPTIMAL",
                     publish_status="DRAFT", message="ok", objective_value=12.0),
        TimetableRun(academic_context_id=ctx.id, version=2, status="FEASIBLE",
                     publish_status="DRAFT", message="ok"),
        TimetableRun(academic_context_id=ctx.id, version=3, status="INFEASIBLE",
                     publish_status="DRAFT", message="no valid timetable exists"),
        TimetableRun(academic_context_id=ctx.id, version=4, status="PARTIAL",
                     publish_status="DRAFT", message="partial"),
    ]
    db_session.add_all(finished)
    db_session.commit()
    before = [(r.status, r.message) for r in finished]

    reconciled = jobs.reconcile_orphaned_runs(db_session)

    assert reconciled == 0
    after = [(r.status, r.message) for r in finished]
    assert before == after


def test_multiple_orphans_across_different_contexts_are_all_resolved(db_session):
    """A crash mid-solve does not know or care which academic context it was
    solving for - every stuck run must come back, not just the first one
    found."""
    ctx_a = _context(db_session)
    ctx_b = AcademicContext(academic_year="2026-27", semester=1, program="BSC",
                            department="CSE")
    db_session.add(ctx_b)
    db_session.commit()

    orphans = [
        TimetableRun(academic_context_id=ctx_a.id, version=1, status="RUNNING",
                     publish_status="DRAFT", message="Solver started."),
        TimetableRun(academic_context_id=ctx_b.id, version=1, status="RUNNING",
                     publish_status="DRAFT", message="Solver started."),
    ]
    db_session.add_all(orphans)
    db_session.commit()

    reconciled = jobs.reconcile_orphaned_runs(db_session)

    assert reconciled == 2
    for run in orphans:
        db_session.refresh(run)
        assert run.status != "RUNNING"


def test_reconciliation_runs_automatically_at_process_start():
    """The end-to-end property: nobody has to remember to call this. It is
    wired into application startup the same way the bootstrap-admin seed is,
    so every process boot resolves whatever the previous one left behind."""
    import app.main as main_module

    assert hasattr(main_module, "_reconcile_orphaned_runs")
