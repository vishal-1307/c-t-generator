"""Phase 3 part 3: soft constraints as a weighted objective.

Soft constraints must *improve* the timetable without ever bending a hard one.
Every test here therefore checks two things: the quality metric moved in the
right direction, and `check_all` still reports zero violations.

Quality is measured independently from the persisted rows rather than by reading
the solver's own penalty variables, so a mistake in the objective cannot report
its own success. The two are cross-checked once, in
``test_solver_penalties_match_independent_count``.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from app.config import settings
from app.grid import build_grid
from app.models import (
    AcademicContext,
    Assignment,
    Faculty,
    Room,
    Section,
    Subject,
    TimeSlot,
)
from app.solver.run import generate

from constraint_checker import check_all


@pytest.fixture(scope="module")
def solved():
    """Build the dataset and solve it twice - once hard-only, once with the
    objective - then reuse both results across every test in this file.

    Module-scoped on purpose: each solve takes ~10s, and re-running them per
    test made this file take almost four minutes on its own.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    ctx = AcademicContext(academic_year="2026-27", semester=1, program="BCA", department="CSE")
    db.add(ctx)
    db.flush()

    subjects = {}
    for name, code, type_, length, spw in [
        ("Data Structures", "CS201", "theory", 1, 4),
        ("Operating Systems", "CS202", "theory", 1, 4),
        ("Database Systems", "CS203", "theory", 1, 3),
        ("Computer Networks", "CS204", "theory", 1, 3),
        ("Engineering Mathematics", "MA201", "theory", 1, 4),
        ("Data Structures Lab", "CS251", "practical", 2, 1),
        ("Database Lab", "CS253", "practical", 2, 1),
    ]:
        sub = Subject(name=name, code=code, type=type_,
                      session_length_hours=length, sessions_per_week=spw)
        db.add(sub)
        subjects[code] = sub
    db.flush()

    # Only 2 sections (not 4) and only as many theory rooms as could ever be
    # wanted at once. A pooled/eligible room model - rather than one pinned
    # home room per section - adds a room-choice variable to every pair, and
    # identical interchangeable rooms create search symmetry that makes the
    # *objective* (not feasibility - that stays trivial) much more expensive
    # to prove optimal. Measured: 4 sections x 4 theory rooms could not reach
    # a gap-free proof within 150s. 2 sections x 3 rooms reliably lands close
    # to it well within budget. See TIMETABLE_LOGIC_SPEC.md's room-pool cost
    # note - this is that same, now-measured cost, not a hypothetical one.
    rooms = {}
    for number, room_type, pin in [
        ("A101", "theory", None), ("A102", "theory", None), ("A103", "theory", None),
        ("LAB1", "lab", "CS251"), ("LAB3", "lab", "CS253"),
    ]:
        r = Room(room_number=number, block="A", floor="1", capacity=70, room_type=room_type,
                 fixed_subject_id=subjects[pin].id if pin else None)
        db.add(r)
        rooms[number] = r
    db.flush()

    for fname, fcode, teaches in [
        ("Dr. A", "FAC01", ["CS201", "CS251"]), ("Dr. B", "FAC02", ["CS202"]),
        ("Dr. C", "FAC03", ["CS203", "CS253"]), ("Dr. D", "FAC04", ["CS204"]),
        ("Dr. E", "FAC05", ["MA201"]), ("Dr. F", "FAC06", ["CS201", "CS202"]),
        ("Dr. G", "FAC07", ["CS203", "CS204", "CS253"]),
        ("Dr. H", "FAC08", ["MA201", "CS251"]),
    ]:
        f = Faculty(name=fname, faculty_code=fcode)
        f.subjects = [subjects[c] for c in teaches]
        db.add(f)

    for number in ("CSE-3A", "CSE-3B"):
        sec = Section(academic_context_id=ctx.id, section_number=number, strength=60)
        sec.subjects = list(subjects.values())
        db.add(sec)

    slots = build_grid()
    for slot in slots:
        if slot.period_index == 4:
            slot.is_lunch = True
    db.add_all(slots)
    db.commit()

    hard_result, hard_run = generate(db, academic_context_id=ctx.id, soft=False, max_seconds=20)
    soft_result, soft_run = generate(db, academic_context_id=ctx.id, soft=True, max_seconds=45)

    yield {
        "db": db,
        "context": ctx,
        "hard": (hard_result, hard_run),
        "soft": (soft_result, soft_run),
    }
    db.close()
    engine.dispose()


# ------------------------------------------------------- independent measurement


