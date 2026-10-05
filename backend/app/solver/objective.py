"""Soft constraints, expressed as one weighted objective to minimise.

These never override a hard constraint - they only choose between timetables
that are already valid. Weights live in ``config.py`` and are overridable per
request, so the relative priorities can be retuned without touching this file.

1. **Gaps in a section's day.** A section with a free period wedged between two
   classes wastes everyone's time. Measured as ``span - load``: the distance
   from the day's first class to its last, minus the periods actually taught.
2. **Faculty load stacked onto few days.** Measured as each faculty member's
   busiest day; minimising the sum of those peaks forces the load to spread,
   since total load per faculty is fixed by demand.
3. **The same subject twice in one day for a section.** Counted per (pair, day)
   as ``sessions_that_day - 1``. A 2-hour block is *one* session, so it
   correctly never counts as a repeat.
4. **A teacher kept in class for a long run.** Counted as the periods over the
   preferred run length in each window of consecutive periods. The department
   prefers short runs; it does not forbid long ones, and a timetable refused
   over a matter of taste is a timetable nobody gets. The *rule* - a free
   period on any day a teacher teaches - is hard, and lives in ``model.py``.
5. **A section's classes spread across buildings.** Counted per section (lab
   groups counted with their parent section - they are the same students) as
   the number of blocks used beyond the first, plus, where every block is
   named by a number, the distance between the lowest and highest block used.
   Same block costs nothing, a neighbouring block costs little, a far one
   more. Never a rule: a class goes to a far block when that is the only room
   that fits it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .model import BuiltModel


@dataclass
class SoftTerms:
    """The penalty variables, kept so a solution can be scored after solving."""

    gaps: dict[tuple[int, int], cp_model.IntVar] = field(default_factory=dict)
    excess_gaps: dict[tuple[int, int], cp_model.IntVar] = field(default_factory=dict)
    late_starts: dict[tuple[int, int], cp_model.IntVar] = field(default_factory=dict)
    peak_load: dict[int, cp_model.IntVar] = field(default_factory=dict)
    repeats: dict[tuple[int, int], cp_model.IntVar] = field(default_factory=dict)
    long_runs: dict[tuple[int, int, int], cp_model.IntVar] = field(default_factory=dict)
    extra_blocks: dict[int, cp_model.IntVar] = field(default_factory=dict)
    block_spread: dict[int, cp_model.IntVar] = field(default_factory=dict)
    weights: dict[str, int] = field(default_factory=dict)


def add_objective(
    b: BuiltModel,
    w_gap: int,
    w_spread: int,
    w_repeat: int,
    w_long_run: int = 0,
    w_proximity: int = 0,
) -> SoftTerms:
    """Attach the weighted objective. Returns the penalty vars for reporting."""
    terms = SoftTerms(
        weights={"gap": w_gap, "spread": w_spread, "repeat": w_repeat,
                 "long_run": w_long_run, "proximity": w_proximity}
    )

    days = sorted({s.day_index for s in b.inp.slots})
    slots_by_day = {d: [s for s in b.inp.slots if s.day_index == d] for d in days}
    max_period = max((s.period_index for s in b.inp.slots), default=0)

    _add_gap_terms(b, terms, days, slots_by_day, max_period)
    _add_spread_terms(b, terms, days, slots_by_day)
    _add_repeat_terms(b, terms, days)
    _add_long_run_terms(b, terms, slots_by_day)
    if w_proximity > 0:
        _add_proximity_terms(b, terms)

    b.model.Minimize(
        w_gap * sum(terms.gaps.values())
        + w_gap * 3 * sum(terms.excess_gaps.values())
        + max(1, w_gap // 2) * sum(terms.late_starts.values())
        + w_spread * sum(terms.peak_load.values())
        + w_repeat * sum(terms.repeats.values())
        + w_long_run * sum(terms.long_runs.values())
        + w_proximity * (
            sum(terms.extra_blocks.values()) + sum(terms.block_spread.values())
        )
    )
    return terms


def _add_gap_terms(b, terms, days, slots_by_day, max_period) -> None:
    """Soft constraint 1: minimise idle periods inside a section's day.

    ``first``/``last`` are only bounded from one side each (busy => first <= t,
    busy => last >= t). That is deliberate: because the span appears with a
    positive weight in a minimisation, the solver tightens both ends by itself.
    Pinning them exactly would need far more constraints for the same result.

    A gap is measured over the students who sit through it, which for a section
    with lab groups is the section *plus* its groups. Counting a group's day
    separately from its parent's would let the objective leave a two-hour hole
    between 2401's lecture and 24011's lab and score it as no gap at all - the
    students would still be waiting, and the number would say otherwise.
    """
    pairs_by_section: dict[int, list[int]] = {}
    for pi, pair in enumerate(b.inp.pairs):
        pairs_by_section.setdefault(pair.section_id, []).append(pi)

    # One entry per set of students whose day is continuous to them: a section
    # on its own, or a group together with its parent. A group appears in its
    # own entry and not the parent's, because two groups' days are independent
    # of each other.
    students: dict[int, list[int]] = {}
    for section_id in {*pairs_by_section, *b.inp.section_children}:
        if section_id in b.inp.section_parent:
            continue  # counted below, together with its parent
        indices = list(pairs_by_section.get(section_id, ()))
        kids = b.inp.section_children.get(section_id, ())
        if not kids:
            students[section_id] = indices
            continue
        for kid in kids:
            students[kid] = indices + list(pairs_by_section.get(kid, ()))

    for section_id, pair_indices in students.items():
        for day in days:
            day_slots = slots_by_day[day]

            busy = {}
            for slot in day_slots:
                lits = [
                    lit
                    for pi in pair_indices
                    for lit in b.occupancy.get((pi, slot.index), [])
                ]
                if not lits:
                    continue
                # HC3 already caps this sum at 1, so it is a genuine indicator.
                v = b.model.NewBoolVar(f"busy_s{section_id}_d{day}_t{slot.index}")
                b.model.Add(v == sum(lits))
                busy[slot.period_index] = v

            if not busy:
                continue

            load = b.model.NewIntVar(0, len(day_slots), f"load_s{section_id}_d{day}")
            b.model.Add(load == sum(busy.values()))

            has_class = b.model.NewBoolVar(f"has_s{section_id}_d{day}")
            b.model.Add(load >= 1).OnlyEnforceIf(has_class)
            b.model.Add(load == 0).OnlyEnforceIf(has_class.Not())

            first = b.model.NewIntVar(0, max_period, f"first_s{section_id}_d{day}")
            last = b.model.NewIntVar(0, max_period, f"last_s{section_id}_d{day}")
            for period, var in busy.items():
                b.model.Add(first <= period).OnlyEnforceIf(var)
                b.model.Add(last >= period).OnlyEnforceIf(var)

            span = b.model.NewIntVar(0, max_period + 1, f"span_s{section_id}_d{day}")
            b.model.Add(span == last - first + 1).OnlyEnforceIf(has_class)
            b.model.Add(span == 0).OnlyEnforceIf(has_class.Not())

            gaps = b.model.NewIntVar(0, max_period + 1, f"gaps_s{section_id}_d{day}")
            b.model.Add(gaps == span - load)
            terms.gaps[(section_id, day)] = gaps

            # Tier 1 Anti-Gap: Steep penalty for gaps >= 2 (excess beyond standard single-period rest)
            excess = b.model.NewIntVar(0, max_period + 1, f"xgap_s{section_id}_d{day}")
            b.model.Add(excess >= gaps - 1)
            b.model.Add(excess >= 0)
            terms.excess_gaps[(section_id, day)] = excess

            # Tier 2 Morning Anchor: prefer starting early in the day (P1-P3, periods 0..2)
            late = b.model.NewIntVar(0, max_period, f"late_s{section_id}_d{day}")
            b.model.Add(late >= first - 2).OnlyEnforceIf(has_class)
            b.model.Add(late == 0).OnlyEnforceIf(has_class.Not())
            b.model.Add(late >= 0)
            terms.late_starts[(section_id, day)] = late


def _add_spread_terms(b, terms, days, slots_by_day) -> None:
    """Soft constraint 2: stop a faculty member's week piling onto a few days.

    Total load per faculty is fixed by demand, so minimising their busiest day
    is exactly what spreads the rest out.
    """
    # (faculty_id, day) -> the reified "this faculty is busy here" literals.
    by_faculty_day: dict[tuple[int, int], list[cp_model.IntVar]] = {}
    slot_day = {s.index: s.day_index for s in b.inp.slots}
    for (_pi, faculty_id, slot_index), var in b.f_occ.items():
        by_faculty_day.setdefault((faculty_id, slot_day[slot_index]), []).append(var)

    faculty_ids = {f for (f, _d) in by_faculty_day}
    for faculty_id in sorted(faculty_ids):
        daily = []
        for day in days:
            lits = by_faculty_day.get((faculty_id, day), [])
            if not lits:
                continue
            load = b.model.NewIntVar(
                0, len(slots_by_day[day]), f"fload_f{faculty_id}_d{day}"
            )
            b.model.Add(load == sum(lits))
            daily.append(load)

        if not daily:
            continue
        peak = b.model.NewIntVar(0, len(b.inp.slots), f"peak_f{faculty_id}")
        b.model.AddMaxEquality(peak, daily)
        terms.peak_load[faculty_id] = peak

        # Redundant, but decisive. A faculty member teaching L periods across
        # n days must have a busiest day of at least L/n. That is mathematically
        # implied by AddMaxEquality, yet CP-SAT cannot derive it: the max of a
        # set of variables says nothing about their sum. Stating it explicitly
        # lifted the objective's lower bound from 18 to 48 on the seed dataset,
        # turning a 60s timeout that returned FEASIBLE into a 12s proven OPTIMAL.
        assigned_periods = [
            pair.periods * b.fac[(pi, faculty_id)]
            for pi, pair in enumerate(b.inp.pairs)
            if (pi, faculty_id) in b.fac
        ]
        if assigned_periods:
            b.model.Add(peak * len(days) >= sum(assigned_periods))


def _add_repeat_terms(b, terms, days) -> None:
    """Soft constraint 3: avoid the same subject twice in one day for a section.

    Counts *sessions*, not periods, so a 2-hour block is one session and never
    registers as a repeat.
    """
    slot_day = {s.index: s.day_index for s in b.inp.slots}

    for pi, pair in enumerate(b.inp.pairs):
        if pair.sessions < 2:
            continue  # a single session can never repeat
        for day in days:
            lits = [
                var
                for (p, _k, t), var in b.start.items()
                if p == pi and slot_day[t] == day
            ]
            if len(lits) < 2:
                continue
            repeat = b.model.NewIntVar(0, pair.sessions, f"rep_p{pi}_d{day}")
            b.model.Add(repeat >= sum(lits) - 1)
            terms.repeats[(pi, day)] = repeat


def _add_long_run_terms(b, terms, slots_by_day) -> None:
    """Soft constraint 4: prefer not to keep a teacher in class for hours.

    For every window of `limit + 1` consecutive periods, penalise the amount
    by which the teacher's occupancy exceeds `limit`. Summed over overlapping
    windows, a long run costs more the longer it gets, which is the shape the
    preference should have: five in a row is worse than four, and eight is
    much worse than five.

    This used to be a hard constraint. It was demoted because the rule the
    department actually confirmed is the free period, and a solver that
    refuses to produce any timetable rather than one with a fifth consecutive
    period is answering a question nobody asked.
    """
    limit = b.inp.faculty_soft_max_consecutive
    if limit <= 0:
        return

    windows: list[list[int]] = []
    for slots in slots_by_day.values():
        ordered = sorted(slots, key=lambda s: s.period_index)
        for i in range(len(ordered) - limit):
            window = ordered[i : i + limit + 1]
            if all(
                window[k + 1].period_index == window[k].period_index + 1
                for k in range(limit)
            ):
                windows.append([s.index for s in window])
    if not windows:
        return

    by_teacher_slot: dict[tuple[int, int], list[cp_model.IntVar]] = {}
    for (_pi, faculty_id, slot_index), var in b.f_occ.items():
        by_teacher_slot.setdefault((faculty_id, slot_index), []).append(var)
    teachers = sorted({f for (f, _t) in by_teacher_slot})

    for faculty_id in teachers:
        for w, window in enumerate(windows):
            lits = [v for t in window for v in by_teacher_slot.get((faculty_id, t), ())]
            if len(lits) <= limit:
                continue  # this teacher cannot fill the window anyway
            over = b.model.NewIntVar(0, len(window), f"run_f{faculty_id}_w{w}")
            b.model.Add(over >= sum(lits) - limit)
            terms.long_runs[(faculty_id, w, limit)] = over


def _block_of(room) -> str:
    return str(getattr(room, "block", "") or "").strip()


def _add_proximity_terms(b, terms) -> None:
    """Soft constraint 5: keep a section in as few, and as near, blocks as it can.

    A class keeps one room for the whole week (HC15), so "the same block as the
    section's other classes that day" and "the same block as the section's
    usual pattern" are one question here: which blocks the section's classes
    are in at all.

    Nearness is only measured where the data gives it - every block named by a
    number, so 32 and 34 are the neighbours of 33 and 38 is further. Blocks
    named any other way ("A", "Main") are only same-or-different: nothing in the
    room list says which buildings stand next to each other, and inventing a
    geography would make the preference confidently wrong.
    """
    rooms = b.inp.rooms
    families: dict[int, list[int]] = {}
    for pi, pair in enumerate(b.inp.pairs):
        root = b.inp.section_parent.get(pair.section_id, pair.section_id)
        families.setdefault(root, []).append(pi)

    blocks_in_play = {_block_of(rooms.get(rid)) for (_pi, rid) in b.room}
    numeric = bool(blocks_in_play) and all(blk.isdigit() for blk in blocks_in_play)

    for root, pair_indices in sorted(families.items()):
        by_block: dict[str, list[cp_model.IntVar]] = {}
        for pi in pair_indices:
            for rid in b.inp.pairs[pi].room_ids:
                var = b.room.get((pi, rid))
                if var is not None:
                    by_block.setdefault(_block_of(rooms.get(rid)), []).append(var)
        if len(by_block) <= 1:
            continue  # only one block is possible: nothing to prefer

        uses: dict[str, cp_model.IntVar] = {}
        for blk, chosen in sorted(by_block.items()):
            used = b.model.NewBoolVar(f"blk_s{root}_{blk}")
            for var in chosen:
                b.model.AddImplication(var, used)
            uses[blk] = used

        extra = b.model.NewIntVar(0, len(uses), f"xblk_s{root}")
        b.model.Add(extra >= sum(uses.values()) - 1)
        terms.extra_blocks[root] = extra

        if numeric:
            values = {blk: int(blk) for blk in uses}
            lo, hi = min(values.values()), max(values.values())
            highest = b.model.NewIntVar(lo, hi, f"blkmax_s{root}")
            lowest = b.model.NewIntVar(lo, hi, f"blkmin_s{root}")
            for blk, used in uses.items():
                b.model.Add(highest >= values[blk]).OnlyEnforceIf(used)
                b.model.Add(lowest <= values[blk]).OnlyEnforceIf(used)
            spread = b.model.NewIntVar(0, hi - lo, f"blkspan_s{root}")
            b.model.Add(spread >= highest - lowest)
            terms.block_spread[root] = spread


def score(solver: cp_model.CpSolver, terms: SoftTerms) -> dict[str, int]:
    """Read the penalty totals out of a solution, for reporting and tests."""
    return {
        "gaps": sum(solver.Value(v) for v in terms.gaps.values()),
        "peak_faculty_load": sum(solver.Value(v) for v in terms.peak_load.values()),
        "same_subject_repeats": sum(solver.Value(v) for v in terms.repeats.values()),
        "long_faculty_runs": sum(solver.Value(v) for v in terms.long_runs.values()),
        "room_block_spread": sum(solver.Value(v) for v in terms.extra_blocks.values())
        + sum(solver.Value(v) for v in terms.block_spread.values()),
    }
