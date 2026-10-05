"""Upload a teaching load and a room list; get a timetable's worth of data.

Two endpoints, and the pair is the safety property: `preview` reads both files
and reports exactly what importing them would do, writing nothing; `apply`
re-reads them and writes, all or nothing. Apply does not trust the preview - it
re-runs the whole analysis - because the preview was produced from a file the
caller uploaded against a database that may have moved since.

Everything after parsing is the existing workbook importer. This router turns
two spreadsheets into the sheets that importer already understands, hands them
over, and translates the answer back into the teacher's own row numbers.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import infrastructure as infra_store
from .. import teacher_import as ti
from .. import validation
from ..auth import require_admin
from ..bulk_import import ParseError
from ..bulk_import.workbook import DETECTED, SheetMatch, ordered_entities
from ..bulk_import.workbook_engine import analyse_sheets, apply_sheets
from ..config import settings
from ..database import get_db
from ..grid import build_grid
from ..room_scope import set_usable_rooms
from ..domain import PRACTICAL, room_kind_for, session_specs
from ..models import (
    UPLOAD_PROGRAM,
    AcademicContext,
    Faculty,
    Room,
    Section,
    SectionSubjectAssignment,
    Subject,
    TimeSlot,
    User,
)
from ..room_scope import usable_rooms
from ..solver.data import _eligible_rooms
from ..schemas import TeacherImportPreviewOut
from .bulk_import import _read, _workbook_out

router = APIRouter(prefix="/api/teacher", tags=["teacher-import"])


def _sheet_matches(exploded: ti.ExplodedFiles) -> list[SheetMatch]:
    """Say what each generated sheet is, rather than letting it be guessed.

    `classify` exists for files someone else wrote. These sheets were built for
    the adapters that read them, so scoring them for similarity would be asking
    a question whose answer is already known - and occasionally getting it
    wrong, since a two-column mapping sheet resembles several others.
    """
    from ..teacher_import.explode import ENTITY_FOR_SHEET

    matches = []
    for name, (headers, rows) in exploded.sheets.items():
        entity = ENTITY_FOR_SHEET.get(name)
        if entity is None:
            continue
        matches.append(SheetMatch(
            sheet=name,
            status=DETECTED,
            entity=entity,
            label=name,
            row_count=len(rows),
            confidence=1.0,
            headers=list(headers),
        ))
    return matches


def _relocate(preview, exploded: ti.ExplodedFiles):
    """Point every problem back at the file the teacher has open.

    The importer reports "Sections row 4", which is a row of a sheet that only
    ever existed in memory. Left alone, the one thing a teacher needs from an
    error message - where to look - would be the one thing it does not say.
    """
    for outcome in preview.outcomes:
        for problem in outcome.problems:
            # The engine numbers generated rows from 2, the header being row 1.
            origin = exploded.provenance.get((problem.sheet, problem.row_number - 2))
            if origin is None:
                continue
            problem.sheet, problem.row_number = origin
    return preview


def _known_rooms(db: Session) -> dict[tuple[str, str], dict[str, object]]:
    """What the database already knows about rooms, keyed by block and number.

    The room list has no floor column, and room identity includes one, so
    without this a room already on record would be treated as new and collide
    with the unique constraint on (block, room_number) - an IntegrityError at
    the end of an all-or-nothing import, naming nothing.
    """
    return {
        (str(r.block or "").strip().lower(), str(r.room_number or "").strip().lower()): {
            "floor": r.floor,
            "room_code": r.room_code,
            "capacity": r.capacity,
            "room_type": r.room_type,
            "lab_type": r.lab_type,
            "byod": r.byod,
            "charging": r.charging,
            "charging_sockets": r.charging_sockets,
            "is_active": r.is_active,
        }
        for r in db.query(Room).all()
    }


def _intake(db: Session, given: tuple, load_name: str) -> tuple[str, int, str, str]:
    """Which dataset this upload becomes.

    Neither file names a semester, and the teacher should not have to. So an
    upload that does not say is its own dataset, named by when it was made and
    from which file: "Upload 2 · 2026-09-11 · Load". Every upload is new -
    nothing from an earlier one (its sections, its rooms, its timetable) can
    leak into it - and the earlier ones stay, so their timetables can still be
    opened.

    A caller that does name all four (the API allows it) gets exactly that
    intake, and uploading to it again updates it in place, as before.
    """
    academic_year, semester, program, department = given
    if all(v not in (None, "") for v in given):
        return str(academic_year).strip(), int(semester), str(program).strip(), str(department).strip()

    import datetime as dt

    day = dt.datetime.now(dt.UTC).date().isoformat()
    source = (load_name.rsplit(".", 1)[0] or "Load")[:60]
    taken = (
        db.query(AcademicContext)
        .filter(AcademicContext.academic_year == day,
                AcademicContext.program == UPLOAD_PROGRAM)
        .count()
    )
    return day, taken + 1, UPLOAD_PROGRAM, source


async def _explode(
    db: Session,
    load: UploadFile,
    infra: UploadFile | None,
    academic_year: str,
    semester: int,
    program: str,
    department: str,
) -> ti.ExplodedFiles:
    load_bytes = await _read(load)
    infra_bytes = await _read(infra) if infra is not None else None
    try:
        parsed_load = ti.read_sheet(load_bytes, load.filename or "Load.xlsx")
        parsed_infra = (
            ti.read_sheet(infra_bytes, infra.filename or "Infra.xlsx")
            if infra is not None else None
        )
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    exploded = ti.explode(
        parsed_load,
        parsed_infra,
        academic_year=academic_year,
        semester=semester,
        program=program,
        department=department,
        known_rooms=_known_rooms(db),
    )
    if infra is None:
        # Scheduled against the saved rooms, which say what they add up to.
        saved = infra_store.active(db)
        if saved is not None:
            exploded.summary.update(infra_store.summary(db, saved))
    return exploded


def _require_rooms(db: Session, infra: UploadFile | None) -> None:
    """A teaching load needs rooms: this upload's, or the saved ones."""
    if infra is None and infra_store.active(db) is None:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "No infrastructure is saved yet. Upload Infra.xlsx first - "
                    "it is kept, so it only has to be uploaded once."
                ),
                "problems": [],
            },
        )


