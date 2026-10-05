"""Loading DB rows into plain dataclasses for the CP-SAT model.

The model builder never touches the ORM. Everything it needs is resolved here
once - room eligibility, faculty eligibility, availability, persisted
assignments, and the set of legal start positions for each block length - so
the builder is pure arithmetic over ints.

Room eligibility (TIMETABLE_LOGIC_SPEC.md #14/#15/#16) is resolved in this
priority order per subject:

1. ``Subject.fixed_room_id`` - an explicit single-room override.
2. ``Subject.allowed_rooms`` - an explicit whitelist.
3. Default: room_type + lab_type + capacity matching, excluding rooms pinned
   (``Room.fixed_subject_id``) to a *different* subject and excluding faculty
   rooms and inactive rooms unconditionally.

Persistence (spec #7/#8) is a pre-filter, not a new constraint type: when a
(section, subject) already has a ``SectionSubjectAssignment`` with a faculty
and/or room recorded, this pair's candidate lists are narrowed to exactly that
choice before the model is ever built - the solver structurally cannot pick
anything else.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from ..config import settings
from ..domain import session_specs
from ..grid import is_working_day
from ..room_scope import usable_rooms
from ..models import (
    Assignment,
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


# The model's data lives in `types`, which imports nothing but `dataclasses`
# and `datetime`. Re-exported here because this module is where every caller
# has always got them from, and moving them should not be their problem.
from .types import Pair, SlotInfo, SolverInput  # noqa: E402  (kept at the seam)

__all__ = ["Pair", "SlotInfo", "SolverInput", "load"]



def _legal_starts(slots: list[SlotInfo], length: int) -> dict[int, list[int]]:
    """Every start position where a ``length``-period block fits.

    A block is legal only if all its periods are on the same day, carry
    consecutive period_index values, and none is lunch. Encoding this in the
    *domain* of the start variables is what makes hard constraints 7 (no lunch)
    and 9 (contiguity) structural - the solver cannot express a violation.
    """
    legal: dict[int, list[int]] = {}
    for i, slot in enumerate(slots):
        block = slots[i : i + length]
        if len(block) < length:
            continue
        if any(s.is_lunch for s in block):
            continue
        if any(s.day_index != slot.day_index for s in block):
            continue
        if any(
            block[k + 1].period_index != block[k].period_index + 1
            for k in range(length - 1)
        ):
            continue
        legal[i] = [s.index for s in block]
    return legal


def _blocked_slot_indices(
    rows: list, slot_index_by_id: dict[int, int], owner_attr: str
) -> dict[int, set[int]]:
    """Turn Unavailability rows into {owner_id: {slot_index, ...}}."""
    out: dict[int, set[int]] = {}
    for row in rows:
        owner_id = getattr(row, owner_attr)
        idx = slot_index_by_id.get(row.timeslot_id)
        if idx is not None:
            out.setdefault(owner_id, set()).add(idx)
    return out


def _eligible_rooms(
    subject: Subject,
    section: Section,
    rooms: dict[int, Room],
    session_type: str | None = None,
) -> list[Room]:
    """The candidate room pool for one component of one (section, subject).

    `session_type` decides which kind of room the component belongs in: a
    lecture wants a teaching room, a practical wants a lab. Omitted, it is
    derived from the subject, which is what every caller wanted before a
    subject could have two components.

    Capability requirements narrow the pool further. A programming practical
    can run in an ordinary classroom when students can use their own machines
    and charge them; an electronics one cannot. Modelling that is what lets
    scarce labs go to the sessions that genuinely need them.

    Explicit overrides still win. `fixed_room_id` and `allowed_rooms` are an
    administrator saying "this subject uses these rooms", and are honoured
    ahead of any capability matching - though the universal filter below still
    applies, so an override can never put a class in an office or a room too
    small for the section.
    """
    from ..domain import PRACTICAL, room_kind_for

    if session_type is None:
        session_type = PRACTICAL if subject.type == "practical" else "L"
    wanted = room_kind_for(subject, session_type)

    if subject.fixed_room_id is not None:
        candidates = [rooms[subject.fixed_room_id]] if subject.fixed_room_id in rooms else []
    elif subject.allowed_rooms:
        candidates = list(subject.allowed_rooms)
    elif wanted == "theory":
        candidates = [
            r
            for r in rooms.values()
            if r.room_type == "theory"
            and (r.fixed_subject_id is None or r.fixed_subject_id == subject.id)
        ]
    else:
        candidates = [
            r
            for r in rooms.values()
            if r.room_type == "lab"
            and (r.fixed_subject_id is None or r.fixed_subject_id == subject.id)
            and (
                subject.required_lab_type is None
                or r.lab_type == subject.required_lab_type
            )
        ]

    # Capability requirements describe a practical - a lecture needs a room,
    # not a bench - so by default they narrow only that component.
    #
    # A teaching-load sheet can mark BYOD on a lecture row, and that is
    # genuinely ambiguous: it may mean "students bring laptops, so the room has
    # to suit that", or it may be a note that they are allowed to. Enforcing
    # the first reading when the second was meant makes rooms unusable for no
    # reason; ignoring it when the first was meant puts a class in a room that
    # cannot host it. Neither is a guess worth making here, so the reading is a
    # setting the department chooses, defaulting to what has always happened.
    applies = session_type == PRACTICAL or settings.enforce_byod_on_lectures
    needs_byod = applies and getattr(subject, "byod_required", False)
    needs_power = applies and getattr(subject, "charging_required", False)

    return [
        r
        for r in candidates
        if r.is_active
        and r.room_type != "faculty"
        and r.capacity >= section.strength
        and (not needs_byod or getattr(r, "byod", False))
        and (not needs_power or getattr(r, "charging", False))
    ]


def _type_suffix(subject, spec) -> str:
    """" (lecture)" or " (practical)", but only when the subject has both.

    Saying "the practical" about a subject with only practicals is noise; not
    saying it about a subject with both leaves the reader unable to tell which
    half a message is about.
    """
    from ..domain import LECTURE, MIXED

    if subject.type != MIXED:
        return ""
    return " (lecture)" if spec.session_type == LECTURE else " (practical)"


def _reproduce_lock(
    history: list[Assignment],
    spec,
    pair_starts: dict[int, list[int]],
    slot_index_by_id: dict[int, int],
) -> tuple[list[int], str | None]:
    """The exact starts a locked component must reoccupy, or why it cannot.

    A lock says "this class stays where it is". Honouring it means finding the
    same blocks in the previous run and confirming each still fits the grid as
    it now stands. When that is impossible the lock is dropped - it was already
    - but the reason is returned so somebody is told, rather than discovering
    later that a pinned class quietly moved.
    """
    if not history:
        return [], (
            "it is locked, but no previous timetable has this class in it, so "
            "there is nothing to reproduce."
        )

    by_block: dict[int, list[int]] = {}
    for row in history:
        by_block.setdefault(row.block_id, []).append(slot_index_by_id[row.timeslot_id])
    blocks = sorted((sorted(v) for v in by_block.values()), key=lambda v: v[0])

    if len(blocks) != spec.count:
        return [], (
            f"it is locked to {len(blocks)} session(s) a week but now needs "
            f"{spec.count}, so the old placement cannot be reused. Unlock it, "
            "or set the weekly load back."
        )
    if any(len(b) != spec.length for b in blocks):
        return [], (
            f"it is locked to blocks of a different length than the "
            f"{spec.length} period(s) it now needs, so the old placement cannot "
            "be reused."
        )

    starts = [b[0] for b in blocks]
    if not all(
        t in pair_starts and pair_starts[t] == blocks[i]
        for i, t in enumerate(starts)
    ):
        return [], (
            "it is locked to slots that are no longer usable - the time grid or "
            "this section's availability changed underneath it - so it had to be "
            "scheduled again."
        )
    return sorted(starts), None


def _locked_history_index(
    db: Session,
    academic_context_id: int,
    wanted: set[tuple[int, int, str]],
) -> dict[tuple[int, int, str], list[Assignment]]:
    """The previous placement of each locked pair, in two queries rather than 2N.

    A locked placement is reproduced from the rows of the newest run that has
    any - so the lookup is "the latest run per pair", which used to be a pair
    of queries executed inside the pair loop. At thirty sections that was
    hundreds of round trips before the solver started; with session types it
    would have been more again.

    ``wanted`` is the set of pairs actually locked. Nothing locked means no
    history is needed at all, which is the common case: an ordinary generate
    would otherwise read every assignment of every previous run in the context
    only to discard all of them.
    """
    if not wanted:
        return {}

    runs = (
        db.query(TimetableRun.id)
        .filter(
            TimetableRun.academic_context_id == academic_context_id,
            # A run only has all of its rows once its status is terminal - that
            # is the ordering guarantee persist() is built around. A run still
            # in progress has some rows or none, and reading a lock's previous
            # placement out of a half-written run makes the block look the
            # wrong length, which is reported as a lock that cannot be
            # honoured. The lock is fine; the history was read too early.
            TimetableRun.status != "RUNNING",
        )
        .order_by(TimetableRun.created_at.desc(), TimetableRun.id.desc())
        .all()
    )
    if not runs:
        return {}
    order = {run_id: position for position, (run_id,) in enumerate(runs)}

    rows = [
        row
        for row in (
            db.query(Assignment)
            .filter(
                Assignment.run_id.in_(order),
                Assignment.section_id.in_({k[0] for k in wanted}),
                Assignment.subject_id.in_({k[1] for k in wanted}),
            )
            .all()
        )
        # The two IN clauses cut the rows down in SQL; this drops the few
        # combinations they let through that are not themselves locked.
        if (row.section_id, row.subject_id, row.session_type) in wanted
    ]

    # Keep only the rows of the newest run that mentions each pair.
    best_run: dict[tuple[int, int, str], int] = {}
    for row in rows:
        key = (row.section_id, row.subject_id, row.session_type)
        position = order[row.run_id]
        if key not in best_run or position < order[best_run[key]]:
            best_run[key] = row.run_id

    out: dict[tuple[int, int, str], list[Assignment]] = {}
    for row in rows:
        key = (row.section_id, row.subject_id, row.session_type)
        if row.run_id == best_run.get(key):
            out.setdefault(key, []).append(row)
    return out


def load(
    db: Session,
    academic_context_id: int,
    section_ids: list[int] | None = None,
    respect_persisted: bool = True,
) -> SolverInput:
    """Build the solver's view of the database for one academic context.

    ``section_ids`` further restricts the problem to a subset of that
    context's sections. ``respect_persisted`` seeds each pair's candidates
    from any existing ``SectionSubjectAssignment`` (spec #7/#8) and, for
    locked pairs, additionally pins the exact slot(s) (spec #9).
    """
    # Only the working week. A Saturday row in the grid is data an admin may
    # have seeded, not a day anyone teaches - and dropping it here, before the
    # slots are numbered, keeps every downstream index contiguous.
    slot_rows = [
        s for s in
        db.query(TimeSlot).order_by(TimeSlot.day_index, TimeSlot.period_index).all()
        if is_working_day(s.day)
    ]
    slots = [
        SlotInfo(
            id=s.id, index=i, day=s.day, day_index=s.day_index,
            period_index=s.period_index, start_time=s.start_time,
            end_time=s.end_time, is_lunch=s.is_lunch,
        )
        for i, s in enumerate(slot_rows)
    ]
    slot_index_by_id = {s.id: s.index for s in slots}

    section_rows = (
        db.query(Section)
        .filter(Section.academic_context_id == academic_context_id, Section.is_active.is_(True))
        .order_by(Section.section_number).all()
    )
    if section_ids is not None:
        wanted = set(section_ids)
        section_rows = [s for s in section_rows if s.id in wanted]

    # The intake's own room list, if its rooms came from one.
    rooms = {r.id: r for r in usable_rooms(db, academic_context_id)}
    faculty = {f.id: f for f in db.query(Faculty).all()}
    subjects = {s.id: s for s in db.query(Subject).all()}

    blocked_faculty = _blocked_slot_indices(
        db.query(FacultyUnavailability).all(), slot_index_by_id, "faculty_id"
    )
    blocked_rooms = _blocked_slot_indices(
        db.query(RoomUnavailability).all(), slot_index_by_id, "room_id"
    )
    blocked_sections = _blocked_slot_indices(
        db.query(SectionUnavailability).all(), slot_index_by_id, "section_id"
    )

    persisted: dict[tuple[int, int, str], SectionSubjectAssignment] = {}
    if respect_persisted:
        rows = (
            db.query(SectionSubjectAssignment)
            .filter(SectionSubjectAssignment.academic_context_id == academic_context_id)
            .all()
        )
        # Keyed per component: a subject's lecture and its practical are
        # decided separately and can legitimately have different rooms, so one
        # persisted row per pair would make them share one.
        persisted = {
            (r.section_id, r.subject_id, r.session_type): r for r in rows
        }

    starts_by_length: dict[int, dict[int, list[int]]] = {}

    pairs: list[Pair] = []
    dropped_locks: list[str] = []
    locked_history = _locked_history_index(
        db,
        academic_context_id,
        {key for key, row in persisted.items() if row.locked},
    )

    for section in section_rows:
        section_blocked = blocked_sections.get(section.id, set())
        for subject in sorted(section.subjects, key=lambda s: s.code):
            # Phase 10: an inactive subject is inert, same as one nobody
            # takes (validation.py's own long-standing rule) - excluded from
            # pair-building rather than raising, since a section can list an
            # inactive subject in its curriculum without that being an error.
            if not subject.is_active:
                continue

            # One obligation per component. A subject with a single component
            # yields exactly one, with the counts it always had - which is why
            # existing datasets produce an identical problem to before.
            for spec in session_specs(subject, section):
                if spec.count <= 0:
                    continue
                length = spec.length

                if length not in starts_by_length:
                    starts_by_length[length] = _legal_starts(slots, length)
                base_starts = starts_by_length[length]

                # HC10: a section cannot be scheduled during its own blocked
                # slots. Filtered per-pair (not the shared cache) since
                # different sections block different slots.
                if section_blocked:
                    pair_starts = {
                        t: covered
                        for t, covered in base_starts.items()
                        if not (set(covered) & section_blocked)
                    }
                else:
                    pair_starts = base_starts

                room_ids = [
                    r.id
                    for r in _eligible_rooms(
                        subject, section, rooms, spec.session_type
                    )
                ]
                faculty_ids = sorted(f.id for f in subject.faculties if f.is_active)

                locked_starts: list[int] = []
                key = (section.id, subject.id, spec.session_type)
                assignment = persisted.get(key)
                if assignment is not None:
                    if (
                        assignment.faculty_id is not None
                        and assignment.faculty_id in faculty_ids
                    ):
                        faculty_ids = [assignment.faculty_id]
                    elif assignment.faculty_id is not None:
                        dropped_locks.append(
                            f"{section.section_number}/{subject.code}"
                            f"{_type_suffix(subject, spec)}: the faculty member "
                            "previously assigned is no longer eligible for this "
                            "subject, so one was chosen again."
                        )
                    if assignment.room_id is not None and assignment.room_id in room_ids:
                        room_ids = [assignment.room_id]
                    elif assignment.room_id is not None:
                        dropped_locks.append(
                            f"{section.section_number}/{subject.code}"
                            f"{_type_suffix(subject, spec)}: the room previously "
                            "assigned no longer suits this class (capacity, type "
                            "or availability changed), so one was chosen again."
                        )

                    if assignment.locked:
                        locked_starts, why = _reproduce_lock(
                            locked_history.get(key, []),
                            spec,
                            pair_starts,
                            slot_index_by_id,
                        )
                        if locked_starts:
                            pair_starts = {t: pair_starts[t] for t in locked_starts}
                        elif why:
                            dropped_locks.append(
                                f"{section.section_number}/{subject.code}"
                                f"{_type_suffix(subject, spec)}: {why}"
                            )

                pairs.append(
                    Pair(
                        key=key,
                        session_type=spec.session_type,
                        section_id=section.id,
                        section_number=section.section_number,
                        strength=section.strength,
                        subject_id=subject.id,
                        subject_code=subject.code,
                        subject_name=subject.name,
                        subject_type=subject.type,
                        length=length,
                        sessions=spec.count,
                        room_ids=room_ids,
                        faculty_ids=faculty_ids,
                        starts=pair_starts,
                        locked_starts=locked_starts,
                    )
                )

    # The section hierarchy, as ints, restricted to the sections actually being
    # scheduled. Restricting matters: a link to a parent that is not in this
    # solve would make the clash rule below quietly vacuous, and a timetable
    # that looks fine while double-booking students is the worst outcome
    # available. `validation` reports that case; here it is simply dropped, so
    # the model never carries a constraint it cannot enforce.
    loaded = {s.id for s in section_rows}
    section_parent: dict[int, int] = {}
    section_children: dict[int, list[int]] = {}
    for s in section_rows:
        parent_id = s.parent_section_id
        if parent_id is None or parent_id not in loaded:
            continue
        section_parent[s.id] = parent_id
        section_children.setdefault(parent_id, []).append(s.id)

    return SolverInput(
        slots=slots,
        pairs=pairs,
        sections={s.id: s for s in section_rows},
        subjects=subjects,
        faculty=faculty,
        rooms=rooms,
        blocked_faculty_slots=blocked_faculty,
        blocked_room_slots=blocked_rooms,
        section_children=section_children,
        section_parent=section_parent,
        dropped_locks=dropped_locks,
        faculty_soft_max_consecutive=max(0, settings.faculty_soft_max_consecutive),
        faculty_max_consecutive=max(0, settings.faculty_max_consecutive),
    )
