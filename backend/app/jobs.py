"""Background solve jobs.

At the target scale a solve takes tens of seconds, which is longer than the
idle timeout of most hosting platforms. So generation is asynchronous: the
request creates a RUNNING row and returns immediately, a worker thread solves,
and the client polls the run.

The worker opens its own database session. Reusing the request's session would
outlive the request and is not thread-safe.
"""
from __future__ import annotations

import logging
import threading

from .database import SessionLocal
from .models import TimetableRun
from .solver.run import _next_version, generate

logger = logging.getLogger("timetable.jobs")

# Guards against two solves racing on the same database.
_lock = threading.Lock()
_running: set[int] = set()
_threads: dict[int, threading.Thread] = {}

# The worker opens its own session, so it cannot use the request's. Tests point
# this at their fixture database; production leaves it as SessionLocal.
session_factory = SessionLocal


def use_session_factory(factory) -> None:
    """Point background solves at a different database (used by tests)."""
    global session_factory
    session_factory = factory


def wait(run_id: int, timeout: float = 120.0) -> None:
    """Block until a given solve finishes. For tests and shutdown."""
    thread = _threads.get(run_id)
    if thread is not None:
        thread.join(timeout)


def is_running() -> bool:
    return bool(_running)


def _work(
    run_id: int,
    academic_context_id: int,
    section_ids,
    max_seconds,
    weights,
    respect_persisted,
) -> None:
    db = session_factory()
    try:
        run = db.get(TimetableRun, run_id)
        logger.info(
            "generation started run_id=%s context=%s sections=%s",
            run_id, academic_context_id, section_ids or "all",
        )
        result, _ = generate(
            db,
            academic_context_id=academic_context_id,
            section_ids=section_ids,
            max_seconds=max_seconds,
            weights=weights,
            respect_persisted=respect_persisted,
            into_run=run,
        )
        logger.info(
            "generation finished run_id=%s status=%s solve_time=%.2fs assignments=%d",
            run_id, result.status, result.solve_time, len(result.placements),
        )
    except Exception:
        # Never leak an internal stack trace to the client (spec #42) - it goes
        # to the log, and the run gets a generic, honest message.
        logger.exception("generation crashed run_id=%s", run_id)
        db.rollback()
        run = db.get(TimetableRun, run_id)
        if run is not None:
            run.status = "INFEASIBLE"
            run.message = "The solver encountered an internal error. See server logs."
            db.commit()
    finally:
        db.close()
        with _lock:
            _running.discard(run_id)
            # Drop the finished thread too. Without this the dict grows by one
            # Thread object per solve for the life of the process, and nothing
            # reads it after completion - ``wait()`` on a missing entry
            # correctly returns immediately, because the run is already done.
            _threads.pop(run_id, None)


def start(
    db,
    academic_context_id: int,
    section_ids: list[int] | None = None,
    max_seconds: float | None = None,
    weights: dict[str, int] | None = None,
    respect_persisted: bool = True,
) -> TimetableRun:
    """Create a RUNNING row and hand the solve to a worker thread."""
    run = TimetableRun(
        academic_context_id=academic_context_id,
        version=_next_version(db, academic_context_id),
        status="RUNNING",
        publish_status="DRAFT",
        message="Solver started.",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    with _lock:
        _running.add(run.id)

    thread = threading.Thread(
        target=_work,
        args=(run.id, academic_context_id, section_ids, max_seconds, weights, respect_persisted),
        daemon=True,
        name=f"solve-{run.id}",
    )
    _threads[run.id] = thread
    thread.start()
    return run


def reconcile_orphaned_runs(db) -> int:
    """Resolve every run left ``RUNNING`` from a previous process.

    A solve's progress lives only in this process's memory - the worker
    thread, the `_running` guard, the CP-SAT search itself. None of that
    survives a restart, so a fresh process starts with all three empty by
    construction. That makes the signal exact rather than a guess: a run still
    marked ``RUNNING`` when this runs is *provably* orphaned, because a
    genuinely in-progress solve would have its owning thread alive right here,
    and this process has none.

    Found in production: the free-tier host restarts the process under the
    sustained CPU load a solve produces (soft-objective solves use their whole
    time budget by design, and the instance is a fraction of one core). Before
    this, that left the run's row stuck at "RUNNING" forever - polled by a
    client that would wait indefinitely for a status that was never coming
    again, with nothing to explain why. The fix does not make the crash not
    happen; it makes the failure honest and visible instead of silent, which
    is the property the rest of this codebase already holds every other
    failure to.
    """
    from .models import TimetableRun

    stuck = db.query(TimetableRun).filter(TimetableRun.status == "RUNNING").all()
    for run in stuck:
        run.status = "INFEASIBLE"
        run.message = (
            "This run did not finish - the server restarted while it was "
            "solving, most likely from a hosting resource limit rather than "
            "the data. Generate again; if it keeps happening for this "
            "context, a smaller section_ids batch or a longer-lived instance "
            "may be needed."
        )
    if stuck:
        db.commit()
        logger.warning(
            "reconciled %d orphaned run(s) left RUNNING by a previous process: %s",
            len(stuck), [r.id for r in stuck],
        )
    return len(stuck)
