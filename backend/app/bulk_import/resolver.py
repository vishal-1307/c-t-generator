"""How a row's references are looked up.

Every adapter that references another entity - a section-subject row naming a
section, an availability row naming a time slot - has to turn that name into a
database row. Until now each did it with its own point query, per row, which
meant a five-thousand-row file issued fifteen thousand queries, and did so
twice because `apply` re-runs `analyse`.

Two things change here, and the second is the reason this exists at all:

1. `DbResolver` loads each referenced table once per analysis and answers from
   memory. Same answers, a constant number of queries.

2. Lookup is now an interface. A whole-workbook import needs a row to resolve
   against entities defined in *another sheet of the same file* - a section
   that will exist after the import but does not exist yet. That is impossible
   to express when every adapter queries the database directly, and is the
   exact reason the live importer rejected a valid workbook with "academic
   context does not exist" while holding the sheet that defined it. A resolver
   that consults staged rows before the database makes it a normal case.

The keys are normalised the same way identities are, so a lookup and an
identity cannot disagree about whether two spellings are the same thing.
"""
from __future__ import annotations

from typing import Protocol

from sqlalchemy.orm import Session

from ..models import AcademicContext, Faculty, Room, Section, Subject, TimeSlot


def norm(value: str | None) -> str:
    """Lower-cased, whitespace-collapsed - the same normalisation identities use."""
    return " ".join(str(value or "").split()).lower()


class ReferenceResolver(Protocol):
    """Turning a name in a file into the row it refers to.

    Returns ``None`` when nothing matches, and the adapter turns that into the
    row-level error a user reads. Implementations must not raise for a missing
    reference - "not found" is data, not a fault.
    """

    def faculty_by_code(self, code: str) -> Faculty | None: ...

    def subject_by_code(self, code: str) -> Subject | None: ...

    def room_by_key(self, block: str, floor: str, number: str) -> Room | None: ...

    def room_by_number(self, number: str) -> Room | None:
        """A room by number alone.

        Rooms are identified by (block, floor, number) everywhere else, because
        two buildings can both have a "301". Some sheets name only the number,
        so this stays for that path and returns the first match - the ambiguity
        is in the file, not something this can resolve.
        """
        ...

    def room_by_code(self, code: str) -> Room | None:
        """A room by its institution-wide label, e.g. "36-201".

        This is how availability sheets actually name rooms in a
        multi-building institution, because a bare number is ambiguous there.
        """
        ...

    def sections_by_number(self, number: str) -> list[Section]:
        """Every section with this number, across all contexts.

        A list rather than one row on purpose: the same number can legitimately
        exist in two semesters, and a caller resolving without a context needs
        to know that before choosing.
        """
        ...

    def context(
        self, year: str, semester: str, program: str, department: str
    ) -> AcademicContext | None: ...

    def section(self, context_id: int, number: str) -> Section | None: ...

    def timeslot(self, day: str, period_index: int) -> TimeSlot | None: ...

    def section_anywhere(self, number: str) -> tuple[Section | None, str | None]:
        """A section by number alone, plus the reason when that is ambiguous.

        Sheets that describe one intake often name a section without its
        context. Returning the reason rather than guessing matters: silently
        picking one of two sections sharing a number would attach data to the
        wrong cohort, and nothing downstream would notice.
        """
        ...

    def configured_days(self) -> list[str]:
        """Day names present in the grid, for the "no such day" message."""
        ...