def measure(db, run_id: int) -> dict[str, int]:
    """Recount the three soft metrics straight from the persisted rows."""
    rows = db.query(Assignment).filter(Assignment.run_id == run_id).all()

    # 1. Gaps: idle periods between a section's first and last class each day.
    by_day = defaultdict(set)
    for a in rows:
        by_day[(a.section_id, a.timeslot.day_index)].add(a.timeslot.period_index)
    gaps = sum(
        (max(p) - min(p) + 1) - len(p) for p in by_day.values()
    )

    # 2. Faculty peak daily load, summed across faculty.
    daily = defaultdict(int)
    for a in rows:
        daily[(a.faculty_id, a.timeslot.day_index)] += 1
    peak = defaultdict(int)
    for (faculty_id, _day), n in daily.items():
        peak[faculty_id] = max(peak[faculty_id], n)

    # 3. Same subject twice in a day, counted in blocks so a 2-hour session
    #    is one occurrence rather than two.
    blocks = defaultdict(set)
    for a in rows:
        blocks[(a.section_id, a.subject_id, a.timeslot.day_index)].add(a.block_id)
    repeats = sum(len(v) - 1 for v in blocks.values() if len(v) > 1)

    # 4. Periods over the preferred run length, in every window of
    #    limit + 1 consecutive periods - the same overlapping-window count the
    #    objective makes, recounted here from the rows alone.
    limit = settings.faculty_soft_max_consecutive
    busy = defaultdict(set)
    periods_by_day = defaultdict(set)
    for slot in db.query(TimeSlot).all():
        periods_by_day[slot.day_index].add(slot.period_index)
    for a in rows:
        busy[(a.faculty_id, a.timeslot.day_index)].add(a.timeslot.period_index)
    long_runs = 0
    if limit > 0:
        for (_faculty_id, day), taught in busy.items():
            ordered = sorted(periods_by_day[day])
            for i in range(len(ordered) - limit):
                window = ordered[i : i + limit + 1]
                if window[-1] - window[0] != limit:
                    continue  # not contiguous
                long_runs += max(0, len(taught & set(window)) - limit)

    # 5. Blocks a section family is spread over: each block past the first,
    #    plus - when every block is a number - the distance from the lowest to
    #    the highest. Families counted through the parent section.
    sections = {s.id: s for s in db.query(Section).all()}
    all_blocks = {str(r.block or "").strip() for r in db.query(Room).all()}
    numeric = bool(all_blocks) and all(b.isdigit() for b in all_blocks)
    used = defaultdict(set)
    for a in rows:
        sec = sections[a.section_id]
        root = sec.parent_section_id or sec.id
        used[root].add(str(a.room.block or "").strip())
    spread = 0
    for blocks_used in used.values():
        spread += len(blocks_used) - 1
        if numeric and len(blocks_used) > 1:
            values = [int(b) for b in blocks_used]
            spread += max(values) - min(values)

    return {
        "gaps": gaps,
        "peak_faculty_load": sum(peak.values()),
        "same_subject_repeats": repeats,
        "long_faculty_runs": long_runs,
        "room_block_spread": spread,
    }


# ------------------------------------------------------------------- the point


def test_soft_constraints_strictly_improve_quality(solved):
    db = solved["db"]
    hard_res, hard_run = solved["hard"]
    soft_res, soft_run = solved["soft"]

    assert hard_res.ok and soft_res.ok
    before = measure(db, hard_run.id)
    after = measure(db, soft_run.id)

    assert after["gaps"] < before["gaps"], f"{before} -> {after}"

    # A theory subject meeting a section twice in a day is now a *hard* rule,
    # not only a penalty. So the hard-only baseline already has none, and that
    # is the assertion worth making: the rule holds without any help from the
    # objective. Every repeat in this fixture would be a theory one - the two
    # labs meet once a week.
    assert before["same_subject_repeats"] == 0, f"{before}"
    assert after["same_subject_repeats"] == 0, f"{after}"

    # The same rule spreads every lecture across the week, which flattens each
    # teacher's busiest day as a side effect - often all the way to the
    # optimum. So the objective can no longer be relied on to *strictly* beat
    # an arbitrary hard-only solution here (measured: 11 -> 11 on one run,
    # 14 -> 12 on the next). It must never make it worse, and whether the
    # spread term itself works is proven directly by
    # test_faculty_load_is_spread_across_days.
    assert after["peak_faculty_load"] <= before["peak_faculty_load"], (
        f"{before} -> {after}"
    )


def test_hard_constraints_still_clean_with_objective(solved):
    """The objective must never buy quality by bending a hard rule."""
    db = solved["db"]
    soft_res, soft_run = solved["soft"]
    assert check_all(db, soft_run.id) == []


def test_demand_still_exactly_met_with_objective(solved):
    db = solved["db"]
    _, soft_run = solved["soft"]
    rows = db.query(Assignment).filter(Assignment.run_id == soft_run.id).all()
    assert len(rows) == 44, "2 sections x 22 periods"


# -------------------------------------------------------- individual objectives


def test_gaps_are_substantially_reduced(solved):
    """A gap-free timetable is not provably reachable within a practical time
    budget once rooms come from a real eligible pool instead of one pinned
    home room per section (see the fixture comment - this is a measured cost,
    not a design choice). What the objective must still demonstrably do is
    drive gaps down hard from the hard-only baseline (13, asserted via
    test_soft_constraints_strictly_improve_quality) rather than leave them
    untouched."""
    db = solved["db"]
    _, soft_run = solved["soft"]
    gaps = measure(db, soft_run.id)["gaps"]
    assert 0 <= gaps <= 4, gaps


