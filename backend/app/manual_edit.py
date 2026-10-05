"""Phase 6: manual timetable operations - move a class, change its room or
faculty, lock/unlock, and partial regeneration - all validated against the
exact same hard constraints and candidate resolution the solver itself uses
(``_eligible_rooms`` for compatibility, the same availability tables, the
same contiguity rule), never a separate weaker check. Every applied change is
independently revalidated with ``app.timetable_checker.check_all`` before
commit - if that finds anything wrong, nothing is written (spec #21/#24).

Two granularities, and this is a deliberate consequence of the existing hard
constraints, not a design choice made fresh in this phase:

* A **move** (day/slot change) affects one *block* - one weekly occurrence -
  because different sessions of the same (section, subject) pair are not
  required to share a day; moving one leaves the others untouched.
* A **room or faculty change** affects every occurrence of the same
  *component* - the clicked assignment's ``session_type`` - because HC14/HC15
  require exactly one faculty and one room for a component's entire week.
  Since a subject's lecture and its practical are scheduled as independent
  obligations (usually in different kinds of room), HC14/HC15 are enforced
  per ``session_type``, not across the whole subject - so a room/faculty
  change is scoped the same way: it never touches the OTHER component's rows.
  There is therefore no "single-session temporary exception" for room/faculty
  in the current model; adding one would mean weakening HC14/HC15, which this
  phase is explicitly not permitted to do. A room/faculty change also updates
  the matching ``SectionSubjectAssignment`` row (one per (section, subject,
  session_type)) - the persistence anchor every future regeneration reads -
  since that is what "the semester assignment changed" means in this
  codebase.

A successful change locks the affected component's ``SectionSubjectAssignment``
by default (``lock_after=True``): an admin manually placing a class is
exactly the "explicitly changed by an authorized user" case persistence
exists to honour, and leaving it unlocked would let the very next
regeneration silently undo the edit. Locking one component (e.g. a mixed
subject's lecture) must never lock or otherwise affect the other component
(its practical) - they are independent obligations with independent rooms,
faculty and lock state.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .models import (
    Assignment,
    ChangeHistory,
    Faculty,
    FacultyUnavailability,
    Room,
    RoomUnavailability,
    Section,
    SectionSubjectAssignment,
    SectionUnavailability,
    Subject,
    TimeSlot,
    TimetableRun,
)
from .config import settings
from .domain import LECTURE
from .grid import is_working_day
from .room_scope import usable_rooms
from .solver.data import _eligible_rooms
from .timetable_checker import check_all


class ManualEditError(Exception):
    """Base for every rejection this module raises. ``code`` is a stable
    machine-readable slug; ``str(exc)`` is the human-readable reason."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class NotFound(ManualEditError):
    def __init__(self, message: str):
        super().__init__("not_found", message)


class Locked(ManualEditError):
    def __init__(self, message: str):
        super().__init__("locked", message)


class Protected(ManualEditError):
    def __init__(self, message: str):
        super().__init__("protected", message)


