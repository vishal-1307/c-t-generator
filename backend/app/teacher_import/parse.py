"""Reading the two teacher files, with their row numbers kept.

Thin on purpose: `bulk_import.parsing.parse_table` already turns a spreadsheet
into rows of strings, and every value in this application is validated as the
string a human typed. The only thing added here is provenance - which row of
which file a value came from - because everything downstream happens on
generated sheets, and "Subjects row 4" is not something a teacher can act on.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..bulk_import.parsing import parse_table


@dataclass
class ParsedSheet:
    """One uploaded file, with each row's position in it."""

    filename: str
    headers: list[str]
    # Each entry is the row as typed, plus `row_number` - the line in the
    # original file, counting the header as row 1.
    rows: list[dict[str, str]] = field(default_factory=list)
    row_numbers: list[int] = field(default_factory=list)

    def numbered(self):
        """Iterate `(row_number, row)` pairs."""
        return zip(self.row_numbers, self.rows)


def read_sheet(data: bytes, filename: str) -> ParsedSheet:
    """Read one uploaded file into rows of strings.

    Blank rows are dropped rather than reported: a spreadsheet routinely has
    trailing empty lines, and calling those "incomplete records" would bury the
    real problems under noise. The row numbers of everything that survives
    still refer to the original file.
    """
    headers, rows = parse_table(data, filename)

    kept: list[dict[str, str]] = []
    numbers: list[int] = []
    for index, row in enumerate(rows, start=2):  # header occupies row 1
        if not any(str(v).strip() for v in row.values()):
            continue
        kept.append(row)
        numbers.append(index)

    return ParsedSheet(
        filename=filename, headers=list(headers), rows=kept, row_numbers=numbers
    )
