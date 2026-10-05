"""Phase 9: bulk reference-data import with preview, upsert and full sync.

The one import engine: Load.xlsx and Infra.xlsx are turned into sheets and
written by this code. What it does, and why:

* **Stable identity, not display label.** A room is identified by the
  normalized ``(block, floor, room_number)`` triple, not by ``room_number``
  alone. Two buildings can legitimately both have a "301"; matching on the
  label alone would silently merge them or create duplicates depending on
  which way the collision fell.
* **Upsert by default.** A row missing from the file is *never* deleted -
  reference data arrives in partial extracts all the time, and treating an
  absent row as a deletion would destroy rooms that simply were not in this
  export. Deactivation happens only in explicit ``full_sync`` mode, and even
  then it deactivates rather than deletes.
* **Whole-file validation before any write.** The preview and the apply run
  the same analysis; apply refuses outright if any row is invalid (unless the
  caller explicitly allows a partial apply), so an import is never half-done.
* **CSV and XLSX.** College data arrives as both.

The workflow is entity-agnostic (``EntityAdapter``), so a new entity means a
new adapter and nothing else.

Structure
---------
``types``      verdicts, modes, and the preview/result shapes
``parsing``    bytes -> (headers, rows of strings), for one sheet or a workbook
``resolver``   how a row's references are looked up - the seam that lets a
               whole-workbook import resolve against rows it has not written yet
``adapters``   one per entity
``engine``     analyse / apply / templates / history

Everything the routers and the tests used when this was a single
module is re-exported here, so ``from .. import bulk_import as bi`` keeps
working unchanged.
"""
from __future__ import annotations

from .adapters import ADAPTERS, EntityAdapter
from .engine import analyse, apply, template_csv, template_xlsx
from .parsing import parse_table, parse_workbook
from .staging import StagedResolver, StagingArea
from .workbook import classify, ordered_entities
from .workbook_engine import analyse_workbook, apply_workbook
from .resolver import DbResolver, ReferenceResolver
from .types import (
    CREATE,
    DUPLICATE,
    INVALID,
    MODE_ADD_UPDATE,
    MODE_FULL_SYNC,
    MODES,
    UNCHANGED,
    UPDATE,
    Deactivation,
    FieldChange,
    ImportPreview,
    ParseError,
    RowResult,
)

__all__ = [
    "ADAPTERS", "EntityAdapter",
    "analyse", "apply", "template_csv", "template_xlsx",
    "parse_table", "parse_workbook",
    "analyse_workbook", "apply_workbook",
    "classify", "ordered_entities", "StagedResolver", "StagingArea",
    "DbResolver", "ReferenceResolver",
    "CREATE", "UPDATE", "UNCHANGED", "INVALID", "DUPLICATE",
    "MODES", "MODE_ADD_UPDATE", "MODE_FULL_SYNC",
    "Deactivation", "FieldChange", "ImportPreview", "ParseError", "RowResult",
]
