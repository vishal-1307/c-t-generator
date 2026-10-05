"""One flat load row becomes five records; one infra row becomes a room.

A line of the load sheet says "Faculty A (90001) teaches Embedded Systems
(ECE181) to section 2401, which has 68 students, as a 1-period class needing
BYOD". That is a faculty member, a subject, a section, an eligibility mapping
and a curriculum entry, and this module separates them.

Two rules govern the whole file, and both are about not guessing:

* **Identity is the code, never the name.** The teacher's own file proves why:
  "Communication Systems" (ECE101) and "Communication systems" (ECE151) are
  different subjects that differ only in capitalisation, and merging them on
  the name would silently delete one.
* **Disagreements are reported, never resolved.** Two rows giving section 2401
  different strengths is something only the department can settle. Taking the
  larger, or the last, would produce a timetable built on a number nobody
  chose.

Nothing here touches the database. It reads rows and returns sheets, which
`bulk_import` then validates and writes - so there is one set of rules about
what a valid room is, not two.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..bulk_import.adapters import header_key
from .normalise import (
    INFRA_ALIASES,
    LOAD_ALIASES,
    as_bool,
    normalise_headers,
    unrecognised,
)
from .parse import ParsedSheet

# Where a generated row came from, so a problem can be reported against the
# file the teacher actually has open.
Origin = tuple[str, int]


@dataclass
class Problem:
    """Something wrong with the input, located in the input."""

    file: str
    row_number: int
    message: str
    identity: str = ""


@dataclass
class ExplodedFiles:
    """The teacher's two files, restated as sheets the importer understands."""

    sheets: dict[str, tuple[list[str], list[dict[str, str]]]] = field(
        default_factory=dict
    )
    # (generated sheet, row index within it) -> where it came from.
    provenance: dict[tuple[str, int], Origin] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # How the file was read where it does not say something outright - that
    # 24011 is a lab group of 2401. Expected, correct in the ordinary case,
    # and nothing to fix, so kept apart from `warnings`, which are.
    inferences: list[str] = field(default_factory=list)
    # Rooms already on record that the room list describes differently, one
    # entry per room with each changed field. Structured rather than prose so
    # the page can show "Capacity 72 -> 60" next to the room, which is the
    # form in which a dangerous change is actually noticed.
    room_changes: list[dict] = field(default_factory=list)
    # Who teaches each (section, subject), exactly as the load sheet says:
    # (section number, subject code, faculty UID). The sheet does not say a
    # teacher *may* teach a subject - it says this teacher teaches it to this
    # section - so this is pinned, not left for the solver to choose.
    teachers: list[tuple[str, str, str]] = field(default_factory=list)
    # What the two files add up to - counted here, from the files, so it is
    # true before anything is written.
    summary: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems


LOAD_SHEET = "Load"
INFRA_SHEET = "Infra"


def _group_parent(section_number: str, known: set[str]) -> str | None:
    """Read `24011` as a group of `2401`, when `2401` is a section we have.

    A convention, not a rule the data states, so it is applied only when the
    parent it implies actually exists in this file - and the preview says out
    loud what it concluded, because the alternative reading (24011 is simply
    another section) is equally plausible and produces a timetable that looks
    just as finished.
    """
    text = section_number.strip()
    if len(text) < 2 or not text.isdigit():
        return None
    candidate = text[:-1]
    return candidate if candidate in known else None