class Invalid(ManualEditError):
    """One or more hard-constraint violations would result. ``issues``
    carries every reason found, not just the first, so a preview can show the
    admin the whole picture at once."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("invalid", "; ".join(issues))


@dataclass
class ValidationResult:
    ok: bool
    issues: list[str] = field(default_factory=list)


@dataclass
class ChangeOutcome:
    ok: bool
    issues: list[str] = field(default_factory=list)
    change_history_id: int | None = None


# --------------------------------------------------------------------- shared


def _block_rows(db: Session, run_id: int, block_id: int) -> list[Assignment]:
    rows = (
        db.query(Assignment)
        .filter(Assignment.run_id == run_id, Assignment.block_id == block_id)
        .all()
    )
    rows.sort(key=lambda a: a.timeslot.period_index)
    return rows


def _pair_rows(
    db: Session, run_id: int, section_id: int, subject_id: int, session_type: str
) -> list[Assignment]:
    """Every row of one *component* (session_type) of a (section, subject)
    pair - never the other component's rows, since they are independent
    obligations that can legitimately sit in different rooms with different
    faculty (see the module docstring)."""
    rows = (
        db.query(Assignment)
        .filter(
            Assignment.run_id == run_id,
            Assignment.section_id == section_id,
            Assignment.subject_id == subject_id,
            Assignment.session_type == session_type,
        )
        .all()
    )
    rows.sort(key=lambda a: (a.block_id, a.timeslot.period_index))
    return rows


def _get_ssa(
    db: Session, academic_context_id: int, section_id: int, subject_id: int,
    session_type: str,
) -> SectionSubjectAssignment | None:
    return (
        db.query(SectionSubjectAssignment)
        .filter(
            SectionSubjectAssignment.academic_context_id == academic_context_id,
            SectionSubjectAssignment.section_id == section_id,
            SectionSubjectAssignment.subject_id == subject_id,
            SectionSubjectAssignment.session_type == session_type,
        )
        .first()
    )


def _require_editable(run: TimetableRun) -> None:
    """A published version is the timetable people are following, and an
    archived one is the record of what they followed. Neither is changed in
    place: the change goes into a new version, which is then published."""
    if run.publish_status in ("PUBLISHED", "ARCHIVED"):
        raise Protected(
            f"version {run.version} is {run.publish_status.lower()} and cannot be changed; "
            "generate a new version, make the change there, then publish it"
        )


def _require_unlocked(
    db: Session, run: TimetableRun, section_id: int, subject_id: int, session_type: str,
) -> None:
    _require_editable(run)
    ssa = _get_ssa(db, run.academic_context_id, section_id, subject_id, session_type)
    if ssa is not None and ssa.locked:
        raise Locked(
            "this section-subject is locked; unlock it before making a manual change"
        )


def _lock(db: Session, run: TimetableRun, section_id: int, subject_id: int,
          faculty_id: int, room_id: int, session_type: str) -> None:
    ssa = _get_ssa(db, run.academic_context_id, section_id, subject_id, session_type)
    if ssa is None:
        ssa = SectionSubjectAssignment(
            academic_context_id=run.academic_context_id,
            section_id=section_id, subject_id=subject_id, session_type=session_type,
        )
        db.add(ssa)
    ssa.faculty_id = faculty_id
    ssa.room_id = room_id
    ssa.locked = True


def _record(
    db: Session, run_id: int, section_id: int, subject_id: int,
    change_type: str, old_value: str, new_value: str,
    actor: str | None, reason: str | None,
    user_id: int | None = None, user_role: str | None = None,
) -> ChangeHistory:
    """``actor`` is the pre-Phase-10 free-text fallback, kept for compatibility.
    ``user_id``/``user_role`` (Phase 10) are the real, authenticated identity
    when one exists - see ChangeHistory's own docstring."""
    entry = ChangeHistory(
        run_id=run_id, section_id=section_id, subject_id=subject_id,
        change_type=change_type, old_value=old_value[:300], new_value=new_value[:300],
        actor=actor, reason=reason, user_id=user_id, user_role=user_role,
    )
    db.add(entry)
    return entry


def _label(slot: TimeSlot) -> str:
    return f"{slot.day} P{slot.period_index + 1}"


def _revalidate_or_rollback(db: Session, run_id: int) -> None:
    """The atomic-transaction safety net (spec #21/#24): re-run the full
    independent checker against the run's post-change state before commit.
    Any violation rolls the whole change back - the previous valid state is
    never left half-applied."""
    db.flush()
    violations = check_all(db, run_id)
    if violations:
        db.rollback()
        raise Invalid(violations)
    db.commit()


# ----------------------------------------------------------------------- move


def contiguous_window(db: Session, target_timeslot_id: int, length: int) -> list[TimeSlot] | None:
    """The same rule ``solver/data.py::_legal_starts`` encodes - ``length``
    consecutive same-day periods, none of them lunch - expressed directly
    against one target slot instead of enumerating every legal start in the
    grid, since a manual move already knows exactly where it wants to land."""
    target = db.get(TimeSlot, target_timeslot_id)
    if target is None:
        return None
    day_slots = (
        db.query(TimeSlot)
        .filter(TimeSlot.day_index == target.day_index)
        .order_by(TimeSlot.period_index)
        .all()
    )
    try:
        start = next(i for i, s in enumerate(day_slots) if s.id == target.id)
    except StopIteration:
        return None
    window = day_slots[start:start + length]
    if len(window) < length:
        return None
    if any(s.is_lunch for s in window):
        return None
    for prev, cur in zip(window, window[1:]):
        if cur.period_index != prev.period_index + 1:
            return None
    return window


