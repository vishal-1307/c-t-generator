"""What this department's column names mean.

Kept separate from `bulk_import.adapters` deliberately. The adapters' alias
lists are the product's vocabulary - spellings any institution might plausibly
use, and worth accepting from anyone. This file is one department's, including
its typos: `Strenght` for strength, `Faculty  UID` with two spaces. Teaching
the product to accept a typo as a synonym would make it part of the vocabulary
forever, and the next file's typo would have an equal claim.

So the translation happens here, on the way in, and the adapters keep their
own standards.
"""
from __future__ import annotations

from ..bulk_import.adapters import header_key

# Load sheet: one row is a faculty member, a subject, a section and two
# mappings all at once. These names map to what that row means, not to any one
# entity's column - `explode` distributes them.
LOAD_ALIASES: dict[str, str] = {
    "nameoffaculty": "faculty_name",
    "facultyname": "faculty_name",
    "teachername": "faculty_name",
    "facultyuid": "faculty_id",
    "uid": "faculty_id",
    "facultycode": "faculty_id",
    "employeeid": "faculty_id",
    "subjectname": "subject_name",
    "subjectcode": "subject_code",
    "coursecode": "subject_code",
    "section": "section_number",
    "sectioncode": "section_number",
    # The typo, and the spelling it was meant to be.
    "strenght": "strength",
    "strength": "strength",
    "students": "strength",
    "type": "type",
    "byod": "byod",
    # "No of Hrs/Duration" is how many consecutive periods ONE session runs
    # for - not how many sessions there are in a week. That distinction is the
    # single most consequential thing in this file: read the other way, a
    # three-period lab becomes three separate one-period classes scattered
    # across the week, and the timetable is wrong in a way that still looks
    # plausible.
    "noofhrsduration": "duration",
    "noofhrs": "duration",
    "duration": "duration",
    "hrs": "duration",
    "hours": "duration",
    # Required: how many times a week this section takes this subject. Every
    # row states it, because nothing else in the file can - the duration says
    # how long one class runs, never how often it happens - and a guessed
    # number is a timetable quietly built for a different week.
    "classesperweek": "sessions_per_week",
    "classesweek": "sessions_per_week",
    "classesaweek": "sessions_per_week",
    "noofclassesperweek": "sessions_per_week",
    "numberofclassesperweek": "sessions_per_week",
    "sessionsperweek": "sessions_per_week",
    "lecturesperweek": "sessions_per_week",
    "periodsperweek": "sessions_per_week",
    # Optional, and absent from the current file: says outright which section a
    # group belongs to, instead of leaving it to be read off the number.
    "parentsection": "parent_section_number",
    "parent": "parent_section_number",
}

# Infrastructure sheet.
INFRA_ALIASES: dict[str, str] = {
    "block": "block",
    "room": "room_number",
    "roomno": "room_number",
    "roomnumber": "room_number",
    "strenght": "capacity",
    "strength": "capacity",
    "capacity": "capacity",
    "seats": "capacity",
    "type": "room_type",
    "byod": "byod",
    "charging": "charging",
    "power": "charging",
    "labtype": "lab_type",
    "floor": "floor",
}


def normalise_headers(
    row: dict[str, str], aliases: dict[str, str]
) -> dict[str, str]:
    """Rewrite a row's keys to the meanings above.

    Unknown columns are kept under their original name. A department that adds
    a column this does not know about has not made its file invalid, and the
    preview reports what went unused rather than silently discarding it.
    """
    out: dict[str, str] = {}
    for key, value in row.items():
        out[aliases.get(header_key(key), key)] = value
    return out


def unrecognised(headers: list[str], aliases: dict[str, str]) -> list[str]:
    """Columns this file has that nothing here knows how to read."""
    return [h for h in headers if header_key(h) not in aliases and str(h).strip()]


TRUTHY = {"y", "yes", "true", "1", "t"}
FALSEY = {"n", "no", "false", "0", "f", ""}


def as_bool(value: str) -> bool | None:
    """Yes/No as a spreadsheet writes it. None when it is neither."""
    text = str(value).strip().lower()
    if text in TRUTHY:
        return True
    if text in FALSEY:
        return False
    return None
