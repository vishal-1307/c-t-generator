"""Two spreadsheets in, a timetable's worth of data out.

A department keeps its teaching load in one flat sheet - who teaches what, to
which section, how big it is, and how long a session runs - and its rooms in
another. Neither is shaped like anything this application stores: one row of
the load sheet is simultaneously a faculty member, a subject, a section, an
eligibility mapping and a curriculum entry.

So this package does one job: turn those two sheets into the per-entity sheets
the existing workbook importer already validates and writes. It reads files and
returns data. It never touches the database, and there is no second validation
path, no second writer, and no second set of rules about what a valid room is -
`explode()` produces sheets, and `bulk_import` does the rest.

That boundary is the point. The alternative - a parallel importer that knows
how to upsert a Faculty - would be a second place for every rule to live and a
second place for each of them to quietly stop matching.
"""
from .explode import ExplodedFiles, explode, explode_infra
from .normalise import LOAD_ALIASES, INFRA_ALIASES, normalise_headers
from .parse import ParsedSheet, read_sheet

__all__ = [
    "ExplodedFiles",
    "explode",
    "explode_infra",
    "LOAD_ALIASES",
    "INFRA_ALIASES",
    "normalise_headers",
    "ParsedSheet",
    "read_sheet",
]
