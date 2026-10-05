"""Importing a whole workbook: analyse everything, then apply it or nothing.

The single-entity engine answers "is this sheet valid against the database".
That question is unanswerable for a workbook, because a sheet is routinely
valid only in the company of its siblings - a curriculum row naming a section
three sheets away is correct, and was previously rejected as a broken
reference.

So the unit of validation here is the file. Sheets are read in dependency
order; each one's rows are staged as they are validated, and later sheets
resolve against the database *plus* everything staged so far. Nothing is
written until every sheet has been judged, and then it is written in one
transaction or not at all.

What this is careful about, in order:

* **Nothing partial.** One invalid required row means no writes, so a
  half-imported dataset cannot exist.
* **Say where.** Every problem carries its sheet, row number, column and a
  suggestion, because "row 87 is wrong" in a 419-row workbook is not help.
* **Nothing implicit and destructive.** Creating an academic context or
  replacing the time grid are surfaced as their own decisions, not as side
  effects of a sheet being present.
* **Idempotent.** Applying the same workbook twice changes nothing the second
  time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..models import Assignment, ImportHistory, TimeSlot
from .adapters import ADAPTERS, match_key, missing_required, normalise_row
from .parsing import parse_workbook
from .staging import StagedResolver, StagingArea
from .types import CREATE, DUPLICATE, INVALID, UNCHANGED, UPDATE, ParseError
from .workbook import (
    DETECTED,
    SheetMatch,
    classify,
    implied_contexts,
    ordered_entities,
)


@dataclass
class RowProblem:
    """One thing wrong, located precisely enough to go and fix it."""

    sheet: str
    row_number: int
    identity: str
    message: str
    column: str | None = None
    suggestion: str | None = None


@dataclass
class SheetOutcome:
    """What one sheet would do, if applied."""

    sheet: str
    entity: str
    label: str
    creates: int = 0
    updates: int = 0
    unchanged: int = 0
    invalid: int = 0
    duplicates: int = 0
    problems: list[RowProblem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.creates + self.updates + self.unchanged + self.invalid + self.duplicates


@dataclass
class Decision:
    """Something consequential the workbook implies, stated as its own choice.

    Creating an academic context or replacing the weekly grid changes the shape
    of everything around it. Neither should be discoverable only after the fact
    by noticing a row count moved.
    """

    kind: str
    label: str
    detail: str
    destructive: bool = False


@dataclass
class WorkbookPreview:
    """Everything an operator needs before deciding to import."""

    filename: str
    sheets: list[SheetMatch]
    outcomes: list[SheetOutcome]
    decisions: list[Decision] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    committed: bool = False

    @property
    def problems(self) -> list[RowProblem]:
        return [p for outcome in self.outcomes for p in outcome.problems]

    @property
    def has_errors(self) -> bool:
        return bool(self.blockers) or any(
            o.invalid or o.duplicates for o in self.outcomes
        )

    @property
    def can_apply(self) -> bool:
        if self.has_errors:
            return False
        return any(o.creates or o.updates for o in self.outcomes)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "creates": sum(o.creates for o in self.outcomes),
            "updates": sum(o.updates for o in self.outcomes),
            "unchanged": sum(o.unchanged for o in self.outcomes),
            "invalid": sum(o.invalid for o in self.outcomes),
            "duplicates": sum(o.duplicates for o in self.outcomes),
        }


# Columns worth naming in a problem, so the message can point at one.
_COLUMN_HINTS = {
    "academic_year": "academic_year",
    "semester": "semester",
    "program": "program",
    "department": "department",
    "section_number": "section_number",
    "subject_code": "subject_code",
    "faculty": "faculty_id",
    "room": "room_number",
    "capacity": "capacity",
    "room_type": "room_type",
    "lab_type": "lab_type",
    "day": "day",
    "period_code": "period_code",
    "slot": "period_code",
    "strength": "strength",
    "type": "type",
    "start_time": "start_time",
    "end_time": "end_time",
}

_SUGGESTIONS = {
    "does not exist": "Add it to the sheet that defines it, or create it first.",
    "was not found": "Check the spelling, or add it to the sheet that defines it.",
    "is required": "Fill this cell in.",
    "whole number": "Use a plain number, with no units or decimals.",
    "must be one of": "Use one of the listed values exactly.",
    "not a time": "Use 24-hour HH:MM, e.g. 09:30.",
    "ambiguous": "Add the academic context columns so the row is unambiguous.",
    "more than once": "Add the academic context columns so the row is unambiguous.",
    "contradict": "Make the two columns agree, or remove one of them.",
}


def _locate(message: str) -> tuple[str | None, str | None]:
    """Guess the column a message is about, and what to do about it."""
    lowered = message.lower()
    column = next(
        (col for token, col in _COLUMN_HINTS.items() if token in lowered), None
    )
    suggestion = next(
        (text for token, text in _SUGGESTIONS.items() if token in lowered), None
    )
    return column, suggestion


def _grid_replacement(db: Session, staged_slots: int, changing: int) -> Decision | None:
    """State the cost when a workbook rewrites an existing timetable grid."""
    if not changing:
        return None
    existing = db.query(TimeSlot).count()
    if not existing:
        return None
    assignments = db.query(Assignment).count()
    detail = (
        f"The workbook changes {changing} of the {existing} time slots already "
        "configured."
    )
    if assignments:
        detail += (
            f" Times are updated in place, so the {assignments} scheduled "
            "class(es) keep their slots - but check the new times are what you "
            "expect before publishing."
        )
    return Decision(
        kind="grid_change", label="Time grid changes", detail=detail, destructive=False
    )


def analyse_workbook(
    db: Session, data: bytes, filename: str = "workbook.xlsx"
) -> WorkbookPreview:
    """Validate the whole file. Writes nothing."""
    sheets = parse_workbook(data)
    if not sheets:
        raise ParseError("The workbook has no sheets.")

    matches = classify(sheets)
    detected = ordered_entities(matches)
    if not detected:
        raise ParseError(
            "No sheet in this workbook matched an importable entity. Check that "
            "each sheet has a header row naming its columns."
        )
    return analyse_sheets(db, sheets, matches, detected, filename)


def analyse_sheets(
    db: Session,
    sheets: dict[str, tuple[list[str], list[dict[str, str]]]],
    matches: list[SheetMatch],
    detected: list[SheetMatch],
    filename: str = "workbook.xlsx",
) -> WorkbookPreview:
    """Validate sheets that have already been identified.

    Split out of `analyse_workbook` so a caller that *builds* its sheets rather
    than reading them can still use all of this - the staging resolver, the
    row-level verdicts, the decision list, the one-transaction guarantee. The
    teacher import does exactly that: it turns two flat spreadsheets into the
    per-entity sheets this engine already knows, so there is one validation
    path and one writer rather than a second pipeline that drifts.

    Passing `detected` in also skips the scoring contest, which a generated
    sheet should not have to win against the adapter it was generated for.
    """
    staging = StagingArea()
    refs = StagedResolver(db, staging)
    outcomes: list[SheetOutcome] = []
    decisions: list[Decision] = []
    blockers: list[str] = []

    # Contexts a section names but no sheet declares. Staged before anything
    # reads them, so sections referencing them validate cleanly, and surfaced
    # as explicit decisions so their creation is never a side effect.
    context_adapter = ADAPTERS["academic_contexts"]
    extra_contexts = implied_contexts(sheets, matches)

    for entity_match in detected:
        adapter = ADAPTERS[entity_match.entity]
        headers, raw_rows = sheets[entity_match.sheet]

        missing = missing_required(adapter, headers)
        if missing:
            blockers.append(
                f"{entity_match.sheet}: missing required column(s) "
                f"{', '.join(missing)}."
            )
            continue

        outcome = SheetOutcome(
            sheet=entity_match.sheet,
            entity=entity_match.entity,
            label=adapter.label,
        )
        existing = adapter.load_existing(db)
        seen: dict[tuple, int] = {}

        for index, raw in enumerate(raw_rows, start=2):  # header occupies row 1
            row = normalise_row(adapter, raw)
            label = adapter.identity_label(row)
            payload, errors = adapter.validate(row, db, refs)
            identity = match_key(adapter, row, payload)

            if errors:
                outcome.invalid += 1
                for message in errors:
                    column, suggestion = _locate(message)
                    outcome.problems.append(RowProblem(
                        sheet=entity_match.sheet, row_number=index, identity=label,
                        message=message, column=column, suggestion=suggestion,
                    ))
                continue

            if identity in seen:
                outcome.duplicates += 1
                outcome.problems.append(RowProblem(
                    sheet=entity_match.sheet, row_number=index, identity=label,
                    message=(
                        f"Duplicate of row {seen[identity]} in this sheet - both "
                        "describe the same record."
                    ),
                    suggestion="Remove one of the two rows.",
                ))
                continue
            seen[identity] = index

            current = existing.get(identity)
            if current is None:
                outcome.creates += 1
                # A row this import will create: later sheets resolve it to a
                # stand-in, which is enough to validate a reference to it.
                staging.add(
                    entity_match.entity, identity, payload, entity_match.sheet, index
                )
            else:
                if adapter.diff(current, payload):
                    outcome.updates += 1
                else:
                    outcome.unchanged += 1
                # A row that already exists must be staged as *itself*, with
                # its real id. Staging a stand-in instead would make later
                # sheets key their references off a pending id and report every
                # one of them as new - which is what a second import of an
                # unchanged workbook looked like.
                refs.replace_with_real(entity_match.entity, identity, current)

        # Contexts implied by sections are staged once the contexts sheet has
        # been read, so a declared context always wins over an inferred one.
        if entity_match.entity == "academic_contexts":
            for candidate in extra_contexts:
                payload, errors = context_adapter.validate(candidate, db, refs)
                if errors:
                    continue
                key = context_adapter.identity(candidate)
                if key in staging.contexts:
                    continue
                already = existing.get(key)
                if already is not None:
                    # Created by an earlier import. Counted so the totals
                    # describe the whole workbook rather than quietly omitting
                    # the rows that happen to need no work.
                    outcome.unchanged += 1
                    refs.replace_with_real("academic_contexts", key, already)
                    continue
                staging.add("academic_contexts", key, payload, entity_match.sheet, 0)
                outcome.creates += 1
                outcome.notes.append(
                    f"{context_adapter.identity_label(candidate)} is not listed on "
                    "this sheet but is used by the Sections sheet, so it will be "
                    "created."
                )

        outcomes.append(outcome)

    # A workbook with sections but no contexts sheet still needs its contexts.
    if not any(o.entity == "academic_contexts" for o in outcomes) and extra_contexts:
        outcome = SheetOutcome(
            sheet="(derived from Sections)",
            entity="academic_contexts",
            label=context_adapter.label,
        )
        for candidate in extra_contexts:
            payload, errors = context_adapter.validate(candidate, db, refs)
            if errors:
                continue
            key = context_adapter.identity(candidate)
            if key in context_adapter.load_existing(db):
                outcome.unchanged += 1
                continue
            staging.add("academic_contexts", key, payload, outcome.sheet, 0)
            outcome.creates += 1
        if outcome.creates or outcome.unchanged:
            outcome.notes.append(
                "No Academic Context sheet was found, so these were taken from "
                "the context columns on the Sections sheet."
            )
            outcomes.insert(0, outcome)

    for key, staged in staging.contexts.items():
        decisions.append(Decision(
            kind="create_context",
            label=context_adapter.identity_label(staged.payload),
            detail=(
                "A new academic context will be created. Sections, timetables "
                "and published versions all belong to one, so this is the scope "
                "the rest of the import hangs from."
            ),
        ))

    slot_outcome = next((o for o in outcomes if o.entity == "timeslots"), None)
    if slot_outcome is not None:
        change = _grid_replacement(
            db, slot_outcome.creates, slot_outcome.creates + slot_outcome.updates
        )
        if change is not None:
            decisions.append(change)

    return WorkbookPreview(
        filename=filename,
        sheets=matches,
        outcomes=outcomes,
        decisions=decisions,
        blockers=blockers,
    )


def apply_workbook(
    db: Session, data: bytes, filename: str = "workbook.xlsx", *, actor: str | None = None
) -> WorkbookPreview:
    """Re-validate, then write everything in one transaction, or nothing.

    The preview is deliberately not trusted: it was produced from a file the
    caller uploaded and may have been produced against a database that has
    since changed. Re-running the analysis is cheap next to importing the wrong
    thing.
    """
    preview = analyse_workbook(db, data, filename)
    sheets = parse_workbook(data)
    detected = ordered_entities(classify(sheets))
    return _apply_analysed(db, preview, sheets, detected, actor)


def apply_sheets(
    db: Session,
    sheets: dict[str, tuple[list[str], list[dict[str, str]]]],
    matches: list[SheetMatch],
    detected: list[SheetMatch],
    filename: str = "workbook.xlsx",
    *,
    actor: str | None = None,
    commit: bool = True,
    before_commit=None,
) -> WorkbookPreview:
    """Write already-identified sheets, all or nothing. See `analyse_sheets`.

    ``commit=False`` writes and flushes but neither commits nor records an
    import - for a caller that wants to ask questions of the written data and
    then roll it back. The teacher preview does exactly that, so that "the data
    is ready" is checked against the data as it would be, not guessed at.

    ``before_commit(db)`` runs after every sheet is written and before the
    commit, inside the same transaction - for work that has to succeed or fail
    together with the import.
    """
    preview = analyse_sheets(db, sheets, matches, detected, filename)
    return _apply_analysed(db, preview, sheets, detected, actor, commit=commit,
                           before_commit=before_commit)


def _apply_analysed(
    db: Session,
    preview: WorkbookPreview,
    sheets: dict[str, tuple[list[str], list[dict[str, str]]]],
    detected: list[SheetMatch],
    actor: str | None,
    commit: bool = True,
    before_commit=None,
) -> WorkbookPreview:
    if preview.has_errors or not preview.can_apply:
        if commit:
            # Nothing in the sheets changed, but the rest of an import can
            # still apply - a file re-uploaded unchanged must still leave the
            # caller's own follow-up work done.
            if not preview.has_errors and before_commit is not None:
                before_commit(db)
            _record(db, preview, "rejected", actor)
            db.commit()
        return preview

    try:
        staging = StagingArea()
        refs = StagedResolver(db, staging)
        created: dict[str, dict[tuple, Any]] = {}

        for outcome in preview.outcomes:
            if outcome.sheet == "(derived from Sections)":
                continue
            adapter = ADAPTERS[outcome.entity]
            _, raw_rows = sheets[outcome.sheet]
            existing = adapter.load_existing(db)

            written: list[tuple[Any, Any]] = []
            for raw in raw_rows:
                row = normalise_row(adapter, raw)
                payload, errors = adapter.validate(row, db, refs)
                identity = match_key(adapter, row, payload)
                if errors:
                    # analyse_workbook already proved there are none; if that
                    # changed under us, stop rather than write a partial file.
                    raise ParseError(
                        f"{outcome.sheet} row for {adapter.identity_label(row)} "
                        f"became invalid during apply: {'; '.join(errors)}"
                    )
                obj = _write(db, adapter, existing, identity, payload, created, outcome.entity)
                written.append((identity, obj))

            # One flush per sheet rather than one per row. Ids are needed by
            # *later* sheets, and sheets are applied in dependency order, so
            # flushing at the boundary is enough - and turns roughly one round
            # trip per row into one per sheet.
            db.flush()
            for identity, obj in written:
                refs.remember(obj)
                refs.replace_with_real(outcome.entity, identity, obj)

            if outcome.entity == "academic_contexts":
                _apply_implied_contexts(
                    db, sheets, preview, staging, refs, created, existing
                )

        if not commit:
            db.flush()
            return preview
        if before_commit is not None:
            before_commit(db)
        _record(db, preview, "applied", actor)
        db.commit()
        preview.committed = True
        return preview
    except Exception:
        db.rollback()
        raise


def _apply_implied_contexts(db, sheets, preview, staging, refs, created, existing):
    """Create the contexts sections need but no sheet declared."""
    adapter = ADAPTERS["academic_contexts"]
    matches = preview.sheets
    for candidate in implied_contexts(sheets, matches):
        payload, errors = adapter.validate(candidate, db, refs)
        if errors:
            continue
        identity = adapter.identity(candidate)
        if identity in existing or identity in created.get("academic_contexts", {}):
            continue
        obj = _write(db, adapter, existing, identity, payload, created, "academic_contexts")
        db.flush()
        refs.remember(obj)
        refs.replace_with_real("academic_contexts", identity, obj)


def _write(db, adapter, existing, identity, payload, created, entity):
    """Create or update one row. Does not flush - the caller does, per sheet."""
    current = existing.get(identity)
    if current is None:
        current = created.setdefault(entity, {}).get(identity)

    if current is None:
        obj = adapter.create(db, payload)
        created.setdefault(entity, {})[identity] = obj
        return obj

    if adapter.diff(current, payload):
        adapter.update(current, payload)
    return current


def _record(db: Session, preview: WorkbookPreview, status: str, actor: str | None) -> None:
    counts = preview.counts
    db.add(ImportHistory(
        entity="workbook",
        filename=preview.filename,
        mode="workbook",
        rows_processed=sum(o.total for o in preview.outcomes),
        rows_created=counts["creates"],
        rows_updated=counts["updates"],
        rows_unchanged=counts["unchanged"],
        rows_rejected=counts["invalid"] + counts["duplicates"],
        rows_deactivated=0,
        status=status,
        message="; ".join(
            f"{o.label}: {o.creates} new, {o.updates} updated" for o in preview.outcomes
        )[:500],
        actor=actor,
    ))
