"""Working out what each sheet of a workbook is.

An institution's workbook is organised for people: sheets are named
``03_Rooms`` or ``Room Master``, ordered for reading, and interleaved with
summaries and notes. Requiring exact sheet names would make a correct file fail
for a cosmetic reason, so a sheet is identified by what its columns are rather
than by what it is called, with the name used only to break a tie.

Every sheet ends up in one of four states, and all four are reported. A sheet
that is skipped in silence is the failure mode this replaces: the previous
importer read one sheet and ignored the other thirteen without saying so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .adapters import ADAPTERS, header_key

# Sheets a workbook carries for its readers rather than for an importer.
# Recognised by name so they can be reported as "ignored, on purpose" instead
# of "unrecognised", which would read as a problem.
NARRATIVE_SHEETS = {
    "summary", "readme", "read me", "notes", "instructions", "cover",
    "index", "changelog", "legend", "glossary",
}

# Sheets whose content this importer deliberately does not consume, with the
# reason a user should be told.
DECLINED_SHEETS: dict[str, str] = {
    "buildings": (
        "Buildings are described by the rooms themselves (block and floor), so "
        "there is nothing separate to import."
    ),
    "reference_timetable": (
        "A sample timetable is an illustration, not input. Timetables are "
        "produced by generating one, not by importing it."
    ),
    "reference timetable": (
        "A sample timetable is an illustration, not input. Timetables are "
        "produced by generating one, not by importing it."
    ),
}

DETECTED = "detected"
IGNORED = "ignored"
UNKNOWN = "unknown"
EMPTY = "empty"


@dataclass
class SheetMatch:
    """What one sheet was taken to be, and why."""

    sheet: str
    status: str
    entity: str | None = None
    label: str | None = None
    row_count: int = 0
    reason: str = ""
    confidence: float = 0.0
    headers: list[str] = field(default_factory=list)
    unrecognised_columns: list[str] = field(default_factory=list)


def _name_tokens(sheet_name: str) -> set[str]:
    """Words in a sheet name, with ordering prefixes discarded.

    ``03_Rooms`` and ``Rooms (final)`` should both read as "rooms".
    """
    cleaned = "".join(ch if ch.isalnum() else " " for ch in sheet_name.lower())
    return {tok for tok in cleaned.split() if tok and not tok.isdigit()}


def _accepted_headers(adapter) -> dict[str, str]:
    """Every spelling this adapter accepts, keyed comparably."""
    accepted = {header_key(c): c for c in adapter.columns}
    for canonical, spellings in getattr(adapter, "aliases", {}).items():
        for spelling in spellings:
            accepted[header_key(spelling)] = canonical
    return accepted


def score_sheet(adapter, headers: list[str], sheet_name: str) -> float:
    """How well this sheet's columns fit this adapter, from 0 to 1.

    Required columns dominate, because they are what makes a sheet usable at
    all. Optional columns and a matching name each add a little, which is
    enough to separate two adapters that share required columns without ever
    outweighing the columns themselves.
    """
    accepted = _accepted_headers(adapter)
    present = {header_key(h) for h in headers}

    canonical_present = {accepted[h] for h in present if h in accepted}
    required = list(adapter.required)
    if not required:
        return 0.0

    matched_required = sum(1 for c in required if c in canonical_present)
    if matched_required < len(required):
        # A sheet missing a required column is not this entity. Reporting it as
        # a poor match would send a user hunting for a validation problem in a
        # sheet that is simply something else.
        return 0.0

    optional = [c for c in adapter.columns if c not in adapter.required]
    matched_optional = sum(1 for c in optional if c in canonical_present)
    optional_ratio = (matched_optional / len(optional)) if optional else 0.0

    tokens = _name_tokens(sheet_name)
    name_bonus = 0.0
    for token in (adapter.key, *adapter.key.split("_"), *adapter.label.lower().split()):
        if header_key(token) in {header_key(t) for t in tokens}:
            name_bonus = 0.15
            break

    return min(1.0, 0.6 + 0.25 * optional_ratio + name_bonus)


def classify(
    sheets: dict[str, tuple[list[str], list[dict[str, str]]]]
) -> list[SheetMatch]:
    """Decide what every sheet is, in the order the workbook lists them.

    One adapter per sheet and one sheet per adapter: if two sheets both look
    like rooms, the better fit wins and the other is reported as unrecognised
    rather than imported twice.
    """
    scored: list[tuple[float, str, str]] = []
    matches: dict[str, SheetMatch] = {}

    for name, (headers, rows) in sheets.items():
        tokens = _name_tokens(name)

        if not headers:
            matches[name] = SheetMatch(
                sheet=name, status=EMPTY, row_count=len(rows),
                reason="No header row, so there is nothing to read.",
            )
            continue

        if tokens & NARRATIVE_SHEETS:
            matches[name] = SheetMatch(
                sheet=name, status=IGNORED, row_count=len(rows), headers=headers,
                reason="Notes for readers rather than data to import.",
            )
            continue

        # A declined sheet is recognised when its name contains all the words
        # of a known one - "12_Reference_Timetable" is the reference timetable
        # whatever number it carries.
        declined = next(
            (
                why for key, why in DECLINED_SHEETS.items()
                if _name_tokens(key) and _name_tokens(key) <= tokens
            ),
            None,
        )
        if declined:
            matches[name] = SheetMatch(
                sheet=name, status=IGNORED, row_count=len(rows), headers=headers,
                reason=declined,
            )
            continue

        best_key, best_score = None, 0.0
        for key, adapter in ADAPTERS.items():
            value = score_sheet(adapter, headers, name)
            if value > best_score:
                best_key, best_score = key, value

        if best_key is None:
            matches[name] = SheetMatch(
                sheet=name, status=UNKNOWN, row_count=len(rows), headers=headers,
                reason=(
                    "Its columns do not match any importable entity. Nothing "
                    "from this sheet will be imported."
                ),
            )
            continue

        scored.append((best_score, name, best_key))

    # Resolve contention: the best-fitting sheet claims an entity.
    claimed: dict[str, tuple[float, str]] = {}
    for score, name, key in sorted(scored, key=lambda t: -t[0]):
        held = claimed.get(key)
        if held is None:
            claimed[key] = (score, name)

    for score, name, key in scored:
        headers, rows = sheets[name]
        adapter = ADAPTERS[key]
        if claimed[key][1] != name:
            winner = claimed[key][1]
            matches[name] = SheetMatch(
                sheet=name, status=UNKNOWN, row_count=len(rows), headers=headers,
                reason=(
                    f"Looks like {adapter.label}, but {winner!r} matches that "
                    "more closely and was used instead."
                ),
            )
            continue

        accepted = _accepted_headers(adapter)
        unrecognised = [h for h in headers if header_key(h) not in accepted]
        matches[name] = SheetMatch(
            sheet=name, status=DETECTED, entity=key, label=adapter.label,
            row_count=len(rows), confidence=round(score, 2), headers=headers,
            unrecognised_columns=unrecognised,
            reason=(
                f"Matched {adapter.label} on its required columns"
                + (
                    f"; {len(unrecognised)} extra column(s) will be ignored"
                    if unrecognised else ""
                )
            ),
        )

    return [matches[name] for name in sheets]


# The order entities have to be created in, because each depends on the ones
# before it. Not the order a workbook lists its sheets - a file is arranged for
# a reader, and rearranging one to satisfy an importer is not a reasonable
# thing to ask.
DEPENDENCY_ORDER = [
    "academic_contexts",
    "timeslots",
    "rooms",
    "faculty",
    "subjects",
    "sections",
    "faculty_subjects",
    "section_subjects",
    "section_groups",
    # After `sections`, and that ordering is load-bearing: a parent link is
    # written onto a row that must already exist and already have a real id.
    # Rows are flushed once per sheet, so a section naming its parent from
    # within the sections sheet would still be a staged placeholder.
    "section_parents",
    "availability",
]


def ordered_entities(matches: list[SheetMatch]) -> list[SheetMatch]:
    """Detected sheets, in the order they must be applied."""
    by_entity = {m.entity: m for m in matches if m.status == DETECTED}
    return [by_entity[key] for key in DEPENDENCY_ORDER if key in by_entity]


def implied_contexts(
    sheets: dict[str, tuple[list[str], list[dict[str, str]]]],
    matches: list[SheetMatch],
) -> list[dict[str, str]]:
    """Academic contexts the sections sheet needs but no sheet declares.

    A real workbook declares the context it is "about" and then carries
    sections for several programmes, each naming its own year, semester,
    programme and department. Those are contexts too, and refusing the file
    because one sheet did not repeat them would be pedantry about presentation.

    They are returned so the preview can list each as an explicit creation -
    a context is the scope everything else hangs from, and one appearing as a
    side effect is not something a user should have to notice.
    """
    from .adapters import normalise_row

    sections = next((m for m in matches if m.entity == "sections"), None)
    if sections is None:
        return []

    declared: set[tuple] = set()
    contexts_sheet = next((m for m in matches if m.entity == "academic_contexts"), None)
    if contexts_sheet is not None:
        adapter = ADAPTERS["academic_contexts"]
        for row in sheets[contexts_sheet.sheet][1]:
            declared.add(adapter.identity(normalise_row(adapter, row)))

    adapter = ADAPTERS["academic_contexts"]
    out: list[dict[str, str]] = []
    seen: set[tuple] = set()
    for row in sheets[sections.sheet][1]:
        row = normalise_row(ADAPTERS["sections"], row)
        candidate = {
            "academic_year": row.get("academic_year", "").strip(),
            "semester": row.get("semester", "").strip(),
            "program": row.get("program", "").strip(),
            "department": row.get("department", "").strip(),
        }
        if not all(candidate.values()):
            continue
        key = adapter.identity(candidate)
        if key in declared or key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out
