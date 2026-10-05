"""Reset the application's data, for a host with no shell.

Administrator only, and deliberately awkward: the GET says exactly what would
be deleted and deletes nothing; the POST deletes only when the body carries
the confirmation phrase word for word. It refuses while a timetable is being
generated, because the solve thread would write rows back into tables being
emptied. See `app/reset.py` for what is and is not touched.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import jobs
from ..auth import require_admin
from ..database import get_db
from ..models import User
from ..reset import CONFIRMATION, data_counts, reset_application_data

logger = logging.getLogger("timetable.api")

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ResetRequest(BaseModel):
    confirm: str


@router.get("/reset")
def what_a_reset_would_delete(
    db: Session = Depends(get_db), _user: User = Depends(require_admin)
):
    counts = data_counts(db)
    return {
        "would_delete": {k: v for k, v in counts.items() if v},
        "total_rows": sum(counts.values()),
        "kept": ["user accounts", "schema and migrations", "configuration"],
        "confirmation_phrase": CONFIRMATION,
    }


@router.post("/reset")
def reset(
    body: ResetRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    if body.confirm != CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail=f'Nothing was deleted. To reset, send confirm: "{CONFIRMATION}".',
        )
    if jobs.is_running():
        raise HTTPException(
            status_code=409,
            detail="A timetable is being generated. Wait for it to finish, then reset.",
        )
    deleted = reset_application_data(db)
    logger.warning(
        "application data reset by %s: %s rows deleted",
        user.username, sum(deleted.values()),
    )
    return {"deleted": {k: v for k, v in deleted.items() if v},
            "total_rows": sum(deleted.values())}