def explode(
    load: ParsedSheet,
    infra: ParsedSheet | None,
    *,
    academic_year: str,
    semester: int,
    program: str,
    department: str,
    known_rooms: dict[tuple[str, str], dict[str, object]] | None = None,
) -> ExplodedFiles:
    """Turn the two files into per-entity sheets. Reads nothing, writes nothing.

    ``known_rooms`` is what the database already has, keyed by (block, room
    number) - supplied by the caller so this stays a pure function of its
    arguments. It is needed because the room list has no floor column while
    room identity includes one; see `_rooms`.

    ``infra`` is None when the rooms are already saved: a new teaching load is
    scheduled against the saved infrastructure, and only the load is read.
    """
    out = ExplodedFiles()

    sheets = [(load, LOAD_ALIASES, load.filename)]
    if infra is not None:
        sheets.append((infra, INFRA_ALIASES, infra.filename))
    for sheet, aliases, name in sheets:
        extra = unrecognised(sheet.headers, aliases)
        if extra:
            out.notes.append(
                f"{name}: {', '.join(extra)} was not used - nothing in the "
                "timetable depends on it."
            )

    if infra is not None:
        _rooms(infra, out, known_rooms or {})
    _load(
        load,
        out,
        academic_year=academic_year,
        semester=semester,
        program=program,
        department=department,
    )

    out.sheets["Academic context"] = (
        ["academic_year", "semester", "program", "department"],
        [{
            "academic_year": academic_year,
            "semester": str(semester),
            "program": program,
            "department": department,
        }],
    )
    return out


def explode_infra(
    infra: ParsedSheet,
    known_rooms: dict[tuple[str, str], dict[str, object]] | None = None,
) -> ExplodedFiles:
    """The room list on its own, restated as a Rooms sheet.

    Used to save the infrastructure without a teaching load: the same reading,
    the same checks and the same carried-over attributes as when both files
    arrive together, so a room saved on its own is identical to one imported
    alongside a load.
    """
    out = ExplodedFiles()
    extra = unrecognised(infra.headers, INFRA_ALIASES)
    if extra:
        out.notes.append(
            f"{infra.filename}: {', '.join(extra)} was not used - nothing in the "
            "timetable depends on it."
        )
    _rooms(infra, out, known_rooms or {})
    return out


# ------------------------------------------------------------------ rooms


def _rooms(
    infra: ParsedSheet,
    out: ExplodedFiles,
    known: dict[tuple[str, str], dict[str, object]],
) -> None:
    """Rooms, matched to what is already recorded where the file is silent.

    Room identity is (block, floor, room_number), and this file has no floor
    column. So "36 / 301" and the "36 / floor 3 / 301" already in the database
    look like different rooms - while the table's unique constraint on (block,
    room_number) says they cannot both exist. Importing would end in an
    IntegrityError: a 500 at the end of an all-or-nothing transaction, naming
    no room.

    They are the same room; the file simply does not say which floor. So a
    floor already on record is adopted rather than blanked, which turns an
    impossible insert into the update it always was.

    What that cannot resolve is a room the file describes *differently* -
    Block 36 room 309 being a faculty office here and a 120-seat classroom
    there. That is a real disagreement about a real room, so it is reported
    rather than applied silently.
    """
    headers = [
        "block", "floor", "room_number", "room_code", "capacity",
        "room_type", "lab_type", "byod", "charging", "charging_sockets",
        "is_active",
    ]
    rows: list[dict[str, str]] = []
    seen: dict[tuple[str, str], Origin] = {}
    kinds = {"theory": 0, "lab": 0, "faculty": 0}
    byod_rooms = 0

    for row_number, raw in infra.numbered():
        row = normalise_headers(raw, INFRA_ALIASES)
        block = str(row.get("block", "")).strip()
        number = str(row.get("room_number", "")).strip()
        origin = (infra.filename, row_number)

        if not number:
            out.problems.append(Problem(
                infra.filename, row_number, "this row has no room number"
            ))
            continue

        key = (block.lower(), number.lower())
        if key in seen:
            first = seen[key]
            out.problems.append(Problem(
                infra.filename, row_number,
                f"room {block}-{number} is listed twice (also on row "
                f"{first[1]}). Two rooms cannot share a block and number.",
                identity=f"{block}-{number}",
            ))
            continue
        seen[key] = origin

        byod = as_bool(row.get("byod", ""))
        if byod is None:
            out.problems.append(Problem(
                infra.filename, row_number,
                f"BYOD is {row.get('byod')!r} - it should say Yes or No",
                identity=f"{block}-{number}",
            ))
            continue

        recorded = known.get((block.strip().lower(), number.strip().lower()))

        # The room importer writes every column it is given, and blanks the
        # ones it is not. This file has no floor, charging, sockets, lab type
        # or code - so for a room already on record, each of those is carried
        # over rather than cleared. Otherwise an import would quietly strip
        # attributes nobody asked it to touch: exactly the kind of change that
        # is invisible until something is scheduled into the room.
        def kept(column: str, default: str = "") -> str:
            stated = str(row.get(column, "")).strip()
            if stated:
                return stated
            if recorded and recorded.get(column) is not None:
                value = recorded[column]
                if isinstance(value, bool):
                    return "true" if value else "false"
                return str(value)
            return default

        kind = _room_kind(row.get("room_type", ""))
        if kind in kinds:
            kinds[kind] += 1
        if byod:
            byod_rooms += 1

        if recorded:
            change = _room_change(block, number, row, recorded, byod)
            if change:
                out.room_changes.append(change)

        out.provenance[("Rooms", len(rows))] = origin
        rows.append({
            "block": block,
            "floor": kept("floor"),
            "room_number": number,
            # The label people use. Two blocks both have a room "101", so a
            # bare number is not something anyone can pick from a list.
            "room_code": kept("room_code") or (f"{block}-{number}" if block else number),
            "capacity": str(row.get("capacity", "")).strip(),
            # "Class room" and "Lab" are translated by the room adapter itself.
            "room_type": str(row.get("room_type", "")).strip(),
            "lab_type": kept("lab_type"),
            "byod": "true" if byod else "false",
            # BYOD is charging points at the benches; `charging` is a separate
            # column the general importer carries. Not in this file, so kept
            # as recorded, or false for a new room rather than guessed.
            "charging": kept("charging", "false"),
            "charging_sockets": kept("charging_sockets"),
            # Listed in the room list means in use.
            "is_active": "true",
        })

    out.sheets["Rooms"] = (headers, rows)
    out.summary.update({
        "rooms": len(rows),
        "classrooms": kinds["theory"],
        "labs": kinds["lab"],
        "byod_rooms": byod_rooms,
    })


