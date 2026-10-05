"""Reading an uploaded file into rows of strings.

Split out of the single import module so the workbook path can reuse exactly
this normalisation - every cell arrives as the string a human typed, and every
adapter validates strings, so a spreadsheet and a CSV cannot disagree about
what a value was.
"""
from __future__ import annotations

import csv
import io
from typing import Any

from .types import ParseError


def parse_table(data: bytes, filename: str) -> tuple[list[str], list[dict[str, str]]]:
    """Read CSV or XLSX into (headers, rows-of-strings).

    Everything is normalised to strings here so the per-entity validators have
    exactly one input shape to reason about, regardless of whether a value
    arrived as an Excel number or a CSV token.
    """
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        return _parse_xlsx(data)
    return _parse_csv(data)


def _cell_to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        # Excel stores every number as a float; "70.0" is not a capacity a
        # human typed, and would fail int() parsing downstream.
        return str(int(value))
    return str(value).strip()


# A real teaching load is a few hundred rows. These are far above that and
# far below what would exhaust memory: a small compressed file can expand to
# millions of rows, and every row read is kept.
MAX_ROWS = 50_000
MAX_SHEETS = 50


def _too_many_rows() -> ParseError:
    return ParseError(
        f"The file has more than {MAX_ROWS:,} rows, which is far more than a "
        "timetable needs. Remove the unused rows and upload it again."
    )


def _parse_xlsx(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise ParseError("XLSX support requires openpyxl.") from exc

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ParseError(f"Could not read the workbook: {exc}") from exc

    ws = wb.active
    if ws is None:
        raise ParseError("The workbook has no sheets.")

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        raise ParseError("The file is empty.") from None

    headers = [_cell_to_str(h).lower() for h in header_row]
    rows: list[dict[str, str]] = []
    for raw in rows_iter:
        values = [_cell_to_str(v) for v in raw]
        if not any(values):
            continue  # blank spreadsheet row
        rows.append({h: values[i] if i < len(values) else "" for i, h in enumerate(headers) if h})
        if len(rows) > MAX_ROWS:
            wb.close()
            raise _too_many_rows()
    wb.close()
    return [h for h in headers if h], rows


def _parse_csv(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ParseError("Could not decode the file as text.")

    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if reader.fieldnames is None:
        raise ParseError("The file is empty.")

    headers = [(h or "").strip().lower() for h in reader.fieldnames]
    rows: list[dict[str, str]] = []
    for raw in reader:
        row = {
            (k or "").strip().lower(): (v or "").strip()
            for k, v in raw.items()
            if k is not None
        }
        if any(row.values()):
            rows.append(row)
    return [h for h in headers if h], rows




def parse_workbook(data: bytes) -> dict[str, tuple[list[str], list[dict[str, str]]]]:
    """Every sheet of an XLSX, as ``{sheet name: (headers, rows)}``.

    ``parse_table`` reads only the active sheet, which is the right behaviour
    for a single-entity upload and the reason a fourteen-sheet workbook could
    never be imported: thirteen of its sheets were silently ignored.

    Read-only and streamed, because a real institution's workbook is large.
    Sheets with no header row are returned empty rather than skipped, so the
    caller can report them as unrecognised rather than pretend they were absent.
    """
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as a message
        raise ParseError(f"Could not read the workbook: {exc}") from exc

    out: dict[str, tuple[list[str], list[dict[str, str]]]] = {}
    try:
        if len(wb.sheetnames) > MAX_SHEETS:
            raise ParseError(
                f"The workbook has {len(wb.sheetnames)} sheets; at most {MAX_SHEETS} are read."
            )
        total = 0
        for name in wb.sheetnames:
            ws = wb[name]
            rows_iter = ws.iter_rows(values_only=True)
            try:
                header_row = next(rows_iter)
            except StopIteration:
                out[name] = ([], [])
                continue

            headers = [_cell_to_str(h) for h in header_row]
            if not any(headers):
                out[name] = ([], [])
                continue

            rows: list[dict[str, str]] = []
            for raw in rows_iter:
                row = {
                    h: _cell_to_str(v)
                    for h, v in zip(headers, raw)
                    if h
                }
                if any(row.values()):
                    rows.append(row)
                    total += 1
                    if total > MAX_ROWS:
                        raise _too_many_rows()
            out[name] = ([h for h in headers if h], rows)
    finally:
        wb.close()
    return out
