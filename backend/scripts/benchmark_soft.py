"""Phase 5.1: soft-objective benchmark - is the weighted objective usable at scale?

Phase 5 proved the *hard-constraint* model reaches OPTIMAL from 4 to 30
sections. This script answers the separate question the objective raises:
once we also ask for a good timetable rather than merely a valid one, what
does that cost, and is the quality worth it?

Two modes:

    sweep      - hard-only vs soft=True across every size, same dataset each
    components - isolate which objective term drives the cost, at one size

Run in isolation. Never alongside the test suite or another benchmark -
CPU contention from a second CP-SAT process alone has been measured to turn
"OPTIMAL in under a second" into "UNKNOWN at 30s" with identical code.

    uv run python -m scripts.benchmark_soft sweep 4 8 12 16 20 24 30
    uv run python -m scripts.benchmark_soft components 12 20
"""
from __future__ import annotations

import argparse
import sys
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from app.models import Assignment, Faculty, Room, TimeSlot  # noqa: E402
from app.solver.data import load  # noqa: E402
from app.solver.run import generate  # noqa: E402

from constraint_checker import check_all  # noqa: E402
from scripts.benchmark import build_dataset  # noqa: E402

# A practical operational ceiling. Deliberately the same for every size so
# the sweep answers "what quality do you get for a fixed budget?" rather than
# "how long until optimal?" - the former is the question an admin actually
# faces when pressing Generate.
DEFAULT_BUDGET = 120.0


def measure_quality(db, run_id: int) -> dict[str, int]:
    """Recount the three soft metrics from the persisted rows.

    Independent of the solver's own penalty variables on purpose: a mistake
    in the objective encoding cannot then report its own success. The two are
    cross-checked in tests/test_soft_constraints.py.
    """
    rows = db.query(Assignment).filter(Assignment.run_id == run_id).all()
    if not rows:
        return {"gaps": 0, "peak_faculty_load": 0, "same_subject_repeats": 0,
                "room_utilisation_pct": 0}

    by_day = defaultdict(set)
    for a in rows:
        by_day[(a.section_id, a.timeslot.day_index)].add(a.timeslot.period_index)
    gaps = sum((max(p) - min(p) + 1) - len(p) for p in by_day.values())

    daily = defaultdict(int)
    for a in rows:
        daily[(a.faculty_id, a.timeslot.day_index)] += 1
    peak = defaultdict(int)
    for (faculty_id, _day), n in daily.items():
        peak[faculty_id] = max(peak[faculty_id], n)

    blocks = defaultdict(set)
    for a in rows:
        blocks[(a.section_id, a.subject_id, a.timeslot.day_index)].add(a.block_id)
    repeats = sum(len(v) - 1 for v in blocks.values() if len(v) > 1)

    # Room utilisation: occupied (room, slot) cells as a percentage of all
    # schedulable (non-faculty, active room) x (teachable slot) cells.
    teachable = db.query(TimeSlot).filter(TimeSlot.is_lunch.is_(False)).count()
    usable_rooms = (
        db.query(Room)
        .filter(Room.is_active.is_(True), Room.room_type != "faculty")
        .count()
    )
    capacity_cells = teachable * usable_rooms
    occupied_cells = len({(a.room_id, a.timeslot_id) for a in rows})
    utilisation = round(100 * occupied_cells / capacity_cells) if capacity_cells else 0

    return {
        "gaps": gaps,
        "peak_faculty_load": sum(peak.values()),
        "same_subject_repeats": repeats,
        "room_utilisation_pct": utilisation,
    }