def _replaced_timetables(db: Session, exploded: ti.ExplodedFiles) -> int:
    """How many timetables saving this upload's room list would remove.

    Zero when the upload carries no room list, when nothing is saved yet, or
    when the room list is the one already saved, room for room.
    """
    if "Rooms" not in exploded.sheets:
        return 0
    saved = infra_store.active(db)
    if saved is None:
        return 0
    _, rows = exploded.sheets["Rooms"]
    listed = {
        (str(r.get("block", "")).strip().lower(), str(r.get("room_number", "")).strip().lower())
        for r in rows
    }
    current = {
        (str(r.block or "").strip().lower(), str(r.room_number or "").strip().lower())
        for r in db.query(Room).filter(Room.id.in_(infra_store.room_ids(db, saved.id)))
    }
    if listed == current and not exploded.room_changes:
        return 0
    return infra_store.timetable_count(db)


def _to_out(
    preview, exploded: ti.ExplodedFiles, *, readiness=None, context_id=None,
    grid_notes: list[str] | None = None, dataset_label: str | None = None,
) -> TeacherImportPreviewOut:
    body = _workbook_out(preview).model_dump()
    body["warnings"] = list(grid_notes or []) + list(exploded.warnings)
    body["notes"] = list(exploded.notes)
    body["inferences"] = list(exploded.inferences)
    body["summary"] = dict(exploded.summary)
    body["room_changes"] = list(exploded.room_changes)
    body["readiness"] = readiness
    body["academic_context_id"] = context_id
    body["dataset_label"] = dataset_label
    return TeacherImportPreviewOut(**body)


def _grid_notes(db: Session) -> list[str]:
    """What the import will do to the teaching week, said before it does it.

    Neither file describes the week. If none is set up yet, the default one is
    created; if the week has a common lunch, it is removed - the department's
    rule is that there is no common lunch, and each teacher gets a free period
    of their own on any day they teach instead.
    """
    slots = db.query(TimeSlot).all()
    if not slots:
        return [
            "No teaching week is set up yet, so the default is used: Monday to "
            "Friday, nine 50-minute periods from 09:30, with no common lunch. "
            "It can be changed on the Time slots page."
        ]
    lunch = sorted({f"P{s.period_index + 1}" for s in slots if s.is_lunch})
    if lunch:
        return [
            f"The teaching week marks {', '.join(lunch)} as lunch for everyone. "
            "There is no common lunch - each teacher gets a free period of "
            f"their own on any day they teach - so importing makes "
            f"{', '.join(lunch)} an ordinary period."
        ]
    return []


