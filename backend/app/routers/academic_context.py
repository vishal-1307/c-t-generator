"""Academic context CRUD - the scope every Section and TimetableRun lives in.

A timetable for 2026-27 / Semester 1 must never be confused with one for
2026-27 / Semester 3 or a different program/department (spec #5). Rooms,
Faculty and the Subject catalog are institution-wide and are deliberately NOT
scoped here - only Section and TimetableRun reference a context.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..auth import require_admin
from ..database import get_db
from ..models import AcademicContext, User

router = APIRouter(prefix="/api/academic-contexts", tags=["academic-context"])


@router.get("", response_model=list[schemas.AcademicContextOut])
def list_contexts(db: Session = Depends(get_db)):
    return crud.list_all(
        db, AcademicContext, order_by=AcademicContext.academic_year.desc()
    )


@router.post("", response_model=schemas.AcademicContextOut, status_code=status.HTTP_201_CREATED)
def create_context(
    payload: schemas.AcademicContextCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    existing = (
        db.query(AcademicContext)
        .filter(
            AcademicContext.academic_year == payload.academic_year,
            AcademicContext.semester == payload.semester,
            AcademicContext.program == payload.program,
            AcademicContext.department == payload.department,
        )
        .first()
    )
    if existing is not None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{existing.label} already exists",
        )
    ctx = AcademicContext(**payload.model_dump())
    db.add(ctx)
    db.commit()
    db.refresh(ctx)
    return ctx


@router.get("/{context_id}", response_model=schemas.AcademicContextOut)
def get_context(context_id: int, db: Session = Depends(get_db)):
    return crud.get_or_404(db, AcademicContext, context_id)


@router.delete("/{context_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_context(
    context_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    crud.delete(db, AcademicContext, context_id)