def run_one(
    n_sections: int,
    budget: float,
    soft: bool,
    weights: dict[str, int] | None = None,
    label: str = "",
) -> dict:
    db, ctx, engine = build_dataset(n_sections)

    t0 = time.perf_counter()
    inp = load(db, academic_context_id=ctx.id)
    load_time = time.perf_counter() - t0

    n_faculty = db.query(Faculty).count()
    n_rooms = db.query(Room).count()

    tracemalloc.start()
    t0 = time.perf_counter()
    result, run = generate(
        db, academic_context_id=ctx.id, soft=soft, max_seconds=budget,
        weights=weights, diagnose_failures=False, track_first_solution=True,
    )
    wall = time.perf_counter() - t0
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    violations: list[str] = []
    quality: dict[str, int] = {}
    if run is not None and result.ok:
        violations = check_all(db, run.id)
        quality = measure_quality(db, run.id)

    row = {
        "label": label or ("soft" if soft else "hard-only"),
        "sections": n_sections,
        "pairs": len(inp.pairs),
        "faculty": n_faculty,
        "rooms": n_rooms,
        "variables": result.variables,
        "constraints": result.constraints,
        "load_s": round(load_time, 2),
        "wall_s": round(wall, 1),
        "solve_s": round(result.solve_time, 1),
        "first_solution_s": (
            round(result.first_solution_seconds, 2)
            if result.first_solution_seconds is not None else None
        ),
        "status": result.status,
        "objective": result.objective,
        "violations": len(violations),
        "gaps": quality.get("gaps"),
        "repeats": quality.get("same_subject_repeats"),
        "peak_fac_load": quality.get("peak_faculty_load"),
        "room_util_pct": quality.get("room_utilisation_pct"),
        "peak_mb": round(peak_mem / (1024 * 1024), 1),
    }
    db.close()
    engine.dispose()
    return row


COLUMNS = [
    "label", "sections", "pairs", "variables", "constraints", "wall_s",
    "solve_s", "first_solution_s", "status", "objective", "violations",
    "gaps", "repeats", "peak_fac_load", "room_util_pct", "peak_mb",
]


def _print_table(rows: list[dict]) -> None:
    print("\n" + " | ".join(COLUMNS))
    for row in rows:
        print(" | ".join(str(row.get(c)) for c in COLUMNS))


def sweep(sizes: list[int], budget: float) -> None:
    """Hard-only vs soft at every size, so the cost of optimisation and the
    quality it buys are both visible against the same dataset."""
    rows = []
    for n in sizes:
        for soft in (False, True):
            tag = "soft" if soft else "hard-only"
            print(f"--- {n} sections, {tag} (budget {budget}s) ---", flush=True)
            row = run_one(n, budget, soft=soft)
            rows.append(row)
            print(row, flush=True)
    print("\n=== SWEEP SUMMARY ===")
    _print_table(rows)


def components(sizes: list[int], budget: float) -> None:
    """Isolate which objective term drives the cost.

    A weight of 0 removes that term from the minimisation (its penalty vars
    still exist but contribute nothing, so the solver has no reason to work
    on it) - that is the isolation, without touching the model's structure.
    """
    configs = [
        ("hard-only", False, None),
        ("gaps-only", True, {"gap": 10, "spread": 0, "repeat": 0}),
        ("spread-only", True, {"gap": 0, "spread": 3, "repeat": 0}),
        ("repeat-only", True, {"gap": 0, "spread": 0, "repeat": 5}),
        ("all (default weights)", True, None),
    ]
    rows = []
    for n in sizes:
        for label, soft, weights in configs:
            print(f"--- {n} sections, {label} (budget {budget}s) ---", flush=True)
            row = run_one(n, budget, soft=soft, weights=weights, label=label)
            rows.append(row)
            print(row, flush=True)
    print("\n=== COMPONENT SUMMARY ===")
    _print_table(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["sweep", "components"])
    parser.add_argument("sizes", nargs="*", type=int)
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET)
    args = parser.parse_args()

    sizes = args.sizes or ([4, 8, 12, 16, 20, 24, 30] if args.mode == "sweep" else [12, 20])
    if args.mode == "sweep":
        sweep(sizes, args.budget)
    else:
        components(sizes, args.budget)


if __name__ == "__main__":
    main()