class DbResolver:
    """Resolves against what is already committed, prefetched once.

    The cache is per-analysis and deliberately not shared between requests: an
    import must see the database as it is when it runs, not as it was when some
    earlier import ran.
    """

    def __init__(self, db: Session):
        self._db = db
        self._faculty: dict[str, Faculty] | None = None
        self._subjects: dict[str, Subject] | None = None
        self._rooms: dict[tuple[str, str, str], Room] | None = None
        self._contexts: dict[tuple[str, str, str, str], AcademicContext] | None = None
        self._sections: dict[tuple[int, str], Section] | None = None
        self._slots: dict[tuple[str, int], TimeSlot] | None = None
        self._days: list[str] | None = None
        self._room_codes: dict[str, Room] | None = None

    # -- prefetches ---------------------------------------------------------

    def faculty_by_code(self, code: str) -> Faculty | None:
        if self._faculty is None:
            self._faculty = {
                norm(f.faculty_code): f for f in self._db.query(Faculty).all()
            }
        return self._faculty.get(norm(code))

    def subject_by_code(self, code: str) -> Subject | None:
        if self._subjects is None:
            self._subjects = {norm(s.code): s for s in self._db.query(Subject).all()}
        return self._subjects.get(norm(code))

    def room_by_key(self, block: str, floor: str, number: str) -> Room | None:
        if self._rooms is None:
            self._rooms = {
                (norm(r.block), norm(r.floor), norm(r.room_number)): r
                for r in self._db.query(Room).all()
            }
        return self._rooms.get((norm(block), norm(floor), norm(number)))

    def room_by_number(self, number: str) -> Room | None:
        # Try the institution-wide code first. A sheet that names "36-201"
        # means the room in block 36, and a bare-number search would miss it -
        # the same order the workbook resolver uses, so a single-entity upload
        # and a workbook cannot disagree about which room was meant.
        by_code = self.room_by_code(number)
        if by_code is not None:
            return by_code

        if self._rooms is None:
            self.room_by_key("", "", "")  # prime the cache
        assert self._rooms is not None
        wanted = norm(number)
        for (_block, _floor, num), room in self._rooms.items():
            if num == wanted:
                return room
        return None

    def section_anywhere(self, number: str) -> tuple[Section | None, str | None]:
        """A section by number alone, with the reason when that is not enough."""
        matches = self.sections_by_number(number)
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, (
                f"section {number!r} exists in {len(matches)} academic contexts - "
                "add the context columns to this sheet so it is clear which one "
                "is meant"
            )
        return None, (
            f"section {number!r} was not found - it must appear in the Sections "
            "sheet or already exist"
        )

    def room_by_code(self, code: str) -> Room | None:
        if self._room_codes is None:
            self._room_codes = {
                norm(r.room_code): r
                for r in self._db.query(Room).all()
                if r.room_code
            }
        return self._room_codes.get(norm(code))

    def sections_by_number(self, number: str) -> list[Section]:
        if self._sections is None:
            self.section(-1, "")  # prime the cache
        assert self._sections is not None
        wanted = norm(number)
        return [s for (_ctx, num), s in self._sections.items() if num == wanted]

    def context(
        self, year: str, semester: str, program: str, department: str
    ) -> AcademicContext | None:
        if self._contexts is None:
            self._contexts = {
                (
                    norm(c.academic_year),
                    norm(str(c.semester)),
                    norm(c.program),
                    norm(c.department),
                ): c
                for c in self._db.query(AcademicContext).all()
            }
        return self._contexts.get(
            (norm(year), norm(semester), norm(program), norm(department))
        )

    def section(self, context_id: int, number: str) -> Section | None:
        if self._sections is None:
            self._sections = {
                (s.academic_context_id, norm(s.section_number)): s
                for s in self._db.query(Section).all()
            }
        return self._sections.get((context_id, norm(number)))

    def timeslot(self, day: str, period_index: int) -> TimeSlot | None:
        self._load_slots()
        assert self._slots is not None
        return self._slots.get((norm(day), period_index))

    def configured_days(self) -> list[str]:
        self._load_slots()
        assert self._days is not None
        return self._days

    def _load_slots(self) -> None:
        if self._slots is not None:
            return
        slots = self._db.query(TimeSlot).all()
        self._slots = {(norm(s.day), s.period_index): s for s in slots}
        seen: dict[int, str] = {}
        for s in slots:
            seen.setdefault(s.day_index, s.day)
        self._days = [seen[i] for i in sorted(seen)]

    # -- write-through ------------------------------------------------------

    def remember(self, obj: object) -> None:
        """Add a row created during this import to the caches.

        Within one file, later rows may reference something an earlier row
        created. Without this the second reference would miss the prefetched
        cache and be reported as unknown - correct against the database as it
        was, and wrong about the import as a whole.
        """
        if isinstance(obj, Faculty) and self._faculty is not None:
            self._faculty[norm(obj.faculty_code)] = obj
        elif isinstance(obj, Subject) and self._subjects is not None:
            self._subjects[norm(obj.code)] = obj
        elif isinstance(obj, Room):
            if self._rooms is not None:
                self._rooms[
                    (norm(obj.block), norm(obj.floor), norm(obj.room_number))
                ] = obj
            if self._room_codes is not None and obj.room_code:
                self._room_codes[norm(obj.room_code)] = obj
        elif isinstance(obj, Section) and self._sections is not None:
            self._sections[(obj.academic_context_id, norm(obj.section_number))] = obj
        elif isinstance(obj, AcademicContext) and self._contexts is not None:
            self._contexts[
                (
                    norm(obj.academic_year),
                    norm(str(obj.semester)),
                    norm(obj.program),
                    norm(obj.department),
                )
            ] = obj