def _validate_move(
    db: Session, assignment_id: int, target_timeslot_id: int,
) -> tuple[list[str], dict]:
    a = db.get(Assignment, assignment_id)
    if a is None:
        raise NotFound(f"assignment {assignment_id} not found")
    run = db.get(TimetableRun, a.run_id)
    section = db.get(Section, a.section_id)
    subject = db.get(Subject, a.subject_id)

    _require_unlocked(db, run, a.section_id, a.subject_id, a.session_type)

    block = _block_rows(db, a.run_id, a.block_id)
    # How many periods are actually being relocated - which is the size of the
    # block in front of us, not `subject.session_length_hours`.
    #
    # For a mixed subject those differ: its components carry their own lengths
    # (`lecture_session_length` / `practical_session_length`, see
    # domain.session_specs), and `session_length_hours` is the single-component
    # field, which defaults to 1. A two-period practical of a mixed subject was
    # therefore validated against a one-period window and then applied to a
    # two-row block: apply_move zips block against window, so only the first
    # row moved and the block was left straddling two days. The independent
    # re-check caught that and rolled the whole thing back, so nothing was ever
    # corrupted - but the preview had already said "valid", and the move could
    # never succeed.
    length = len(block)

    issues: list[str] = []
    window = contiguous_window(db, target_timeslot_id, length)
    if window is None:
        issues.append(
            f"no {length} contiguous non-lunch period(s) starting at timeslot "
            f"{target_timeslot_id}"
        )
        return issues, {}

    window_ids = {s.id for s in window}
    day = window[0].day
    if not is_working_day(day):
        issues.append(f"{day} is not a teaching day - classes run Monday to Friday")

    # A lab group's students are also its parent's, so a class for either one
    # occupies both. The independent re-check has always known this; the
    # preview did not, so it could approve a move that apply then rolled back.
    family = _section_family(db, section)

    others = (
        db.query(Assignment)
        .filter(Assignment.run_id == a.run_id, Assignment.block_id != a.block_id)
        .all()
    )
    for o in others:
        if o.timeslot_id not in window_ids:
            continue
        if o.section_id == a.section_id:
            issues.append(
                f"section {section.section_number} already has a class at "
                f"{_label(o.timeslot)} ({o.subject.code})"
            )
        elif o.section_id in family:
            issues.append(
                f"section {o.section.section_number} shares students with "
                f"{section.section_number} and already has a class at "
                f"{_label(o.timeslot)} ({o.subject.code})"
            )
        if o.faculty_id == a.faculty_id:
            issues.append(
                f"faculty is already teaching {o.section.section_number}/{o.subject.code} "
                f"at {_label(o.timeslot)}"
            )
        if o.room_id == a.room_id:
            issues.append(
                f"room {a.room.room_number} is already in use by "
                f"{o.section.section_number}/{o.subject.code} at {_label(o.timeslot)}"
            )

    blocked_section = {
        u.timeslot_id for u in
        db.query(SectionUnavailability).filter(SectionUnavailability.section_id == a.section_id)
    }
    blocked_faculty = {
        u.timeslot_id for u in
        db.query(FacultyUnavailability).filter(FacultyUnavailability.faculty_id == a.faculty_id)
    }
    blocked_room = {
        u.timeslot_id for u in
        db.query(RoomUnavailability).filter(RoomUnavailability.room_id == a.room_id)
    }
    if window_ids & blocked_section:
        issues.append(f"section {section.section_number} is marked unavailable at that time")
    if window_ids & blocked_faculty:
        issues.append(f"{a.faculty.name} is marked unavailable at that time")
    if window_ids & blocked_room:
        issues.append(f"room {a.room.room_number} is marked unavailable at that time")

    # The two rules that are about a whole day rather than one period.
    if a.session_type == LECTURE:
        same_day = {
            o.block_id for o in others
            if o.section_id == a.section_id
            and o.subject_id == a.subject_id
            and o.session_type == LECTURE
            and o.timeslot.day == day
        }
        if same_day:
            issues.append(
                f"{subject.code} already meets {section.section_number} on {day} - "
                "a theory subject meets a section at most once a day"
            )

    teacher_periods = {
        o.timeslot.period_index for o in others
        if o.faculty_id == a.faculty_id and o.timeslot.day == day
    } | {slot.period_index for slot in window}
    issue = _break_issue(db, a.faculty, day, teacher_periods)
    if issue:
        issues.append(issue)

    return issues, {"assignment": a, "run": run, "block": block, "window": window}