def test_same_subject_not_repeated_in_a_day(solved):
    db = solved["db"]
    _, soft_run = solved["soft"]
    assert measure(db, soft_run.id)["same_subject_repeats"] == 0


def test_two_hour_block_is_not_counted_as_a_repeat(solved):
    """A 2-hour practical occupies two periods on one day. That is one session,
    so it must not register as 'same subject twice in a day'."""
    db = solved["db"]
    _, soft_run = solved["soft"]
    rows = db.query(Assignment).filter(Assignment.run_id == soft_run.id).all()

    practicals = [a for a in rows if a.subject.type == "practical"]
    assert practicals
    by_block = defaultdict(list)
    for a in practicals:
        by_block[a.block_id].append(a)
    # Each practical block is 2 rows on the same day, yet repeats stay at zero.
    assert all(len(g) == 2 for g in by_block.values())
    assert len({a.timeslot.day_index for a in by_block[next(iter(by_block))]}) == 1
    assert measure(db, soft_run.id)["same_subject_repeats"] == 0


def test_faculty_load_is_spread_across_days(solved):
    """No faculty should be stacked into one day when their load can spread."""
    db = solved["db"]
    _, soft_run = solved["soft"]
    rows = db.query(Assignment).filter(Assignment.run_id == soft_run.id).all()

    daily = defaultdict(int)
    for a in rows:
        daily[(a.faculty.faculty_code, a.timeslot.day_index)] += 1

    total = defaultdict(int)
    for (code, _d), n in daily.items():
        total[code] += n

    n_days = 5
    for code, load in total.items():
        peak = max(n for (c, _d), n in daily.items() if c == code)
        floor = -(-load // n_days)          # ceil(load / days)
        assert peak <= floor + 1, f"{code}: peak {peak}, load {load} over {n_days} days"


# --------------------------------------------------------------- bookkeeping


def test_the_long_run_preference_actually_shortens_runs(solved):
    """Turning the preference off should not make the timetable better at it.

    The four-in-a-row limit used to be a hard rule; it is now a preference,
    which is a weaker promise and worth checking is a promise at all. Solving
    the same data with the weight at zero gives a control: the weighted run
    may not carry more long-run penalty than the unweighted one.
    """
    db = solved["db"]
    context = solved["context"]
    soft_res, _ = solved["soft"]

    indifferent, _ = generate(
        db, academic_context_id=context.id,
        weights={"long_run": 0}, max_seconds=20,
    )
    assert indifferent.ok, indifferent.message
    assert (
        soft_res.penalties["long_faculty_runs"]
        <= indifferent.penalties["long_faculty_runs"]
    ), (
        "caring about long runs produced more of them than not caring: "
        f"{soft_res.penalties} vs {indifferent.penalties}"
    )


def test_solver_penalties_match_independent_count(solved):
    """Cross-check: the solver's own penalty variables must agree with the
    independent recount. If they diverge, one of them is wrong."""
    db = solved["db"]
    soft_res, soft_run = solved["soft"]
    assert soft_res.penalties == measure(db, soft_run.id)


def test_objective_value_equals_weighted_penalties(solved):
    db = solved["db"]
    soft_res, soft_run = solved["soft"]
    w = soft_res.weights
    p = soft_res.penalties
    expected = (
        w["gap"] * p["gaps"]
        + w["spread"] * p["peak_faculty_load"]
        + w["repeat"] * p["same_subject_repeats"]
        + w["long_run"] * p["long_faculty_runs"]
        + w["proximity"] * p["room_block_spread"]
    )
    assert soft_res.objective == expected


def test_weights_are_overridable(solved):
    """Adds a third run rather than clearing the shared ones - the cached hard
    and soft runs must survive for the tests that follow."""
    db = solved["db"]
    context = solved["context"]
    result, run = generate(
        db, academic_context_id=context.id,
        weights={"gap": 99, "spread": 1, "repeat": 7}, max_seconds=20,
    )
    assert result.weights["gap"] == 99
    assert result.weights["spread"] == 1
    assert result.weights["repeat"] == 7
    assert check_all(db, run.id) == []


def test_run_records_objective_and_weights(solved):
    db = solved["db"]
    soft_res, soft_run = solved["soft"]
    assert soft_run.objective_value == soft_res.objective
    assert soft_run.weights_json is not None

    import json

    meta = json.loads(soft_run.weights_json)
    assert meta["weights"]["gap"] > 0
    assert meta["penalties"] == soft_res.penalties


def test_hard_only_mode_has_no_objective(solved):
    """soft=False is what the hard-constraint tests use to isolate HC1-HC9."""
    db = solved["db"]
    hard_res, hard_run = solved["hard"]
    assert hard_res.objective is None
    assert hard_res.penalties == {}
    assert check_all(db, hard_run.id) == []
