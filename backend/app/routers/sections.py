"""Section CRUD and the section -> subject curriculum map.

Sections are scoped to an AcademicContext (spec #5): a section for
2026-27/Sem-1 is a different row from one with the same number in a different
year or semester, never confused via a shared global namespace.

There is no home-room concept here. Theory room eligibility comes from the
solver's candidate pool (capacity + room_type + availability), not a per-section
pin (TIMETABLE_LOGIC_SPEC.md #8/#16).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import crud, entity_detail, listing, schemas
from ..auth import require_admin
from ..database import get_db
from ..manual_edit import contiguous_window
from ..models import AcademicContext, Assignment, Section, SectionUnavailability, Subject, TimeSlot, User

router = APIRouter(prefix="/api/sections", tags=["sections"])


SECTION_LIST = listing.Sortable(
    search=(Section.section_number,),
    sorts={
        "section_number": Section.section_number,
        "strength": Section.strength,
    },
    default_sort=Section.section_number,
)


@router.get("")
def list_sections(
    academic_context_id: int | None = Query(None),
    params: listing.ListParams = Depends(listing.list_params),
    db: Session = Depends(get_db),
):
    """Sections, optionally scoped to one academic context.

    The context filter narrows the base query, so a paged `total` describes
    that context rather than every section in the institution.
    """
    query = db.query(Section)
    if academic_context_id is not None:
        query = query.filter(Section.academic_context_id == academic_context_id)
    return listing.serialize(
        listing.apply(db, Section, params, SECTION_LIST, base_query=query),
        schemas.SectionOut,
        schemas.SectionRowBrief,
    )


@router.post("", response_model=schemas.SectionOut, status_code=status.HTTP_201_CREATED)
def create_section(
    payload: schemas.SectionCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    data = payload.model_dump()
    crud.get_or_404(db, AcademicContext, data["academic_context_id"])
    existing = (
        db.query(Section)
        .filter(
            Section.academic_context_id == data["academic_context_id"],
            Section.section_number == data["section_number"],
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Section {data['section_number']!r} already exists in this "
            "academic context",
        )
    section = Section(**data)
    db.add(section)
    db.commit()
    db.refresh(section)
    return section


@router.get("/{section_id}", response_model=schemas.SectionOut)
def get_section(section_id: int, db: Session = Depends(get_db)):
    return crud.get_or_404(db, Section, section_id)


@router.get("/{section_id}/detail", response_model=schemas.SectionDetailOut)
def get_section_detail(section_id: int, db: Session = Depends(get_db)):
    """Phase 9: curriculum, assigned faculty/rooms per subject, rooms
    actually used, and declared unavailability - one request."""
    section = crud.get_or_404(db, Section, section_id)
    return schemas.SectionDetailOut(
        id=section.id,
        section_number=section.section_number,
        strength=section.strength,
        is_active=section.is_active,
        academic_context=section.academic_context,
        subjects=section.subjects,
        assignments=entity_detail.assignment_briefs(db, section_id=section_id),
        rooms_used=entity_detail.section_rooms_used(db, section_id),
        blocked_slots=entity_detail.section_blocked_slots(db, section_id),
    )


@router.put("/{section_id}", response_model=schemas.SectionOut)
def update_section(
    section_id: int, payload: schemas.SectionUpdate, db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    data = payload.model_dump(exclude_unset=True)
    return crud.update(db, Section, section_id, data)


@router.delete("/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_section(
    section_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    crud.delete(db, Section, section_id)


# --------------------------------------------------------------- curriculum map


@router.post("/{section_id}/subjects", response_model=schemas.SectionOut)
def add_subject(
    section_id: int, payload: schemas.SubjectIdPayload, db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    section = crud.get_or_404(db, Section, section_id)
    subject = crud.get_or_404(db, Subject, payload.subject_id)
    if subject in section.subjects:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Section {section.section_number} already takes {subject.code}",
        )
    section.subjects.append(subject)
    db.commit()
    db.refresh(section)
    return section


@router.delete("/{section_id}/subjects/{subject_id}", response_model=schemas.SectionOut)
def remove_subject(
    section_id: int, subject_id: int, db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    section = crud.get_or_404(db, Section, section_id)
    subject = crud.get_or_404(db, Subject, subject_id)
    if subject not in section.subjects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Section {section.section_number} does not take {subject.code}",
        )
    section.subjects.remove(subject)
    db.commit()
    db.refresh(section)
    return section


# ------------------------------------------------------------------- availability


@router.get("/{section_id}/availability/check")
def check_section_availability(
    section_id: int,
    timeslot_id: int = Query(...),
    length: int = Query(1, ge=1, le=3),
    run_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """Spec #33: is this section free for a given day/slot window?"""
    section = crud.get_or_404(db, Section, section_id)
    window = contiguous_window(db, timeslot_id, length)
    if window is None:
        return {"available": False, "issues": [
            f"no {length} contiguous non-lunch period(s) starting at timeslot {timeslot_id}"
        ]}
    window_ids = {s.id for s in window}

    issues: list[str] = []
    blocked = {
        u.timeslot_id for u in
        db.query(SectionUnavailability).filter(SectionUnavailability.section_id == section_id)
    }
    if window_ids & blocked:
        issues.append(f"{section.section_number} is marked unavailable during that window")
    if run_id is not None:
        clashing = (
            db.query(Assignment)
            .filter(
                Assignment.run_id == run_id,
                Assignment.section_id == section_id,
                Assignment.timeslot_id.in_(window_ids),
            )
            .all()
        )
        for a in clashing:
            issues.append(f"{section.section_number} already has {a.subject.code} at that time")

    return {"available": not issues, "issues": issues}


@router.get("/{section_id}/availability/free-slots", response_model=list[schemas.TimeSlotOut])
def section_free_slots(
    section_id: int,
    length: int = Query(1, ge=1, le=3),
    run_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """Spec #33: which slots are open for this section - every legal
    ``length``-period window with no declared unavailability and (when
    ``run_id`` is given) no existing class."""
    crud.get_or_404(db, Section, section_id)
    blocked = {
        u.timeslot_id for u in
        db.query(SectionUnavailability).filter(SectionUnavailability.section_id == section_id)
    }
    if run_id is not None:
        for a in (
            db.query(Assignment)
            .filter(Assignment.run_id == run_id, Assignment.section_id == section_id)
        ):
            blocked.add(a.timeslot_id)

    free: list[TimeSlot] = []
    for slot in db.query(TimeSlot).order_by(TimeSlot.day_index, TimeSlot.period_index).all():
        window = contiguous_window(db, slot.id, length)
        if window is None:
            continue
        if any(s.id in blocked for s in window):
            continue
        free.append(slot)
    return free
