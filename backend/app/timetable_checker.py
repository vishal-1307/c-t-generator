"""Independent verification of hard constraints - the single implementation.

Originally a test-only helper (``tests/constraint_checker.py``); promoted here
in Phase 6 so the manual-edit service (``app/manual_edit.py``) can revalidate
a timetable after a move/room/faculty change using the exact same rules the
test suite already trusts, rather than a second, weaker implementation. This
was flagged as a future extension point since Phase 4/5 - see
TIMETABLE_LOGIC_SPEC.md's "Checker -> shared validation service" note.

This deliberately does NOT import the solver's model-building code. It
re-reads the persisted ``Assignment`` rows and re-derives every rule from the
raw data, so a bug in the CP-SAT model cannot mask itself by being checked
against its own assumptions. If the model and this file ever disagree, one of
them is wrong - which is the point.

It DOES reuse ``solver.data._eligible_rooms`` for the room-eligibility checks
(HC6/HC7), rather than re-implementing type/lab-type/capacity/pin resolution a
third time (validation.py already reuses it too) - deliberately, since that
function's *logic* (which rooms are allowed) is not what these checks are
verifying. What they verify is independent: that the persisted room for every
assignment is actually a member of the room *this specific solve* declared
eligible, re-derived fresh from today's data - not that the eligibility
function itself is correct (constraint tests in test_solver_*.py cover that
by construction, with hand-built fixtures).
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from .config import settings
from .domain import LECTURE
from .grid import DEFAULT_DAYS, is_working_day
from .room_scope import usable_rooms
from .models import (
    Assignment,
    FacultyUnavailability,
    Room,
    RoomUnavailability,
    Section,
    SectionUnavailability,
    Subject,
    TimeSlot,
    TimetableRun,
)
from .solver.data import _eligible_rooms


def check_all(db: Session, run_id: int) -> list[str]:
    """Return a list of violations. Empty list means the timetable is valid."""
    rows = db.query(Assignment).filter(Assignment.run_id == run_id).all()
    slots = {s.id: s for s in db.query(TimeSlot).all()}
    # Every room is needed to describe a row; only the intake's own may be
    # *eligible*. See `_hc_room_eligibility`.
    rooms = {r.id: r for r in db.query(Room).all()}
    run = db.get(TimetableRun, run_id)
    usable = {r.id: r for r in usable_rooms(db, run.academic_context_id if run else None)}
    sections = {s.id: s for s in db.query(Section).all()}
    subjects = {s.id: s for s in db.query(Subject).all()}

    violations: list[str] = []
    violations += _hc1_faculty_clash(rows, slots)
    violations += _hc2_room_clash(rows, slots, rooms)
    violations += _hc3_section_clash(rows, slots, sections)
    violations += _hc4_capacity(rows, rooms, sections)
    violations += _hc_room_eligibility(rows, rooms, sections, subjects, usable)
    violations += _hc_no_faculty_room(rows, rooms)
    violations += _hc7_lunch(rows, slots)
    violations += _hc8_faculty_mapping(rows, subjects)
    violations += _hc9_contiguity(rows, slots, subjects)
    violations += _hc_one_room_per_pair(rows)
    violations += _hc_faculty_availability(db, rows, slots)
    violations += _hc_room_availability(db, rows, slots)
    violations += _hc_section_availability(db, rows, slots)
    violations += _demand_met(db, rows, sections, subjects)
    violations += _working_days_only(rows, slots)
    violations += _theory_once_per_day(rows, slots)
    violations += _faculty_breaks(rows, slots)
    violations += _faculty_runs(rows, slots)
    return violations


def longest_faculty_run(db: Session, run_id: int) -> int:
    """The longest run of back-to-back periods any teacher teaches in the run."""
    rows = db.query(Assignment).filter(Assignment.run_id == run_id).all()
    slots = {s.id: s for s in db.query(TimeSlot).all()}
    runs = _runs_by_teacher_day(rows, slots)
    return max((length for length, _ in runs.values()), default=0)


def _runs_by_teacher_day(rows, slots) -> dict[tuple[int, str], tuple[int, int]]:
    """(teacher, day) -> (longest run of consecutive periods, where it starts)."""
    busy: dict[tuple[int, str], set[int]] = defaultdict(set)
    for a in rows:
        slot = slots[a.timeslot_id]
        busy[(a.faculty_id, slot.day)].add(slot.period_index)
    out: dict[tuple[int, str], tuple[int, int]] = {}
    for key, taught in busy.items():
        best, best_start, length, start = 0, 0, 0, 0
        for p in sorted(taught):
            if length and p == start + length:
                length += 1
            else:
                start, length = p, 1
            if length > best:
                best, best_start = length, start
        out[key] = (best, best_start)
    return out


def _hc1_faculty_clash(rows, slots) -> list[str]:
    seen = defaultdict(list)
    for a in rows:
        seen[(a.faculty_id, a.timeslot_id)].append(a)
    out = []
    for (faculty_id, slot_id), group in seen.items():
        if len(group) > 1:
            where = ", ".join(f"{a.section.section_number}/{a.subject.code}" for a in group)
            out.append(
                f"HC1 faculty clash: {group[0].faculty.name} is in {len(group)} places "
                f"at {slots[slot_id].day} P{slots[slot_id].period_index + 1} ({where})"
            )
    return out


def _hc2_room_clash(rows, slots, rooms) -> list[str]:
    seen = defaultdict(list)
    for a in rows:
        seen[(a.room_id, a.timeslot_id)].append(a)
    out = []
    for (room_id, slot_id), group in seen.items():
        if len(group) > 1:
            where = ", ".join(f"{a.section.section_number}/{a.subject.code}" for a in group)
            out.append(
                f"HC2 room clash: {rooms[room_id].room_number} double-booked at "
                f"{slots[slot_id].day} P{slots[slot_id].period_index + 1} ({where})"
            )
    return out


def _hc3_section_clash(rows, slots, sections) -> list[str]:
    seen = defaultdict(list)
    for a in rows:
        seen[(a.section_id, a.timeslot_id)].append(a)
    out = []
    for (section_id, slot_id), group in seen.items():
        if len(group) > 1:
            what = ", ".join(a.subject.code for a in group)
            out.append(
                f"HC3 section clash: {sections[section_id].section_number} has "
                f"{len(group)} classes at {slots[slot_id].day} "
                f"P{slots[slot_id].period_index + 1} ({what})"
            )

    # A lab group's students are also its parent's students, so a class for one
    # collides with a class for the other even though the two rows name
    # different sections. Derived here from the section rows and the assignment
    # rows, not from anything the solver produced - the point of this module is
    # to disagree with the solver when the solver is wrong, which it cannot do
    # if it asks the solver how to check.
    for section_id, section in sections.items():
        parent_id = section.parent_section_id
        if parent_id is None or parent_id not in sections:
            continue
        for slot_id in {a.timeslot_id for a in rows if a.section_id == section_id}:
            mine = seen.get((section_id, slot_id), [])
            theirs = seen.get((parent_id, slot_id), [])
            if mine and theirs:
                what = ", ".join(a.subject.code for a in [*mine, *theirs])
                out.append(
                    f"HC3 section clash: {section.section_number} is a group of "
                    f"{sections[parent_id].section_number} and both are teaching at "
                    f"{slots[slot_id].day} P{slots[slot_id].period_index + 1} ({what})"
                )
    return out


def _hc4_capacity(rows, rooms, sections) -> list[str]:
    out = []
    for a in rows:
        room, section = rooms[a.room_id], sections[a.section_id]
        if room.capacity < section.strength:
            out.append(
                f"HC4 capacity: {section.section_number} ({section.strength}) placed in "
                f"{room.room_number} (seats {room.capacity})"
            )
    return sorted(set(out))


def _hc_room_eligibility(rows, rooms, sections, subjects, usable=None) -> list[str]:
    """HC6/HC7: the persisted room must be a member of the pool
    ``_eligible_rooms`` computes fresh for this (section, subject) today -
    covers type match, lab-type match, fixed/allowed-room overrides, capacity,
    is_active and the faculty-room exclusion all at once, using the exact same
    resolution the solver used to build its candidates."""
    out = []
    for a in rows:
        subject = subjects[a.subject_id]
        section = sections[a.section_id]
        # The component matters: a mixed subject's lecture belongs in a
        # teaching room and its practical in a lab, and judging both against
        # one pool would call one of them a violation.
        # Judged against the intake's own rooms (`usable`), described from
        # every room - a row can name a room outside the list, and that is
        # exactly the row this has to report rather than crash on.
        pool = usable if usable is not None else rooms
        eligible_ids = {
            r.id
            for r in _eligible_rooms(subject, section, pool, a.session_type)
        }
        if a.room_id not in eligible_ids:
            room = rooms[a.room_id]
            outside = usable is not None and a.room_id not in usable
            component = (
                " lecture" if a.session_type == "L" else " practical"
            ) if subject.type == "mixed" else ""
            out.append(
                f"HC6/7: {subject.code}{component} for {section.section_number} "
                f"placed in {room.room_number} ({room.room_type}"
                f"{'/' + room.lab_type if room.lab_type else ''}), which is not an "
                "eligible room for it"
                + (" - it is not in this semester's room list" if outside else "")
            )
    return sorted(set(out))


def _hc_no_faculty_room(rows, rooms) -> list[str]:
    """A faculty room must never host a class, regardless of what any
    eligibility function claims (belt-and-braces on top of HC6/HC7 above)."""
    out = []
    for a in rows:
        if rooms[a.room_id].room_type == "faculty":
            out.append(
                f"FACULTY-ROOM: {a.section.section_number}/{a.subject.code} scheduled in "
                f"faculty room {rooms[a.room_id].room_number}"
            )
    return sorted(set(out))


def _hc7_lunch(rows, slots) -> list[str]:
    out = []
    for a in rows:
        if slots[a.timeslot_id].is_lunch:
            slot = slots[a.timeslot_id]
            out.append(
                f"HC16 lunch: {a.section.section_number}/{a.subject.code} scheduled on "
                f"the lunch slot {slot.day} P{slot.period_index + 1}"
            )
    return sorted(set(out))


def _hc8_faculty_mapping(rows, subjects) -> list[str]:
    out = []
    for a in rows:
        allowed = {f.id for f in subjects[a.subject_id].faculties}
        if a.faculty_id not in allowed:
            out.append(
                f"HC4: {a.faculty.name} teaches {subjects[a.subject_id].code} "
                "but is not mapped to it"
            )
    return sorted(set(out))


def _expected_block_length(subject, session_type: str) -> int:
    """How many consecutive periods one block of this component occupies.

    Derived from the subject the same way the solver derives it, so the two
    cannot disagree about what a block should look like - but computed here
    from the stored row rather than by importing solver state, which is what
    keeps this checker independent.
    """
    from .domain import session_specs

    for spec in session_specs(subject):
        if spec.session_type == session_type:
            return spec.length
    return subject.session_length_hours


def _hc9_contiguity(rows, slots, subjects) -> list[str]:
    """Each block must be `session_length_hours` consecutive periods, one day,
    one room, one faculty."""
    blocks = defaultdict(list)
    for a in rows:
        blocks[a.block_id].append(a)

    out = []
    for block_id, group in blocks.items():
        subject = subjects[group[0].subject_id]
        # A mixed subject's two components can have different block lengths,
        # so the expected size comes from the component this block belongs to.
        expected = _expected_block_length(subject, group[0].session_type)
        if len(group) != expected:
            out.append(
                f"HC12: block {block_id} ({subject.code}) spans {len(group)} periods, "
                f"expected {expected}"
            )
            continue

        block_slots = sorted((slots[a.timeslot_id] for a in group), key=lambda s: s.period_index)
        if len({s.day_index for s in block_slots}) != 1:
            out.append(f"HC13: block {block_id} ({subject.code}) spans multiple days")
        periods = [s.period_index for s in block_slots]
        if periods != list(range(periods[0], periods[0] + expected)):
            out.append(
                f"HC12: block {block_id} ({subject.code}) periods {periods} are not consecutive"
            )
        if len({a.room_id for a in group}) != 1:
            out.append(f"HC15: block {block_id} ({subject.code}) changes room mid-block")
        if len({a.faculty_id for a in group}) != 1:
            out.append(f"HC14: block {block_id} ({subject.code}) changes faculty mid-block")
    return out


def _hc_one_room_per_pair(rows) -> list[str]:
    """HC15: every session of one component must use the same room.

    Per component, not per subject: a mixed subject's lecture and practical
    are *meant* to be in different rooms, and requiring one room for both
    would report the intended arrangement as a violation.
    """
    by_pair = defaultdict(set)
    for a in rows:
        by_pair[(a.section_id, a.subject_id, a.session_type)].add(a.room_id)
    out = []
    for (section_id, subject_id, session_type), room_ids in by_pair.items():
        if len(room_ids) > 1:
            sample = next(
                a for a in rows
                if a.section_id == section_id
                and a.subject_id == subject_id
                and a.session_type == session_type
            )
            component = (
                " lecture" if session_type == "L" else " practical"
            ) if sample.subject.type == "mixed" else ""
            out.append(
                f"HC15: {sample.section.section_number}/{sample.subject.code}"
                f"{component} used {len(room_ids)} different rooms across its "
                "sessions this week"
            )
    return out


def _hc_faculty_availability(db, rows, slots) -> list[str]:
    """HC8: a faculty member cannot be scheduled during a slot they have
    declared unavailable."""
    blocked = defaultdict(set)
    for u in db.query(FacultyUnavailability).all():
        blocked[u.faculty_id].add(u.timeslot_id)
    out = []
    for a in rows:
        if a.timeslot_id in blocked.get(a.faculty_id, ()):
            slot = slots[a.timeslot_id]
            out.append(
                f"HC8: {a.faculty.name} scheduled at {slot.day} P{slot.period_index + 1}, "
                "which they declared unavailable"
            )
    return sorted(set(out))


def _hc_room_availability(db, rows, slots) -> list[str]:
    """HC9: a room cannot be used during a slot it is declared unavailable
    (e.g. maintenance)."""
    blocked = defaultdict(set)
    for u in db.query(RoomUnavailability).all():
        blocked[u.room_id].add(u.timeslot_id)
    out = []
    for a in rows:
        if a.timeslot_id in blocked.get(a.room_id, ()):
            slot = slots[a.timeslot_id]
            out.append(
                f"HC9: {a.room.room_number} used at {slot.day} P{slot.period_index + 1}, "
                "which is marked unavailable"
            )
    return sorted(set(out))


def _hc_section_availability(db, rows, slots) -> list[str]:
    """HC10: a section cannot have a class during a slot it is declared
    unavailable (e.g. a college-wide event for that section)."""
    blocked = defaultdict(set)
    for u in db.query(SectionUnavailability).all():
        blocked[u.section_id].add(u.timeslot_id)
    out = []
    for a in rows:
        if a.timeslot_id in blocked.get(a.section_id, ()):
            slot = slots[a.timeslot_id]
            out.append(
                f"HC10: {a.section.section_number} scheduled at {slot.day} "
                f"P{slot.period_index + 1}, which is marked unavailable"
            )
    return sorted(set(out))


def _demand_met(db, rows, sections, subjects) -> list[str]:
    """Not one of the numbered hard constraints, but a valid-but-empty
    timetable would pass all of them. Every (section, subject) must get
    exactly its weekly demand (HC11).

    Counted per component. A subject that is taught as both a lecture and a
    practical owes each of them separately, and a total-only check would call
    a timetable correct that gave a subject all six of its periods in a lab
    and none in a classroom.
    """
    from .domain import session_specs

    got = defaultdict(int)
    for a in rows:
        got[(a.section_id, a.subject_id, a.session_type)] += 1

    out = []
    scheduled_sections = {a.section_id for a in rows}
    for section in sections.values():
        if section.id not in scheduled_sections:
            continue
        for subject in section.subjects:
            specs = session_specs(subject, section)
            for spec in specs:
                want = spec.periods
                have = got.get((section.id, subject.id, spec.session_type), 0)
                if have != want:
                    out.append(
                        f"HC11 DEMAND: {section.section_number}/{subject.code}"
                        f"{_component_suffix(subject, spec.session_type)} got {have} "
                        f"periods, expected {want}"
                    )

            # Periods of a component the subject does not have at all - a
            # practical for a pure lecture subject, say. The loop above cannot
            # see these, and they are the shape a bad backfill would take.
            expected_types = {spec.session_type for spec in specs}
            for (sec_id, subj_id, session_type), have in got.items():
                if (
                    sec_id == section.id
                    and subj_id == subject.id
                    and session_type not in expected_types
                ):
                    out.append(
                        f"HC11 DEMAND: {section.section_number}/{subject.code} got "
                        f"{have} periods of an unexpected kind ({session_type!r})"
                    )
    return out


def _component_suffix(subject, session_type: str) -> str:
    """Name the component only when the subject actually has more than one -
    otherwise every message on an ordinary subject grows a redundant label."""
    from .domain import session_specs

    if len(session_specs(subject)) < 2:
        return ""
    return " (lecture)" if session_type == "L" else " (practical)"


def one_faculty_per_pair(db: Session, run_id: int) -> list[str]:
    """All weekly sessions of a (section, subject, component) share one teacher.

    Per component, not per subject: a subject's lectures and its lab sessions
    are commonly taught by different people, and that is a fact about how the
    subject is staffed rather than an inconsistency to report.
    """
    rows = db.query(Assignment).filter(Assignment.run_id == run_id).all()
    by_pair = defaultdict(set)
    for a in rows:
        by_pair[(a.section_id, a.subject_id, a.session_type)].add(a.faculty_id)
    return [
        f"{rows[0].section.section_number}: pair {pair} taught by {len(faculty)} faculty"
        for pair, faculty in by_pair.items()
        if len(faculty) > 1
    ]


def working_days_are_within(db: Session, allowed_days: set[str] | None = None) -> list[str]:
    """HC17: the time grid must only use the allowed working days. Standalone
    (not part of check_all) because it is a property of the grid, not of one
    run's assignments."""
    allowed = allowed_days if allowed_days is not None else set(DEFAULT_DAYS)
    days_in_grid = {s.day for s in db.query(TimeSlot).all()}
    stray = days_in_grid - allowed
    if stray:
        return [f"HC17: grid contains day(s) outside the working week: {sorted(stray)}"]
    return []


