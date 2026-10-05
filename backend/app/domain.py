"""How a subject's weekly load becomes the sessions a solver schedules.

One function, `session_specs`, and everything that needs to know "what does
this subject actually require each week" goes through it: the solver when it
builds pairs, validation when it checks the week has room for them, and the
importer when it reports what a row means.

The reason it exists is that a subject can have two components. CAP460 is two
lectures and four practical periods a week, and those belong in different kinds
of room. Before this, a subject carried exactly one `(sessions_per_week x
session_length_hours)` and a type of theory *or* practical, so such a subject
could only be modelled as two subjects with two invented codes - which is not
what any institution's data looks like, and made the codes in a timetable stop
matching the codes in the syllabus.

Keeping the decision here rather than at each call site means the solver, the
validator and the importer cannot disagree about what a subject requires. It
also means Phase 6 changes how sessions are scheduled without changing what
they are.
"""
from __future__ import annotations

from dataclasses import dataclass

# What kind of session this is. Deliberately terse: these values reach the
# database (`assignment.session_type`) and a timetable cell badge.
LECTURE = "L"
PRACTICAL = "P"
SESSION_TYPES = (LECTURE, PRACTICAL)

# The three shapes a subject can take.
THEORY = "theory"
PRACTICAL_ONLY = "practical"
MIXED = "mixed"
DELIVERY_TYPES = (THEORY, PRACTICAL_ONLY, MIXED)


@dataclass(frozen=True)
class SessionSpec:
    """One component of a subject's week.

    `count` blocks of `length` consecutive periods each. A pure subject yields
    exactly one of these, with the same numbers it always had - which is what
    makes the change invisible to existing data.
    """

    session_type: str
    count: int
    length: int

    @property
    def periods(self) -> int:
        return self.count * self.length


def session_specs(subject, section=None) -> list[SessionSpec]:
    """Everything `subject` needs scheduled each week, for one section taking it.

    A theory or practical subject returns a single spec. Its weekly count is
    the section's own - the teaching load states it per row, and two sections
    can take one subject a different number of times - falling back to the
    subject's count where the section has none, as every mapping made before
    that was recorded does.

    A mixed subject returns two - the lecture component and the practical
    component - which the caller schedules independently, because they need
    different rooms. Its counts are the subject's, per component; one number
    per section could not say how to split between the two.

    Pass `section` wherever a count matters. Callers asking only which
    components exist, or how long a block is, can leave it out.
    """
    if subject.type == MIXED:
        return [
            SessionSpec(
                LECTURE,
                subject.lecture_sessions_per_week or 0,
                subject.lecture_session_length or 1,
            ),
            SessionSpec(
                PRACTICAL,
                subject.practical_sessions_per_week or 0,
                subject.practical_session_length or 1,
            ),
        ]

    session_type = PRACTICAL if subject.type == PRACTICAL_ONLY else LECTURE
    own = section.sessions_per_week_for(subject.id) if section is not None else None
    return [
        SessionSpec(
            session_type, own or subject.sessions_per_week, subject.session_length_hours
        )
    ]


def periods_per_week(subject, section=None) -> int:
    """Total periods a section spends on this subject each week."""
    return sum(spec.periods for spec in session_specs(subject, section))


def is_mixed(subject) -> bool:
    return subject.type == MIXED


def room_kind_for(subject, session_type: str) -> str:
    """Which room type a given session of this subject belongs in.

    Lectures go in ordinary teaching rooms; practicals go in labs. For a pure
    subject this simply restates its type, which is why the existing single-
    component behaviour is unchanged.

    Capability requirements (`byod_required`, `charging_required`) narrow the
    pool further and are applied by the solver's room eligibility, not here -
    this answers only "classroom or lab".
    """
    if session_type == PRACTICAL:
        return "lab"
    return "theory"
