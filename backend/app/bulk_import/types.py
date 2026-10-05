"""Verdicts, modes, and the shapes a preview is made of.

Kept free of model and SQLAlchemy imports so every other module in the package
can depend on it without a cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any



# Row verdicts.
CREATE = "create"
UPDATE = "update"
UNCHANGED = "unchanged"
INVALID = "invalid"
DUPLICATE = "duplicate"

# Import modes.
MODE_ADD_UPDATE = "add_update"
MODE_FULL_SYNC = "full_sync"
MODES = (MODE_ADD_UPDATE, MODE_FULL_SYNC)


class ParseError(Exception):
    """The file could not be read at all - wrong format, missing columns."""


@dataclass
class FieldChange:
    field: str
    old: str
    new: str


@dataclass
class RowResult:
    row_number: int              # 1-based spreadsheet row (header is row 1)
    identity: str                # human-readable stable key, e.g. "A101 (38 / 5)"
    verdict: str
    message: str = ""
    changes: list[FieldChange] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    # The normalized identity tuple this row matched on. Carried from analysis
    # so apply() re-uses the exact key that produced the verdict rather than
    # re-deriving it and risking a subtly different normalisation.
    identity_key: tuple = ()


@dataclass
class Deactivation:
    """A record present in the database but absent from a full-sync file."""

    identity: str
    currently_active: bool
    # The identity tuple, universal across every adapter regardless of PK
    # shape - what apply()'s fallback path uses to find the record again.
    identity_key: tuple = ()
    # A surrogate integer PK, when the underlying model has one (Room,
    # Faculty, Subject, Section) - used by apply()'s fast bulk-write path.
    # None for junction/mapping adapters with a composite key and no such
    # column.
    id: int | None = None


@dataclass
class ImportPreview:
    entity: str
    mode: str
    filename: str
    columns: list[str]
    rows: list[RowResult]
    deactivations: list[Deactivation] = field(default_factory=list)
    committed: bool = False

    @property
    def counts(self) -> dict[str, int]:
        out = {CREATE: 0, UPDATE: 0, UNCHANGED: 0, INVALID: 0, DUPLICATE: 0}
        for r in self.rows:
            out[r.verdict] = out.get(r.verdict, 0) + 1
        out["deactivate"] = len(self.deactivations)
        out["total"] = len(self.rows)
        return out

    @property
    def has_errors(self) -> bool:
        return any(r.verdict in (INVALID, DUPLICATE) for r in self.rows)

    @property
    def can_apply(self) -> bool:
        """Something would actually change, and nothing is broken."""
        return not self.has_errors and (
            any(r.verdict in (CREATE, UPDATE) for r in self.rows) or bool(self.deactivations)
        )


