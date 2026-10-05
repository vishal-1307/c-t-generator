"""Faculty CRUD plus the faculty -> subject teaching map (hard constraint 8)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import crud, entity_detail, listing, schemas
from ..auth import require_admin
from ..database import get_db
from ..models import Faculty, Subject, User

router = APIRouter(prefix="/api/faculty", tags=["faculty"])


FACULTY_LIST = listing.Sortable(
    search=(Faculty.name, Faculty.faculty_code, Faculty.department),
    sorts={
        "name": Faculty.name,
        "faculty_code": Faculty.faculty_code,
        "department": Faculty.department,
    },
    default_sort=Faculty.name,
)


@router.get("")
def list_faculty(
    params: listing.ListParams = Depends(listing.list_params),
    db: Session = Depends(get_db),
):
    """Every faculty member, or one searchable page of them.

    Without pagination parameters this returns the full array it always has.
    Sending any of `limit`, `offset`, `q` or `sort` switches the response to
    `{total, rows}` - see app/listing.py for why the contract is opt-in.
    """
    return listing.serialize(
        listing.apply(db, Faculty, params, FACULTY_LIST),
        schemas.FacultyOut,
        schemas.FacultyBrief,
    )


@router.post("", response_model=schemas.FacultyOut, status_code=status.HTTP_201_CREATED)
def create_faculty(
    payload: schemas.FacultyCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    return crud.create(db, Faculty, payload.model_dump(), unique_field="faculty_code")


@router.get("/{faculty_id}", response_model=schemas.FacultyOut)
def get_faculty(faculty_id: int, db: Session = Depends(get_db)):
    return crud.get_or_404(db, Faculty, faculty_id)


@router.get("/{faculty_id}/detail", response_model=schemas.FacultyDetailOut)
def get_faculty_detail(faculty_id: int, db: Session = Depends(get_db)):
    """Phase 9: everything an admin needs to inspect one faculty member in
    one request - eligibility, every semester assignment across every
    academic context, workload, declared unavailability, and whether they are
    teaching right now."""
    faculty = crud.get_or_404(db, Faculty, faculty_id)
    assignments = entity_detail.assignment_briefs(db, faculty_id=faculty_id)
    return schemas.FacultyDetailOut(
        id=faculty.id,
        name=faculty.name,
        faculty_code=faculty.faculty_code,
        department=faculty.department,
        is_active=faculty.is_active,
        subjects=faculty.subjects,
        assignments=assignments,
        periods_per_week=sum(a["periods_per_week"] for a in assignments),
        sections_taught=len({a["section_id"] for a in assignments}),
        blocked_slots=entity_detail.faculty_blocked_slots(db, faculty_id),
        current_status=entity_detail.faculty_current_status(db, faculty),
    )


@router.put("/{faculty_id}", response_model=schemas.FacultyOut)
def update_faculty(
    faculty_id: int, payload: schemas.FacultyUpdate, db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    data = payload.model_dump(exclude_unset=True)
    return crud.update(db, Faculty, faculty_id, data, unique_field="faculty_code")


@router.delete("/{faculty_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_faculty(
    faculty_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    crud.delete(db, Faculty, faculty_id)


# --------------------------------------------------------------- teaching map


@router.post("/{faculty_id}/subjects", response_model=schemas.FacultyOut)
def map_subject(
    faculty_id: int, payload: schemas.SubjectIdPayload, db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Declare that this faculty member is able to teach this subject."""
    faculty = crud.get_or_404(db, Faculty, faculty_id)
    subject = crud.get_or_404(db, Subject, payload.subject_id)
    if subject in faculty.subjects:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{faculty.name} is already mapped to {subject.code}",
        )
    faculty.subjects.append(subject)
    db.commit()
    db.refresh(faculty)
    return faculty


@router.delete("/{faculty_id}/subjects/{subject_id}", response_model=schemas.FacultyOut)
def unmap_subject(
    faculty_id: int, subject_id: int, db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    faculty = crud.get_or_404(db, Faculty, faculty_id)
    subject = crud.get_or_404(db, Subject, subject_id)
    if subject not in faculty.subjects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{faculty.name} is not mapped to {subject.code}",
        )
    faculty.subjects.remove(subject)
    db.commit()
    db.refresh(faculty)
    return faculty