def preview_move(db: Session, assignment_id: int, target_timeslot_id: int) -> ValidationResult:
    issues, _ctx = _validate_move(db, assignment_id, target_timeslot_id)
    return ValidationResult(ok=not issues, issues=issues)


def apply_move(
    db: Session, assignment_id: int, target_timeslot_id: int,
    lock_after: bool = True, actor: str | None = None, reason: str | None = None,
    user_id: int | None = None, user_role: str | None = None,
) -> ChangeOutcome:
    """Move one block to a new day/time. Faculty, room, section and subject
    are unchanged - see the module docstring for why only a move, not a
    room/faculty change, can stay scoped to a single occurrence."""
    issues, ctx = _validate_move(db, assignment_id, target_timeslot_id)
    if issues:
        return ChangeOutcome(ok=False, issues=issues)

    a, run, block, window = ctx["assignment"], ctx["run"], ctx["block"], ctx["window"]
    old_label = ", ".join(_label(r.timeslot) for r in block)
    new_label = ", ".join(_label(s) for s in window)

    for row, slot in zip(block, window):
        row.timeslot_id = slot.id

    entry = _record(
        db, run.id, a.section_id, a.subject_id, "move", old_label, new_label, actor, reason,
        user_id, user_role,
    )
    if lock_after:
        _lock(db, run, a.section_id, a.subject_id, a.faculty_id, a.room_id, a.session_type)

    try:
        _revalidate_or_rollback(db, run.id)
    except Invalid as exc:
        return ChangeOutcome(ok=False, issues=exc.issues)
    db.refresh(entry)
    return ChangeOutcome(ok=True, change_history_id=entry.id)


# ------------------------------------------------------------------ room change


def _validate_room_change(
    db: Session, assignment_id: int, room_id: int,
) -> tuple[list[str], dict]:
    a = db.get(Assignment, assignment_id)
    if a is None:
        raise NotFound(f"assignment {assignment_id} not found")
    run = db.get(TimetableRun, a.run_id)
    section = db.get(Section, a.section_id)
    subject = db.get(Subject, a.subject_id)
    new_room = db.get(Room, room_id)

    _require_unlocked(db, run, a.section_id, a.subject_id, a.session_type)

    issues: list[str] = []
    if new_room is None:
        issues.append(f"room {room_id} not found")
        return issues, {}

    all_rooms = {r.id: r for r in usable_rooms(db, run.academic_context_id)}
    eligible_ids = {r.id for r in _eligible_rooms(subject, section, all_rooms, a.session_type)}
    if room_id not in eligible_ids:
        issues.append(
            f"{new_room.room_number} ({new_room.room_type}"
            f"{'/' + new_room.lab_type if new_room.lab_type else ''}) is not eligible for "
            f"{subject.code} ({section.section_number}) - check type, capability, capacity"
        )

    pair_rows = _pair_rows(db, a.run_id, a.section_id, a.subject_id, a.session_type)
    occupied_slots = {r.timeslot_id for r in pair_rows}

    others = (
        db.query(Assignment)
        .filter(
            Assignment.run_id == a.run_id,
            Assignment.room_id == room_id,
            Assignment.timeslot_id.in_(occupied_slots),
        )
        .all()
    )
    for o in others:
        if (
            o.section_id == a.section_id
            and o.subject_id == a.subject_id
            and o.session_type == a.session_type
        ):
            continue  # the pair's own rows, about to be moved into this room together
        issues.append(
            f"{new_room.room_number} is already in use by "
            f"{o.section.section_number}/{o.subject.code} at {_label(o.timeslot)}"
        )

    blocked = {
        u.timeslot_id for u in
        db.query(RoomUnavailability).filter(RoomUnavailability.room_id == room_id)
    }
    if occupied_slots & blocked:
        issues.append(f"{new_room.room_number} is marked unavailable during one of this pair's slots")

    return issues, {"assignment": a, "run": run, "pair_rows": pair_rows, "new_room": new_room}


