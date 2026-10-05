"""Explaining why a timetable could not be produced.

A bare INFEASIBLE is useless to an admin. This module answers "what do I change?"
in three layers, cheapest and most specific first:

**A. Pre-solve checks.** ``validation.validate()`` already encodes every rule the
model depends on - lab capacity, sole-faculty overload, aggregate lab pressure,
contiguous-window fit. If it finds blockers, the model is never even built, and
the admin gets an actionable list instead of a solver status.

**B. Assumption cores.** If CP-SAT still says INFEASIBLE, rebuild with each pair
gated behind a ``scheduled`` literal and register those as assumptions. CP-SAT
then reports a *minimal* set of pairs that cannot all hold at once - the actual
conflict, not a guess.

**C. Maximise-scheduled fallback.** If the core is empty or unhelpful, ask the
opposite question: what is the largest subset that IS schedulable? Whatever gets
dropped is precisely what has to give.

Layer C is also the most useful outcome in practice: a timetable covering 58 of
60 pairs plus a note naming the other two beats no timetable at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model
from sqlalchemy.orm import Session

from ..config import settings
from ..validation import Check, validate
from .data import Pair, SolverInput
from .model import UnschedulablePair, build


@dataclass
class DroppedPair:
    section: str
    subject: str
    sessions: int
    periods: int
    reason: str = ""

    @property
    def label(self) -> str:
        return f"{self.section} / {self.subject}"


@dataclass
class InfeasibilityReport:
    """Why no timetable exists, and the closest thing to one that does."""

    status: str                                   # INFEASIBLE | PARTIAL
    summary: str
    blockers: list[Check] = field(default_factory=list)
    conflicting_pairs: list[str] = field(default_factory=list)
    dropped_pairs: list[DroppedPair] = field(default_factory=list)
    scheduled_pairs: int = 0
    total_pairs: int = 0

    @property
    def has_partial(self) -> bool:
        return self.status == "PARTIAL"


def presolve_blockers(db: Session, academic_context_id: int) -> list[Check]:
    """Layer A. Returns the failing blocker checks, or an empty list."""
    report = validate(db, academic_context_id=academic_context_id)
    return [c for c in report.checks if not c.ok and c.severity == "blocker"]


def _pair_label(pair: Pair) -> str:
    return f"{pair.section_number} / {pair.subject_code}"


def find_conflicting_pairs(
    inp: SolverInput, max_seconds: float | None = None
) -> list[str]:
    """Layer B. A minimal set of pairs that cannot all be scheduled together."""
    try:
        built = build(inp, optional=True)
    except UnschedulablePair as exc:
        return [f"{_pair_label(exc.pair)}: {exc.reason}"]

    # Assume every pair IS scheduled; CP-SAT reports which of those assumptions
    # conflict.
    index_to_pair: dict[int, int] = {}
    for pi, lit in built.scheduled.items():
        built.model.AddAssumption(lit)
        index_to_pair[lit.Index()] = pi

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = (
        settings.solver_max_seconds if max_seconds is None else max_seconds
    )
    solver.parameters.num_search_workers = settings.solver_workers
    # See config.py's solver_max_memory_mb docstring - diagnose() builds its
    # own (larger, relaxed) model and can OOM just as generate() can.
    if settings.solver_max_memory_mb:
        solver.parameters.max_memory_in_mb = settings.solver_max_memory_mb
    solver.parameters.cp_model_probing_level = settings.solver_probing_level

    status = solver.Solve(built.model)
    if status != cp_model.INFEASIBLE:
        return []

    # A minimal set of pairs that cannot all hold at once - a genuine
    # conflict, but reported as "likely" rather than asserted as THE root
    # cause, since several such minimal cores can exist for one instance.
    core = solver.SufficientAssumptionsForInfeasibility()
    labels = []
    for index in core:
        pi = index_to_pair.get(index)
        if pi is not None:
            labels.append(_pair_label(inp.pairs[pi]))
    return sorted(set(labels))


def largest_schedulable_subset(
    inp: SolverInput, max_seconds: float | None = None
) -> tuple[list[int], list[DroppedPair], object]:
    """Layer C. Maximise the number of pairs placed.

    Returns ``(scheduled_pair_indices, dropped, solver)``. The solver is handed
    back so the caller can extract the partial timetable from the same solution
    rather than re-solving.
    """
    built = build(inp, optional=True)
    # Weight by periods so dropping one big block is preferred over dropping
    # several small ones - fewer classes lost overall.
    built.model.Maximize(
        sum(
            inp.pairs[pi].periods * lit
            for pi, lit in built.scheduled.items()
        )
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = (
        settings.solver_max_seconds if max_seconds is None else max_seconds
    )
    solver.parameters.num_search_workers = settings.solver_workers
    # See config.py's solver_max_memory_mb docstring - diagnose() builds its
    # own (larger, relaxed) model and can OOM just as generate() can.
    if settings.solver_max_memory_mb:
        solver.parameters.max_memory_in_mb = settings.solver_max_memory_mb
    solver.parameters.cp_model_probing_level = settings.solver_probing_level

    status = solver.Solve(built.model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return [], [], None

    scheduled, dropped = [], []
    for pi, lit in built.scheduled.items():
        pair = inp.pairs[pi]
        if solver.Value(lit):
            scheduled.append(pi)
        else:
            dropped.append(
                DroppedPair(
                    section=pair.section_number,
                    subject=pair.subject_code,
                    sessions=pair.sessions,
                    periods=pair.periods,
                )
            )
    return scheduled, dropped, (built, solver)


def diagnose(
    db: Session,
    inp: SolverInput,
    academic_context_id: int,
    max_seconds: float | None = None,
) -> tuple[InfeasibilityReport, object]:
    """Run all three layers and return the most specific answer available.

    Also returns the ``(built, solver)`` pair from layer C when a partial
    timetable was found, so the caller can persist it.
    """
    total = len(inp.pairs)

    # ---- Layer A: data problems the solver should never have to discover.
    blockers = presolve_blockers(db, academic_context_id)
    if blockers:
        return (
            InfeasibilityReport(
                status="INFEASIBLE",
                summary=(
                    f"{len(blockers)} data problem"
                    f"{'' if len(blockers) == 1 else 's'} must be fixed before a "
                    "timetable can be generated."
                ),
                blockers=blockers,
                total_pairs=total,
            ),
            None,
        )

    # ---- Layer B: which pairs actually conflict?
    conflicting = find_conflicting_pairs(inp, max_seconds=max_seconds)

    # ---- Layer C: what is the best partial timetable?
    scheduled, dropped, artefacts = largest_schedulable_subset(
        inp, max_seconds=max_seconds
    )

    if artefacts is None:
        return (
            InfeasibilityReport(
                status="INFEASIBLE",
                summary=(
                    "No timetable could be produced, and no partial timetable was "
                    "found within the time limit."
                ),
                conflicting_pairs=conflicting,
                total_pairs=total,
            ),
            None,
        )

    if not dropped:
        # Everything fits when solved this way - the original failure was the
        # time limit rather than genuine infeasibility.
        return (
            InfeasibilityReport(
                status="PARTIAL",
                summary=(
                    "All classes can be scheduled, but the solver ran out of time "
                    "optimising. Try again with a longer time limit."
                ),
                conflicting_pairs=conflicting,
                scheduled_pairs=len(scheduled),
                total_pairs=total,
            ),
            artefacts,
        )

    lost = sum(d.periods for d in dropped)
    listed = ", ".join(f"{d.label} ({d.sessions} session"
                       f"{'' if d.sessions == 1 else 's'})" for d in dropped[:5])
    if len(dropped) > 5:
        listed += f", and {len(dropped) - 5} more"

    return (
        InfeasibilityReport(
            status="PARTIAL",
            summary=(
                f"Scheduled {len(scheduled)} of {total} section-subject pairs. "
                f"Could not place: {listed}. "
                f"That is {lost} period{'' if lost == 1 else 's'} of teaching with "
                "nowhere to go - free up a room, add a qualified teacher, or widen "
                "the timetable grid."
            ),
            conflicting_pairs=conflicting,
            dropped_pairs=dropped,
            scheduled_pairs=len(scheduled),
            total_pairs=total,
        ),
        artefacts,
    )
