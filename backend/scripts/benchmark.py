"""Performance benchmark: sections, candidates, solve time, status, validity.

Run with:  uv run python -m scripts.benchmark [n1 n2 n3 ...]
Defaults to the Phase 4 minimum stress set: 4, 12, 20, 30 sections.

Each size builds an independent in-memory database with a realistic (not
artificially easy) curriculum: shared faculty across sections, a genuinely
scarce lab pool, multiple lab types, mixed capacities, Mon-Fri only. 30
sections is a stress-test *minimum*, not a hard business ceiling - nothing
here hard-codes a cap.

Records: sections, section-subject pairs, solver decision variables, solve
time, solver status, independent hard-constraint validation result, objective
score, and peak Python-heap memory (tracemalloc - a lower bound on process
RSS, since no cross-platform RSS profiler is installed; still useful for
relative comparison across sizes).
"""
from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base  # noqa: E402
from app.grid import build_grid  # noqa: E402
from app.models import AcademicContext, Faculty, Room, Section, Subject  # noqa: E402
from app.solver.data import load  # noqa: E402
from app.solver.run import generate  # noqa: E402

from constraint_checker import check_all  # noqa: E402

SUBJECTS = [
    # (code, type, length, spw, required_lab_type)
    ("CS201", "theory", 1, 4, None),
    ("CS202", "theory", 1, 4, None),
    ("CS203", "theory", 1, 3, None),
    ("CS204", "theory", 1, 3, None),
    ("MA201", "theory", 1, 4, None),
    ("CS251", "practical", 2, 1, "COMPUTING"),
    ("CS253", "practical", 2, 1, "ELECTRONICS"),
]

# The same weekly teaching, taught as real syllabi describe it: two of the
# theory subjects also have a lab, and the standalone lab subjects disappear
# into them. Total periods a week are unchanged - what changes is that a
# subject can now be two obligations instead of one, which is the cost the
# session-type split actually introduces.
MIXED_SUBJECTS = [
    # (code, type, length, spw, lab_type, lecture_spw, lecture_len, prac_spw, prac_len)
    ("CS201", "mixed", 1, 4, "COMPUTING", 4, 1, 1, 2),
    ("CS202", "mixed", 1, 4, "ELECTRONICS", 4, 1, 1, 2),
    ("CS203", "theory", 1, 3, None, None, None, None, None),
    ("CS204", "theory", 1, 3, None, None, None, None, None),
    ("MA201", "theory", 1, 4, None, None, None, None, None),
]


def _subject_rows(mixed: bool) -> list[Subject]:
    if not mixed:
        return [
            Subject(name=code, code=code, type=type_, session_length_hours=length,
                    sessions_per_week=spw, required_lab_type=lab_type)
            for code, type_, length, spw, lab_type in SUBJECTS
        ]
    return [
        Subject(
            name=code, code=code, type=type_, session_length_hours=length,
            sessions_per_week=spw, required_lab_type=lab_type,
            lecture_sessions_per_week=l_spw, lecture_session_length=l_len,
            practical_sessions_per_week=p_spw, practical_session_length=p_len,
        )
        for code, type_, length, spw, lab_type, l_spw, l_len, p_spw, p_len
        in MIXED_SUBJECTS
    ]