def _working_days_only(rows, slots) -> list[str]:
    """Monday to Friday. A grid may carry other days; a timetable may not."""
    out = []
    for a in rows:
        slot = slots[a.timeslot_id]
        if not is_working_day(slot.day):
            out.append(
                f"WORKING DAYS: {a.section.section_number}/{a.subject.code} is "
                f"scheduled on {slot.day}, which is not a teaching day"
            )
    return sorted(set(out))


def _theory_once_per_day(rows, slots) -> list[str]:
    """A theory subject meets a section at most once a day.

    Counted in blocks - one block is one class, however many periods long -
    and only for the lecture component, so a lab that meets twice a day is not
    reported. Re-derived from the rows, independently of the solver.
    """
    blocks: dict[tuple, set[int]] = defaultdict(set)
    labels: dict[tuple, tuple[str, str]] = {}
    for a in rows:
        if (a.session_type or LECTURE) != LECTURE:
            continue
        day = slots[a.timeslot_id].day
        key = (a.section_id, a.subject_id, day)
        blocks[key].add(a.block_id)
        labels[key] = (a.section.section_number, a.subject.code)

    out = []
    for key, ids in blocks.items():
        if len(ids) > 1:
            section, code = labels[key]
            out.append(
                f"THEORY ONCE A DAY: section {section} has {code} {len(ids)} times "
                f"on {key[2]} - a theory subject meets a section at most once a day"
            )
    return sorted(out)


