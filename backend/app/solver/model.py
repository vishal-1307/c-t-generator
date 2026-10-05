"""The CP-SAT model: hard constraints only.

Variable design
---------------
"When" and "where"/"who" are decoupled into separate decisions, reified only
where a constraint actually needs their conjunction - the same pattern for
both room and faculty (Phase 5 unified this; faculty already worked this way
since Phase 3)::

    start[p, k, t]  == 1  <=>  session k of pair p begins at slot t
    room[p, r]      == 1  <=>  pair p uses room r (one per pair, for every session)
    fac[p, f]       == 1  <=>  pair p is taught by faculty f (one per pair)
    room_occ[p,r,t] == 1  <=>  (pair p occupies slot t) AND (pair p uses room r)
    f_occ[p,f,t]    == 1  <=>  (pair p occupies slot t) AND (pair p is taught by f)

Phase 5 profiling (``scripts/profile_solver.py``) found the previous design -
folding room directly into ``start``'s identity as a 4th dimension - made
``start`` the overwhelming majority of the CP-SAT model (12,000 of 14,500
total variables on a 4-section benchmark instance), because every legal
(session, start-slot) combination was multiplied by every candidate room.
Reifying room the same way faculty already was cut total variables by ~35% on
that instance and roughly halved measured solve time across multiple seeds,
with zero change to the feasible region - confirmed by an independent
hard-constraint re-derivation from the extracted solution, the same discipline
``tests/constraint_checker.py`` uses for the persisted path. See
TIMETABLE_LOGIC_SPEC.md's solver-scalability section for the full evidence.

Two of the hard constraints are enforced by the *shape* of the ``start``
domain rather than by any added constraint, which means the solver cannot
represent a violation at all:

* **HC16 (no lunch/break)** - lunch and section-blocked slots never appear in
  a legal start window (``data._legal_starts`` / the per-pair filter in
  ``data.load``).
* **HC12/HC13 (contiguous multi-hour blocks, one day)** - a block is one
  variable covering ``length`` consecutive same-day periods, so its periods
  cannot drift apart or straddle a day boundary.

More are enforced by pre-filtering the room/faculty domain in ``data.load``,
before any variable is created:

* **HC6** room capacity >= section strength
* **HC7** room type/lab-type compatibility (theory/lab/faculty, required_lab_type)
* **HC4** faculty eligible for the subject
* **HC5** section takes the subject (pairs are only built for mapped subjects)
* **HC18** persisted (semester-stable) faculty/room restrict the candidate set
* Room availability (HC9) and faculty availability (HC8) - both enforced by
  pinning the corresponding reified var to 0 for a blocked (resource, slot)
  combination, since both are pair-level decisions, not part of the per-slot
  start domain.

The remaining are explicit constraints: HC1 faculty clash, HC2 room clash,
HC3 section clash, HC14/HC15 one faculty / one room for every session of a pair.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from ..domain import LECTURE
from .types import Pair, SolverInput


@dataclass
class BuiltModel:
    model: cp_model.CpModel
    inp: SolverInput
    # start[(pair_index, session, start_slot)] -> BoolVar
    start: dict[tuple[int, int, int], cp_model.IntVar]
    # fac[(pair_index, faculty_id)] -> BoolVar
    fac: dict[tuple[int, int], cp_model.IntVar]
    # room[(pair_index, room_id)] -> BoolVar - the one room used for every
    # session of this pair (HC15).
    room: dict[tuple[int, int], cp_model.IntVar]
    # occupancy[(pair_index, slot_index)] -> list of literals covering that slot
    occupancy: dict[tuple[int, int], list[cp_model.IntVar]]
    # room_occ[(pair_index, room_id, slot_index)] -> BoolVar
    room_occ: dict[tuple[int, int, int], cp_model.IntVar] = field(default_factory=dict)
    # f_occ[(pair_index, faculty_id, slot_index)] -> BoolVar
    f_occ: dict[tuple[int, int, int], cp_model.IntVar] = field(default_factory=dict)
    # Per-pair "this pair is scheduled" literals, used by diagnostics.
    scheduled: dict[int, cp_model.IntVar] = field(default_factory=dict)
    # Diagnostic only: how many symmetry-breaking constraints were added
    # (Phase 5 experiment). 0 when room_symmetry_breaking=False.
    room_symmetry_constraints_added: int = 0


class UnschedulablePair(Exception):
    """A pair has no legal placement at all, so the model would be trivially
    infeasible. Raised with a human-readable reason rather than letting CP-SAT
    return a bare INFEASIBLE."""

    def __init__(self, pair: Pair, reason: str):
        self.pair = pair
        self.reason = reason
        super().__init__(f"{pair.label}: {reason}")


def build(
    inp: SolverInput,
    optional: bool = False,
    room_symmetry_breaking: bool = False,
    decision_strategy: str = "practicals_first",
) -> BuiltModel:
    """Construct the model.

    ``optional`` gates each pair's "place every session" constraint behind a
    literal, so diagnostics can ask "what is the largest subset that *is*
    schedulable?" instead of only ever hearing INFEASIBLE.

    ``room_symmetry_breaking`` (Phase 5 experiment, default off) adds ordering
    constraints between rooms that are 100% interchangeable *for this solve* -
    see ``_add_room_symmetry_breaking``.

    ``decision_strategy`` (Phase 5 experiment) selects which branching order
    CP-SAT is told to try first: ``"practicals_first"`` (default, unchanged
    from Phase 3/4), ``"scarcity_first"`` (most-constrained pairs first), or
    ``"none"`` (no explicit strategy - CP-SAT's own default search).
    """
    model = cp_model.CpModel()
    start: dict[tuple[int, int, int], cp_model.IntVar] = {}
    fac: dict[tuple[int, int], cp_model.IntVar] = {}
    room: dict[tuple[int, int], cp_model.IntVar] = {}
    occupancy: dict[tuple[int, int], list[cp_model.IntVar]] = {}
    scheduled: dict[int, cp_model.IntVar] = {}

    for pi, pair in enumerate(inp.pairs):
        if not pair.room_ids:
            raise UnschedulablePair(
                pair, "no eligible room (check type, capability, capacity and availability)"
            )
        if not pair.faculty_ids:
            raise UnschedulablePair(pair, "no faculty is mapped to teach this subject")
        if not pair.starts:
            raise UnschedulablePair(
                pair,
                f"no day offers {pair.length} consecutive non-lunch, non-blocked periods",
            )

        sched = model.NewBoolVar(f"sched_p{pi}")
        scheduled[pi] = sched
        if not optional:
            model.Add(sched == 1)

        # HC15: exactly one room for the whole pair, used by every session.
        room_lits = []
        for r in pair.room_ids:
            v = model.NewBoolVar(f"r_p{pi}_r{r}")
            room[(pi, r)] = v
            room_lits.append(v)
        model.Add(sum(room_lits) == 1).OnlyEnforceIf(sched)
        model.Add(sum(room_lits) == 0).OnlyEnforceIf(sched.Not())

        # Channelling var for symmetry breaking, below.
        slot_of: list[cp_model.IntVar] = []

        for k in range(pair.sessions):
            literals = []
            indexed: list[tuple[int, cp_model.IntVar]] = []
            for t in sorted(pair.starts):
                covered = pair.starts[t]
                var = model.NewBoolVar(f"s_p{pi}_k{k}_t{t}")
                start[(pi, k, t)] = var
                literals.append(var)
                indexed.append((t, var))
                for c in covered:
                    occupancy.setdefault((pi, c), []).append(var)

            # Exactly one placement per session - or none, if this pair is
            # switched off in optional mode.
            model.Add(sum(literals) == 1).OnlyEnforceIf(sched)
            model.Add(sum(literals) == 0).OnlyEnforceIf(sched.Not())

            # The chosen start index, so sessions can be ordered.
            idx = model.NewIntVar(0, len(inp.slots), f"idx_p{pi}_k{k}")
            model.Add(idx == sum(t * v for t, v in indexed)).OnlyEnforceIf(sched)
            model.Add(idx == 0).OnlyEnforceIf(sched.Not())
            slot_of.append(idx)

        # Symmetry breaking: sessions of one pair are interchangeable, so fix
        # them in increasing start order. Without this the solver explores
        # sessions!-many identical permutations of every solution.
        for a, b in zip(slot_of, slot_of[1:]):
            model.Add(a < b).OnlyEnforceIf(sched)

        # HC14: exactly one faculty per pair, drawn only from the mapping, and
        # the same one for every session that week.
        fac_lits = []
        for f in pair.faculty_ids:
            v = model.NewBoolVar(f"f_p{pi}_f{f}")
            fac[(pi, f)] = v
            fac_lits.append(v)
        model.Add(sum(fac_lits) == 1).OnlyEnforceIf(sched)
        model.Add(sum(fac_lits) == 0).OnlyEnforceIf(sched.Not())

    built = BuiltModel(
        model=model,
        inp=inp,
        start=start,
        fac=fac,
        room=room,
        occupancy=occupancy,
        scheduled=scheduled,
    )

    _add_section_clash(built)
    _add_room_clash(built)
    _add_faculty_clash(built)
    _add_theory_once_per_day(built)
    # After the faculty clash, which creates the per-teacher occupancy
    # literals this reads.
    _add_faculty_breaks(built)
    _add_max_consecutive(built)
    if room_symmetry_breaking:
        _add_room_symmetry_breaking(built)
    _add_search_strategy(built, decision_strategy)
    return built


def _add_search_strategy(b: BuiltModel, decision_strategy: str) -> None:
    """Tell CP-SAT which literals to branch on first (Phase 5 §9/§B).

    This only influences search *order* - it can never make a hard constraint
    optional or remove a candidate, so no variant here can change what is
    feasible, only how fast the solver finds out.
    """
    if decision_strategy == "none":
        return

    if decision_strategy == "practicals_first":
        # Labs are typically the more contended resource once room pooling is
        # in effect. Deciding the scarce ones first prunes earlier. Measured a
        # small but consistent gain at 20-30 sections in the earlier
        # (home-room) model; re-benchmarked after the room-pool rework in
        # Phase 5 (see TIMETABLE_LOGIC_SPEC.md).
        practicals = [
            var
            for (pi, _k, _t), var in sorted(b.start.items())
            if b.inp.pairs[pi].subject_type == "practical"
        ]
        if practicals:
            b.model.AddDecisionStrategy(
                practicals, cp_model.CHOOSE_FIRST, cp_model.SELECT_MAX_VALUE
            )
        return

    if decision_strategy == "scarcity_first":
        # Most-constrained-pair-first: order every pair by how few rooms, then
        # how few faculty, it has to choose from (practicals as a tiebreak),
        # and branch on that pair's start variables before a less-constrained
        # pair's. The intuition: a pair with 1 eligible room and 1 eligible
        # faculty has almost no slack, so deciding it early lets CP-SAT's
        # propagation rule out the rest of that room's/faculty's slots for
        # every other pair immediately, instead of discovering the conflict
        # deep inside an unrelated branch.
        order = sorted(
            range(len(b.inp.pairs)),
            key=lambda pi: (
                len(b.inp.pairs[pi].room_ids),
                len(b.inp.pairs[pi].faculty_ids),
                0 if b.inp.pairs[pi].subject_type == "practical" else 1,
            ),
        )
        by_pair: dict[int, list[cp_model.IntVar]] = {}
        for (pi, _k, _t), var in sorted(b.start.items()):
            by_pair.setdefault(pi, []).append(var)

        ordered_vars = [v for pi in order for v in by_pair.get(pi, [])]
        if ordered_vars:
            b.model.AddDecisionStrategy(
                ordered_vars, cp_model.CHOOSE_FIRST, cp_model.SELECT_MAX_VALUE
            )
        return

    raise ValueError(f"unknown decision_strategy {decision_strategy!r}")


def _add_room_symmetry_breaking(b: BuiltModel) -> None:
    """Phase 5 experiment: break permutation symmetry among rooms that are
    100% interchangeable *for this specific solve*.

    Two rooms are grouped only if they share every scheduling-relevant
    property (type, lab_type, capacity, and identical blocked-slot pattern)
    AND are offered to *exactly* the same set of pairs. That last condition is
    what makes this safe in general, not just in the common case: an explicit
    pin (``fixed_room_id``/``fixed_subject_id``) can make two rooms that look
    identical NOT interchangeable, because only one of them is actually
    offered to a given pair. Grouping by the literal pair-set a room is
    offered to - rather than assuming "same properties implies same pairs" -
    means a pinned room is automatically excluded from any class it is not
    genuinely symmetric with, with no special-casing required.

    Within each class, sorted by room id, add ``usage(r_i) >= usage(r_{i+1})``
    between consecutive rooms. This does not shrink the feasible region: any
    feasible solution can be relabelled by permuting equivalent rooms (that
    permutation is by definition a symmetry of the problem) to satisfy the
    ordering, so an optimal/feasible solution still exists under it - the
    constraint only picks one representative labelling per symmetry class
    instead of leaving CP-SAT to rediscover that the labellings are
    equivalent by search.

    Phase 5 measured this as a small, non-harmful effect on its own (CP-SAT's
    built-in automatic symmetry detection already covers much of the same
    ground) - kept as an available, off-by-default toggle rather than the
    primary win of this phase.
    """
    pairs_by_room: dict[int, set[int]] = {}
    for (pi, r) in b.room:
        pairs_by_room.setdefault(r, set()).add(pi)
    pairs_by_room_frozen = {r: frozenset(pis) for r, pis in pairs_by_room.items()}

    classes: dict[tuple, list[int]] = {}
    for r, room_obj in b.inp.rooms.items():
        if r not in pairs_by_room_frozen:
            continue  # never a candidate for any pair - nothing to break
        blocked = frozenset(b.inp.blocked_room_slots.get(r, ()))
        key = (
            room_obj.room_type,
            room_obj.lab_type,
            room_obj.capacity,
            blocked,
            pairs_by_room_frozen[r],
        )
        classes.setdefault(key, []).append(r)

    added = 0
    for room_ids in classes.values():
        if len(room_ids) < 2:
            continue
        room_ids = sorted(room_ids)
        pis = pairs_by_room_frozen[room_ids[0]]
        for r_lo, r_hi in zip(room_ids, room_ids[1:]):
            usage_lo = sum(b.room[(pi, r_lo)] for pi in pis)
            usage_hi = sum(b.room[(pi, r_hi)] for pi in pis)
            b.model.Add(usage_lo >= usage_hi)
            added += 1
    b.room_symmetry_constraints_added = added


def _add_section_clash(b: BuiltModel) -> None:
    """HC3: a group of students cannot be in two places at once.

    The unit is the student body, not the section row. Usually those are the
    same thing. They stop being the same when a section is split into lab
    groups: 24011 and 24012 are each half of 2401, so a lecture for 2401 and a
    lab for 24011 are the same students and cannot overlap, while the two
    groups' labs are different students and *should* be free to run at once -
    running them in parallel is the entire reason for splitting.

    So the constraint is pairwise between a parent and each child, never across
    a whole family. A single clash set per family would forbid the sibling
    overlap and quietly cost the timetable the thing groups exist for.
    """
    by_section: dict[int, list[int]] = {}
    for pi, pair in enumerate(b.inp.pairs):
        by_section.setdefault(pair.section_id, []).append(pi)

    children = b.inp.section_children

    # Iterate the union of "has obligations" and "has children": a parent whose
    # own subjects are all taught to its groups still has to be kept apart from
    # them, and iterating only `by_section` would skip that family entirely.
    clash_sets: list[frozenset[int]] = []
    for section_id in {*by_section, *children}:
        kids = children.get(section_id, ())
        if kids:
            clash_sets.extend(frozenset((section_id, kid)) for kid in kids)
        else:
            clash_sets.append(frozenset((section_id,)))

    for group in dict.fromkeys(clash_sets):  # de-dup, order preserved
        for slot in b.inp.slots:
            lits = [
                lit
                for sid in group
                for pi in by_section.get(sid, ())
                for lit in b.occupancy.get((pi, slot.index), [])
            ]
            if len(lits) > 1:
                b.model.Add(sum(lits) <= 1)


def _add_room_clash(b: BuiltModel) -> None:
    """HC2: a room cannot host two classes at once. HC9: a room cannot be used
    during its own blocked slots.

    ``occupancy`` says *whether pair p is running* at slot t; ``room`` says
    *which room pair p uses*. The clash is about the conjunction, so it needs
    reifying: room_occ[p,r,t] <=> (pair p occupies t) AND (pair p uses room r).
    Same pattern as the faculty clash below - Phase 5 unified the two
    (previously room was folded directly into the ``start`` variable's
    identity instead; see the module docstring for the measured effect of
    this change).
    """
    for pi, pair in enumerate(b.inp.pairs):
        for r in pair.room_ids:
            room_var = b.room[(pi, r)]
            blocked = b.inp.blocked_room_slots.get(r, ())
            for slot in b.inp.slots:
                lits = b.occupancy.get((pi, slot.index), [])
                if not lits:
                    continue
                occ = sum(lits)
                v = b.model.NewBoolVar(f"ro_p{pi}_r{r}_t{slot.index}")
                b.room_occ[(pi, r, slot.index)] = v
                # v <= occ, v <= room, v >= occ + room - 1
                b.model.Add(v <= occ)
                b.model.Add(v <= room_var)
                b.model.Add(v >= occ + room_var - 1)
                if slot.index in blocked:
                    b.model.Add(v == 0)

    by_room: dict[tuple[int, int], list[cp_model.IntVar]] = {}
    for (pi, r, slot_index), var in b.room_occ.items():
        by_room.setdefault((r, slot_index), []).append(var)

    for lits in by_room.values():
        if len(lits) > 1:
            b.model.Add(sum(lits) <= 1)


def _add_faculty_clash(b: BuiltModel) -> None:
    """HC1: a faculty member cannot be in two places at once. HC8: a faculty
    member cannot be scheduled during their own blocked slots.

    ``occupancy`` says *whether pair p is running* at slot t; ``fac`` says *who
    teaches pair p*. The clash is about the conjunction, so it needs reifying:
    f_occ[p,f,t] <=> (pair p occupies t) AND (faculty f teaches p).
    """
    for pi, pair in enumerate(b.inp.pairs):
        for f in pair.faculty_ids:
            fac_var = b.fac[(pi, f)]
            blocked = b.inp.blocked_faculty_slots.get(f, ())
            for slot in b.inp.slots:
                lits = b.occupancy.get((pi, slot.index), [])
                if not lits:
                    continue
                occ = sum(lits)
                v = b.model.NewBoolVar(f"fo_p{pi}_f{f}_t{slot.index}")
                b.f_occ[(pi, f, slot.index)] = v
                # v <= occ, v <= fac, v >= occ + fac - 1
                b.model.Add(v <= occ)
                b.model.Add(v <= fac_var)
                b.model.Add(v >= occ + fac_var - 1)
                if slot.index in blocked:
                    b.model.Add(v == 0)

    by_faculty: dict[tuple[int, int], list[cp_model.IntVar]] = {}
    for (pi, f, slot_index), var in b.f_occ.items():
        by_faculty.setdefault((f, slot_index), []).append(var)

    for lits in by_faculty.values():
        if len(lits) > 1:
            b.model.Add(sum(lits) <= 1)


def _add_theory_once_per_day(b: BuiltModel) -> None:
    """A theory subject meets a section at most once a day.

    Counted in *sessions*, not periods. "Duration" is how long one class runs,
    so a two-period lecture is one class and is allowed; two separate lectures
    of the same subject on one day are not. Different theory subjects on the
    same day are unaffected - the limit is per (section, subject).

    Only the lecture component is limited. A practical may meet two or three
    times in a day when its demand needs it, and a mixed subject's practical
    keeps that freedom while its lecture does not - which is why this keys on
    the pair's component rather than on the subject's type.

    With sessions ordered by start (see `build`), this is one linear
    constraint per (pair, day): of all the start literals that begin a session
    on that day, at most one may be true.
    """
    limited = {
        pi for pi, pair in enumerate(b.inp.pairs)
        if pair.session_type == LECTURE and pair.sessions >= 2
    }
    if not limited:
        return
    days: dict[int, int] = {s.index: s.day_index for s in b.inp.slots}

    # One pass over the start literals, not one per pair: at production size
    # there are hundreds of thousands of them.
    by_pair_day: dict[tuple[int, int], list] = {}
    for (pi, _k, t), var in b.start.items():
        if pi in limited:
            by_pair_day.setdefault((pi, days[t]), []).append(var)
    for lits in by_pair_day.values():
        if len(lits) > 1:
            b.model.Add(sum(lits) <= 1)


def _add_faculty_breaks(b: BuiltModel) -> None:
    """Every teacher gets a free period on any day they teach.

    This is the faculty break, and it is the whole of it: there is no common
    lunch, so instead nobody's day is filled end to end, and each teacher's
    break lands wherever their own day puts it.

    Stated per teacher-day as "at most one period fewer than the day has". A
    teacher occupies a period through exactly one pair at a time (the faculty
    clash guarantees it), so the sum of their occupancy literals over a day
    *is* how many periods they teach that day. A teacher with nothing on that
    day satisfies it at zero, which is what "on any day they teach" means.

    A lunch period, where a grid has one, is never taught and so is a free
    period in its own right - the same reading validation uses.

    How long a run may be is a separate matter: `_add_max_consecutive` caps
    it, and the objective's `faculty_soft_max_consecutive` prefers it short.

    Built from `f_occ`, which the faculty clash has already created, so this
    adds constraints but no variables.
    """
    by_day: dict[int, list] = {}
    for slot in b.inp.slots:
        by_day.setdefault(slot.day_index, []).append(slot)

    # f_occ keyed by (faculty, slot) -> literals, one per pair that faculty
    # could be teaching then.
    by_teacher_slot: dict[tuple[int, int], list] = {}
    for (_pi, f, t), var in b.f_occ.items():
        by_teacher_slot.setdefault((f, t), []).append(var)
    teachers = {f for (f, _t) in by_teacher_slot}

    for f in teachers:
        for slots in by_day.values():
            lits = [v for s in slots for v in by_teacher_slot.get((f, s.index), ())]
            # A teacher who could not fill the day anyway needs no constraint.
            if len(lits) >= len(slots):
                b.model.Add(sum(lits) <= len(slots) - 1)


def _add_max_consecutive(b: BuiltModel) -> None:
    """No teacher teaches more than `faculty_max_consecutive` periods in a row.

    For every window of cap + 1 back-to-back periods in a day, a teacher is in
    at most cap of them - which is exactly "no run longer than cap". A lunch
    inside a window is never taught, so it breaks a run by itself.

    Like the break, built from `f_occ`: constraints, no variables. A teacher
    who could not fill a window anyway gets no constraint for it.
    """
    cap = b.inp.faculty_max_consecutive
    if cap <= 0:
        return

    windows: list[list[int]] = []
    by_day: dict[int, list] = {}
    for slot in b.inp.slots:
        by_day.setdefault(slot.day_index, []).append(slot)
    for slots in by_day.values():
        ordered = sorted(slots, key=lambda s: s.period_index)
        for i in range(len(ordered) - cap):
            window = ordered[i : i + cap + 1]
            if all(
                window[k + 1].period_index == window[k].period_index + 1
                for k in range(cap)
            ):
                windows.append([s.index for s in window])
    if not windows:
        return

    by_teacher_slot: dict[tuple[int, int], list] = {}
    for (_pi, f, t), var in b.f_occ.items():
        by_teacher_slot.setdefault((f, t), []).append(var)
    teachers = {f for (f, _t) in by_teacher_slot}

    for f in teachers:
        for window in windows:
            lits = [v for t in window for v in by_teacher_slot.get((f, t), ())]
            if len(lits) > cap:
                b.model.Add(sum(lits) <= cap)