def _room_kind(raw: str) -> str:
    """The stored room type a file's wording means - the same translation the
    room importer applies, so a count here matches what gets written."""
    from ..bulk_import.adapters import ADAPTERS

    text = str(raw).strip().lower().replace(" ", "_")
    return ADAPTERS["rooms"].TYPE_SYNONYMS.get(text, text)


_KIND_LABEL = {"theory": "Classroom", "lab": "Lab", "faculty": "Faculty room"}


def _room_change(block, number, row, recorded, byod: bool) -> dict | None:
    """What importing would change about a room that already exists.

    A capacity or a type that disagrees with the record is not a correction
    this can make on its own: another timetable may already be using that room
    on the strength of what it currently says. Changing a faculty office into a
    120-seat classroom is exactly the kind of edit that is invisible until
    something is scheduled there - so every one is listed, field by field.
    """
    label = f"{block}-{number}" if block else number
    changes: list[dict[str, str]] = []

    was_type = str(recorded.get("room_type") or "")
    now_type = _room_kind(row.get("room_type", ""))
    if was_type and now_type and was_type != now_type:
        changes.append({
            "field": "room_type", "label": "Type",
            "before": _KIND_LABEL.get(was_type, was_type),
            "after": _KIND_LABEL.get(now_type, now_type),
        })

    try:
        was_cap = int(recorded.get("capacity") or 0)
        now_cap = int(float(str(row.get("capacity", "")).strip() or 0))
    except ValueError:
        was_cap = now_cap = 0
    if was_cap and now_cap and was_cap != now_cap:
        changes.append({
            "field": "capacity", "label": "Capacity",
            "before": str(was_cap), "after": str(now_cap),
        })

    # BYOD - charging points at the benches - decides which classes may be
    # taught in the room, exactly as capacity does.
    was_byod = recorded.get("byod")
    if was_byod is not None and bool(was_byod) != byod:
        changes.append({
            "field": "byod", "label": "BYOD (charging points)",
            "before": "Yes" if was_byod else "No",
            "after": "Yes" if byod else "No",
        })

    if recorded.get("is_active") is False:
        changes.append({
            "field": "is_active", "label": "In use",
            "before": "No", "after": "Yes",
        })

    return {"room": label, "changes": changes} if changes else None