def _faculty_runs(rows, slots) -> list[str]:
    """No teacher teaches more than `faculty_max_consecutive` periods in a row."""
    cap = settings.faculty_max_consecutive
    if cap <= 0:
        return []
    names = {
        a.faculty_id: (f"{a.faculty.name} ({a.faculty.faculty_code})" if a.faculty
                       else str(a.faculty_id))
        for a in rows
    }
    out = []
    for (faculty_id, day), (length, start) in _runs_by_teacher_day(rows, slots).items():
        if length > cap:
            out.append(
                f"LONG RUN: {names[faculty_id]} teaches {length} periods in a row on "
                f"{day} from P{start + 1} - the most allowed is {cap}"
            )
    return sorted(out)


def _faculty_breaks(rows, slots) -> list[str]:
    """Every teacher has a free period on any day they teach.

    The faculty break, in place of a common lunch. A day a teacher does not
    appear in needs no break; a day they fill end to end has none, and that is
    the violation. A lunch period is never taught, so it is a free period.
    How long a run may be is `_faculty_runs`.
    """
    periods_per_day: dict[str, set[int]] = defaultdict(set)
    for slot in slots.values():
        periods_per_day[slot.day].add(slot.period_index)

    busy: dict[tuple[int, str], set[int]] = defaultdict(set)
    names: dict[int, str] = {}
    for a in rows:
        slot = slots[a.timeslot_id]
        busy[(a.faculty_id, slot.day)].add(slot.period_index)
        f = a.faculty
        names[a.faculty_id] = f"{f.name} ({f.faculty_code})" if f else str(a.faculty_id)

    out = []
    for (faculty_id, day), taught in busy.items():
        if not periods_per_day[day] - taught:
            out.append(
                f"FACULTY BREAK: {names[faculty_id]} teaches all "
                f"{len(taught)} periods on {day} with no free period"
            )
    return sorted(out)
