"""Solving: build a model, run CP-SAT, read the answer back out.

Everything here is a function of its arguments. No database, no settings, no
FastAPI - the deliberate consequence is that a solve can run anywhere Python
and OR-Tools can, which is what makes an out-of-process solver possible without
a second copy of the algorithm.

`run.py` is the adapter that gives this module a database: it loads a
`SolverInput`, calls `solve()`, and writes the result back. The split is only
about *reach*, not about behaviour - the model built here is the same model,
and `tests/test_solver_purity.py` holds the import boundary in place.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ortools.sat.python import cp_model

from .model import BuiltModel, UnschedulablePair, build
from .objective import add_objective, score
from .params import SolverParams
from .types import Pair, SolverInput

if TYPE_CHECKING:  # `diagnostics` reaches the database; only the name is needed
    from .diagnostics import InfeasibilityReport


@dataclass
class Placement:
    """One scheduled block, before it is exploded into per-period rows."""

    pair: Pair
    # Position of `pair` in `SolverInput.pairs`. Carried so a result can be
    # described without the object graph - which is what lets a placement
    # cross a process boundary as plain integers.
    pair_index: int
    session: int
    block_id: int
    faculty_id: int
    room_id: int
    slot_indices: list[int]


@dataclass
class SolveResult:
    status: str            # OPTIMAL | FEASIBLE | INFEASIBLE | UNKNOWN | MODEL_INVALID
    placements: list[Placement]
    solve_time: float
    objective: float | None
    inp: SolverInput
    message: str = ""
    # Per-soft-constraint penalty totals, for reporting and regression tests.
    penalties: dict[str, int] = field(default_factory=dict)
    weights: dict[str, int] = field(default_factory=dict)
    # Populated only when no full timetable exists; explains why.
    report: InfeasibilityReport | None = None
    # Model-size telemetry, for benchmarking and capacity planning. Read off
    # the built CP-SAT proto, so these are what the solver actually saw.
    variables: int = 0
    constraints: int = 0
    # Seconds from solve start to the first feasible solution, when a
    # callback was supplied. None when unmeasured or never reached.
    first_solution_seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"OPTIMAL", "FEASIBLE"}

    @property
    def proven_optimal(self) -> bool:
        """FEASIBLE means valid but the time limit stopped the search before
        optimality could be proved - a better arrangement may exist."""
        return self.status == "OPTIMAL"


class _FirstSolutionTimer(cp_model.CpSolverSolutionCallback):
    """Records when the first feasible solution appeared.

    "Found a valid timetable in 4s then spent 116s proving it optimal" and
    "found nothing for 120s" are operationally very different outcomes that a
    single wall-time number cannot tell apart - this is what separates them.
    """

    def __init__(self) -> None:
        super().__init__()
        self.first_solution_seconds: float | None = None
        self.solution_count = 0
        self._t0 = time.perf_counter()

    def on_solution_callback(self) -> None:  # noqa: N802 (OR-Tools naming)
        self.solution_count += 1
        if self.first_solution_seconds is None:
            self.first_solution_seconds = time.perf_counter() - self._t0


def solve(
    inp: SolverInput,
    max_seconds: float | None = None,
    workers: int | None = None,
    soft: bool = True,
    weights: dict[str, int] | None = None,
    track_first_solution: bool = False,
    params: SolverParams | None = None,
) -> SolveResult:
    """Build and solve.

    ``soft=False`` solves for feasibility only, which is what the hard-constraint
    tests use to isolate the hard rules from the objective.

    ``track_first_solution`` attaches a solution callback to time the first
    feasible solution. Off by default: a callback fires on every improvement,
    so it is pure overhead for a normal generate() that only wants the final
    answer. Benchmarks turn it on.

    ``params`` carries the solver knobs. Omitted, the documented defaults
    apply; the application passes ``SolverParams.from_settings`` so deployment
    configuration still reaches the solver.
    """
    p = params or SolverParams()
    w = {
        "gap": p.w_gap,
        "spread": p.w_spread,
        "repeat": p.w_repeat,
        "long_run": p.w_long_run,
        "proximity": p.w_proximity,
        **(weights or {}),
    }

    try:
        built = build(inp)
    except UnschedulablePair as exc:
        return SolveResult(
            status="INFEASIBLE",
            placements=[],
            solve_time=0.0,
            objective=None,
            inp=inp,
            message=str(exc),
            weights=w if soft else {},
        )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = (
        p.max_seconds if max_seconds is None else max_seconds
    )
    solver.parameters.num_search_workers = (
        p.workers if workers is None else workers
    )
    # See config.py's solver_max_memory_mb docstring: without this, a large
    # context can OOM-kill the whole process on a memory-constrained
    # instance rather than end the run with a normal status.
    if p.max_memory_mb:
        solver.parameters.max_memory_in_mb = p.max_memory_mb
    # Phase 5: disabling probing is a pure presolve-effort dial, not a model
    # change - see config.py's solver_probing_level docstring for the
    # measurements behind this default.
    solver.parameters.cp_model_probing_level = p.probing_level
    if p.random_seed is not None:
        solver.parameters.random_seed = p.random_seed

    budget = solver.parameters.max_time_in_seconds
    timer = _FirstSolutionTimer() if track_first_solution else None

    # Feasibility first, then quality.
    #
    # Measured on the real workbook's largest context at the deployed worker
    # count: with the objective in the model the solver found no solution at
    # all inside five minutes, while the same model without it was solved to
    # optimality in under two. The objective does not merely slow the search
    # down, it changes what the search spends its time on, and a registrar who
    # waits three minutes for "no timetable" is worse off than one who waits
    # ninety seconds for a plain one.
    #
    # So: solve for feasibility, keep that answer, then let the objective
    # improve on it from a warm start. Raising the time limit does not fix
    # this - that was measured too, and five minutes was not enough.
    fallback: list[Placement] | None = None
    fallback_status = ""
    if soft:
        feasibility_budget = min(budget * FEASIBILITY_SHARE, budget)
        solver.parameters.max_time_in_seconds = feasibility_budget
        first_status = (
            solver.Solve(built.model, timer) if timer else solver.Solve(built.model)
        )
        if first_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            fallback = _extract(built, solver)
            fallback_status = solver.StatusName(first_status)
            # Hand the whole assignment back to the optimising pass as a hint,
            # so it starts from a valid timetable rather than looking for one.
            _hint_from(built, solver)
        solver.parameters.max_time_in_seconds = max(budget - solver.WallTime(), 1.0)

    terms = (
        add_objective(built, w["gap"], w["spread"], w["repeat"], w["long_run"],
                      w["proximity"])
        if soft
        else None
    )

    proto = built.model.Proto()
    n_vars, n_constraints = len(proto.variables), len(proto.constraints)

    status = solver.Solve(built.model, timer) if timer else solver.Solve(built.model)
    status_name = solver.StatusName(status)
    first_solution = timer.first_solution_seconds if timer else None

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE) and fallback is not None:
        # The objective pass ran out of time without improving on it. The
        # feasible timetable is still a correct answer to "build me a
        # timetable", and returning nothing would discard work already done.
        return SolveResult(
            status=fallback_status,
            placements=compact_section_gaps(fallback, inp),
            solve_time=solver.WallTime(),
            objective=None,
            inp=inp,
            weights=w,
            variables=n_vars,
            constraints=n_constraints,
            first_solution_seconds=first_solution,
            message=(
                "Found a valid timetable but ran out of time trying to improve "
                "it. Allow more time for a better-spread result."
            ),
        )

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolveResult(
            status=status_name,
            placements=[],
            solve_time=solver.WallTime(),
            objective=None,
            inp=inp,
            weights=w if soft else {},
            variables=n_vars,
            constraints=n_constraints,
            first_solution_seconds=first_solution,
        )

    return SolveResult(
        status=status_name,
        placements=compact_section_gaps(_extract(built, solver), inp),
        solve_time=solver.WallTime(),
        objective=solver.ObjectiveValue() if terms else None,
        inp=inp,
        penalties=score(solver, terms) if terms else {},
        weights=w if soft else {},
        variables=n_vars,
        constraints=n_constraints,
        first_solution_seconds=first_solution,
    )


# How much of the budget goes to finding a timetable at all, before any of it
# goes to making that timetable nicer. Generous on purpose: a plain timetable
# is worth far more than a well-spread absence of one, and the optimising pass
# starts from a valid solution so it uses its share efficiently.
FEASIBILITY_SHARE = 0.5


def _hint_from(built: BuiltModel, solver: cp_model.CpSolver) -> None:
    """Seed the next solve with the solution just found.

    CP-SAT takes a hint as a starting point rather than a constraint, so a
    later pass is still free to move anything - it simply does not have to
    rediscover a feasible arrangement before it can start improving one.
    """
    for group in (built.start, built.fac, built.room):
        for var in group.values():
            built.model.AddHint(var, solver.Value(var))


def _extract(
    b: BuiltModel, solver: cp_model.CpSolver, skip_unscheduled: bool = False
) -> list[Placement]:
    """Read the chosen start variables back out of the solution.

    ``skip_unscheduled`` is for partial solves, where pairs switched off by the
    diagnostics have no faculty assigned and must simply be omitted.
    """
    chosen_faculty: dict[int, int] = {}
    for (pi, f), var in b.fac.items():
        if solver.Value(var):
            chosen_faculty[pi] = f

    chosen_room: dict[int, int] = {}
    for (pi, r), var in b.room.items():
        if solver.Value(var):
            chosen_room[pi] = r

    placements: list[Placement] = []
    block_id = 0
    for (pi, k, t), var in sorted(b.start.items()):
        if not solver.Value(var):
            continue
        if skip_unscheduled and pi not in chosen_faculty:
            continue
        pair = b.inp.pairs[pi]
        placements.append(
            Placement(
                pair=pair,
                pair_index=pi,
                session=k,
                block_id=block_id,
                faculty_id=chosen_faculty[pi],
                room_id=chosen_room[pi],
                slot_indices=pair.starts[t],
            )
        )
        block_id += 1

    placements.sort(key=lambda p: (p.slot_indices[0], p.pair.section_number))
    return placements


def compact_section_gaps(placements: list[Placement], inp: SolverInput) -> list[Placement]:
    """Tier 3 Anti-Gap: Post-optimization compactor pass.

    Inspects each section's daily timetable. If any gap exists between
    classes or classes start late in the afternoon, it attempts to slide
    the later classes forward into earlier available slots on the same day,
    strictly verifying room, faculty, and section availability with zero clashes.
    """
    if not placements:
        return placements

    slot_by_idx = {s.index: s for s in inp.slots}
    # Group pairs by section hierarchy
    section_family: dict[int, set[int]] = {}
    for sid in {p.pair.section_id for p in placements}:
        family = {sid}
        if sid in inp.section_parent:
            family.add(inp.section_parent[sid])
        if sid in inp.section_children:
            family.update(inp.section_children[sid])
        section_family[sid] = family

    changed = True
    iterations = 0
    max_iterations = 20

    while changed and iterations < max_iterations:
        changed = False
        iterations += 1

        # Sort placements chronologically
        placements.sort(key=lambda p: (slot_by_idx[p.slot_indices[0]].day_index,
                                       slot_by_idx[p.slot_indices[0]].period_index,
                                       p.pair.section_number))

        for p in placements:
            # Do not move locked placements
            if p.pair.locked_starts and p.slot_indices[0] in p.pair.locked_starts:
                continue

            current_start_slot = p.slot_indices[0]
            current_slot_info = slot_by_idx[current_start_slot]
            day = current_slot_info.day_index
            current_period = current_slot_info.period_index

            # Candidate starts on the same day that are earlier
            candidates = [
                start_slot for start_slot in p.pair.starts
                if slot_by_idx[start_slot].day_index == day
                and slot_by_idx[start_slot].period_index < current_period
            ]
            # Try earlier starts in ascending order (earliest first)
            candidates.sort(key=lambda s: slot_by_idx[s].period_index)

            p_family = section_family.get(p.pair.section_id, {p.pair.section_id})

            for cand_start in candidates:
                cand_slots = p.pair.starts[cand_start]
                cand_slot_set = set(cand_slots)

                # 1. Unavailability check
                blocked_fac = inp.blocked_faculty_slots.get(p.faculty_id, set())
                blocked_rm = inp.blocked_room_slots.get(p.room_id, set())
                if any(s in blocked_fac or s in blocked_rm for s in cand_slots):
                    continue

                # 2. Clash checks against all other placements
                has_clash = False
                for other in placements:
                    if other is p:
                        continue
                    other_slots = set(other.slot_indices)
                    overlap = cand_slot_set & other_slots
                    if not overlap:
                        continue
                    if other.room_id == p.room_id:
                        has_clash = True
                        break
                    if other.faculty_id == p.faculty_id:
                        has_clash = True
                        break
                    other_family = section_family.get(other.pair.section_id, {other.pair.section_id})
                    if p_family & other_family:
                        has_clash = True
                        break

                if has_clash:
                    continue

                # 3. Faculty constraints (max consecutive <= 6, break >= 1)
                fac_day_slots = []
                for other in placements:
                    if other.faculty_id == p.faculty_id and slot_by_idx[other.slot_indices[0]].day_index == day:
                        if other is p:
                            fac_day_slots.extend(cand_slots)
                        else:
                            fac_day_slots.extend(other.slot_indices)

                fac_periods = sorted({slot_by_idx[s].period_index for s in fac_day_slots})
                day_total_periods = len([s for s in inp.slots if s.day_index == day])

                # Free period break
                if len(fac_periods) >= day_total_periods:
                    continue

                # Max consecutive check
                max_consec = 0
                curr_consec = 0
                last_p = -2
                for prd in fac_periods:
                    if prd == last_p + 1:
                        curr_consec += 1
                    else:
                        curr_consec = 1
                    last_p = prd
                    if curr_consec > max_consec:
                        max_consec = curr_consec

                if max_consec > 6:
                    continue

                # All checks passed! Slide placement forward
                p.slot_indices = cand_slots
                changed = True
                break

    placements.sort(key=lambda p: (slot_by_idx[p.slot_indices[0]].day_index,
                                   slot_by_idx[p.slot_indices[0]].period_index,
                                   p.pair.section_number))
    return placements