def build_dataset(n_sections: int, mixed: bool = False):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    ctx = AcademicContext(academic_year="2026-27", semester=1, program="BCA", department="CSE")
    db.add(ctx)
    db.flush()

    subjects = {}
    for s in _subject_rows(mixed):
        db.add(s)
        subjects[s.code] = s
    db.flush()

    # Theory rooms: enough for realistic (not full) concurrency - not one per
    # section, not one for everybody either.
    n_theory_rooms = max(4, -(-n_sections // 2))
    for i in range(n_theory_rooms):
        db.add(Room(room_number=f"A{100 + i}", block="A", floor=str(1 + i % 3),
                     capacity=70, room_type="theory"))

    # Labs: deliberately scarce, split across two required lab types so
    # neither pool alone can absorb all practical demand.
    n_labs_each = max(2, -(-n_sections // 8))
    for i in range(n_labs_each):
        db.add(Room(room_number=f"LC{i}", block="C", floor="1", capacity=70,
                     room_type="lab", lab_type="COMPUTING"))
        db.add(Room(room_number=f"LE{i}", block="C", floor="2", capacity=70,
                     room_type="lab", lab_type="ELECTRONICS"))

    # One faculty office - must never be scheduled.
    db.add(Room(room_number="A-F1", block="A", floor="1", capacity=2, room_type="faculty"))
    db.flush()

    # Faculty: shared across sections (real cross-section contention), pool
    # size grows sub-linearly with sections so sharing stays meaningful even
    # at 30 sections rather than each section getting its own teacher.
    faculty_per_subject = max(2, -(-n_sections // 6))
    for code in subjects:
        for i in range(faculty_per_subject):
            fcode = f"F{code}{i}"
            f = Faculty(name=fcode, faculty_code=fcode)
            f.subjects = [subjects[code]]
            db.add(f)
    db.flush()

    sections = []
    strengths = [55, 60, 65, 70]
    for i in range(n_sections):
        sec = Section(academic_context_id=ctx.id, section_number=f"S-{i:03d}",
                       strength=strengths[i % len(strengths)])
        sec.subjects = list(subjects.values())
        db.add(sec)
        sections.append(sec)

    slots = build_grid()
    for s in slots:
        if s.period_index == 4:
            s.is_lunch = True
    db.add_all(slots)
    db.commit()

    return db, ctx, engine


def run_one(n_sections: int, max_seconds: float, soft: bool,
            mixed: bool = False) -> dict:
    db, ctx, engine = build_dataset(n_sections, mixed=mixed)
    inp = load(db, academic_context_id=ctx.id)
    n_pairs = len(inp.pairs)
    n_starts = sum(len(p.starts) * max(1, len(p.room_ids)) for p in inp.pairs)

    tracemalloc.start()
    t0 = time.perf_counter()
    result, run = generate(
        db, academic_context_id=ctx.id, soft=soft, max_seconds=max_seconds,
        diagnose_failures=False,
    )
    wall = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    violations: list[str] = []
    if run is not None and result.status in ("OPTIMAL", "FEASIBLE", "PARTIAL"):
        violations = check_all(db, run.id)

    row = {
        "sections": n_sections,
        "mixed_subjects": mixed,
        "pairs": n_pairs,
        "approx_candidate_starts": n_starts,
        "objective_on": soft,
        "status": result.status,
        "wall_seconds": round(wall, 2),
        "solve_time_seconds": round(run.solve_time_seconds, 2) if run else None,
        "objective": result.objective,
        "hard_constraint_violations": len(violations),
        "peak_python_heap_mb": round(peak / (1024 * 1024), 1),
    }
    db.close()
    engine.dispose()
    return row


def main() -> None:
    args = sys.argv[1:]
    # `--mixed` measures the same weekly teaching expressed as mixed subjects,
    # so the cost of the lecture/practical split is a measured row rather than
    # an assumption. Comparing the two modes at one size is the whole point.
    mixed = "--mixed" in args
    sizes = [int(x) for x in args if not x.startswith("-")] or [4, 12, 20, 30]
    rows = []
    # Primary benchmark: hard constraints only (soft=False). This is what the
    # room-pool cost actually leaves cheap - measured separately (see
    # TIMETABLE_LOGIC_SPEC.md #8) that the *objective* search, not feasibility,
    # is where the real cost of a room pool lands. A soft=True pass at every
    # size would mostly measure that already-documented cost again, at a much
    # higher time budget, rather than answering "can this scale schedule at
    # all" - so soft stays off here except for one illustrative run below.
    for n in sizes:
        budget = 30 if n <= 4 else 60 if n <= 12 else 90 if n <= 20 else 150
        label = " (mixed subjects)" if mixed else ""
        print(f"--- {n} sections{label}, hard constraints only (budget {budget}s) ---", flush=True)
        row = run_one(n, budget, soft=False, mixed=mixed)
        rows.append(row)
        print(row, flush=True)

    print("\n=== summary (soft=False) ===")
    header = list(rows[0].keys())
    print(" | ".join(header))
    for row in rows:
        print(" | ".join(str(row[k]) for k in header))

    # One illustrative soft=True run at the smallest size, to show the
    # objective-search cost concretely without spending the whole benchmark
    # budget on it (already characterized in test_soft_constraints.py).
    smallest = min(sizes)
    print(f"\n--- {smallest} sections, WITH objective (soft=True, budget 60s) ---", flush=True)
    soft_row = run_one(smallest, 60, soft=True, mixed=mixed)
    print(soft_row, flush=True)


if __name__ == "__main__":
    main()
