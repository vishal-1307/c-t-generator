"""Phase 5: solver profiling - where do variables/constraints/time actually go?

Run in isolation. Never alongside the test suite or another benchmark process -
Phase 4 confirmed by direct A/B comparison that CPU contention from a second
CP-SAT process materially changes solve times (a 4-section case went from
"OPTIMAL in <1s" to "UNKNOWN at 30s" purely from a concurrent process, with
identical code and data).

Usage:
    uv run python -m scripts.profile_solver [n1 n2 ...] [--soft] [--seed N]
    uv run python -m scripts.profile_solver 4 8 12 16 20 24 30
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from ortools.sat.python import cp_model  # noqa: E402

from app.solver.data import load  # noqa: E402
from app.solver.model import build  # noqa: E402
from app.solver.objective import add_objective  # noqa: E402

from scripts.benchmark import build_dataset  # noqa: E402


def _stats(values: list[int]) -> dict[str, float]:
    if not values:
        return {"min": 0, "max": 0, "avg": 0.0}
    return {"min": min(values), "max": max(values), "avg": round(statistics.mean(values), 1)}


class _FirstSolutionTimer(cp_model.CpSolverSolutionCallback):
    """Records the wall-clock time of the first callback invocation, so we can
    tell "found *a* valid timetable quickly" apart from "spent the whole
    budget proving/improving it" - the distinction Phase 5 cares about."""

    def __init__(self) -> None:
        super().__init__()
        self.first_solution_time: float | None = None
        self.solution_count = 0
        self._t0 = time.perf_counter()

    def on_solution_callback(self) -> None:  # noqa: N802 (OR-Tools naming)
        self.solution_count += 1
        if self.first_solution_time is None:
            self.first_solution_time = time.perf_counter() - self._t0


def profile_one(
    n_sections: int,
    max_seconds: float,
    soft: bool,
    seed: int | None = None,
    workers: int = 8,
    room_symmetry_breaking: bool = False,
    decision_strategy: str = "practicals_first",
) -> dict:
    from app import config as _config

    db, ctx, engine = build_dataset(n_sections)

    t0 = time.perf_counter()
    inp = load(db, academic_context_id=ctx.id)
    load_time = time.perf_counter() - t0

    n_pairs = len(inp.pairs)
    faculty_counts = [len(p.faculty_ids) for p in inp.pairs]
    room_counts = [len(p.room_ids) for p in inp.pairs]
    start_counts = [len(p.starts) for p in inp.pairs]

    t0 = time.perf_counter()
    built = build(
        inp,
        room_symmetry_breaking=room_symmetry_breaking,
        decision_strategy=decision_strategy,
    )
    build_time = time.perf_counter() - t0

    proto = built.model.Proto()

    terms = None
    if soft:
        terms = add_objective(
            built, _config.settings.w_gap, _config.settings.w_spread, _config.settings.w_repeat
        )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_seconds
    solver.parameters.num_search_workers = workers
    if seed is not None:
        solver.parameters.random_seed = seed
        # Deterministic single-worker search is what makes a seed meaningful -
        # the 8-worker portfolio races independent strategies and the first
        # one home wins, which a seed does not pin down.
        solver.parameters.num_search_workers = 1

    cb = _FirstSolutionTimer()
    t0 = time.perf_counter()
    status = solver.Solve(built.model, cb)
    solve_time = time.perf_counter() - t0

    row = {
        "sections": n_sections,
        "pairs": n_pairs,
        "faculty_candidates": _stats(faculty_counts),
        "room_candidates": _stats(room_counts),
        "legal_start_windows": _stats(start_counts),
        "vars_start": len(built.start),
        "vars_room": len(built.room),
        "vars_fac": len(built.fac),
        "vars_f_occ": len(built.f_occ),
        "cpsat_vars_total": len(proto.variables),
        "cpsat_constraints_total": len(proto.constraints),
        "load_time_s": round(load_time, 3),
        "build_time_s": round(build_time, 3),
        "solve_time_s": round(solve_time, 3),
        "solver_wall_time_s": round(solver.WallTime(), 3),
        "status": solver.StatusName(status),
        "first_solution_time_s": (
            round(cb.first_solution_time, 3) if cb.first_solution_time is not None else None
        ),
        "solution_count": cb.solution_count,
        "objective": solver.ObjectiveValue() if terms and status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
    }
    db.close()
    engine.dispose()
    return row


def _print_row(row: dict) -> None:
    print(
        f"n={row['sections']:>3} pairs={row['pairs']:>4}  "
        f"fac(avg/min/max)={row['faculty_candidates']['avg']}/{row['faculty_candidates']['min']}/{row['faculty_candidates']['max']}  "
        f"room(avg/min/max)={row['room_candidates']['avg']}/{row['room_candidates']['min']}/{row['room_candidates']['max']}  "
        f"starts(avg)={row['legal_start_windows']['avg']}",
        flush=True,
    )
    print(
        f"    vars: start={row['vars_start']} room={row['vars_room']} fac={row['vars_fac']} "
        f"f_occ={row['vars_f_occ']}  cpsat_total_vars={row['cpsat_vars_total']} "
        f"cpsat_total_constraints={row['cpsat_constraints_total']}",
        flush=True,
    )
    print(
        f"    load={row['load_time_s']}s build={row['build_time_s']}s "
        f"solve={row['solve_time_s']}s status={row['status']} "
        f"first_solution={row['first_solution_time_s']}s solutions_seen={row['solution_count']} "
        f"objective={row['objective']}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sizes", nargs="*", type=int, default=[4, 8, 12, 16, 20, 24, 30])
    parser.add_argument("--soft", action="store_true", help="Enable the soft objective (Stage 2).")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--budget", type=float, default=None, help="Fixed time budget for every size.")
    parser.add_argument("--symmetry", action="store_true", help="Enable room symmetry-breaking.")
    parser.add_argument(
        "--strategy",
        default="practicals_first",
        choices=["none", "practicals_first", "scarcity_first"],
    )
    args = parser.parse_args()

    for n in args.sizes:
        budget = args.budget or (30 if n <= 4 else 60 if n <= 12 else 90 if n <= 20 else 150)
        print(f"--- {n} sections (budget {budget}s, soft={args.soft}, "
              f"symmetry={args.symmetry}, strategy={args.strategy}) ---", flush=True)
        row = profile_one(
            n, budget, soft=args.soft, seed=args.seed,
            room_symmetry_breaking=args.symmetry, decision_strategy=args.strategy,
        )
        _print_row(row)


if __name__ == "__main__":
    main()
