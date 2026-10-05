"""One adapter per importable entity.

Each declares its identity (a normalised natural key), how to validate a row,
and how to read and write one record. Everything shared - preview shape,
upsert, full sync, history - lives in `engine.py`, so adding an entity means
adding an adapter and nothing else.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ..models import (
    AcademicContext,
    SectionGroup,
    Faculty,
    FacultySubject,
    FacultyUnavailability,
    Room,
    RoomUnavailability,
    Section,
    SectionSubject,
    SectionUnavailability,
    Subject,
    TimeSlot,
)
from .resolver import ReferenceResolver
from .types import CREATE, DUPLICATE, INVALID, UNCHANGED, UPDATE, FieldChange


class EntityAdapter(Protocol):
    """What a bulk-importable entity has to provide.

    Deliberately small: identity, validation, and how to read/write one
    record. Everything else - preview shape, upsert, full sync, history - is
    the shared workflow below.
    """

    key: str
    label: str
    columns: list[str]
    required: list[str]
    sample: list[dict[str, str]]
    description: str

    def identity(self, row: dict[str, str]) -> tuple: ...
    def identity_label(self, row: dict[str, str]) -> str: ...
    def validate(
        self, row: dict[str, str], db: Session, refs: ReferenceResolver
    ) -> tuple[dict[str, Any], list[str]]: ...
    def load_existing(self, db: Session) -> dict[tuple, Any]: ...
    def record_identity(self, obj: Any) -> tuple: ...
    def record_label(self, obj: Any) -> str: ...
    def diff(self, obj: Any, payload: dict[str, Any]) -> list[FieldChange]: ...
    def create(self, db: Session, payload: dict[str, Any]) -> Any: ...
    def update(self, obj: Any, payload: dict[str, Any]) -> None: ...
    def deactivate(self, db: Session, obj: Any) -> None: ...
    def is_active(self, obj: Any) -> bool: ...


def _norm(value: str) -> str:
    """Case- and whitespace-insensitive key component. Room 'a101' typed by
    one clerk and 'A101 ' by another are the same room."""
    return " ".join(value.split()).strip().lower()


def header_key(name: str) -> str:
    """Reduce a spreadsheet header to a comparable form.

    Real files spell the same column differently - ``BYOD`` and ``byod``,
    ``faculty name`` and ``faculty_name``, ``Room Number`` and ``room-number``.
    Case, spaces, underscores and hyphens all carry no meaning here, so they are
    removed before anything is compared.
    """
    return "".join(ch for ch in name.strip().lower() if ch.isalnum())


def normalise_row(adapter, row: dict[str, str]) -> dict[str, str]:
    """Rewrite a row's keys to the names an adapter validates against.

    An institution exports the columns its own systems produce. Requiring it to
    rename ``faculty_name`` to ``name`` before an import will work is a demand
    that has nothing to do with the data being correct, so each adapter instead
    declares what it will accept - see ``aliases``.

    Unrecognised columns are kept as-is rather than dropped, so an adapter can
    still see (and a preview can still report) something unexpected.
    """
    accepted: dict[str, str] = {}
    for canonical in adapter.columns:
        accepted[header_key(canonical)] = canonical
    for canonical, spellings in getattr(adapter, "aliases", {}).items():
        for spelling in spellings:
            accepted[header_key(spelling)] = canonical

    out: dict[str, str] = {}
    for key, value in row.items():
        out[accepted.get(header_key(key), key)] = value
    return out


def match_key(adapter, row: dict[str, str], payload: dict[str, Any]):
    """The key used to decide whether a row already exists.

    Usually the natural key read straight from the sheet. Some entities cannot
    be identified from their text alone - an availability block names a room by
    its code or its number, and a section with or without its context, all of
    which are the same record - so those adapters derive the key from what the
    references resolved to instead.
    """
    derive = getattr(adapter, "identity_from_payload", None)
    if derive is not None:
        return derive(payload)
    return adapter.identity(row)


def missing_required(adapter, headers: list[str]) -> list[str]:
    """Required columns absent from `headers`, judged after aliasing.

    Checking raw names would reject a file whose columns are all present under
    the spellings that file's system produces - which is the usual case, not an
    edge one.
    """
    present = {header_key(h) for h in headers}
    for canonical, spellings in getattr(adapter, "aliases", {}).items():
        if any(header_key(sp) in present for sp in spellings):
            present.add(header_key(canonical))
    return [c for c in adapter.required if header_key(c) not in present]


class RoomAdapter:
    """Rooms, keyed by (block, floor, room_number)."""

    key = "rooms"
    label = "Rooms"
    columns = [
        "block", "floor", "room_number", "room_code", "capacity",
        "room_type", "lab_type", "byod", "charging", "charging_sockets",
        "is_faculty_room", "is_active",
    ]
    required = ["room_number", "capacity", "room_type"]
    sample = [
        {
            "block": "38", "floor": "5", "room_number": "502", "room_code": "38-502",
            "capacity": "70", "room_type": "theory", "lab_type": "",
            "byod": "true", "charging": "true", "charging_sockets": "30",
            "is_active": "true",
        },
        {
            "block": "36", "floor": "7", "room_number": "701", "room_code": "36-701",
            "capacity": "60", "room_type": "lab", "lab_type": "COMPUTING",
            "byod": "false", "charging": "true", "charging_sockets": "60",
            "is_active": "true",
        },
    ]
    description = (
        "room_type is 'theory', 'lab' or 'faculty'. lab_type applies to labs only "
        "and is matched against a subject's required lab type. Faculty rooms are "
        "never schedulable. Rooms are matched on block + floor + room_number, so "
        "the same room number in two blocks stays two distinct rooms. byod and "
        "charging say whether students can work on their own machines here, "
        "which lets a software practical use an ordinary classroom instead of a "
        "scarce lab. room_code is the label people use (e.g. 36-101); it is "
        "derived from block and room_number when left blank."
    )

    VALID_TYPES = ("theory", "lab", "faculty")
    # What a real rooms export calls these columns.
    aliases = {
        "room_number": ["room", "room_no", "roomnumber", "number"],
        "room_code": ["code", "room code"],
        "room_type": ["type", "space_type"],
        "byod": ["BYOD", "byod_available", "own_device"],
        "charging": ["power", "charging_available"],
        "charging_sockets": ["sockets", "power_points"],
        "capacity": ["seats", "seating_capacity"],
    }
    # A spreadsheet says what a building says. The scheduler's vocabulary is
    # narrower, so translate rather than reject: a "classroom" is a teaching
    # room, which this model has always called 'theory'.
    TYPE_SYNONYMS = {
        "classroom": "theory",
        "class_room": "theory",
        "lecture": "theory",
        "lecture_hall": "theory",
        "faculty_room": "faculty",
        "office": "faculty",
        "staff_room": "faculty",
        "laboratory": "lab",
    }

    def identity(self, row: dict[str, str]) -> tuple:
        return (
            _norm(row.get("block", "")),
            _norm(row.get("floor", "")),
            _norm(row.get("room_number", "")),
        )

    def identity_label(self, row: dict[str, str]) -> str:
        block = row.get("block", "").strip()
        floor = row.get("floor", "").strip()
        number = row.get("room_number", "").strip() or "(no room_number)"
        parts = [p for p in (block, floor) if p]
        return f"{number}" + (f" ({' / '.join(parts)})" if parts else "")

    def validate(
        self, row: dict[str, str], db: Session, refs: ReferenceResolver
    ) -> tuple[dict[str, Any], list[str]]:
        errors: list[str] = []

        room_number = row.get("room_number", "").strip()
        if not room_number:
            errors.append("room_number is required")

        raw_capacity = row.get("capacity", "").strip()
        capacity: int | None = None
        if not raw_capacity:
            errors.append("capacity is missing")
        else:
            try:
                capacity = int(float(raw_capacity))
            except ValueError:
                errors.append(f"capacity must be a whole number, got {raw_capacity!r}")
            else:
                if capacity <= 0:
                    errors.append(f"capacity must be greater than zero, got {capacity}")
                elif capacity > 1000:
                    errors.append(f"capacity {capacity} looks wrong (max 1000)")

        room_type = row.get("room_type", "").strip().lower().replace(" ", "_")
        room_type = self.TYPE_SYNONYMS.get(room_type, room_type)
        if not room_type:
            errors.append("room_type is required")
        elif room_type not in self.VALID_TYPES:
            errors.append(
                f"room_type must be one of {', '.join(self.VALID_TYPES)}, got {room_type!r}"
            )

        lab_type = row.get("lab_type", "").strip() or None
        if lab_type and room_type != "lab":
            errors.append(
                f"lab_type {lab_type!r} is only meaningful for a lab, "
                f"but room_type is {room_type or 'missing'}"
            )
        if room_type == "faculty" and lab_type:
            errors.append("a faculty room cannot have a lab_type")

        # Some exports carry both a room_type and a separate faculty-room flag.
        # When they disagree, guessing which one the building actually meant
        # would be inventing an answer.
        raw_faculty_flag = row.get("is_faculty_room", "").strip()
        if raw_faculty_flag:
            says_faculty = _parse_bool(raw_faculty_flag, default=False)
            if says_faculty and room_type not in ("faculty", ""):
                errors.append(
                    f"is_faculty_room says yes but room_type is {room_type!r} - "
                    "set room_type to 'faculty' (or clear is_faculty_room) so "
                    "there is one answer"
                )
            elif not says_faculty and room_type == "faculty":
                errors.append(
                    "room_type is 'faculty' but is_faculty_room says no - "
                    "these contradict each other"
                )

        raw_active = row.get("is_active", "").strip().lower()
        is_active = raw_active not in ("false", "0", "no", "n")

        block = row.get("block", "").strip()

        # The label people use. Derived from block and number when absent,
        # which is what most files leave it as - room numbers repeat across
        # buildings, so the pair is what makes it unambiguous.
        room_code = row.get("room_code", "").strip() or None
        if room_code is None and room_number:
            room_code = f"{block}-{room_number}" if block else room_number

        raw_sockets = row.get("charging_sockets", "").strip()
        sockets: int | None = None
        if raw_sockets:
            try:
                sockets = int(float(raw_sockets))
            except ValueError:
                errors.append(
                    f"charging_sockets must be a whole number, got {raw_sockets!r}"
                )
            else:
                if sockets < 0:
                    errors.append("charging_sockets cannot be negative")

        payload = {
            "room_number": room_number,
            "block": block,
            "floor": row.get("floor", "").strip() or None,
            "room_code": room_code,
            "capacity": capacity,
            "room_type": room_type,
            "lab_type": lab_type,
            "byod": _parse_bool(row.get("byod", ""), default=False),
            "charging": _parse_bool(row.get("charging", ""), default=False),
            "charging_sockets": sockets,
            "is_active": is_active,
        }
        return payload, errors

    def load_existing(self, db: Session) -> dict[tuple, Any]:
        # One query for the whole file rather than a lookup per row - a
        # 1000-room import would otherwise be 1000 round trips.
        return {self.record_identity(r): r for r in db.query(Room).all()}

    def record_identity(self, obj: Room) -> tuple:
        return (_norm(obj.block or ""), _norm(obj.floor or ""), _norm(obj.room_number))

    def record_label(self, obj: Room) -> str:
        parts = [p for p in (obj.block, obj.floor) if p]
        return obj.room_number + (f" ({' / '.join(parts)})" if parts else "")

    def diff(self, obj: Room, payload: dict[str, Any]) -> list[FieldChange]:
        out: list[FieldChange] = []
        for name in (
            "room_number", "block", "floor", "room_code", "capacity", "room_type",
            "lab_type", "byod", "charging", "charging_sockets", "is_active",
        ):
            old, new = getattr(obj, name), payload[name]
            if (old or None) != (new or None):
                out.append(FieldChange(field=name, old=_fmt(old), new=_fmt(new)))
        return out

    def create(self, db: Session, payload: dict[str, Any]) -> Room:
        room = Room(**payload)
        db.add(room)
        return room

    def update(self, obj: Room, payload: dict[str, Any]) -> None:
        for name, value in payload.items():
            setattr(obj, name, value)

    def deactivate(self, db: Session, obj: Room) -> None:
        obj.is_active = False

    def is_active(self, obj: Room) -> bool:
        return bool(obj.is_active)

    # Declares this adapter bulk-write capable (see apply() below): Room has
    # no relationship columns to populate, so every payload dict maps
    # directly onto a bulk_insert_mappings/bulk_update_mappings call instead
    # of one INSERT/UPDATE per row (spec PART 21 - no N-query imports).
    model = Room


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _parse_bool(raw: str, default: bool = True) -> bool:
    v = raw.strip().lower()
    if not v:
        return default
    return v not in ("false", "0", "no", "n")


class FacultyAdapter:
    """Faculty, keyed by the stable ``faculty_id`` business code (spec PART
    4's own example: ``T-1``, ``T-4``) - which this schema stores as
    ``Faculty.faculty_code``. A faculty member's *name* is not the identity;
    two different people can share a display name, and a rename must not be
    read as "a different faculty member"."""

    key = "faculty"
    label = "Faculty"
    aliases = {
        "faculty_id": ["faculty_code", "code", "staff_id", "employee_id"],
        "name": ["faculty_name", "full_name", "teacher_name"],
    }
    columns = ["faculty_id", "name", "department", "is_active"]
    required = ["faculty_id", "name"]
    sample = [
        {"faculty_id": "T-1", "name": "Dr. A. Sharma", "department": "CSE", "is_active": "true"},
        {"faculty_id": "T-4", "name": "Dr. B. Iyer", "department": "ECE", "is_active": "true"},
    ]
    description = (
        "faculty_id is the stable code used everywhere else (e.g. in the Faculty <-> "
        "Subject mapping import) - it is the identity, so renaming 'name' updates the "
        "existing faculty member rather than creating a new one."
    )
    model = Faculty

    def identity(self, row: dict[str, str]) -> tuple:
        return (_norm(row.get("faculty_id", "")),)

    def identity_label(self, row: dict[str, str]) -> str:
        return row.get("faculty_id", "").strip() or "(no faculty_id)"

    def validate(
        self, row: dict[str, str], db: Session, refs: ReferenceResolver
    ) -> tuple[dict[str, Any], list[str]]:
        errors: list[str] = []
        faculty_id = row.get("faculty_id", "").strip()
        if not faculty_id:
            errors.append("faculty_id is required")
        name = row.get("name", "").strip()
        if not name:
            errors.append("name is required")

        payload = {
            "faculty_code": faculty_id,
            "name": name,
            "department": row.get("department", "").strip() or None,
            "is_active": _parse_bool(row.get("is_active", "")),
        }
        return payload, errors

    def load_existing(self, db: Session) -> dict[tuple, Any]:
        return {self.record_identity(f): f for f in db.query(Faculty).all()}

    def record_identity(self, obj: Faculty) -> tuple:
        return (_norm(obj.faculty_code),)

    def record_label(self, obj: Faculty) -> str:
        return f"{obj.faculty_code} ({obj.name})"

    def diff(self, obj: Faculty, payload: dict[str, Any]) -> list[FieldChange]:
        out: list[FieldChange] = []
        for name in ("name", "department", "is_active"):
            old, new = getattr(obj, name), payload[name]
            if (old or None) != (new or None):
                out.append(FieldChange(field=name, old=_fmt(old), new=_fmt(new)))
        return out

    def create(self, db: Session, payload: dict[str, Any]) -> Faculty:
        f = Faculty(**payload)
        db.add(f)
        return f

    def update(self, obj: Faculty, payload: dict[str, Any]) -> None:
        for k, v in payload.items():
            setattr(obj, k, v)

    def deactivate(self, db: Session, obj: Faculty) -> None:
        obj.is_active = False

    def is_active(self, obj: Faculty) -> bool:
        return bool(obj.is_active)


class SubjectAdapter:
    """Subjects, keyed by ``subject_code`` (spec PART 5) - already the
    schema's own unique business key."""

    key = "subjects"
    label = "Subjects"
    columns = [
        "subject_code", "subject_name", "type", "sessions_per_week",
        "duration_slots", "lecture_per_week", "lecture_duration_slots",
        "practical_per_week", "practical_duration_slots",
        "required_lab_type", "byod_required", "charging_required", "is_active",
    ]
    required = ["subject_code", "subject_name", "type"]
    sample = [
        {"subject_code": "CS201", "subject_name": "Data Structures", "type": "theory",
         "sessions_per_week": "3", "duration_slots": "1", "required_lab_type": "",
         "is_active": "true"},
        {"subject_code": "CS251", "subject_name": "Data Structures Lab", "type": "practical",
         "sessions_per_week": "1", "duration_slots": "2", "required_lab_type": "COMPUTING",
         "is_active": "true"},
        {"subject_code": "CAP460", "subject_name": "Fundamentals of Python", "type": "mixed",
         "lecture_per_week": "2", "lecture_duration_slots": "1",
         "practical_per_week": "2", "practical_duration_slots": "2",
         "required_lab_type": "programming", "byod_required": "true",
         "charging_required": "true", "is_active": "true"},
    ]
    description = (
        "duration_slots is how many consecutive periods one session occupies (1-3). "
        "required_lab_type only applies to practicals, matched against a lab room's "
        "lab_type. Use type='mixed' for a subject with both a lecture and a "
        "practical component - give it lecture_per_week/practical_per_week (and "
        "optionally their durations) instead of sessions_per_week, since the two "
        "components are scheduled separately and usually in different rooms. "
        "byod_required/charging_required narrow which rooms a practical can use. "
        "Room overrides (fixed/allowed rooms) are set on the Subjects page."
    )
    VALID_TYPES = ("theory", "practical", "mixed")
    # The same translation RoomAdapter does for spaces, for delivery types. A
    # teaching-load sheet says what a timetable says - "Class" and "Lab" - and
    # rejecting that as an invalid type would be pedantry about vocabulary
    # rather than a real disagreement about meaning.
    # Deliberately short. Every entry here is a word that unambiguously means
    # one of the three types and nothing else; anything less certain is better
    # rejected, because a wrong delivery type sends a subject to the wrong kind
    # of room and the timetable still looks finished.
    #
    # "lecture" is not on the list on purpose - it reads as a delivery mode,
    # but a subject described as a lecture may still be the theory half of a
    # mixed subject, and guessing there would be guessing about the syllabus.
    TYPE_SYNONYMS = {
        "class": "theory",
        "class_room": "theory",
        "classroom": "theory",
        "lab": "practical",
        "laboratory": "practical",
    }
    aliases = {
        "subject_name": ["name", "title"],
        "type": ["delivery_type", "subject_type"],
        "lecture_per_week": ["lectures_per_week", "lecture_sessions_per_week", "L"],
        "practical_per_week": ["practicals_per_week", "practical_sessions_per_week", "P"],
        "lecture_duration_slots": ["lecture_duration", "lecture_length"],
        "practical_duration_slots": ["practical_duration", "practical_length"],
        "required_lab_type": ["lab_type"],
        "byod_required": ["BYOD_required", "needs_byod"],
        "charging_required": ["needs_charging"],
        "sessions_per_week": ["periods_per_week"],
        "duration_slots": ["duration", "session_length_hours"],
    }
    model = Subject

    def identity(self, row: dict[str, str]) -> tuple:
        return (_norm(row.get("subject_code", "")),)

    def identity_label(self, row: dict[str, str]) -> str:
        return row.get("subject_code", "").strip() or "(no subject_code)"

    def validate(
        self, row: dict[str, str], db: Session, refs: ReferenceResolver
    ) -> tuple[dict[str, Any], list[str]]:
        errors: list[str] = []
        code = row.get("subject_code", "").strip()
        if not code:
            errors.append("subject_code is required")
        name = row.get("subject_name", "").strip()
        if not name:
            errors.append("subject_name is required")

        subject_type = row.get("type", "").strip().lower().replace(" ", "_")
        subject_type = self.TYPE_SYNONYMS.get(subject_type, subject_type)
        if not subject_type:
            errors.append("type is required")
        elif subject_type not in self.VALID_TYPES:
            errors.append(f"type must be one of {', '.join(self.VALID_TYPES)}, got {subject_type!r}")

        raw_spw = row.get("sessions_per_week", "").strip() or "1"
        sessions_per_week: int | None = None
        try:
            sessions_per_week = int(float(raw_spw))
        except ValueError:
            errors.append(f"sessions_per_week must be a whole number, got {raw_spw!r}")
        else:
            if sessions_per_week <= 0:
                errors.append("sessions_per_week must be greater than zero")

        raw_dur = row.get("duration_slots", "").strip() or "1"
        duration: int | None = None
        try:
            duration = int(float(raw_dur))
        except ValueError:
            errors.append(f"duration_slots must be a whole number, got {raw_dur!r}")
        else:
            if not (1 <= duration <= 3):
                errors.append(f"duration_slots must be between 1 and 3, got {duration}")

        lab_type = row.get("required_lab_type", "").strip() or None
        if lab_type and subject_type == "theory":
            errors.append("required_lab_type is only meaningful for a practical subject")

        # A mixed subject carries a separate weekly load per component, because
        # its lecture and its practical are scheduled independently and usually
        # in different kinds of room.
        def _component(field: str, label: str) -> int | None:
            raw = row.get(field, "").strip()
            if not raw:
                return None
            try:
                value = int(float(raw))
            except ValueError:
                errors.append(f"{label} must be a whole number, got {raw!r}")
                return None
            if value < 0:
                errors.append(f"{label} cannot be negative")
                return None
            return value

        lecture_spw = _component("lecture_per_week", "lecture_per_week")
        practical_spw = _component("practical_per_week", "practical_per_week")
        lecture_len = _component("lecture_duration_slots", "lecture_duration_slots")
        practical_len = _component("practical_duration_slots", "practical_duration_slots")

        # A real subjects sheet states a per-component load for every row, not
        # only for mixed ones: a theory subject carries lecture_per_week=3 and
        # practical_per_week=0. Reading that as "the load of the component this
        # subject has" is the natural meaning, and rejecting it would refuse a
        # perfectly clear file over a column it did not need to leave blank.
        if subject_type == "mixed":
            if not lecture_spw:
                errors.append(
                    "a mixed subject needs lecture_per_week greater than zero "
                    "(it is the lecture component)"
                )
            if not practical_spw:
                errors.append(
                    "a mixed subject needs practical_per_week greater than zero "
                    "(it is the practical component) - use type 'theory' if it "
                    "has no practical"
                )
            # One period each unless the file says otherwise, which is the
            # common shape and never invents a longer block than was stated.
            lecture_len = lecture_len or 1
            practical_len = practical_len or 1
            for value, label in ((lecture_len, "lecture"), (practical_len, "practical")):
                if not (1 <= value <= 3):
                    errors.append(
                        f"{label}_duration_slots must be between 1 and 3, got {value}"
                    )
        else:
            # Named `field`/`value` rather than `name`/`value` deliberately -
            # this used to read `name, value = wrong_component`, which shadows
            # the subject's own `name` from higher up the function. Every
            # theory or practical subject took this branch, so every one of
            # them silently lost its real name to the literal string
            # "practical_per_week" or "lecture_per_week" the moment this
            # component happened to be absent or zero - which is the common
            # case, not the rare one. Caught by importing the demo workbook
            # into the live application and finding subjects literally named
            # after a column.
            wrong_component = (
                ("practical_per_week", practical_spw) if subject_type == "theory"
                else ("lecture_per_week", lecture_spw)
            )
            field, value = wrong_component
            if value:
                errors.append(
                    f"{field} is {value} but type is {subject_type!r} - use type "
                    "'mixed' for a subject with both a lecture and a practical "
                    "component"
                )

            # The component that does match becomes this subject's weekly load.
            own_component = lecture_spw if subject_type == "theory" else practical_spw
            own_length = lecture_len if subject_type == "theory" else practical_len
            if own_component:
                sessions_per_week = own_component
            if own_length:
                if 1 <= own_length <= 3:
                    duration = own_length
                else:
                    errors.append(
                        f"duration must be between 1 and 3, got {own_length}"
                    )
            lecture_spw = practical_spw = lecture_len = practical_len = None

        payload = {
            "code": code,
            "name": name,
            "type": subject_type,
            "sessions_per_week": sessions_per_week or 1,
            "session_length_hours": duration or 1,
            "lecture_sessions_per_week": lecture_spw if subject_type == "mixed" else None,
            "lecture_session_length": lecture_len if subject_type == "mixed" else None,
            "practical_sessions_per_week": practical_spw if subject_type == "mixed" else None,
            "practical_session_length": practical_len if subject_type == "mixed" else None,
            "byod_required": _parse_bool(row.get("byod_required", ""), default=False),
            "charging_required": _parse_bool(row.get("charging_required", ""), default=False),
            "required_lab_type": lab_type,
            "is_active": _parse_bool(row.get("is_active", "")),
        }
        return payload, errors

    def load_existing(self, db: Session) -> dict[tuple, Any]:
        return {self.record_identity(s): s for s in db.query(Subject).all()}

    def record_identity(self, obj: Subject) -> tuple:
        return (_norm(obj.code),)

    def record_label(self, obj: Subject) -> str:
        return f"{obj.code} ({obj.name})"

    def diff(self, obj: Subject, payload: dict[str, Any]) -> list[FieldChange]:
        out: list[FieldChange] = []
        for name in ("name", "type", "sessions_per_week", "session_length_hours",
                     "lecture_sessions_per_week", "lecture_session_length",
                     "practical_sessions_per_week", "practical_session_length",
                     "byod_required", "charging_required",
                     "required_lab_type", "is_active"):
            old, new = getattr(obj, name), payload[name]
            if (old or None) != (new or None):
                out.append(FieldChange(field=name, old=_fmt(old), new=_fmt(new)))
        return out

    def create(self, db: Session, payload: dict[str, Any]) -> Subject:
        s = Subject(**payload)
        db.add(s)
        return s

    def update(self, obj: Subject, payload: dict[str, Any]) -> None:
        for k, v in payload.items():
            setattr(obj, k, v)

    def deactivate(self, db: Session, obj: Subject) -> None:
        obj.is_active = False

    def is_active(self, obj: Subject) -> bool:
        return bool(obj.is_active)


def _find_context(db: Session, year: str, semester: str, program: str, department: str) -> AcademicContext | None:
    try:
        sem_int = int(float(semester))
    except ValueError:
        return None
    return (
        db.query(AcademicContext)
        .filter(
            AcademicContext.academic_year == year.strip(),
            AcademicContext.semester == sem_int,
            AcademicContext.program == program.strip(),
            AcademicContext.department == department.strip(),
        )
        .first()
    )


class SectionAdapter:
    """Sections, keyed context-aware (spec PART 6): the same section_number
    in two different academic contexts (e.g. two different semesters, or two
    different programs) is two distinct sections, never merged - matched on
    (academic_year, semester, program, department, section_number) together,
    not section_number alone."""

    key = "sections"
    label = "Sections"
    aliases = {
        "section_number": ["section", "section_code"],
        "strength": ["student_strength", "students", "size"],
        "batch": ["admission_batch", "cohort"],
    }
    columns = [
        "section_number", "academic_year", "semester", "program", "department",
        "strength", "batch", "is_active",
    ]
    required = ["section_number", "academic_year", "semester", "program", "department", "strength"]
    sample = [
        {"section_number": "CSE-3A", "academic_year": "2026-27", "semester": "1",
         "program": "BCA", "department": "CSE", "strength": "60",
         "batch": "BCA-2024", "is_active": "true"},
    ]
    description = (
        "A section is identified by section_number *within* its academic context - "
        "the same section_number in Semester 1 and Semester 3 is two different "
        "sections. The academic context (academic_year/semester/program/department) "
        "must already exist; create it on the Sections page first."
    )
    model = Section

    def identity(self, row: dict[str, str]) -> tuple:
        return (
            _norm(row.get("academic_year", "")), (row.get("semester", "") or "").strip(),
            _norm(row.get("program", "")), _norm(row.get("department", "")),
            _norm(row.get("section_number", "")),
        )

    def identity_label(self, row: dict[str, str]) -> str:
        num = row.get("section_number", "").strip() or "(no section_number)"
        ctx = " / ".join(
            p for p in (row.get("academic_year", ""), f"Sem {row.get('semester', '')}".strip(),
                        row.get("program", ""), row.get("department", ""))
            if p and p != "Sem"
        )
        return f"{num} ({ctx})" if ctx else num

    def validate(
        self, row: dict[str, str], db: Session, refs: ReferenceResolver
    ) -> tuple[dict[str, Any], list[str]]:
        errors: list[str] = []
        section_number = row.get("section_number", "").strip()
        if not section_number:
            errors.append("section_number is required")

        year = row.get("academic_year", "").strip()
        semester = row.get("semester", "").strip()
        program = row.get("program", "").strip()
        department = row.get("department", "").strip()
        ctx = None
        if not (year and semester and program and department):
            errors.append("academic_year, semester, program and department are all required")
        else:
            ctx = refs.context(year, semester, program, department)
            if ctx is None:
                errors.append(
                    f"academic context {year} / Sem {semester} / {program} ({department}) "
                    "does not exist - create it on the Sections page first"
                )

        raw_strength = row.get("strength", "").strip()
        strength: int | None = None
        if not raw_strength:
            errors.append("strength is required")
        else:
            try:
                strength = int(float(raw_strength))
            except ValueError:
                errors.append(f"strength must be a whole number, got {raw_strength!r}")
            else:
                if strength <= 0:
                    errors.append("strength must be greater than zero")

        payload = {
            "section_number": section_number,
            "academic_context_id": ctx.id if ctx else None,
            "strength": strength or 0,
            "batch": row.get("batch", "").strip() or None,
            "is_active": _parse_bool(row.get("is_active", "")),
        }
        return payload, errors

    def load_existing(self, db: Session) -> dict[tuple, Any]:
        return {self.record_identity(s): s for s in db.query(Section).all()}

    def record_identity(self, obj: Section) -> tuple:
        ctx = obj.academic_context
        return (
            _norm(ctx.academic_year), str(ctx.semester), _norm(ctx.program), _norm(ctx.department),
            _norm(obj.section_number),
        )

    def record_label(self, obj: Section) -> str:
        return f"{obj.section_number} ({obj.academic_context.label})"

    def diff(self, obj: Section, payload: dict[str, Any]) -> list[FieldChange]:
        out: list[FieldChange] = []
        for name in ("strength", "batch", "is_active"):
            old, new = getattr(obj, name), payload[name]
            if (old or None) != (new or None):
                out.append(FieldChange(field=name, old=_fmt(old), new=_fmt(new)))
        return out

    def create(self, db: Session, payload: dict[str, Any]) -> Section:
        s = Section(**payload)
        db.add(s)
        return s

    def update(self, obj: Section, payload: dict[str, Any]) -> None:
        # academic_context_id is part of the identity, not a mutable field -
        # never overwritten by an update (a genuine context move would be a
        # different identity entirely, i.e. a separate create).
        obj.strength = payload["strength"]
        obj.is_active = payload["is_active"]

    def deactivate(self, db: Session, obj: Section) -> None:
        obj.is_active = False

    def is_active(self, obj: Section) -> bool:
        return bool(obj.is_active)


class FacultySubjectAdapter:
    """The faculty <-> subject eligibility mapping (spec PART 7). A pure
    association: it either exists or it doesn't, so there is nothing to
    "update" - only create, leave alone, or (full-sync only) remove. No
    surrogate PK exists on this table, so this adapter does not declare
    ``model`` and uses apply()'s per-row fallback path."""

    key = "faculty_subjects"
    label = "Faculty ↔ Subject"
    columns = ["faculty_id", "subject_code"]
    required = ["faculty_id", "subject_code"]
    sample = [
        {"faculty_id": "T-1", "subject_code": "ECE281"},
        {"faculty_id": "T-4", "subject_code": "ECE281"},
    ]
    description = (
        "Declares that a faculty member is eligible to teach a subject. Both sides "
        "must already exist - import Faculty and Subjects first. Full Synchronization "
        "removes a mapping absent from the file (never the faculty or subject "
        "themselves, just the eligibility link)."
    )

    def identity(self, row: dict[str, str]) -> tuple:
        return (_norm(row.get("faculty_id", "")), _norm(row.get("subject_code", "")))

    def identity_label(self, row: dict[str, str]) -> str:
        return f"{row.get('faculty_id', '?')} -> {row.get('subject_code', '?')}"

    def validate(
        self, row: dict[str, str], db: Session, refs: ReferenceResolver
    ) -> tuple[dict[str, Any], list[str]]:
        errors: list[str] = []
        faculty_code = row.get("faculty_id", "").strip()
        subject_code = row.get("subject_code", "").strip()
        faculty = refs.faculty_by_code(faculty_code) if faculty_code else None
        subject = refs.subject_by_code(subject_code) if subject_code else None

        if not faculty_code:
            errors.append("faculty_id is required")
        elif faculty is None:
            errors.append(f"faculty code {faculty_code!r} does not exist - import Faculty first")
        if not subject_code:
            errors.append("subject_code is required")
        elif subject is None:
            errors.append(f"subject code {subject_code!r} does not exist - import Subjects first")

        payload = {
            "faculty_id": faculty.id if faculty else None,
            "subject_id": subject.id if subject else None,
        }
        return payload, errors

    def load_existing(self, db: Session) -> dict[tuple, Any]:
        return {self.record_identity(m): m for m in db.query(FacultySubject).all()}

    def record_identity(self, obj: FacultySubject) -> tuple:
        return (_norm(obj.faculty.faculty_code), _norm(obj.subject.code))

    def record_label(self, obj: FacultySubject) -> str:
        return f"{obj.faculty.faculty_code} -> {obj.subject.code}"

    def diff(self, obj: FacultySubject, payload: dict[str, Any]) -> list[FieldChange]:
        return []  # a mapping has no fields besides its identity

    def create(self, db: Session, payload: dict[str, Any]) -> FacultySubject:
        m = FacultySubject(faculty_id=payload["faculty_id"], subject_id=payload["subject_id"])
        db.add(m)
        return m

    def update(self, obj: FacultySubject, payload: dict[str, Any]) -> None:
        pass  # nothing to update - identity is the whole record

    def deactivate(self, db: Session, obj: FacultySubject) -> None:
        # No is_active concept on a pure association row - full-sync removal
        # means deleting the link, which touches neither the faculty nor the
        # subject it connects.
        db.delete(obj)

    def is_active(self, obj: FacultySubject) -> bool:
        return True


class SectionSubjectAdapter:
    """The section <-> subject curriculum mapping (spec PART 8), context-aware
    the same way SectionAdapter is - a (section, subject) pair only makes
    sense within one academic context."""

    key = "section_subjects"
    label = "Section ↔ Subject"
    columns = [
        "section_number", "subject_code", "sessions_per_week",
        "academic_year", "semester", "program", "department",
    ]
    aliases = {
        "sessions_per_week": ["classes_per_week", "classes_a_week"],
    }
    # Only the pair itself is required. The context columns are accepted and
    # used when present, and inferred from the section when not: a curriculum
    # sheet that lists section and subject is unambiguous on its own, and a
    # workbook describing one intake has no reason to repeat four context
    # columns on all 120 of its rows.
    required = ["section_number", "subject_code"]
    sample = [
        {"academic_year": "2026-27", "semester": "1", "program": "BCA", "department": "CSE",
         "section_number": "CSE-3A", "subject_code": "CS201"},
    ]
    description = (
        "Declares that a section takes a subject, and optionally how many times "
        "a week (sessions_per_week) - two sections can take one subject a "
        "different number of times. Left empty, the subject's own count applies. "
        "Name the academic context "
        "columns if the same section number appears in more than one context; "
        "otherwise the section alone identifies it. Full Synchronization "
        "removes a mapping absent from the file (the curriculum link only, "
        "never the section or subject themselves)."
    )

    def identity(self, row: dict[str, str]) -> tuple:
        # Only for spotting a row repeated within one sheet - which record it
        # *is* comes from `identity_from_payload`. The context columns are part
        # of it when given: 2401 -> ECE181 in two intakes is two rows.
        return (
            _norm(row.get("academic_year", "")),
            _norm(row.get("semester", "")),
            _norm(row.get("program", "")),
            _norm(row.get("department", "")),
            _norm(row.get("section_number", "")),
            _norm(row.get("subject_code", "")),
        )

    def identity_from_payload(self, payload: dict[str, Any]) -> tuple:
        """The mapping is the pair of *records*, not the pair of numbers.

        Keyed on the section and subject the row resolved to. Keyed on the
        typed numbers - as it was - section 2401 -> ECE181 in a second intake
        matched the first intake's identical row and was reported unchanged,
        so the new intake's sections never got their subjects. Keyed on ids,
        the same mapping written with or without its context is still one
        record, which is what the typed key was protecting.
        """
        return ("pair", payload.get("section_id"), payload.get("subject_id"))

    def identity_label(self, row: dict[str, str]) -> str:
        return f"{row.get('section_number', '?')} -> {row.get('subject_code', '?')}"

    def validate(
        self, row: dict[str, str], db: Session, refs: ReferenceResolver
    ) -> tuple[dict[str, Any], list[str]]:
        errors: list[str] = []
        year = row.get("academic_year", "").strip()
        semester = row.get("semester", "").strip()
        program = row.get("program", "").strip()
        department = row.get("department", "").strip()
        section_number = row.get("section_number", "").strip()
        subject_code = row.get("subject_code", "").strip()

        section = None
        names_context = bool(year or semester or program or department)
        if not section_number:
            errors.append("section_number is required")
        elif names_context:
            # The sheet states which context it means, so honour it exactly -
            # a partially filled context is a mistake worth reporting, not
            # something to quietly fall back from.
            if not (year and semester and program and department):
                errors.append(
                    "academic_year, semester, program and department must all be "
                    "given together, or all left out to identify the section by "
                    "its number alone"
                )
            else:
                ctx = refs.context(year, semester, program, department)
                if ctx is None:
                    errors.append(
                        f"academic context {year} / Sem {semester} / {program} "
                        f"({department}) was not found - add it to the Academic "
                        "Context sheet or create it first"
                    )
                else:
                    section = refs.section(ctx.id, section_number)
                    if section is None:
                        errors.append(
                            f"section {section_number!r} was not found in that "
                            "context - check the Sections sheet"
                        )
        else:
            section, problem = refs.section_anywhere(section_number)
            if problem:
                errors.append(problem)

        subject = refs.subject_by_code(subject_code) if subject_code else None
        if not subject_code:
            errors.append("subject_code is required")
        elif subject is None:
            errors.append(f"subject code {subject_code!r} does not exist - import Subjects first")

        raw = row.get("sessions_per_week", "").strip()
        per_week: int | None = None
        if raw:
            try:
                per_week = int(float(raw))
                if per_week != float(raw) or per_week < 1:
                    raise ValueError
            except ValueError:
                errors.append(
                    f"sessions_per_week must be a whole number of at least 1, not {raw!r}"
                )
                per_week = None

        payload = {
            "section_id": section.id if section else None,
            "subject_id": subject.id if subject else None,
            "sessions_per_week": per_week,
        }
        return payload, errors

    def load_existing(self, db: Session) -> dict[tuple, Any]:
        return {self.record_identity(m): m for m in db.query(SectionSubject).all()}

    def record_identity(self, obj: SectionSubject) -> tuple:
        # Mirrors `identity_from_payload` exactly, or an existing mapping is
        # never matched and every re-import reports it as new.
        return ("pair", obj.section_id, obj.subject_id)

    def record_label(self, obj: SectionSubject) -> str:
        return f"{obj.section.section_number} -> {obj.subject.code}"

    def diff(self, obj: SectionSubject, payload: dict[str, Any]) -> list[FieldChange]:
        new = payload.get("sessions_per_week")
        if new is not None and new != obj.sessions_per_week:
            return [FieldChange(field="sessions_per_week",
                                old=_fmt(obj.sessions_per_week), new=_fmt(new))]
        return []

    def create(self, db: Session, payload: dict[str, Any]) -> SectionSubject:
        m = SectionSubject(
            section_id=payload["section_id"],
            subject_id=payload["subject_id"],
            sessions_per_week=payload.get("sessions_per_week"),
        )
        db.add(m)
        return m

    def update(self, obj: SectionSubject, payload: dict[str, Any]) -> None:
        # A sheet that leaves the column empty says nothing about the count,
        # so it keeps whatever was recorded rather than erasing it.
        if payload.get("sessions_per_week") is not None:
            obj.sessions_per_week = payload["sessions_per_week"]

    def deactivate(self, db: Session, obj: SectionSubject) -> None:
        db.delete(obj)

    def is_active(self, obj: SectionSubject) -> bool:
        return True


_AVAILABILITY_MODELS: dict[str, tuple[type, str]] = {
    "faculty": (FacultyUnavailability, "faculty_id"),
    "room": (RoomUnavailability, "room_id"),
    "section": (SectionUnavailability, "section_id"),
}


class AvailabilityAdapter:
    """Faculty/room/section blocked slots (spec PART 9).

    Adapted to fit this schema's own availability model rather than the
    spec's literal suggested columns (the spec explicitly allows this: "or
    another normalized format that fits the current schema"): availability
    here is stored only as *exceptions* - a row means "blocked", and absence
    means "available" (see models.py's own docstring). So every row in this
    file is a block that should exist; there is no separate ``status``
    column, because an "available" status would mean nothing to create. Full
    Synchronization removes a block absent from the file (unblocking it) -
    the natural, schema-consistent meaning of "sync this resource's blocked
    slots to match the file."
    """

    key = "availability"
    label = "Availability"
    aliases = {
        "entity_type": ["resource_type", "owner_type", "type"],
        "entity_identifier": [
            "resource_id", "resource", "owner", "identifier", "code",
        ],
        "slot": ["period_code", "period", "period_index", "slot_code"],
        "reason": ["note", "comment"],
    }
    columns = ["entity_type", "entity_identifier", "day", "slot", "reason",
               "academic_year", "semester", "program", "department"]
    required = ["entity_type", "entity_identifier", "day", "slot"]
    sample = [
        {"entity_type": "faculty", "entity_identifier": "T-1", "day": "Monday", "slot": "1",
         "reason": "Department meeting", "academic_year": "", "semester": "", "program": "", "department": ""},
        {"entity_type": "room", "entity_identifier": "A101", "day": "Friday", "slot": "5",
         "reason": "Maintenance", "academic_year": "", "semester": "", "program": "", "department": ""},
    ]
    description = (
        "entity_type is faculty, room or section. entity_identifier is the faculty_id "
        "code, room_number, or section_number (a section also needs academic_year/"
        "semester/program/department, since section_number alone is not unique across "
        "contexts). slot is the 1-based period number (P1, P2, ...) shown on the Time "
        "Slots page. Every row is a period that should be blocked; a day/slot that "
        "doesn't match any configured time slot is rejected, not silently skipped."
    )

    def identity(self, row: dict[str, str]) -> tuple:
        """A provisional key, used only to report a duplicate row.

        Matching against what is already stored uses `identity_from_payload`
        instead, because a sheet may name a room by its code or its number and
        a section with or without its context - all of which are the same
        record, and none of which can be told apart from the text alone.
        """
        entity_type = row.get("entity_type", "").strip().lower()
        entity_id = _norm(row.get("entity_identifier", ""))
        slot = (row.get("slot", "") or "").strip().lower().lstrip("p").strip()
        return (entity_type, entity_id, row.get("day", "").strip().lower(), slot)

    def identity_from_payload(self, payload: dict[str, Any]) -> tuple:
        """The canonical key: which entity, and which slot.

        Derived from the rows the references actually resolved to, so the same
        block written two different ways is recognised as one record - which is
        what makes re-importing a workbook a no-op rather than a pile of
        duplicate-key failures.
        """
        return (
            payload.get("owner_field"),
            payload.get("owner_id"),
            payload.get("timeslot_id"),
        )

    def identity_label(self, row: dict[str, str]) -> str:
        return (
            f"{row.get('entity_type', '?')}:{row.get('entity_identifier', '?')} "
            f"{row.get('day', '?')} P{row.get('slot', '?')}"
        )

    def _resolve_entity(self, db: Session, refs: ReferenceResolver, row: dict[str, str]) -> tuple[Any, str, str, list[str]]:
        """Returns (owner_object, entity_type, owner_field_name, errors)."""
        entity_type = row.get("entity_type", "").strip().lower()
        identifier = row.get("entity_identifier", "").strip()
        errors: list[str] = []

        if entity_type not in _AVAILABILITY_MODELS:
            errors.append(
                f"entity_type must be one of {', '.join(_AVAILABILITY_MODELS)}, got {entity_type!r}"
            )
            return None, entity_type, "", errors
        if not identifier:
            errors.append("entity_identifier is required")
            return None, entity_type, "", errors

        if entity_type == "faculty":
            owner = refs.faculty_by_code(identifier)
            if owner is None:
                errors.append(f"faculty code {identifier!r} does not exist")
            return owner, entity_type, "faculty_id", errors

        if entity_type == "room":
            owner = refs.room_by_number(identifier)
            if owner is None:
                errors.append(f"room {identifier!r} does not exist")
            return owner, entity_type, "room_id", errors

        # section - needs its academic context too, same as SectionAdapter.
        year = row.get("academic_year", "").strip()
        semester = row.get("semester", "").strip()
        program = row.get("program", "").strip()
        department = row.get("department", "").strip()
        if not (year and semester and program and department):
            # The context columns are optional here for the same reason they are
            # on the curriculum sheet: a workbook describing one intake names a
            # section once and does not repeat four columns on every row. The
            # resolver reports ambiguity rather than guessing.
            owner, problem = refs.section_anywhere(identifier)
            if problem:
                errors.append(problem)
            return owner, entity_type, "section_id", errors
        ctx = refs.context(year, semester, program, department)
        if ctx is None:
            errors.append(f"academic context {year} / Sem {semester} / {program} ({department}) does not exist")
            return None, entity_type, "section_id", errors
        owner = refs.section(ctx.id, identifier)
        if owner is None:
            errors.append(f"section {identifier!r} does not exist in that context")
        return owner, entity_type, "section_id", errors

    def validate(
        self, row: dict[str, str], db: Session, refs: ReferenceResolver
    ) -> tuple[dict[str, Any], list[str]]:
        owner, entity_type, owner_field, errors = self._resolve_entity(db, refs, row)

        day = row.get("day", "").strip()
        raw_slot = row.get("slot", "").strip()
        slot_num: int | None = None
        if not day:
            errors.append("day is required")
        if not raw_slot:
            errors.append("slot is required")
        else:
            # Availability sheets name a period the way the timetable does -
            # "P2" - while a plain "2" is equally clear. Both mean the second
            # period, and neither is worth rejecting a file over.
            text = raw_slot.lower().lstrip("p").strip()
            try:
                slot_num = int(float(text))
            except ValueError:
                errors.append(
                    f"slot {raw_slot!r} is not a period - use 'P2' or '2'"
                )
            else:
                if slot_num <= 0:
                    errors.append(f"slot must be 1 or greater, got {slot_num}")
                    slot_num = None

        timeslot: TimeSlot | None = None
        if day and slot_num is not None:
            timeslot = refs.timeslot(day, slot_num - 1)
            if timeslot is None:
                configured_days = refs.configured_days()
                errors.append(
                    f"no configured time slot for day {day!r} period {slot_num} - "
                    f"configured days are: {', '.join(configured_days) or '(none yet)'}"
                )

        payload = {
            "entity_type": entity_type,
            "owner_field": owner_field,
            "owner_id": owner.id if owner else None,
            "timeslot_id": timeslot.id if timeslot else None,
            "reason": row.get("reason", "").strip() or None,
        }
        return payload, errors

    def load_existing(self, db: Session) -> dict[tuple, Any]:
        out: dict[tuple, Any] = {}
        for row in db.query(FacultyUnavailability).all():
            out[self.record_identity(row)] = row
        for row in db.query(RoomUnavailability).all():
            out[self.record_identity(row)] = row
        for row in db.query(SectionUnavailability).all():
            out[self.record_identity(row)] = row
        return out

    def record_identity(self, obj: Any) -> tuple:
        # Mirrors `identity_from_payload` exactly. Keyed on ids rather than on
        # names, so it cannot disagree with a sheet about spelling.
        if isinstance(obj, FacultyUnavailability):
            return ("faculty_id", obj.faculty_id, obj.timeslot_id)
        if isinstance(obj, RoomUnavailability):
            return ("room_id", obj.room_id, obj.timeslot_id)
        return ("section_id", obj.section_id, obj.timeslot_id)

    def record_label(self, obj: Any) -> str:
        slot = obj.timeslot
        when = f"{slot.day} P{slot.period_index + 1}"
        if isinstance(obj, FacultyUnavailability):
            return f"{obj.faculty.faculty_code} {when}"
        if isinstance(obj, RoomUnavailability):
            return f"{obj.room.room_number} {when}"
        return f"{obj.section.section_number} {when}"

    def diff(self, obj: Any, payload: dict[str, Any]) -> list[FieldChange]:
        old_reason = obj.reason or ""
        new_reason = payload.get("reason") or ""
        if old_reason != new_reason:
            return [FieldChange(field="reason", old=_fmt(obj.reason), new=_fmt(payload.get("reason")))]
        return []

    def create(self, db: Session, payload: dict[str, Any]) -> Any:
        model, _ = _AVAILABILITY_MODELS[payload["entity_type"]]
        row = model(**{payload["owner_field"]: payload["owner_id"]},
                     timeslot_id=payload["timeslot_id"], reason=payload["reason"])
        db.add(row)
        return row

    def update(self, obj: Any, payload: dict[str, Any]) -> None:
        obj.reason = payload["reason"]

    def deactivate(self, db: Session, obj: Any) -> None:
        # No is_active concept on an availability exception row either -
        # full-sync removal means the slot becomes available again, which is
        # exactly what deleting the block row already means in this schema.
        db.delete(obj)

    def is_active(self, obj: Any) -> bool:
        return True


class AcademicContextAdapter:
    """Academic contexts - the scope everything else hangs from.

    There was no import path for these at all, which is why a workbook naming
    its own context was rejected for referencing one that "does not exist": the
    only way to create it was by hand, before importing anything else.

    Creating a context is consequential enough that the preview lists each one
    as its own line item rather than letting it appear as a side effect.
    """

    key = "academic_contexts"
    label = "Academic contexts"
    columns = ["academic_year", "semester", "program", "department"]
    required = ["academic_year", "semester", "program", "department"]
    aliases = {
        "academic_year": ["year", "session"],
        "semester": ["sem", "term"],
        "program": ["programme", "course"],
        "department": ["dept", "school"],
    }
    sample = [
        {"academic_year": "2026-27", "semester": "5",
         "program": "BCA", "department": "Computer Applications"},
    ]
    description = (
        "One row per academic year / semester / programme / department being "
        "scheduled. Sections belong to a context; rooms, faculty and subjects "
        "are shared across all of them."
    )
    model = AcademicContext

    def identity(self, row):
        return (
            _norm(row.get("academic_year", "")),
            _norm(row.get("semester", "")),
            _norm(row.get("program", "")),
            _norm(row.get("department", "")),
        )

    def identity_label(self, row):
        # Called with a raw sheet row and with a validated payload, where
        # semester is already an int - so read every field as text.
        def text(field: str) -> str:
            return str(row.get(field, "") or "").strip()

        return (
            f"{text('academic_year')} / Sem {text('semester')} / "
            f"{text('program')} ({text('department')})"
        )

    def validate(self, row, db, refs):
        errors = []
        year = row.get("academic_year", "").strip()
        program = row.get("program", "").strip()
        department = row.get("department", "").strip()
        raw_sem = row.get("semester", "").strip()

        if not year:
            errors.append("academic_year is required")
        if not program:
            errors.append("program is required")
        if not department:
            errors.append("department is required")

        semester = None
        if not raw_sem:
            errors.append("semester is required")
        else:
            try:
                semester = int(float(raw_sem))
            except ValueError:
                errors.append(f"semester must be a whole number, got {raw_sem!r}")
            else:
                if semester <= 0:
                    errors.append(f"semester must be greater than zero, got {semester}")

        return {
            "academic_year": year,
            "semester": semester or 0,
            "program": program,
            "department": department,
        }, errors

    def load_existing(self, db):
        return {self.record_identity(c): c for c in db.query(AcademicContext).all()}

    def record_identity(self, obj):
        return (
            _norm(obj.academic_year),
            _norm(str(obj.semester)),
            _norm(obj.program),
            _norm(obj.department),
        )

    def record_label(self, obj):
        return obj.label

    def diff(self, obj, payload):
        # Every field is part of the identity, so a matched row is by
        # definition unchanged. Nothing here can be updated in place.
        return []

    def create(self, db, payload):
        ctx = AcademicContext(**payload)
        db.add(ctx)
        return ctx

    def update(self, obj, payload):
        return None

    def deactivate(self, db, obj):
        # Deliberately inert: a context owns sections and generated timetables,
        # and a full sync must never quietly discard a semester's work.
        return None

    def is_active(self, obj):
        return True


class TimeslotAdapter:
    """The weekly grid, as rows rather than as generator settings.

    The grid could previously only be produced by the seed endpoint, which
    builds evenly spaced periods from a start time and a length. That fits a
    regular week, cannot express one with an uneven break or a short Friday,
    and left a workbook's own time-slot sheet with nowhere to go.

    Matched on (day, period index), so re-importing the same grid updates times
    in place rather than duplicating or destroying it. That distinction
    matters: deleting a slot cascades to every availability block and every
    generated class referencing it.
    """

    key = "timeslots"
    label = "Time slots"
    columns = ["day", "period_code", "start_time", "end_time", "is_lunch"]
    required = ["day", "period_code", "start_time", "end_time"]
    aliases = {
        "period_code": ["period", "slot", "period_index", "slot_code"],
        "start_time": ["start", "from"],
        "end_time": ["end", "to"],
        "is_lunch": ["lunch", "break"],
    }
    sample = [
        {"day": "Monday", "period_code": "P1", "start_time": "09:30",
         "end_time": "10:20", "is_lunch": "No"},
        {"day": "Monday", "period_code": "P5", "start_time": "12:50",
         "end_time": "13:40", "is_lunch": "Yes"},
    ]
    description = (
        "One row per day and period. period_code may be 'P1' or just '1'. "
        "Lunch periods are never scheduled into. The working days come from "
        "this sheet, so a six-day week needs only Saturday rows."
    )
    model = TimeSlot

    DAY_ORDER = [
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday",
    ]

    def _period_index(self, raw):
        """'P3' and '3' both mean the third period, stored zero-based."""
        text = raw.strip().lower().lstrip("p").strip()
        if not text:
            return None
        try:
            number = int(float(text))
        except ValueError:
            return None
        return number - 1 if number > 0 else None

    def identity(self, row):
        return (
            _norm(row.get("day", "")),
            self._period_index(row.get("period_code", "")),
        )

    def identity_label(self, row):
        return (
            f"{row.get('day', '').strip()} {row.get('period_code', '').strip()}"
        ).strip()

    def _parse_time(self, raw, field, errors):
        text = raw.strip()
        if not text:
            errors.append(f"{field} is required")
            return None
        # A spreadsheet hands back "09:30", "9:30", "09:30:00", or a datetime
        # the parser has already rendered as one of those.
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
            try:
                return dt.datetime.strptime(text, fmt).time()
            except ValueError:
                continue
        errors.append(
            f"{field} {text!r} is not a time - use 24-hour HH:MM, e.g. 09:30"
        )
        return None

    def validate(self, row, db, refs):
        errors = []

        day = row.get("day", "").strip()
        if not day:
            errors.append("day is required")
        day_index = (
            self.DAY_ORDER.index(day.lower()) if day.lower() in self.DAY_ORDER else None
        )
        if day and day_index is None:
            errors.append(
                f"day {day!r} is not a day of the week - expected one of "
                f"{', '.join(d.capitalize() for d in self.DAY_ORDER)}"
            )

        raw_period = row.get("period_code", "").strip()
        period_index = self._period_index(raw_period)
        if not raw_period:
            errors.append("period_code is required")
        elif period_index is None:
            errors.append(
                f"period_code {raw_period!r} is not a period number - use 'P1' or '1'"
            )

        start = self._parse_time(row.get("start_time", ""), "start_time", errors)
        end = self._parse_time(row.get("end_time", ""), "end_time", errors)
        if start and end and end <= start:
            errors.append(
                f"end_time {end:%H:%M} is not after start_time {start:%H:%M}"
            )

        return {
            "day": day,
            "day_index": day_index if day_index is not None else 0,
            "period_index": period_index if period_index is not None else 0,
            "start_time": start,
            "end_time": end,
            "is_lunch": _parse_bool(row.get("is_lunch", ""), default=False),
        }, errors

    def load_existing(self, db):
        return {self.record_identity(s): s for s in db.query(TimeSlot).all()}

    def record_identity(self, obj):
        return (_norm(obj.day), obj.period_index)

    def record_label(self, obj):
        return f"{obj.day} P{obj.period_index + 1}"

    def diff(self, obj, payload):
        out = []
        for name in ("day_index", "start_time", "end_time", "is_lunch"):
            old, new = getattr(obj, name), payload[name]
            if old != new:
                out.append(FieldChange(field=name, old=_fmt(old), new=_fmt(new)))
        return out

    def create(self, db, payload):
        slot = TimeSlot(**payload)
        db.add(slot)
        return slot

    def update(self, obj, payload):
        for name, value in payload.items():
            setattr(obj, name, value)

    def deactivate(self, db, obj):
        # Never delete a slot as a side effect of an import: availability
        # blocks and generated classes reference it and would go with it.
        return None

    def is_active(self, obj):
        return True


class SectionGroupAdapter:
    """Lab batches within a section - G1, G2 and so on.

    Recorded, not yet scheduled: the solver still treats a section as one unit,
    so a practical needs a room seating all of it. Importing them anyway means
    the sizes are ready when that changes, and lets the interface say plainly
    what is and is not being acted on.
    """

    key = "section_groups"
    label = "Section groups"
    columns = ["section_number", "group_code", "strength"]
    required = ["section_number", "group_code", "strength"]
    aliases = {
        "section_number": ["section", "section_code"],
        "group_code": ["group", "batch_code", "sub_section"],
        "strength": ["group_strength", "students", "size"],
    }
    sample = [
        {"section_number": "D2402", "group_code": "G1", "strength": "34"},
        {"section_number": "D2402", "group_code": "G2", "strength": "34"},
    ]
    description = (
        "Optional. Lab batches within a section. Recorded for now - the "
        "scheduler still places a practical for the whole section, so a lab "
        "must seat all of it."
    )
    model = SectionGroup

    def identity(self, row):
        return (
            _norm(row.get("section_number", "")),
            _norm(row.get("group_code", "")),
        )

    def identity_label(self, row):
        return (
            f"{row.get('section_number', '').strip()} "
            f"{row.get('group_code', '').strip()}"
        ).strip()

    def validate(self, row, db, refs):
        errors = []
        section_number = row.get("section_number", "").strip()
        group_code = row.get("group_code", "").strip()
        if not section_number:
            errors.append("section_number is required")
        if not group_code:
            errors.append("group_code is required")

        raw_strength = row.get("strength", "").strip()
        strength = None
        if not raw_strength:
            errors.append("strength is required")
        else:
            try:
                strength = int(float(raw_strength))
            except ValueError:
                errors.append(f"strength must be a whole number, got {raw_strength!r}")
            else:
                if strength <= 0:
                    errors.append("strength must be greater than zero")

        # This sheet names a section but not its academic context, which is the
        # normal shape - a workbook usually describes one intake. The resolver
        # decides, and says so plainly when a name is ambiguous.
        section = None
        if section_number:
            section, problem = refs.section_anywhere(section_number)
            if problem:
                errors.append(problem)

        return {
            "section_id": getattr(section, "id", None),
            "group_code": group_code,
            "strength": strength or 0,
        }, errors

    def load_existing(self, db):
        # The identity of a group is its section's number, so load the section
        # with it - otherwise matching existing groups costs one query each,
        # which is only visible once a real institution's worth of them exists.
        from sqlalchemy.orm import joinedload

        rows = db.query(SectionGroup).options(joinedload(SectionGroup.section)).all()
        return {self.record_identity(g): g for g in rows}

    def record_identity(self, obj):
        return (_norm(obj.section.section_number), _norm(obj.group_code))

    def record_label(self, obj):
        return f"{obj.section.section_number} {obj.group_code}"

    def diff(self, obj, payload):
        out = []
        if obj.strength != payload["strength"]:
            out.append(FieldChange(
                field="strength",
                old=_fmt(obj.strength),
                new=_fmt(payload["strength"]),
            ))
        return out

    def create(self, db, payload):
        group = SectionGroup(**payload)
        db.add(group)
        return group

    def update(self, obj, payload):
        obj.strength = payload["strength"]

    def deactivate(self, db, obj):
        db.delete(obj)

    def is_active(self, obj):
        return True


class SectionParentAdapter:
    """Which sections are lab groups of which other section.

    Its own entity rather than a column on the Sections sheet, because of how
    the writer works: rows are flushed once per sheet, so a section naming its
    parent from within that same sheet would resolve to a staged placeholder
    carrying a negative id, and write that into a real foreign key. Ordered
    after `sections` in DEPENDENCY_ORDER, both rows exist and both are real.

    Being a group changes exactly one thing about a section: its students are
    also the parent's, so the two are never taught at the same time, while two
    groups of one section may be. See models.Section.parent_section_id.
    """

    key = "section_parents"
    label = "Section groups (parent links)"
    columns = [
        "section_number", "parent_section_number",
        "academic_year", "semester", "program", "department",
    ]
    required = ["section_number", "parent_section_number"]
    aliases = {
        "section_number": ["section", "section_code", "group_section"],
        "parent_section_number": ["parent_section", "parent", "belongs_to"],
    }
    sample = [
        {"section_number": "24011", "parent_section_number": "2401"},
        {"section_number": "24012", "parent_section_number": "2401"},
    ]
    description = (
        "Optional. Says that a section is a lab group of another one - 24011 "
        "and 24012 are the two halves of 2401. A group is scheduled as itself, "
        "with its own size and its own lab, and never at the same time as the "
        "section it belongs to."
    )
    model = Section

    def identity(self, row):
        # Only for spotting a repeated row within one sheet; which section a
        # row links is `identity_from_payload`.
        return (
            _norm(row.get("academic_year", "")),
            _norm(row.get("semester", "")),
            _norm(row.get("program", "")),
            _norm(row.get("department", "")),
            _norm(row.get("section_number", "")),
        )

    def identity_from_payload(self, payload):
        """The section being linked, as a record.

        Keyed on the typed number - as it was - a second intake's 24011
        matched the first intake's 24011, and the "update" rewrote the *first*
        intake's section to point at the second intake's 2401: one upload
        quietly corrupting another's timetable. Keyed on the section's id,
        each intake's group is its own record.
        """
        return ("section", payload.get("id"))

    def identity_label(self, row):
        return row.get("section_number", "").strip() or "(no section_number)"

    def _find(self, refs, number, context, errors):
        """A section by number - within the named intake when the row names
        one, otherwise anywhere it is unambiguous."""
        year, semester, program, department = context
        if any(context):
            if not all(context):
                errors.append(
                    "academic_year, semester, program and department must all be "
                    "given together, or all left out"
                )
                return None
            ctx = refs.context(year, semester, program, department)
            if ctx is None:
                errors.append(
                    f"academic context {year} / Sem {semester} / {program} "
                    f"({department}) was not found"
                )
                return None
            found = refs.section(ctx.id, number)
            if found is None:
                errors.append(f"section {number!r} was not found in that context")
            return found
        found, problem = refs.section_anywhere(number)
        if problem:
            errors.append(problem)
        return found

    def validate(self, row, db, refs):
        errors: list[str] = []
        number = row.get("section_number", "").strip()
        parent_number = row.get("parent_section_number", "").strip()
        context = tuple(
            row.get(c, "").strip()
            for c in ("academic_year", "semester", "program", "department")
        )
        if not number:
            errors.append("section_number is required")
        if not parent_number:
            errors.append("parent_section_number is required")

        self_link = bool(number) and _norm(number) == _norm(parent_number)
        if self_link:
            errors.append(f"section {number} cannot be a lab group of itself")

        section = parent = None
        if number:
            section = self._find(refs, number, context, errors)
        if parent_number and not self_link:
            parent = self._find(refs, parent_number, context, errors)

        # Both sides have to be in the same intake. A link across contexts is
        # never enforced during a solve - only one of the two sections is ever
        # loaded - so it would read as a rule and act as nothing.
        if section is not None and parent is not None:
            mine = getattr(section, "academic_context_id", None)
            theirs = getattr(parent, "academic_context_id", None)
            if mine is not None and theirs is not None and mine != theirs:
                errors.append(
                    f"section {number} and {parent_number} are in different "
                    "academic contexts, so they would never be scheduled "
                    "against each other"
                )

        return {
            "id": getattr(section, "id", None),
            "parent_section_id": getattr(parent, "id", None),
        }, errors

    def load_existing(self, db):
        return {
            self.record_identity(s): s
            for s in db.query(Section).all()
            if s.parent_section_id is not None
        }

    def record_identity(self, obj):
        # Mirrors `identity_from_payload`.
        return ("section", obj.id)

    def record_label(self, obj):
        return obj.section_number

    def diff(self, obj, payload):
        if obj.parent_section_id == payload["parent_section_id"]:
            return []
        return [FieldChange(
            field="parent_section_number",
            old=_fmt(obj.parent_section_number or ""),
            new=_fmt(payload["parent_section_id"]),
        )]

    def create(self, db, payload):
        # Never creates a section: this only links one that already exists.
        section = db.get(Section, payload["id"]) if payload.get("id") else None
        if section is not None:
            section.parent_section_id = payload["parent_section_id"]
        return section

    def update(self, obj, payload):
        obj.parent_section_id = payload["parent_section_id"]

    def deactivate(self, db, obj):
        # A full sync that no longer mentions a link removes the link, not the
        # section - deleting the section would take its timetable with it.
        obj.parent_section_id = None

    def is_active(self, obj):
        return obj.parent_section_id is not None


ADAPTERS: dict[str, EntityAdapter] = {
    a.key: a for a in [
        AcademicContextAdapter(), TimeslotAdapter(),
        RoomAdapter(), FacultyAdapter(), SubjectAdapter(), SectionAdapter(),
        FacultySubjectAdapter(), SectionSubjectAdapter(), SectionGroupAdapter(),
        SectionParentAdapter(), AvailabilityAdapter(),
    ]
}