# ------------------------------------------------------------------- load


def _load(
    load: ParsedSheet,
    out: ExplodedFiles,
    *,
    academic_year: str,
    semester: int,
    program: str,
    department: str,
) -> None:
    # Classes per week is required, and required of the file before any row:
    # a file without the column cannot say how often anything happens, and one
    # message naming the column is worth more than a hundred naming each row.
    if not any(
        LOAD_ALIASES.get(header_key(h)) == "sessions_per_week" for h in load.headers
    ):
        out.problems.append(Problem(
            load.filename, 1,
            "this file has no Classes Per Week column. Every row has to say how "
            "many times a week that section takes that subject - add the column "
            "(the Load template shows where) and upload again.",
            identity="Classes Per Week",
        ))
        return

    faculty: dict[str, dict[str, str]] = {}
    faculty_origin: dict[str, Origin] = {}
    subjects: dict[str, dict[str, str]] = {}
    subject_origin: dict[str, Origin] = {}
    sections: dict[str, dict[str, str]] = {}
    section_origin: dict[str, Origin] = {}
    faculty_subject: dict[tuple[str, str], dict[str, str]] = {}
    section_subject: dict[tuple[str, str], dict[str, str]] = {}
    teacher_of: dict[tuple[str, str], tuple[str, int]] = {}
    parents: dict[str, str] = {}

    for row_number, raw in load.numbered():
        row = normalise_headers(raw, LOAD_ALIASES)
        origin = (load.filename, row_number)

        code = str(row.get("subject_code", "")).strip()
        name = str(row.get("subject_name", "")).strip()
        fid = str(row.get("faculty_id", "")).strip()
        fname = str(row.get("faculty_name", "")).strip()
        section = str(row.get("section_number", "")).strip()
        label = f"{code} / {section}" if code else section

        missing = [
            what for what, value in (
                ("subject code", code), ("subject name", name),
                ("faculty UID", fid), ("faculty name", fname),
                ("section", section),
            ) if not value
        ]
        if missing:
            out.problems.append(Problem(
                load.filename, row_number,
                f"missing {', '.join(missing)}", identity=label,
            ))
            continue

        # ---- faculty. The UID is the identity; the name is a label, and two
        # spellings of one person under one UID is a typo worth reporting.
        if fid in faculty:
            if faculty[fid]["name"].strip().lower() != fname.lower():
                out.problems.append(Problem(
                    load.filename, row_number,
                    f"faculty UID {fid} is {fname!r} here and "
                    f"{faculty[fid]['name']!r} on row {faculty_origin[fid][1]}",
                    identity=label,
                ))
                continue
        else:
            faculty[fid] = {
                "faculty_id": fid, "name": fname, "department": department,
                "is_active": "true",
            }
            faculty_origin[fid] = origin

        # ---- subject
        kind = str(row.get("type", "")).strip()
        duration = str(row.get("duration", "")).strip() or "1"
        byod = as_bool(row.get("byod", ""))
        if byod is None:
            out.problems.append(Problem(
                load.filename, row_number,
                f"BYOD is {row.get('byod')!r} - it should say Yes or No",
                identity=label,
            ))
            continue
        raw_per_week = str(row.get("sessions_per_week", "")).strip()
        try:
            as_number = float(raw_per_week)
            per_week_n = int(as_number)
            if not raw_per_week or per_week_n != as_number or per_week_n < 1:
                raise ValueError
        except ValueError:
            out.problems.append(Problem(
                load.filename, row_number,
                "Classes Per Week is "
                + (f"{raw_per_week!r}" if raw_per_week else "empty")
                + " - it should be a whole number of at least 1",
                identity=label,
            ))
            continue
        per_week = str(per_week_n)

        proposed = {
            "subject_code": code,
            "subject_name": name,
            # "Class"/"Lab" are translated by the subject adapter.
            "type": kind,
            "sessions_per_week": per_week,
            # One session occupies this many consecutive periods.
            "duration_slots": duration,
            "byod_required": "true" if byod else "false",
            "is_active": "true",
        }
        if code in subjects:
            # How often is not part of what a subject *is* - two sections can
            # take one subject a different number of times - so only its type
            # and length have to agree.
            differing = [
                f for f in ("type", "duration_slots")
                if subjects[code][f].strip().lower() != proposed[f].strip().lower()
            ]
            if differing:
                out.problems.append(Problem(
                    load.filename, row_number,
                    f"subject {code} is described differently here than on row "
                    f"{subject_origin[code][1]} ({', '.join(differing)}). One "
                    "code has to mean one subject.",
                    identity=label,
                ))
                continue
        else:
            subjects[code] = proposed
            subject_origin[code] = origin
            out.provenance[("Subjects", len(subjects) - 1)] = origin

        # ---- section. Strength belongs to the section, so two rows disagreeing
        # about it is a contradiction, not two facts.
        strength = str(row.get("strength", "")).strip()
        if section in sections:
            if sections[section]["strength"] != strength:
                out.problems.append(Problem(
                    load.filename, row_number,
                    f"section {section} has {strength or 'no'} students here "
                    f"and {sections[section]['strength']} on row "
                    f"{section_origin[section][1]}",
                    identity=label,
                ))
                continue
        else:
            sections[section] = {
                "section_number": section,
                "strength": strength,
                "academic_year": academic_year,
                "semester": str(semester),
                "program": program,
                "department": department,
                "is_active": "true",
            }
            section_origin[section] = origin

        stated_parent = str(row.get("parent_section_number", "")).strip()
        if stated_parent:
            parents[section] = stated_parent

        # One section, one subject, one teacher. A second row giving the same
        # section the same subject with someone else is a contradiction only
        # the department can settle; picking either would be a guess.
        if (section, code) in section_subject:
            first_fid, first_row = teacher_of[(section, code)]
            if first_fid != fid:
                out.problems.append(Problem(
                    load.filename, row_number,
                    f"section {section} is given {code} by {fname} ({fid}) here "
                    f"and by faculty {first_fid} on row {first_row}. One section "
                    "has one teacher for a subject.",
                    identity=label,
                ))
                continue
        else:
            teacher_of[(section, code)] = (fid, row_number)
            out.teachers.append((section, code, fid))

        faculty_subject[(fid, code)] = {"faculty_id": fid, "subject_code": code}
        previous = section_subject.get((section, code))
        if previous is not None and previous["sessions_per_week"] != per_week:
            out.problems.append(Problem(
                load.filename, row_number,
                f"section {section} takes {code} {per_week} times a week here and "
                f"{previous['sessions_per_week']} times on an earlier row",
                identity=label,
            ))
            continue
        section_subject[(section, code)] = {
            "section_number": section, "subject_code": code,
            "sessions_per_week": per_week,
        }

    _finish(
        out, faculty, subjects, sections, faculty_subject, section_subject,
        parents, faculty_origin, section_origin,
        context={"academic_year": academic_year, "semester": str(semester),
                 "program": program, "department": department},
    )