def preview_room_change(db: Session, assignment_id: int, room_id: int) -> ValidationResult:
    issues, _ctx = _validate_room_change(db, assignment_id, room_id)
    return ValidationResult(ok=not issues, issues=issues)


def apply_room_change(
    db: Session, assignment_id: int, room_id: int,
    lock_after: bool = True, actor: str | None = None, reason: str | None = None,
    user_id: int | None = None, user_role: str | None = None,
) -> ChangeOutcome:
    """Change the room for the whole (section, subject) pair - every session
    this week, not just the clicked-on one (HC15: one room per pair)."""
    issues, ctx = _validate_room_change(db, assignment_id, room_id)
    if issues:
        return ChangeOutcome(ok=False, issues=issues)

    a, run, pair_rows, new_room = ctx["assignment"], ctx["run"], ctx["pair_rows"], ctx["new_room"]
    old_room_number = a.room.room_number
    for row in pair_rows:
        row.room_id = room_id

    entry = _record(
        db, run.id, a.section_id, a.subject_id, "room", old_room_number, new_room.room_number,
        actor, reason, user_id, user_role,
    )
    if lock_after:
        _lock(db, run, a.section_id, a.subject_id, a.faculty_id, room_id, a.session_type)

    try:
        _revalidate_or_rollback(db, run.id)
    except Invalid as exc:
        return ChangeOutcome(ok=False, issues=exc.issues)
    db.refresh(entry)
    return ChangeOutcome(ok=True, change_history_id=entry.id)


# --------------------------------------------------------------- faculty change


def _validate_faculty_change(
    db: Session, assignment_id: int, faculty_id: int,
) -> tuple[list[str], dict]:
    a = db.get(Assignment, assignment_id)
    if a is None:
        raise NotFound(f"assignment {assignment_id} not found")
    run = db.get(TimetableRun, a.run_id)
    subject = db.get(Subject, a.subject_id)
    new_faculty = db.get(Faculty, faculty_id)

    _require_unlocked(db, run, a.section_id, a.subject_id, a.session_type)

    issues: list[str] = []
    if new_faculty is None:
        issues.append(f"faculty {faculty_id} not found")
        return issues, {}

    eligible_ids = {f.id for f in subject.faculties}
    if faculty_id not in eligible_ids:
        issues.append(
            f"{new_faculty.name} is not mapped to teach {subject.code} - "
            "arbitrary faculty cannot be assigned to a subject"
        )

    pair_rows = _pair_rows(db, a.run_id, a.section_id, a.subject_id, a.session_type)
    occupied_slots = {r.timeslot_id for r in pair_rows}

    others = (
        db.query(Assignment)
        .filter(
            Assignment.run_id == a.run_id,
            Assignment.faculty_id == faculty_id,
            Assignment.timeslot_id.in_(occupied_slots),
        )
        .all()
    )
    for o in others:
        if (
            o.section_id == a.section_id
            and o.subject_id == a.subject_id
            and o.session_type == a.session_type
        ):
            continue
        issues.append(
            f"{new_faculty.name} is already teaching {o.section.section_number}/"
            f"{o.subject.code} at {_label(o.timeslot)}"
        )

    blocked = {
        u.timeslot_id for u in
        db.query(FacultyUnavailability).filter(FacultyUnavailability.faculty_id == faculty_id)
    }
    if occupied_slots & blocked:
        issues.append(f"{new_faculty.name} is marked unavailable during one of this pair's slots")

    # Handing this component to someone else can join their existing classes
    # into a day with no free period left, on any day it meets.
    theirs = (
        db.query(Assignment)
        .filter(Assignment.run_id == a.run_id, Assignment.faculty_id == faculty_id)
        .all()
    )
    pair_ids = {r.id for r in pair_rows}
    for day in sorted({r.timeslot.day for r in pair_rows}):
        periods = {
            o.timeslot.period_index for o in theirs
            if o.id not in pair_ids and o.timeslot.day == day
        } | {r.timeslot.period_index for r in pair_rows if r.timeslot.day == day}
        issue = _break_issue(db, new_faculty, day, periods)
        if issue:
            issues.append(issue)

    return issues, {"assignment": a, "run": run, "pair_rows": pair_rows, "new_faculty": new_faculty}


