"""Pre-solve validation - the single source of truth for "can this be solved?".

Every rule the CP-SAT model relies on is checked here, in plain Python, before
the solver ever runs. Two consumers share it:

* ``GET /api/validate`` - powers the readiness dashboard.
* ``solver/diagnostics.py`` layer A - runs the same checks and reports
  blockers instead of a bare INFEASIBLE.

Room eligibility reuses ``solver.data._eligible_rooms`` rather than
re-implementing the type/lab-type/capacity/pin resolution a second time - one
implementation, so this can never silently drift from what the solver actually
does.

Validation is scoped to one academic context (spec #5): a timetable for
2026-27/Sem-1 must never be judged against data that only exists for a
different semester or program.

A **blocker** makes a valid timetable impossible. A **warning** is suspicious
data that still permits a solution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

from sqlalchemy.orm import Session

from .models import (
    AcademicContext,
    Faculty,
    FacultyUnavailability,
    Room,
    RoomUnavailability,
    Section,
    SectionSubjectAssignment,
    SectionUnavailability,
    Subject,
    TimeSlot,
)
from .cross_context import find_cross_context_clashes
from .domain import (
    LECTURE,
    PRACTICAL,
    periods_per_week,
    room_kind_for,
    session_specs,
)
from .config import settings
from .grid import is_working_day
from .room_scope import usable_rooms
from .solver.data import _eligible_rooms

# A practical that names no lab type: it can use any lab, so it competes with
# every specialised pool rather than having one of its own.
ANY_LAB = "(unspecified)"


@dataclass
class Check:
    """One readiness rule, with the evidence behind its verdict."""

    id: str
    label: str
    ok: bool
    detail: str
    severity: str = "blocker"  # blocker | warning
    offenders: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    ready: bool
    blocker_count: int
    warning_count: int
    checks: list[Check]
    stats: dict[str, int]


def _contiguous_windows(slots: list[TimeSlot], length: int) -> int:
    """How many valid start positions exist for a block of ``length`` periods.

    Valid means: ``length`` consecutive period_index values on one day, all
    present, none marked lunch. This is exactly the domain the CP-SAT model will
    build its start variables over, so if this returns 0 the subject is
    unschedulable no matter what else is true.
    """
    by_day: dict[int, dict[int, TimeSlot]] = {}
    for s in slots:
        by_day.setdefault(s.day_index, {})[s.period_index] = s

    total = 0
    for periods in by_day.values():
        for p, slot in periods.items():
            block = [periods.get(p + k) for k in range(length)]
            if all(b is not None and not b.is_lunch for b in block):
                total += 1
    return total


def _why_not_usable(
    kind: str,
    usable: set[int],
    lab_rooms: list[Room],
    taken_subjects: list[Subject],
    taken: dict[int, list[Section]],
) -> str:
    """Why the labs of this type that exist still cannot take these practicals.

    Without this, a shortage reads as "build another lab", and someone builds
    one that turns out to be just as unusable as the ones already standing. A
    lab of the right kind is refused for a reason - it seats too few students,
    or the practical needs students' own devices and power and the room has
    neither - and naming the reason is the difference between advice and a
    number.
    """
    candidates = [
        r for r in lab_rooms if (r.lab_type or ANY_LAB) == kind and r.id not in usable
    ]
    if not candidates:
        return ""

    subs = [s for s in taken_subjects if (s.required_lab_type or ANY_LAB) == kind]
    needs_byod = any(s.byod_required for s in subs)
    needs_charging = any(s.charging_required for s in subs)
    largest = max(
        (sec.strength for s in subs for sec in taken.get(s.id, [])), default=0
    )

    too_small = sum(1 for r in candidates if r.capacity < largest)
    no_byod = sum(1 for r in candidates if needs_byod and not r.byod)
    no_power = sum(1 for r in candidates if needs_charging and not r.charging)

    reasons = []
    if too_small:
        reasons.append(f"{too_small} seat(s) fewer than {largest} students")
    if no_byod:
        reasons.append(f"{no_byod} allow no personal devices")
    if no_power:
        reasons.append(f"{no_power} have no charging")
    if not reasons:
        return ""
    return (
        f"{len(candidates)} other {kind} lab(s) exist but cannot be used: "
        + ", ".join(reasons)
    )


def _agree(items: list[str], singular: str, plural: str) -> str:
    """Join offenders with a verb that agrees in number."""
    return f"{', '.join(items)} {singular if len(items) == 1 else plural}"


def validate(db: Session, academic_context_id: int | None = None) -> ValidationReport:
    """Run every pre-solve check.

    ``academic_context_id`` scopes Sections (and therefore the pairs derived
    from them) to one semester's offering. Rooms, Faculty and the Subject
    catalog are institution-wide and are never scoped. Passing ``None``
    validates every section in the database at once - useful for a
    whole-database health check, but not what the solver ever does.
    """
    # Full inventory, same as `rooms` always was - stats reflect everything
    # on file, active or not. Scheduling determination below uses the
    # *_active sublists, matching solver/data.py::load() exactly so this
    # check can never disagree with what generation actually does.
    faculty = db.query(Faculty).all()
    subjects = db.query(Subject).all()
    # Scoped to the intake's own room list when it has one - the same pool
    # the solver draws from, or this could approve what generation refuses.
    rooms = usable_rooms(db, academic_context_id)
    section_query = db.query(Section)
    if academic_context_id is not None:
        section_query = section_query.filter(
            Section.academic_context_id == academic_context_id
        )
    sections = section_query.all()
    all_slots = db.query(TimeSlot).all()
    # Only the working week is ever scheduled - the solver drops every other
    # day before it starts, so this has to as well or the counts disagree.
    slots = [s for s in all_slots if is_working_day(s.day)]
    off_days = sorted(
        {s.day for s in all_slots if not is_working_day(s.day)},
        key=lambda d: min(x.day_index for x in all_slots if x.day == d),
    )

    faculty_active = [f for f in faculty if f.is_active]
    subjects_active = [s for s in subjects if s.is_active]
    sections_active = [s for s in sections if s.is_active]

    teachable = [s for s in slots if not s.is_lunch]
    n_teachable = len(teachable)

    active_rooms = [r for r in rooms if r.is_active and r.room_type != "faculty"]
    theory_rooms = [r for r in active_rooms if r.room_type == "theory"]
    lab_rooms = [r for r in active_rooms if r.room_type == "lab"]

    # Only subjects some section actually takes need to be schedulable. A
    # subject nobody takes is inert, and flagging it produced false reds.
    # Restricted to active sections/subjects - an inactive one generates no
    # pair at all (solver/data.py::load() skips it identically).
    taken: dict[int, list[Section]] = {}
    for sec in sections_active:
        for sub in sec.subjects:
            if not sub.is_active:
                continue
            taken.setdefault(sub.id, []).append(sec)
    taken_subjects = [s for s in subjects_active if s.id in taken]

    # Eligible-room pool per (section, subject) pair, reusing the solver's own
    # resolution so this check can never disagree with what generation does.
    # Keyed per component: a subject's lecture and its practical need different
    # kinds of room, so "this subject has an eligible room" is two questions
    # once a subject can have both.
    eligible_by_pair: dict[tuple[int, int, str], list[Room]] = {}
    room_index = {r.id: r for r in rooms}
    for sec in sections_active:
        for sub in sec.subjects:
            if not sub.is_active:
                continue
            for spec in session_specs(sub, sec):
                if spec.count <= 0:
                    continue
                eligible_by_pair[(sec.id, sub.id, spec.session_type)] = _eligible_rooms(
                    sub, sec, room_index, spec.session_type
                )

    checks: list[Check] = []

    def add(
        id: str,
        label: str,
        ok: bool,
        ok_detail: str,
        bad_detail: str,
        offenders: list[str] | None = None,
        severity: str = "blocker",
    ) -> None:
        checks.append(
            Check(
                id=id,
                label=label,
                ok=ok,
                detail=ok_detail if ok else bad_detail,
                severity=severity,
                offenders=offenders or [],
            )
        )

    # -------------------------------------------------------- academic context
    if academic_context_id is not None:
        ctx = db.get(AcademicContext, academic_context_id)
        add(
            "context_exists",
            "Academic context exists",
            ctx is not None,
            ctx.label if ctx else "",
            f"Academic context #{academic_context_id} not found",
        )

    # ---------------------------------------------------------------- grid
    add(
        "grid_exists",
        "Time grid generated",
        n_teachable > 0,
        f"{len(slots)} slots, {n_teachable} teachable",
        "No teachable time slots - generate the weekly grid",
    )

    # -------------------------------------------------------------- sections
    no_curriculum = [
        s.section_number for s in sections_active
        if not any(sub.is_active for sub in s.subjects)
    ]
    add(
        "sections_have_curriculum",
        "Sections have a curriculum",
        bool(sections_active) and not no_curriculum,
        "All sections have subjects",
        "No active sections defined yet"
        if not sections_active
        else _agree(no_curriculum, "has", "have") + " no subjects",
        no_curriculum,
    )

    # ----------------------------------------------------------- lab groups
    #
    # A group is a section with `parent_section_id` set, and the only thing
    # that makes it special is that its students are also the parent's. Three
    # ways that can be stated wrongly, and each fails differently: a parent in
    # another context is never loaded so the clash rule silently does nothing;
    # a group of a group is a hierarchy the rule only walks one level of; and
    # groups whose strengths do not add up to the parent's mean somebody is
    # unaccounted for. The first two are blockers because they produce a
    # timetable that looks right and double-books students. The third is a
    # warning, because uneven or partial groups are legitimate and only the
    # department knows.
    by_id = {s.id: s for s in sections_active}
    cross_context = [
        s.section_number
        for s in sections_active
        if s.parent_section_id is not None and s.parent_section_id not in by_id
    ]
    add(
        "group_parent_in_same_context",
        "Every lab group's section is scheduled with it",
        not cross_context,
        "Group sections sit with their parent",
        _agree(cross_context, "is a lab group", "are lab groups")
        + " whose section is not in this context, so nothing would stop them"
        " being taught at the same time",
        cross_context,
    )

    too_deep = [
        s.section_number
        for s in sections_active
        if s.parent_section_id is not None
        and by_id.get(s.parent_section_id) is not None
        and by_id[s.parent_section_id].parent_section_id is not None
    ]
    add(
        "group_depth_is_one",
        "Lab groups are one level deep",
        not too_deep,
        "No group is a group of a group",
        _agree(too_deep, "is a group of a group", "are groups of groups")
        + ", which is one level more than clash checking understands",
        too_deep,
    )

    mismatched: list[str] = []
    for parent in sections_active:
        kids = [s for s in sections_active if s.parent_section_id == parent.id]
        if not kids:
            continue
        total = sum(k.strength for k in kids)
        if total != parent.strength:
            mismatched.append(
                f"{parent.section_number} ({parent.strength}) vs "
                f"{len(kids)} group(s) totalling {total}"
            )
    add(
        "group_strengths_add_up",
        "Lab group sizes match their section",
        not mismatched,
        "Group strengths add up",
        _agree(mismatched, "section does", "sections do")
        + " not match the total of its groups - either a group is missing or a"
        " strength is out of date",
        mismatched,
        severity="warning",
    )

    # -------------------------------------------------------------- faculty
    unmapped = [s.code for s in taken_subjects if not any(f.is_active for f in s.faculties)]
    add(
        "subjects_have_faculty",
        "Every scheduled subject has a qualified faculty",
        bool(taken_subjects) and not unmapped,
        "All scheduled subjects mapped",
        "No section takes any subject yet"
        if not taken_subjects
        else _agree(unmapped, "has", "have") + " no faculty mapped",
        unmapped,
    )

    # A faculty who is the only person able to teach a set of subjects inherits
    # the whole demand for those subjects across every section taking them,
    # minus whatever slots they have declared unavailable.
    blocked_by_faculty: dict[int, int] = {}
    for row in db.query(FacultyUnavailability).all():
        blocked_by_faculty[row.faculty_id] = blocked_by_faculty.get(row.faculty_id, 0) + 1

    blocked_slots_by_faculty: dict[int, set[int]] = {}
    for row in db.query(FacultyUnavailability).all():
        blocked_slots_by_faculty.setdefault(row.faculty_id, set()).add(row.timeslot_id)

    sole_overload: list[str] = []
    no_break: list[str] = []
    for f in faculty_active:
        load = 0
        owned: list[str] = []
        for sub in f.subjects:
            active_teachers = [t for t in sub.faculties if t.is_active]
            if len(active_teachers) == 1 and sub.id in taken:
                n = len(taken[sub.id])
                load += sum(periods_per_week(sub, sec) for sec in taken[sub.id])
                owned.append(f"{sub.code}x{n}")
        capacity = n_teachable - blocked_by_faculty.get(f.id, 0)
        if capacity > 0 and load > capacity:
            sole_overload.append(
                f"{f.name} is sole teacher of {', '.join(owned)} = {load} periods > "
                f"{capacity} available slots"
            )
        elif load:
            ceiling = _most_periods_with_breaks(
                slots, blocked_slots_by_faculty.get(f.id, set()),
                cap=max(0, settings.faculty_max_consecutive),
            )
            if load > ceiling:
                no_break.append(
                    f"{f.name} ({f.faculty_code}) must teach {load} periods a week, "
                    f"but with a free period each day and no more than "
                    f"{settings.faculty_max_consecutive or 'unlimited'} in a row there "
                    f"is room for at most {ceiling}"
                )
    add(
        "faculty_not_overloaded",
        "No faculty is single-handedly overloaded",
        not sole_overload,
        "Sole-teacher loads fit",
        "; ".join(sole_overload),
        sole_overload,
    )

    # The faculty break: every teacher gets a free period on any day they
    # teach, so a day of n periods holds at most n - 1 of teaching; and no run
    # is longer than the cap, so a day also holds at most n - floor(n/(cap+1)).
    # A load that fits the week can still not fit those - and a single session
    # as long as the whole day, or longer than the cap, can never fit at all.
    cap = max(0, settings.faculty_max_consecutive)
    longest_day = max(
        (sum(1 for s2 in slots if s2.day_index == d) for d in {s1.day_index for s1 in slots}),
        default=0,
    )
    for sub in taken_subjects:
        for spec in session_specs(sub):
            if spec.count > 0 and longest_day and spec.length >= longest_day:
                no_break.append(
                    f"{sub.code} runs {spec.length} periods in one session, which "
                    f"fills a {longest_day}-period day and leaves its teacher no "
                    f"free period"
                )
            elif spec.count > 0 and cap and spec.length > cap:
                no_break.append(
                    f"{sub.code} runs {spec.length} periods in one session, longer "
                    f"than the {cap} periods in a row a teacher may teach"
                )
    add(
        "faculty_breaks_fit",
        "Every teacher can have a break",
        not no_break,
        "Every teacher can have a free period on the days they teach",
        "; ".join(no_break),
        no_break,
    )

    # ------------------------------------------------------- room eligibility
    def _no_room_detail(sec: Section, sub: Subject, session_type: str) -> str:
        """Name the specific shortfall when a pair has zero eligible rooms.

        If a room of the right type/lab-type exists but is too small, name
        both numbers (needed vs. the best available) rather than a generic
        "no match" - that is the actionable version of this message.
        """
        wanted = room_kind_for(sub, session_type)
        same_kind = [
            r for r in active_rooms
            if (
                r.room_type == "theory" if wanted == "theory"
                else r.room_type == "lab" and (
                    sub.required_lab_type is None or r.lab_type == sub.required_lab_type
                )
            )
        ]
        component = ""
        if sub.type == "mixed":
            component = " lecture" if session_type == LECTURE else " practical"

        if not same_kind:
            kind = "teaching room" if wanted == "theory" else (
                f"{sub.required_lab_type} lab" if sub.required_lab_type else "lab"
            )
            return (
                f"{sub.code}{component} ({sec.section_number}) needs a {kind} and "
                "there are none"
            )

        big_enough = [r for r in same_kind if r.capacity >= sec.strength]
        if not big_enough:
            best = max(r.capacity for r in same_kind)
            return (
                f"{sub.code}{component} ({sec.section_number}) needs capacity "
                f"{sec.strength}, best available room has only {best}"
            )

        # The room exists and is big enough, so what excluded it is a
        # capability. Naming which one turns "no eligible room" into something
        # a person can act on.
        missing: list[str] = []
        if session_type == PRACTICAL and getattr(sub, "byod_required", False):
            if not any(getattr(r, "byod", False) for r in big_enough):
                missing.append("student devices (BYOD)")
        if session_type == PRACTICAL and getattr(sub, "charging_required", False):
            if not any(getattr(r, "charging", False) for r in big_enough):
                missing.append("power at seats")
        if missing:
            return (
                f"{sub.code}{component} ({sec.section_number}) needs "
                f"{' and '.join(missing)}; no room of the right kind and size "
                "has it"
            )

        return (
            f"{sub.code}{component} ({sec.section_number}) has no room matching "
            "type, capability and capacity together"
        )

    # Indexed rather than scanned: this ran a linear search over sections and
    # subjects for every pair, which is quadratic once a real catalogue exists.
    section_by_id = {s.id: s for s in sections}
    subject_by_id = {s.id: s for s in subjects}
    no_room = [
        _no_room_detail(section_by_id[sid], subject_by_id[subid], session_type)
        for (sid, subid, session_type), rms in eligible_by_pair.items()
        if not rms
    ]
    add(
        "pairs_have_eligible_room",
        "Every section-subject has an eligible room",
        not no_room,
        "All pairs have a compatible room",
        "; ".join(no_room),
        no_room,
    )

    # A mixed subject's lecture needs a teaching room just as a theory
    # subject's does, so this asks which components exist, not which subjects.
    wants_teaching_room = any(
        spec.session_type == LECTURE and spec.count > 0
        for sub in taken_subjects
        for spec in session_specs(sub)
    )
    add(
        "theory_rooms_exist",
        "At least one active theory room exists",
        not wants_teaching_room or bool(theory_rooms),
        f"{len(theory_rooms)} active theory room(s)"
        if theory_rooms
        else "No theory subjects scheduled",
        "No active theory rooms exist, but theory subjects are scheduled",
    )

    # Aggregate lab pressure: total practical periods demanded vs total
    # lab-periods the eligible labs can offer. This is the check that catches
    # "not enough lab rooms for this many practical sections".
    #
    # Counted per component, so a mixed subject's practical is included. It
    # was not before, because the subject's type is 'mixed' rather than
    # 'practical' - which would have understated demand for exactly the
    # subjects most likely to need a lab.
    # Grouped by required lab type, because the pools are not interchangeable:
    # the solver matches lab_type exactly, so a spare programming lab does
    # nothing for a cybersecurity practical. Summed across types, a shortage of
    # one kind hides behind a surplus of another, and the advice a total gives
    # ("find more lab-periods") sends someone to add the wrong room.
    lab_demand = 0
    usable_labs: set[int] = set()
    demand_by_type: dict[str, int] = {}
    labs_by_type: dict[str, set[int]] = {}
    for sub in taken_subjects:
        kind = sub.required_lab_type or ANY_LAB
        for sec in taken[sub.id]:
            for spec in session_specs(sub, sec):
                if spec.session_type != PRACTICAL or spec.count <= 0:
                    continue
                lab_demand += spec.periods
                demand_by_type[kind] = demand_by_type.get(kind, 0) + spec.periods
                eligible = {
                    r.id
                    for r in eligible_by_pair.get((sec.id, sub.id, PRACTICAL), [])
                }
                usable_labs.update(eligible)
                labs_by_type.setdefault(kind, set()).update(eligible)

    # A lab blocked for a period cannot host a practical then, so it supplies
    # only the teachable periods it is not blocked for.
    teachable_ids = {s.id for s in teachable}
    room_blocked: dict[int, int] = {}
    for u in db.query(RoomUnavailability).all():
        if u.timeslot_id in teachable_ids:
            room_blocked[u.room_id] = room_blocked.get(u.room_id, 0) + 1

    def room_periods(room_ids) -> int:
        return sum(max(0, n_teachable - room_blocked.get(r, 0)) for r in room_ids)

    # A subject that names no lab type can use any lab, so it competes with
    # every other kind. Counting it against its own pool would let two groups
    # claim the same room, so its demand is added to every type it could land
    # in - deliberately pessimistic, and stated as such in the message.
    shortages: list[str] = []
    for kind, demand in sorted(demand_by_type.items()):
        supply = room_periods(labs_by_type.get(kind, set()))
        shared = demand_by_type.get(ANY_LAB, 0) if kind != ANY_LAB else 0
        if demand + shared > supply:
            usable = labs_by_type.get(kind, set())
            named = (
                "Practicals that name no lab type"
                if kind == ANY_LAB
                else f"{kind} practicals"
            )
            detail = (
                f"{named} need {demand} lab-period(s) this week but "
                f"{len(usable)} usable lab(s) provide only {supply}"
            )
            if shared:
                detail += (
                    f" (plus {shared} period(s) from practicals that name no "
                    "lab type and could need this kind)"
                )
            near = _why_not_usable(kind, usable, lab_rooms, taken_subjects, taken)
            if near:
                detail += f". {near}"
            shortages.append(detail)

    lab_supply = room_periods(usable_labs)
    add(
        "lab_pressure",
        "Enough lab capacity for all practicals",
        not shortages,
        f"{lab_demand} lab-period(s) needed across {len(demand_by_type)} lab "
        f"type(s), each within its own supply",
        "; ".join(shortages),
        shortages,
    )

    # A room pinned to a theory subject reserves it for a subject that no
    # longer needs a pin by default - the room is wasted.
    theory_pinned = [
        f"{r.room_number} -> {r.fixed_subject.code}"
        for r in rooms
        if r.fixed_subject is not None and r.fixed_subject.type == "theory"
    ]
    add(
        "no_theory_pinned",
        "No room is reserved for a theory subject",
        not theory_pinned,
        "No misdirected pins",
        f"Theory subjects pinned (room is wasted): {'; '.join(theory_pinned)}",
        theory_pinned,
        severity="warning",
    )

    # ------------------------------------------------------------- capacity
    unavailable_by_section: dict[int, int] = {}
    for row in db.query(SectionUnavailability).all():
        unavailable_by_section[row.section_id] = (
            unavailable_by_section.get(row.section_id, 0) + 1
        )

    overloaded: list[str] = []
    for s in sections_active:
        demand = sum(
            periods_per_week(sub, s) for sub in s.subjects if sub.is_active
        )
        available = n_teachable - unavailable_by_section.get(s.id, 0)
        # No `available > 0` guard: a zero-slot grid must not read as "fits"
        # just because the arithmetic comparison happens to involve a zero.
        if demand > max(available, 0):
            overloaded.append(
                f"{s.section_number} needs {demand}, "
                f"{max(available, 0)} available (grid has {n_teachable} teachable slot"
                f"{'' if n_teachable == 1 else 's'})"
            )
    add(
        "demand_fits_grid",
        "Weekly demand fits the grid",
        not overloaded,
        "All sections fit",
        "; ".join(overloaded),
        overloaded,
    )

    # Multi-hour blocks need contiguous non-lunch periods on a single day. Where
    # the lunch slot sits decides whether a 3-hour block can exist at all.
    no_window: list[str] = []
    for sub in taken_subjects:
        if sub.session_length_hours > 1 and n_teachable:
            if _contiguous_windows(slots, sub.session_length_hours) == 0:
                no_window.append(
                    f"{sub.code} needs {sub.session_length_hours} contiguous periods; no day offers that"
                )
    add(
        "multi_hour_blocks_fit",
        "Multi-hour sessions have contiguous windows",
        not no_window,
        "Contiguous windows available",
        "; ".join(no_window),
        no_window,
    )

    # A theory subject meets a section at most once a day, so it cannot meet
    # more often than there are days able to hold one of its sessions.
    too_often: list[str] = []
    for sec in sections_active:
        for sub in sec.subjects:
            if not sub.is_active:
                continue
            for spec in session_specs(sub, sec):
                if spec.session_type != LECTURE or spec.count < 2:
                    continue
                days = _days_with_window(slots, spec.length)
                if spec.count > days:
                    too_often.append(
                        f"{sub.code} ({sec.section_number}) meets {spec.count} times a "
                        f"week, but a theory subject meets a section at most once a "
                        f"day and only {days} day(s) can hold it"
                    )
    add(
        "theory_once_a_day_fits",
        "Theory subjects fit at once a day",
        not too_often,
        "Every theory subject has enough days",
        "; ".join(too_often),
        too_often,
    )

    add(
        "working_days_only",
        "Only Monday to Friday is scheduled",
        not off_days,
        "The time grid covers the working week",
        f"The time grid includes {', '.join(off_days)}, which is not a teaching "
        "day - nothing will be scheduled then",
        off_days,
        severity="warning",
    )

    # Phase 10 PART 1: advisory only, never a blocker - this context's own
    # solve is entirely correct on its own terms even if it clashes with a
    # concurrently published context in the same real-world period. See
    # app/cross_context.py's module docstring for the full policy.
    if academic_context_id is not None:
        clashes = find_cross_context_clashes(db, academic_context_id)
        add(
            "cross_context_resource_clash",
            "No resource clash with other contexts in the same period",
            not clashes,
            "No other context in this academic year/semester shares a double-booked room or faculty",
            "; ".join(c.message for c in clashes[:10])
            + (f" (+{len(clashes) - 10} more)" if len(clashes) > 10 else ""),
            [c.message for c in clashes],
            severity="warning",
        )

    total_demand = sum(
        periods_per_week(sub, s)
        for s in sections_active
        for sub in s.subjects if sub.is_active
    )

    blockers = [c for c in checks if not c.ok and c.severity == "blocker"]
    warnings = [c for c in checks if not c.ok and c.severity == "warning"]

    # The teachers this dataset's classes are given to. Each class names its
    # teacher, and that choice is recorded per class; counting everyone
    # *qualified* for a subject instead counted teachers from other uploads who
    # happen to share a subject code. A dataset recorded before teachers were
    # pinned falls back to the qualified ones.
    pinned = {
        f for (f,) in db.query(SectionSubjectAssignment.faculty_id).filter(
            SectionSubjectAssignment.section_id.in_([s.id for s in sections_active] or [-1]),
            SectionSubjectAssignment.faculty_id.isnot(None),
        ).distinct()
    }
    context_faculty = len(pinned) if pinned else len({
        t.id for sub in taken_subjects for t in sub.faculties if t.is_active
    })

    return ValidationReport(
        ready=not blockers,
        blocker_count=len(blockers),
        warning_count=len(warnings),
        checks=checks,
        stats={
            "faculty": len(faculty),
            "subjects": len(subjects),
            "rooms": len(rooms),
            "sections": len(sections),
            "slots": len(slots),
            "teachable_slots": n_teachable,
            "scheduled_pairs": sum(len(v) for v in taken.values()),
            "total_periods_demanded": total_demand,
            "total_periods_available": n_teachable * len(sections_active) if sections_active else 0,
            "ceil_days_needed": ceil(total_demand / n_teachable) if n_teachable else 0,
            # Scoped to this intake, for the Generate page's summary. The
            # counts above are the whole database, which on a shared
            # deployment says little about the timetable being built.
            "context_sections": len(sections_active),
            "context_subjects": len(taken_subjects),
            "context_faculty": context_faculty,
            # Class sessions a week (a two-period lab is one class), and the
            # periods they fill: the same two numbers the upload summary shows.
            "context_classes": sum(
                spec.count
                for s in sections_active
                for sub in s.subjects if sub.is_active
                for spec in session_specs(sub, s)
            ),
            "context_required_periods": total_demand,
            "usable_rooms": len(active_rooms),
            "usable_labs": len(lab_rooms),
        },
    )


def _days_with_window(slots: list[TimeSlot], length: int) -> int:
    """How many days offer at least one legal start for a `length`-period block."""
    by_day: dict[int, dict[int, TimeSlot]] = {}
    for slot in slots:
        by_day.setdefault(slot.day_index, {})[slot.period_index] = slot
    count = 0
    for periods in by_day.values():
        for p in periods:
            block = [periods.get(p + k) for k in range(length)]
            if all(b is not None and not b.is_lunch for b in block):
                count += 1
                break
    return count


def _most_periods_with_breaks(
    slots: list[TimeSlot], blocked: set[int], cap: int = 0,
) -> int:
    """The most periods one teacher can teach in a week and still get a break.

    A day of n periods they could teach holds at most n - 1, because one of
    them has to be free. A lunch, a gap in the grid, or a period this teacher
    is unavailable is already free, so a day that has one costs nothing
    further - the break is that period.

    With a cap on runs, each back-to-back stretch of m usable periods holds at
    most m - floor(m / (cap + 1)): every (cap + 1)th period has to be free.
    """
    by_day: dict[int, list[TimeSlot]] = {}
    for slot in slots:
        by_day.setdefault(slot.day_index, []).append(slot)
    total = 0
    for day_slots in by_day.values():
        usable = [s for s in day_slots if not s.is_lunch and s.id not in blocked]
        if not usable:
            continue
        # Already a free period in this day? Then every usable one can be taught.
        day_most = len(usable) if len(usable) < len(day_slots) else len(usable) - 1
        if cap > 0:
            usable_periods = {s.period_index for s in usable}
            capped, run = 0, 0
            for p in sorted({s.period_index for s in day_slots}):
                if p in usable_periods:
                    run += 1
                else:
                    capped += run - run // (cap + 1)
                    run = 0
            capped += run - run // (cap + 1)
            day_most = min(day_most, capped)
        total += day_most
    return total