def _finish(
    out, faculty, subjects, sections, faculty_subject, section_subject,
    parents, faculty_origin, section_origin, context,
) -> None:
    known = set(sections)

    # Parentage the file did not state, read off the numbering. Only applied
    # when the section it implies is one we actually have.
    inferred: list[str] = []
    for number in sections:
        if number in parents:
            continue
        parent = _group_parent(number, known)
        if parent:
            parents[number] = parent
            inferred.append(f"{number} as a lab group of {parent}")

    for line in inferred:
        out.inferences.append(
            f"Read {line}: the group is scheduled separately from its section, "
            "and never at the same time."
        )

    # Groups whose sizes do not add up to their section's. Legitimate often
    # enough - a section can have one group listed and another still to come -
    # that it is said rather than refused. Nothing is invented for the missing
    # half: no students, subjects or teachers the file does not state.
    for parent, kids in _families(parents).items():
        if parent not in sections:
            continue
        total = sum(int(sections[k]["strength"] or 0) for k in kids if k in sections)
        stated = int(sections[parent]["strength"] or 0)
        if not total or total == stated:
            continue
        listed = ", ".join(
            f"{k} ({sections[k]['strength']} students)" for k in sorted(kids)
        )
        # A section is split into two groups, numbered 1 and 2 after it. Only
        # named when the listed groups follow that numbering - otherwise there
        # is no basis for saying which number is missing.
        expected = {parent + "1", parent + "2"}
        missing = sorted(expected - set(kids)) if set(kids) <= expected else []
        if missing:
            out.warnings.append(
                # The missing group leads: shown as one line, this is the part
                # anyone needs to act on.
                f"No rows for {', '.join(missing)}: section {parent} has {stated} "
                f"students, but only {listed} is in the file, so {stated - total} "
                f"of {parent}'s students have no lab scheduled until those rows "
                "are added."
            )
        else:
            out.warnings.append(
                f"Section {parent} has {stated} students, but its listed groups "
                f"({listed}) add up to {total}. Either a group is missing from "
                "the file or a number is out of date - the timetable only covers "
                "the students that are listed."
            )

    out.summary.update({
        "faculty": len(faculty),
        "subjects": len(subjects),
        "sections": len(sections),
        "groups": len(parents),
        # Class sessions a week: every (section, subject) meets
        # `sessions_per_week` times. A three-period lab is one class.
        "classes": sum(
            int(link["sessions_per_week"] or 0)
            for link in section_subject.values()
        ),
        # ... and the periods they fill, each session `duration` long.
        "required_periods": sum(
            int(link["sessions_per_week"] or 0)
            * int(subjects[code]["duration_slots"] or 0)
            for (_section, code), link in section_subject.items()
        ),
    })

    out.sheets["Faculty"] = (
        ["faculty_id", "name", "department", "is_active"], list(faculty.values()),
    )
    for i, fid in enumerate(faculty):
        out.provenance[("Faculty", i)] = faculty_origin[fid]

    out.sheets["Subjects"] = (
        ["subject_code", "subject_name", "type", "sessions_per_week",
         "duration_slots", "byod_required", "is_active"],
        list(subjects.values()),
    )
    out.sheets["Sections"] = (
        ["section_number", "strength", "academic_year", "semester", "program",
         "department", "is_active"],
        list(sections.values()),
    )
    for i, number in enumerate(sections):
        out.provenance[("Sections", i)] = section_origin[number]

    out.sheets["Faculty and subjects"] = (
        ["faculty_id", "subject_code"], list(faculty_subject.values()),
    )
    # Both name their intake. A section number alone is ambiguous as soon as
    # a second upload has the same sections, and these rows must attach to
    # this upload's sections - never to an earlier upload's.
    out.sheets["Sections and subjects"] = (
        ["section_number", "subject_code", "sessions_per_week", *context],
        [{**row, **context} for row in section_subject.values()],
    )
    out.sheets["Section groups"] = (
        ["section_number", "parent_section_number", *context],
        [
            {"section_number": child, "parent_section_number": parent, **context}
            for child, parent in parents.items()
        ],
    )


def _families(parents: dict[str, str]) -> dict[str, list[str]]:
    families: dict[str, list[str]] = {}
    for child, parent in parents.items():
        families.setdefault(parent, []).append(child)
    return families


# Which generated sheet feeds which importer entity. Stated here rather than
# discovered by scoring: a sheet built for an adapter should not have to win a
# similarity contest against it.
ENTITY_FOR_SHEET = {
    "Academic context": "academic_contexts",
    "Rooms": "rooms",
    "Faculty": "faculty",
    "Subjects": "subjects",
    "Sections": "sections",
    "Faculty and subjects": "faculty_subjects",
    "Sections and subjects": "section_subjects",
    "Section groups": "section_parents",
}