def preview_faculty_change(db: Session, assignment_id: int, faculty_id: int) -> ValidationResult:
    issues, _ctx = _validate_faculty_change(db, assignment_id, faculty_id)
    return ValidationResult(ok=not issues, issues=issues)


def apply_faculty_change(
    db: Session, assignment_id: int, faculty_id: int,
    lock_after: bool = True, actor: str | None = None, reason: str | None = None,
    user_id: int | None = None, user_role: str | None = None,
) -> ChangeOutcome:
    """Change the faculty for the whole (section, subject) pair - every
    session this week (HC14: one faculty per pair), and only to a faculty
    eligible for the subject."""
    issues, ctx = _validate_faculty_change(db, assignment_id, faculty_id)
    if issues:
        return ChangeOutcome(ok=False, issues=issues)

    a, run, pair_rows, new_faculty = ctx["assignment"], ctx["run"], ctx["pair_rows"], ctx["new_faculty"]
    old_name = a.faculty.name
    for row in pair_rows:
        row.faculty_id = faculty_id

    entry = _record(
        db, run.id, a.section_id, a.subject_id, "faculty", old_name, new_faculty.name,
        actor, reason, user_id, user_role,
    )
    if lock_after:
        _lock(db, run, a.section_id, a.subject_id, faculty_id, a.room_id, a.session_type)

    try:
        _revalidate_or_rollback(db, run.id)
    except Invalid as exc:
        return ChangeOutcome(ok=False, issues=exc.issues)
    db.refresh(entry)
    return ChangeOutcome(ok=True, change_history_id=entry.id)


def _section_family(db: Session, section: Section) -> set[int]:
    """The sections whose students overlap this one's: its parent and its
    groups. Two groups of one section are different students, so a group's
    siblings are deliberately not included."""
    family = {
        s.id for s in
        db.query(Section).filter(Section.parent_section_id == section.id)
    }
    if section.parent_section_id is not None:
        family.add(section.parent_section_id)
    return family


def _periods_in_day(db: Session, day: str) -> int:
    """How many periods that day has, which is what a full day means."""
    return db.query(TimeSlot).filter(TimeSlot.day == day).count()


def _break_issue(
    db: Session, faculty: Faculty | None, day: str, periods: set[int]
) -> str | None:
    """Say so if this would leave a teacher no free period that day, or a run
    longer than the cap.

    The same two rules the solver enforces: a teacher gets one free period on
    any day they teach, and never teaches more than `faculty_max_consecutive`
    periods in a row. A run short of the cap is a preference the solver
    weighs, not something a manual edit is refused over.
    """
    if not periods:
        return None
    name = faculty.name if faculty is not None else "the teacher"
    total = _periods_in_day(db, day)
    if total and len(periods) >= total:
        return (
            f"{name} would teach all {total} periods on {day}, leaving no free period"
        )
    cap = settings.faculty_max_consecutive
    if cap > 0:
        best = run = 0
        previous = None
        for p in sorted(periods):
            run = run + 1 if previous is not None and p == previous + 1 else 1
            best = max(best, run)
            previous = p
        if best > cap:
            return (
                f"{name} would teach {best} periods in a row on {day}; "
                f"the most allowed is {cap}"
            )
    return None
