"""Solve orchestration: build, solve, extract, persist.

Persistence write-back (spec #7/#8) happens here, in ``generate()``: after a
successful solve, every pair that did NOT already have a
``SectionSubjectAssignment`` gets one created from what was just decided. A
pair that already had one keeps it untouched - ``data.load`` already
restricted that pair's candidates to the persisted choice, so the solver could
not have picked anything else anyway. This is what makes regeneration stable
without depending on solver determinism: the database remembers, not the RNG.
"""
from __future__ import annotations

import json
import time

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Assignment, SectionSubjectAssignment, TimetableRun
from .data import SolverInput, load
from .diagnostics import diagnose
from .params import SolverParams


# Solving itself lives in `solve`, which reaches nothing outside itself. This
# module is the half that has a database. Re-exported because every caller and
# every test has always imported these from here.
from .solve import (  # noqa: E402  (kept at the seam)
    FEASIBILITY_SHARE,
    Placement,
    SolveResult,
    _extract,
    _hint_from,
    solve,
)


def _next_version(db: Session, academic_context_id: int) -> int:
    latest = (
        db.query(TimetableRun)
        .filter(TimetableRun.academic_context_id == academic_context_id)
        .order_by(TimetableRun.version.desc())
        .first()
    )
    return (latest.version + 1) if latest else 1


def persist(
    db: Session,
    result: SolveResult,
    academic_context_id: int,
    into_run: TimetableRun | None = None,
) -> TimetableRun:
    """Write a run and its assignments in one transaction.

    One Assignment row per occupied period; rows belonging to the same block
    share a ``block_id`` so the grid view can merge multi-hour cells.

    ``into_run`` updates an existing row rather than creating one. The async
    path needs this: the client is already polling a RUNNING run, and that id
    must be the one that ends up holding the result. Writing a second run and
    re-pointing its assignments does not work - the delete-orphan cascade on
    TimetableRun.assignments removes them when the temporary run is deleted.

    On a successful solve, also writes back SectionSubjectAssignment rows for
    every pair that did not already have one - this is the persistence anchor
    (spec #7/#8) that future regenerations will read and honour.
    """
    meta = {"weights": result.weights, "penalties": result.penalties}
    if into_run is not None:
        run = into_run
    else:
        run = TimetableRun(
            academic_context_id=academic_context_id,
            version=_next_version(db, academic_context_id),
            # Placeholder. The real outcome is written at the end of this
            # function, once the assignment rows exist - see the comment
            # above the status block below. Never observable: it is replaced
            # before this transaction commits.
            status="RUNNING",
        )
        db.add(run)
    # Assigns run.id, which the Assignment rows below need.
    db.flush()

    for placement in result.placements:
        for slot_index in placement.slot_indices:
            db.add(
                Assignment(
                    run_id=run.id,
                    section_id=placement.pair.section_id,
                    subject_id=placement.pair.subject_id,
                    faculty_id=placement.faculty_id,
                    room_id=placement.room_id,
                    timeslot_id=result.inp.slot_by_index(slot_index).id,
                    block_id=placement.block_id,
                    session_type=placement.pair.session_type,
                )
            )

    if result.ok:
        _write_back_persistence(db, result, academic_context_id)

    # Send the rows to the database now, before the outcome is even set.
    # Setting the status last on the object is not enough on its own: at
    # commit the unit of work writes a parent before its children, so the
    # run's UPDATE went out ahead of its assignments' INSERTs and a reader on
    # the same connection could still catch a terminal run with part of its
    # rows. Seen once in a full test run as "12 of 14 rows". This flush is
    # what makes the ordering described below true in SQL, not only in code.
    db.flush()

    # The outcome is written LAST, immediately before the commit, so that a
    # terminal status and the rows explaining it become visible together.
    #
    # This used to be set before the rows were added. Correctness then rested
    # entirely on transaction isolation: a reader on another connection cannot
    # see an uncommitted flush, so in production the two did land together.
    # But anything sharing this session's connection - notably the test
    # suite's StaticPool, and any future same-session reader - could observe
    # the flushed terminal status while the run still had no assignments, and
    # conclude the timetable was empty. The async path makes that reachable:
    # the client polls until the status is terminal and then immediately reads
    # the timetable.
    #
    # Ordering the writes this way makes the invariant "a terminal run always
    # has its assignments" a property of the code rather than of the isolation
    # level it happens to run under.
    # Warnings go in with the outcome, and before the status for the same
    # reason the assignments do: a finished run must never be visible without
    # the things that explain it. A dropped lock is exactly such a thing - it
    # means the timetable disagrees with an instruction someone gave.
    warnings = list(getattr(result.inp, "dropped_locks", []) or [])
    run.warnings_json = json.dumps(warnings) if warnings else None

    run.status = result.status
    run.objective_value = result.objective
    run.solve_time_seconds = result.solve_time
    run.weights_json = json.dumps(meta) if result.weights else None
    run.message = result.message or None

    db.commit()
    db.refresh(run)
    return run


