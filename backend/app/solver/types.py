"""The data the CP-SAT model works on, with nothing behind it.

These three dataclasses are the whole contract between "read the database" and
"build a model". `data.load()` fills them in; `model.build()` and
`objective.add_objective()` read them and nothing else.

Keeping them here, rather than next to the loader, is what makes that boundary
real instead of aspirational. This module imports `dataclasses` and `datetime`
and stops. Importing `solver.model` used to pull in SQLAlchemy, `app.models`,
`app.config`, and `app.database` - which opens a database engine at import time
- so anything that wanted to solve had to be able to reach a database first,
even though the solver itself never touches one. `tests/test_solver_purity.py`
holds that line mechanically.

`SolverInput.sections/subjects/faculty/rooms` are the exception: they hold ORM
rows and are populated only on the database path. Everything the model needs
for a decision is already resolved into ints by the loader, so those dicts are
there for labels and for one optional symmetry-breaking pass, and default to
empty when the input did not come from a database.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SlotInfo:
    """One timeslot, with the global ordering the model indexes by."""

    id: int
    index: int          # position in the globally sorted slot list
    day: str
    day_index: int
    period_index: int
    start_time: dt.time
    end_time: dt.time
    is_lunch: bool

    @property
    def label(self) -> str:
        return f"{self.day} P{self.period_index + 1} {self.start_time:%H:%M}"


@dataclass
class Pair:
    """One obligation the solver schedules: a section, a subject, and which
    component of it.

    ``sessions`` blocks must be placed, each occupying ``length`` contiguous
    non-lunch periods on a single day.

    A subject with only lectures or only practicals produces exactly one of
    these, with the counts it always had. A subject with both produces two,
    scheduled independently - which is what lets a lecture sit in a classroom
    while its practical sits in a lab, the ordinary shape of a real syllabus
    and previously inexpressible.
    """

    key: tuple[int, int, str]
    session_type: str                 # domain.LECTURE or domain.PRACTICAL
    section_id: int
    section_number: str
    strength: int
    subject_id: int
    subject_code: str
    subject_name: str
    subject_type: str
    length: int                       # session_length_hours
    sessions: int                     # sessions_per_week
    room_ids: list[int]               # eligible rooms, pre-filtered
    faculty_ids: list[int]            # eligible faculty, pre-filtered
    # Legal start positions: global slot index -> the indices it would occupy.
    # Per-pair (not shared) because section-availability filtering removes
    # some starts for some pairs but not others of the same block length.
    starts: dict[int, list[int]] = field(default_factory=dict)
    # Locked start indices this pair must reproduce exactly, one per session,
    # in ascending order (spec #9). Empty when not locked.
    locked_starts: list[int] = field(default_factory=list)

    @property
    def periods(self) -> int:
        return self.length * self.sessions

    @property
    def label(self) -> str:
        # The session type is only worth saying when a subject has more than
        # one component; naming it always would clutter every message about a
        # subject that only ever had one.
        return f"{self.section_number}/{self.subject_code}"

    @property
    def detailed_label(self) -> str:
        return f"{self.section_number}/{self.subject_code} ({self.session_type})"


@dataclass
class SolverInput:
    slots: list[SlotInfo]
    pairs: list[Pair]
    # ORM rows, and the only part of this that is not plain data. Populated by
    # `data.load()`; left empty by any other producer. Nothing the model needs
    # in order to *decide* lives here - see the module docstring.
    sections: dict[int, Any] = field(default_factory=dict)
    subjects: dict[int, Any] = field(default_factory=dict)
    faculty: dict[int, Any] = field(default_factory=dict)
    rooms: dict[int, Any] = field(default_factory=dict)
    # slot INDEX (not timeslot id) sets, keyed by resource id.
    blocked_faculty_slots: dict[int, set[int]] = field(default_factory=dict)
    blocked_room_slots: dict[int, set[int]] = field(default_factory=dict)
    # Which sections are groups of which. A section and one of its groups are
    # the same students and cannot be taught at once; two groups of the same
    # section are different students and can. The solver needs the hierarchy
    # to express that - see `model._add_section_clash` - and gets it as plain
    # ints so this whole object stays serialisable.
    section_children: dict[int, list[int]] = field(default_factory=dict)
    section_parent: dict[int, int] = field(default_factory=dict)
    # Locks that could not be honoured, and why. A lock is a decision someone
    # made deliberately; dropping one silently - which is what happened before
    # - means the next timetable quietly disagrees with an instruction and
    # nobody is told. These reach the run's warnings and the Generate page.
    dropped_locks: list[str] = field(default_factory=list)
    # The run length the department would rather not exceed - a preference,
    # not a rule, so it is weighted in the objective rather than enforced.
    # 0 switches the preference off. Carried as data rather than read from
    # settings, because the solver deliberately knows nothing about
    # configuration. (The break itself - a free period on any day a teacher
    # teaches - needs no parameter: it is a property of the grid.)
    faculty_soft_max_consecutive: int = 0
    # The longest run allowed at all: a hard rule, next to the break. 0 means
    # no cap, which is also what a hand-built input gets.
    faculty_max_consecutive: int = 0

    @property
    def teachable(self) -> list[SlotInfo]:
        return [s for s in self.slots if not s.is_lunch]

    def slot_by_index(self, index: int) -> SlotInfo:
        return self.slots[index]
