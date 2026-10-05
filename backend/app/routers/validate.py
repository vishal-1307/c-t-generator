"""Readiness validation endpoint.

Runs the pre-solve checks and reports what would block the solver. Exposed
separately from generation so the admin can check data at any time.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import ValidationReportOut
from ..validation import validate

router = APIRouter(prefix="/api/validate", tags=["validation"])


@router.get("", response_model=ValidationReportOut)
def run_validation(academic_context_id: int | None = None, db: Session = Depends(get_db)):
    """Without a context, validates every section in the database at once -
    useful for a whole-database check, but not what generation ever does."""
    return validate(db, academic_context_id=academic_context_id)