def _prepare_grid(db: Session) -> None:
    """Make the teaching week what the department runs. See `_grid_notes`."""
    slots = db.query(TimeSlot).all()
    if not slots:
        db.add_all(build_grid())
    else:
        for slot in slots:
            if slot.is_lunch:
                slot.is_lunch = False
    db.flush()


def _listed_room_ids(db: Session, exploded: ti.ExplodedFiles) -> set[int]:
    """The rooms the room list names, as they now exist in the database."""
    _, rows = exploded.sheets.get("Rooms", ([], []))
    wanted = {
        (str(r.get("block", "")).strip().lower(), str(r.get("room_number", "")).strip().lower())
        for r in rows
    }
    return {
        room.id for room in db.query(Room).all()
        if (str(room.block or "").strip().lower(),
            str(room.room_number or "").strip().lower()) in wanted
    }


def _scope_notes(db: Session, exploded: ti.ExplodedFiles) -> list[str]:
    """Say that rooms outside the room list exist, and will not be used."""
    if "Rooms" not in exploded.sheets:
        return []
    _, rows = exploded.sheets.get("Rooms", ([], []))
    listed = {
        (str(r.get("block", "")).strip().lower(), str(r.get("room_number", "")).strip().lower())
        for r in rows
    }
    others = [
        room for room in db.query(Room).all()
        if room.is_active and room.room_type != "faculty"
        and (str(room.block or "").strip().lower(),
             str(room.room_number or "").strip().lower()) not in listed
    ]
    if not others:
        return []
    return [
        f"There are {len(others)} other rooms on record from earlier uploads. This "
        f"semester's timetable uses only the {len(rows)} rooms in the room list; "
        "the others are left exactly as they are."
    ]


def _finish(
    db: Session,
    exploded: ti.ExplodedFiles,
    context_fields: tuple,
    infra_filename: str | None = None,
    actor: str | None = None,
) -> int | None:
    """Everything that belongs to the import but is not a sheet: the teaching
    week, which rooms this dataset may use, and who teaches each class.

    The rooms are the saved infrastructure's. An upload that brings its own
    room list saves that list as the infrastructure first - the same thing
    uploading it on its own does - so a dataset is always built on exactly one
    saved room list, and it says which.
    """
    _prepare_grid(db)
    context_id = _context_id(db, *context_fields)
    if context_id is not None:
        if "Rooms" in exploded.sheets:
            saved = infra_store.save(
                db, infra_filename or "Infra.xlsx", _listed_room_ids(db, exploded), actor
            )
        else:
            saved = infra_store.active(db)
        listed = infra_store.room_ids(db, saved.id) if saved is not None else set()
        set_usable_rooms(db, context_id, listed)
        ctx = db.get(AcademicContext, context_id)
        if ctx is not None and saved is not None:
            ctx.infrastructure_id = saved.id
        _pin_teachers(db, exploded, context_id)
    return context_id


def _pin_teachers(db: Session, exploded: ti.ExplodedFiles, context_id: int) -> None:
    """Hold every class to the teacher its row names.

    The load sheet says "this teacher teaches this subject to this section".
    Recorded only as "may teach this subject", two teachers of one subject
    would be interchangeable across sections, and the solver would be free to
    swap them. A per-section assignment is what the solver already reads to
    hold a class to one teacher, so the import writes one.
    """
    sections = {
        s.section_number: s for s in
        db.query(Section).filter(Section.academic_context_id == context_id)
    }
    subjects = {s.code: s for s in db.query(Subject).all()}
    faculty = {f.faculty_code: f for f in db.query(Faculty).all()}
    existing = {
        (a.section_id, a.subject_id, a.session_type): a for a in
        db.query(SectionSubjectAssignment)
        .filter(SectionSubjectAssignment.academic_context_id == context_id)
    }
    for section_number, code, fid in exploded.teachers:
        section, subject, teacher = (
            sections.get(section_number), subjects.get(code), faculty.get(fid)
        )
        if section is None or subject is None or teacher is None:
            continue
        for spec in session_specs(subject):
            key = (section.id, subject.id, spec.session_type)
            row = existing.get(key)
            if row is None:
                db.add(SectionSubjectAssignment(
                    academic_context_id=context_id, section_id=section.id,
                    subject_id=subject.id, session_type=spec.session_type,
                    faculty_id=teacher.id,
                ))
            else:
                row.faculty_id = teacher.id
    db.flush()


