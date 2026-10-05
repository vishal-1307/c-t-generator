"""Resolving a reference against rows the import has not written yet.

This is the fix for the failure that started the redesign. Importing a
fourteen-sheet workbook rejected every Section-Subject row with

    academic context 2026-27 / Sem 5 / BCA (CSE) does not exist

while holding, three sheets earlier, the sheet that defined that very context.
Each row was checked against the database alone, so a reference to something
later in the same file could only ever be an error. The workbook was correct;
the importer's idea of "exists" was too narrow.

`StagedResolver` widens it to *the database plus everything this workbook
declares*. A row may legitimately point at a section defined on another sheet,
and validation can then judge the workbook as a whole - which is the only level
at which a workbook is either valid or not.

Nothing here writes. Staged entities are the parsed, validated payloads of
earlier sheets; the engine decides separately whether to commit them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..models import AcademicContext, Faculty, Room, Section, Subject, TimeSlot
from .resolver import DbResolver, norm


@dataclass
class _Staged:
    """One row a sheet intends to create, keyed the way it will be looked up."""

    payload: dict[str, Any]
    sheet: str
    row_number: int


@dataclass
class StagingArea:
    """What the workbook declares, in the order its sheets are processed.

    Deliberately keyed by the same natural keys the adapters use for identity,
    so "does this exist" gets the same answer whether the row is in the
    database or three sheets away.
    """

    contexts: dict[tuple, _Staged] = field(default_factory=dict)
    faculty: dict[str, _Staged] = field(default_factory=dict)
    subjects: dict[str, _Staged] = field(default_factory=dict)
    rooms: dict[tuple, _Staged] = field(default_factory=dict)
    room_codes: dict[str, _Staged] = field(default_factory=dict)
    sections: dict[tuple, _Staged] = field(default_factory=dict)
    timeslots: dict[tuple, _Staged] = field(default_factory=dict)

    # Indexes for the lookups that would otherwise scan. A curriculum sheet
    # resolves a section by number once per row, so scanning every staged
    # section each time makes the import quadratic in section count - invisible
    # on a demo file and the dominant cost on a real one.
    section_keys_by_number: dict[str, list[tuple]] = field(default_factory=dict)
    room_keys_by_number: dict[str, list[tuple]] = field(default_factory=dict)

    def add(self, entity: str, key, payload: dict[str, Any], sheet: str, row: int) -> None:
        staged = _Staged(payload=payload, sheet=sheet, row_number=row)
        if entity == "academic_contexts":
            self.contexts[key] = staged
        elif entity == "faculty":
            self.faculty[norm(payload.get("faculty_code", ""))] = staged
        elif entity == "subjects":
            self.subjects[norm(payload.get("code", ""))] = staged
        elif entity == "rooms":
            self.rooms[key] = staged
            code = payload.get("room_code")
            if code:
                self.room_codes[norm(code)] = staged
            number = norm(payload.get("room_number", ""))
            if number:
                keys = self.room_keys_by_number.setdefault(number, [])
                if key not in keys:
                    keys.append(key)
        elif entity == "sections":
            self.sections[key] = staged
            number = norm(payload.get("section_number", "")) or (
                key[-1] if isinstance(key, tuple) and key else ""
            )
            if number:
                keys = self.section_keys_by_number.setdefault(number, [])
                if key not in keys:
                    keys.append(key)
        elif entity == "timeslots":
            self.timeslots[key] = staged


class _Placeholder:
    """Stands in for a row that will exist once the import is applied.

    Validation needs an object with the attributes an adapter reads - an id
    above all - but no id exists before the write. A placeholder carries the
    staged payload and a sentinel id, and the engine swaps in the real row when
    it creates it. Never persisted, never returned to a caller.
    """

    def __init__(self, payload: dict[str, Any], entity: str):
        # `id` first, so a payload that already carries one (a row this import
        # has just written) keeps it. Setting it afterwards would blank the id
        # of every real row and write null foreign keys for everything that
        # referenced it.
        self.id = None
        self.__dict__.update(payload)
        self._entity = entity

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<pending {self._entity}>"


class StagedResolver:
    """Staging first, then the database.

    Order matters: a workbook that redefines something already stored should be
    judged on what it says, since that is what the import will make true.
    """

    def __init__(self, db: Session, staging: StagingArea):
        self._db = DbResolver(db)
        self._staging = staging
        self._placeholders: dict[tuple[str, Any], _Placeholder] = {}
        # Pending rows have no id until they are written, but validation still
        # has to tell them apart - two availability rows blocking two different
        # faculty are not the same row. Each placeholder therefore gets a
        # stable negative id, unique within this analysis and impossible to
        # confuse with a real one.
        self._next_pending_id = -1

    # -- helpers ------------------------------------------------------------

    def _placeholder(self, entity: str, key, payload: dict[str, Any]) -> _Placeholder:
        cached = self._placeholders.get((entity, key))
        if cached is None:
            cached = _Placeholder(payload, entity)
            if cached.id is None:
                cached.id = self._next_pending_id
                self._next_pending_id -= 1
            self._placeholders[(entity, key)] = cached
        return cached

    # -- the ReferenceResolver interface ------------------------------------

    def faculty_by_code(self, code: str) -> Faculty | None:
        staged = self._staging.faculty.get(norm(code))
        if staged is not None:
            return self._placeholder("faculty", norm(code), staged.payload)
        return self._db.faculty_by_code(code)

    def subject_by_code(self, code: str) -> Subject | None:
        staged = self._staging.subjects.get(norm(code))
        if staged is not None:
            return self._placeholder("subjects", norm(code), staged.payload)
        return self._db.subject_by_code(code)

    def room_by_key(self, block: str, floor: str, number: str) -> Room | None:
        key = (norm(block), norm(floor), norm(number))
        staged = self._staging.rooms.get(key)
        if staged is not None:
            return self._placeholder("rooms", key, staged.payload)
        return self._db.room_by_key(block, floor, number)

    def room_by_number(self, number: str) -> Room | None:
        # Availability sheets name a room the way people do, which in a
        # multi-building institution is its code ("36-201"), not its number.
        # Try the code first, then fall back to a bare number.
        by_code = self.room_by_code(number)
        if by_code is not None:
            return by_code
        for key in self._staging.room_keys_by_number.get(norm(number), []):
            staged = self._staging.rooms.get(key)
            if staged is not None:
                return self._placeholder("rooms", key, staged.payload)
        return self._db.room_by_number(number)

    def room_by_code(self, code: str) -> Room | None:
        staged = self._staging.room_codes.get(norm(code))
        if staged is not None:
            key = (
                norm(staged.payload.get("block", "")),
                norm(staged.payload.get("floor", "")),
                norm(staged.payload.get("room_number", "")),
            )
            return self._placeholder("rooms", key, staged.payload)
        return self._db.room_by_code(code)

    def context(
        self, year: str, semester: str, program: str, department: str
    ) -> AcademicContext | None:
        key = (norm(year), norm(semester), norm(program), norm(department))
        staged = self._staging.contexts.get(key)
        if staged is not None:
            return self._placeholder("academic_contexts", key, staged.payload)
        return self._db.context(year, semester, program, department)

    def section(self, context_id, number: str) -> Section | None:
        for key in self._staging.section_keys_by_number.get(norm(number), []):
            staged = self._staging.sections.get(key)
            if staged is None:
                continue
            # A staged section's context may itself be pending, in which case
            # its id is None and matching by id would fail for a section that
            # is perfectly well defined. Fall back to identity.
            staged_ctx = staged.payload.get("academic_context_id")
            if staged_ctx == context_id or staged_ctx is None:
                return self._placeholder("sections", key, staged.payload)
        if context_id is None:
            return None
        return self._db.section(context_id, number)

    def section_anywhere(self, number: str) -> tuple[Section | None, str | None]:
        """A section by number alone, for sheets that do not name a context.

        Returns the section and, when it cannot be resolved, the sentence a
        user should read. Ambiguity is reported rather than guessed: picking
        one of two sections that share a number would attach lab groups to the
        wrong cohort, and nothing downstream would notice.
        """
        matches = [
            (key, self._staging.sections[key])
            for key in self._staging.section_keys_by_number.get(norm(number), [])
            if key in self._staging.sections
        ]
        if len(matches) == 1:
            key, staged = matches[0]
            return self._placeholder("sections", key, staged.payload), None
        if len(matches) > 1:
            return None, (
                f"section {number!r} is defined more than once in this workbook, "
                "in different academic contexts - add the context columns to "
                "this sheet so it is clear which one is meant"
            )

        existing = self._db.sections_by_number(number)
        if len(existing) == 1:
            return existing[0], None
        if len(existing) > 1:
            return None, (
                f"section {number!r} exists in {len(existing)} academic contexts - "
                "add the context columns to this sheet so it is clear which one "
                "is meant"
            )
        return None, (
            f"section {number!r} was not found - it must appear in the Sections "
            "sheet or already exist"
        )

    def timeslot(self, day: str, period_index: int) -> TimeSlot | None:
        key = (norm(day), period_index)
        staged = self._staging.timeslots.get(key)
        if staged is not None:
            return self._placeholder("timeslots", key, staged.payload)
        return self._db.timeslot(day, period_index)

    def configured_days(self) -> list[str]:
        """Days from the workbook when it defines a grid, else from the database.

        Reporting the stored days while the file is replacing them would name
        the wrong set in an error message about a day that is, in this
        workbook, perfectly valid.
        """
        if self._staging.timeslots:
            seen: dict[int, str] = {}
            for staged in self._staging.timeslots.values():
                index = staged.payload.get("day_index", 0)
                seen.setdefault(index, staged.payload.get("day", ""))
            return [seen[i] for i in sorted(seen)]
        return self._db.configured_days()

    def remember(self, obj: object) -> None:
        self._db.remember(obj)

    #: Entities later sheets resolve by name. Junction rows are never
    #: referenced by anything, and have no id of their own to hand back.
    RESOLVABLE = {
        "academic_contexts", "faculty", "subjects", "rooms", "sections", "timeslots",
    }

    def replace_with_real(self, entity: str, key, obj) -> None:
        """Swap a pending stand-in for the row that now exists.

        A placeholder is cached the first time it is resolved, so once the real
        row is written the cached one has to go - otherwise a later sheet keeps
        resolving to a stand-in whose id is None and writes a null foreign key.
        """
        if entity not in self.RESOLVABLE:
            return
        self._placeholders.pop((entity, key), None)
        payload = {
            column.name: getattr(obj, column.name) for column in obj.__table__.columns
        }
        self._staging.add(entity, key, payload, "", 0)
