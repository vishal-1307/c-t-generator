"""Comparing two timetable versions.

Extracted from ``routers/generate.py`` so the comparison has one
implementation. The comparison
logic is unchanged; only its home moved.

Deliberately basic (spec #38 asked for "basic", not a diff library): the unit
of comparison is the ``(section, subject, session_type)`` component, because
that is the granularity at which a manual edit or a semester decision
actually happens in this model - a mixed subject's lecture and its practical
are independent obligations that can move, or change room/faculty,
independently of each other. Comparing individual ``Assignment`` rows would
report a two-hour lab moving as two unrelated changes; comparing by
``(section, subject)`` alone would blend a mixed subject's two components
into one arbitrary "before"/"after" pair.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .models import Assignment, TimetableRun


class UnknownRun(Exception):
    def __init__(self, run_id: int):
        super().__init__(f"Run {run_id} not found")
        self.run_id = run_id


@dataclass
class DiffRow:
    section_number: str
    subject_code: str
    change: str  # added | removed | moved | faculty_changed | room_changed
    detail: str


@dataclass
class VersionDiff:
    from_run_id: int
    to_run_id: int
    rows: list[DiffRow] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    moved: int = 0
    faculty_changed: int = 0
    room_changed: int = 0

    @property
    def total_changes(self) -> int:
        return (
            self.added + self.removed + self.moved
            + self.faculty_changed + self.room_changed
        )


def _labelled_code(a: Assignment) -> str:
    """The subject code, plus " (lecture)"/" (practical)" for a mixed subject
    - saying which component a diff row is about only matters when a subject
    has two."""
    if a.subject.type != "mixed":
        return a.subject.code
    return a.subject.code + (" (lecture)" if a.session_type == "L" else " (practical)")


def diff_runs(db: Session, from_run_id: int, to_run_id: int) -> VersionDiff:
    for run_id in (from_run_id, to_run_id):
        if db.get(TimetableRun, run_id) is None:
            raise UnknownRun(run_id)

    def _by_pair(run_id: int) -> dict[tuple[int, int, str], list[Assignment]]:
        out: dict[tuple[int, int, str], list[Assignment]] = {}
        for a in db.query(Assignment).filter(Assignment.run_id == run_id).all():
            out.setdefault((a.section_id, a.subject_id, a.session_type), []).append(a)
        return out

    before, after = _by_pair(from_run_id), _by_pair(to_run_id)
    diff = VersionDiff(from_run_id=from_run_id, to_run_id=to_run_id)

    for key in sorted(before.keys() - after.keys()):
        sample = before[key][0]
        diff.rows.append(DiffRow(
            sample.section.section_number, _labelled_code(sample), "removed",
            f"present in run {from_run_id}, gone from run {to_run_id}",
        ))
        diff.removed += 1

    for key in sorted(after.keys() - before.keys()):
        sample = after[key][0]
        diff.rows.append(DiffRow(
            sample.section.section_number, _labelled_code(sample), "added",
            f"new in run {to_run_id}",
        ))
        diff.added += 1

    for key in sorted(before.keys() & after.keys()):
        b, a = before[key], after[key]
        section_number, subject_code = b[0].section.section_number, _labelled_code(b[0])

        if b[0].faculty_id != a[0].faculty_id:
            diff.rows.append(DiffRow(
                section_number, subject_code, "faculty_changed",
                f"{b[0].faculty.name} -> {a[0].faculty.name}",
            ))
            diff.faculty_changed += 1

        if b[0].room_id != a[0].room_id:
            diff.rows.append(DiffRow(
                section_number, subject_code, "room_changed",
                f"{b[0].room.room_number} -> {a[0].room.room_number}",
            ))
            diff.room_changed += 1

        before_slots = sorted((r.timeslot.day_index, r.timeslot.period_index) for r in b)
        after_slots = sorted((r.timeslot.day_index, r.timeslot.period_index) for r in a)
        if before_slots != after_slots:
            diff.rows.append(DiffRow(
                section_number, subject_code, "moved",
                f"{before_slots} -> {after_slots}",
            ))
            diff.moved += 1

    return diff