def _unassignable(db: Session, context_id: int) -> list[dict]:
    """Classes no room in the dataset's room list can take - and why.

    The same eligibility the solver uses, asked one filter at a time so the
    reason names the one that ruled every room out: no room of the kind, none
    big enough, or none big enough with BYOD charging points.
    """
    rooms = [r for r in usable_rooms(db, context_id)
             if r.is_active and r.room_type != "faculty"]
    by_id = {r.id: r for r in rooms}
    out: list[dict] = []
    sections = (
        db.query(Section)
        .filter(Section.academic_context_id == context_id, Section.is_active.is_(True))
        .order_by(Section.section_number)
    )
    for section in sections:
        for subject in section.subjects:
            if not subject.is_active:
                continue
            for spec in session_specs(subject):
                if spec.count <= 0 or _eligible_rooms(subject, section, by_id, spec.session_type):
                    continue
                kind = room_kind_for(subject, spec.session_type)
                noun = "lab" if kind == "lab" else "classroom"
                byod = bool(subject.byod_required) and (
                    spec.session_type == PRACTICAL or settings.enforce_byod_on_lectures
                )
                of_kind = [r for r in rooms if r.room_type == kind]
                big = [r for r in of_kind if r.capacity >= section.strength]
                if not of_kind:
                    reason = f"There is no {noun} in the room list."
                elif not big:
                    largest = max(r.capacity for r in of_kind)
                    reason = (f"No {noun} with capacity >= {section.strength}; "
                              f"the largest seats {largest}.")
                elif byod and not [r for r in big if r.byod]:
                    reason = (f"No available BYOD-capable {noun} with capacity >= "
                              f"{section.strength}.")
                else:
                    reason = f"No {noun} matches this class's room requirements."
                out.append({
                    "subject_code": subject.code,
                    "subject_name": subject.name,
                    "section": section.section_number,
                    "strength": section.strength,
                    "byod": byod,
                    "type": "Lab" if spec.session_type == PRACTICAL else "Theory",
                    "reason": reason,
                })
    return out


def _context_id(db: Session, academic_year, semester, program, department) -> int | None:
    ctx = (
        db.query(AcademicContext)
        .filter(
            AcademicContext.academic_year == academic_year.strip(),
            AcademicContext.semester == semester,
            AcademicContext.program == program.strip(),
            AcademicContext.department == department.strip(),
        )
        .one_or_none()
    )
    return ctx.id if ctx else None


_SAID_BY_THE_IMPORT = frozenset({"group_strengths_add_up"})


def _readiness(db: Session, context_id: int | None) -> dict | None:
    """The pre-generation check, in the shape the upload page shows."""
    if context_id is None:
        return None
    report = validation.validate(db, academic_context_id=context_id)
    return {
        "unassignable": _unassignable(db, context_id),
        "ready": report.ready,
        "blockers": [
            {"label": c.label, "detail": c.detail}
            for c in report.checks if not c.ok and c.severity == "blocker"
        ],
        "warnings": [
            {"label": c.label, "detail": c.detail}
            for c in report.checks if not c.ok and c.severity == "warning"
            # The import names the missing groups itself, more precisely; the
            # general check would only repeat it in vaguer words.
            and c.id not in _SAID_BY_THE_IMPORT
        ],
    }


def _refuse_on_input_problems(exploded: ti.ExplodedFiles) -> None:
    """Stop before the importer if the files contradict themselves.

    These are disagreements inside the upload - one section with two sizes, one
    subject code describing two subjects - which no amount of database context
    can settle. Reporting them here keeps them attached to the row that caused
    them.
    """
    if exploded.ok:
        return
    raise HTTPException(
        status_code=422,
        detail={
            "message": "The files disagree with themselves and were not imported.",
            "problems": [
                {
                    "file": p.file,
                    "row_number": p.row_number,
                    "identity": p.identity,
                    "message": p.message,
                }
                for p in exploded.problems
            ],
        },
    )