def _write_back_persistence(
    db: Session, result: SolveResult, academic_context_id: int
) -> None:
    """Create a SectionSubjectAssignment for every newly-decided pair.

    A pair that already had one is left alone - its faculty/room could not
    have changed anyway, since data.load restricted its candidates to exactly
    that choice before the model was built.
    """
    existing = {
        (r.section_id, r.subject_id, r.session_type): r
        for r in db.query(SectionSubjectAssignment)
        .filter(SectionSubjectAssignment.academic_context_id == academic_context_id)
        .all()
    }
    # Keyed per component: a subject's lecture and its practical are separate
    # decisions and usually land in different rooms, so one row per subject
    # would make them share one.
    by_pair: dict[tuple[int, int, str], Placement] = {}
    for placement in result.placements:
        key = (
            placement.pair.section_id,
            placement.pair.subject_id,
            placement.pair.session_type,
        )
        by_pair[key] = placement  # any placement for the pair carries its faculty/room

    for key, placement in by_pair.items():
        if key in existing:
            continue
        db.add(
            SectionSubjectAssignment(
                academic_context_id=academic_context_id,
                section_id=key[0],
                subject_id=key[1],
                session_type=key[2],
                faculty_id=placement.faculty_id,
                room_id=placement.room_id,
                locked=False,
            )
        )


def generate(
    db: Session,
    academic_context_id: int,
    section_ids: list[int] | None = None,
    max_seconds: float | None = None,
    soft: bool = True,
    weights: dict[str, int] | None = None,
    respect_persisted: bool = True,
    diagnose_failures: bool = True,
    into_run: TimetableRun | None = None,
    track_first_solution: bool = False,
) -> tuple[SolveResult, TimetableRun | None]:
    """Load, solve, and persist for one academic context.

    When no full timetable exists, fall back to diagnostics rather than
    returning a bare INFEASIBLE: report the blocking data problems, and where
    possible persist the largest valid partial timetable so the admin has
    something to work from.
    """
    budget = settings.solver_max_seconds if max_seconds is None else max_seconds
    request_start = time.perf_counter()

    inp = load(
        db,
        academic_context_id=academic_context_id,
        section_ids=section_ids,
        respect_persisted=respect_persisted,
    )
    result = solve(
        inp,
        max_seconds=max_seconds,
        soft=soft,
        weights=weights,
        track_first_solution=track_first_solution,
        # `solve` has no settings of its own on purpose. Handing it the
        # deployed configuration is this layer's job, and the only place it
        # happens - so SOLVER_WORKERS and friends still take effect.
        params=SolverParams.from_settings(settings),
    )

    if result.ok:
        return result, persist(db, result, academic_context_id, into_run)

    if not diagnose_failures:
        return result, None

    # `diagnose` runs up to two more CP-SAT solves (layers B and C), and each
    # took the *full* configured budget regardless of what the primary solve
    # above had already spent - so a call that failed at every stage could run
    # for close to three times what was asked for. On a host where a solve
    # already pins the one available CPU for its whole budget by design (the
    # soft objective keeps improving until time runs out), stacking that
    # three deep is real sustained load for no proportional benefit: minutes
    # spent explaining an infeasibility a user is still waiting to see.
    #
    # So diagnosis gets what is left of the original budget, split across its
    # two solving layers - never more than what was requested, and never a
    # negative or zero timeout that would make CP-SAT return instantly with
    # nothing useful.
    remaining = max(budget - (time.perf_counter() - request_start), 5.0)
    diagnose_budget = remaining / 2

    report, artefacts = diagnose(
        db, inp, academic_context_id, max_seconds=diagnose_budget
    )
    result.report = report
    result.message = report.summary

    if artefacts is None:
        # Only say INFEASIBLE when something actually proved it. Layer A found
        # data problems that make a timetable impossible, layer B extracted a
        # genuine conflict core (find_conflicting_pairs returns nothing unless
        # CP-SAT itself returned INFEASIBLE), or the primary solve proved it.
        #
        # A solve that merely ran out of time proves nothing at all. Reporting
        # that as INFEASIBLE tells an admin "your data makes this impossible"
        # when the truth is "we did not finish looking" - and the two call for
        # opposite responses: one means edit the data, the other means give it
        # more time or a smaller batch. This was found when a full-workbook
        # generation that succeeds on an idle machine reported INFEASIBLE
        # under CPU contention, on data that is demonstrably schedulable.
        proved = (
            bool(report.blockers)
            or bool(report.conflicting_pairs)
            or result.status == "INFEASIBLE"
        )
        if proved:
            result.status = "INFEASIBLE"
        else:
            result.status = "TIMEOUT"
            result.message = (
                "Ran out of time before finding a timetable - this is not a "
                "proof that none exists. Try again with more time, or generate "
                "a smaller batch of sections."
            )
        return result, persist(db, result, academic_context_id, into_run)

    built, solver = artefacts
    result.status = "PARTIAL"
    result.placements = _extract(built, solver, skip_unscheduled=True)
    return result, persist(db, result, academic_context_id, into_run)