@router.post("/preview", response_model=TeacherImportPreviewOut)
async def preview(
    load: UploadFile = File(..., description="The teaching load sheet"),
    infra: UploadFile | None = File(
        None, description="A room list to save as the infrastructure; omit to use the saved one"
    ),
    academic_year: str | None = Form(None),
    semester: int | None = Form(None),
    program: str | None = Form(None),
    department: str | None = Form(None),
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """What importing these two files would do. Writes nothing.

    Writes it and takes it back, to be exact. "The data is ready" is only true
    if the full pre-generation check passes on the data as it would be after
    importing - a file can import cleanly and still describe a lecture no room
    can hold. So the import runs inside a transaction, the same check that
    guards generation runs against it, and the transaction is rolled back.
    """
    _require_rooms(db, infra)
    academic_year, semester, program, department = _intake(
        db, (academic_year, semester, program, department), load.filename or "Load.xlsx"
    )
    exploded = await _explode(db, load, infra, academic_year, semester, program, department)
    _refuse_on_input_problems(exploded)
    matches = _sheet_matches(exploded)
    notes = _grid_notes(db) + _scope_notes(db, exploded)
    removed = _replaced_timetables(db, exploded)
    if removed:
        notes.append(
            f"This room list replaces the saved infrastructure, which removes the "
            f"{removed} timetable{'s' if removed != 1 else ''} built on the current rooms."
        )
    label = AcademicContext(academic_year=academic_year, semester=semester,
                            program=program, department=department).label
    readiness = None
    try:
        result = apply_sheets(
            db, exploded.sheets, matches, ordered_entities(matches),
            load.filename or "Load.xlsx", commit=False,
        )
        if not result.has_errors:
            context_id = _finish(
                db, exploded, (academic_year, semester, program, department),
                infra_filename=infra.filename if infra is not None else None,
            )
            readiness = _readiness(db, context_id)
    finally:
        db.rollback()
    return _to_out(_relocate(result, exploded), exploded,
                   readiness=readiness, grid_notes=notes, dataset_label=label)


@router.post("/apply", response_model=TeacherImportPreviewOut)
async def apply(
    load: UploadFile = File(...),
    infra: UploadFile | None = File(None),
    replace_infrastructure: bool = Form(False),
    academic_year: str | None = Form(None),
    semester: int | None = Form(None),
    program: str | None = Form(None),
    department: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Import the teaching load - against the saved rooms, or with a room list
    that is saved as the infrastructure - all or nothing, and set up the week."""
    _require_rooms(db, infra)
    academic_year, semester, program, department = _intake(
        db, (academic_year, semester, program, department), load.filename or "Load.xlsx"
    )
    exploded = await _explode(db, load, infra, academic_year, semester, program, department)
    _refuse_on_input_problems(exploded)
    removed = _replaced_timetables(db, exploded)
    if removed and not replace_infrastructure:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"This room list differs from the saved infrastructure. Saving it "
                    f"removes the {removed} timetable{'s' if removed != 1 else ''} built "
                    "on the current rooms. Confirm to replace it."
                ),
                "timetables": removed,
            },
        )
    matches = _sheet_matches(exploded)
    notes = _grid_notes(db) + _scope_notes(db, exploded)
    fields = (academic_year, semester, program, department)
    infra_name = infra.filename if infra is not None else None
    result = apply_sheets(
        db,
        exploded.sheets,
        matches,
        ordered_entities(matches),
        load.filename or "Load.xlsx",
        actor=user.username,
        # The week and the intake's room list are set in the same transaction:
        # files, grid and room list together, or none of them.
        before_commit=lambda session: _finish(
            session, exploded, fields, infra_filename=infra_name, actor=user.username),
    )
    readiness = None
    context_id = None
    if not result.has_errors:
        context_id = _context_id(db, academic_year, semester, program, department)
        readiness = _readiness(db, context_id)
    label = AcademicContext(academic_year=academic_year, semester=semester,
                            program=program, department=department).label
    return _to_out(_relocate(result, exploded), exploded, readiness=readiness,
                   context_id=context_id, grid_notes=notes if result.committed else [],
                   dataset_label=label)
