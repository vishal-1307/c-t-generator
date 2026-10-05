"""Demo dataset endpoints (Phase 13 PART 5, 16).

A "reset the data" button is a genuinely dangerous thing to put in an
application that will later hold a real semester's timetable, so this router is
guarded three ways rather than one:

1. **Admin only** - the same authorization as every other reference-data write.
2. **Disabled in production** - ``settings.demo_features_enabled`` is false
   whenever ``APP_ENV=production``, and every endpoint here returns 403 rather
   than doing anything. Turning it back on requires deliberately setting both
   ``APP_ENV=development`` and ``ENABLE_DEMO_DATA=true``.
3. **Explicit confirmation** - reset requires ``confirm=true`` in the body, so
   a stray POST cannot delete anything.

Underneath, ``seed_demo.reset_demo`` is itself scoped to a manifest of the
codes it created and skips anything another academic context is using. The
guards above stop the endpoint being *called*; that scoping is what makes it
safe even when it is.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..config import settings
from ..database import get_db
from ..models import User
from ..seed_demo import DemoDataDisabled, reset_demo, seed_demo
from ..seed_demo import status as demo_status

router = APIRouter(prefix="/api/demo", tags=["demo"])


class DemoStatusOut(BaseModel):
    enabled: bool
    present: bool
    label: str | None = None
    academic_context_id: int | None = None
    sections: int = 0
    runs: int = 0
    detail: str


class DemoResetIn(BaseModel):
    # Not a formality: a reset deletes a whole context and its runs, so it
    # should be impossible to trigger by accident.
    confirm: bool = Field(
        default=False,
        description="Must be true. Guards against an accidental POST.",
    )


class DemoActionOut(BaseModel):
    ok: bool
    counts: dict[str, int]
    detail: str


def _require_enabled() -> None:
    if not settings.demo_features_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Demo data management is disabled. It is off whenever "
                "APP_ENV=production, because seeding or resetting sample data "
                "must not be possible against a live semester."
            ),
        )


@router.get("/status", response_model=DemoStatusOut)
def get_demo_status(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    """Whether demo data exists, and whether it may be managed here.

    Readable even when management is disabled, so an admin on a production
    deployment can still see that demo data is (or is not) present.
    """
    info = demo_status(db)
    enabled = settings.demo_features_enabled
    return DemoStatusOut(
        enabled=enabled,
        present=info["present"],
        label=info.get("label"),
        academic_context_id=info.get("academic_context_id"),
        sections=info.get("sections", 0),
        runs=info.get("runs", 0),
        detail=(
            "Demo data can be created and reset here."
            if enabled
            else "Demo management is disabled in this environment."
        ),
    )


@router.post("/seed", response_model=DemoActionOut)
def post_demo_seed(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    """Create or refresh the demo dataset. Safe to run repeatedly - entities
    are matched by their natural keys, so a second run updates rather than
    duplicating."""
    _require_enabled()
    try:
        counts = seed_demo(db, quiet=True)
    except DemoDataDisabled as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    info = demo_status(db)
    return DemoActionOut(
        ok=True,
        counts=counts,
        detail=(
            f"Demo data ready in {info['label']}. This is invented sample "
            "data, not real college data."
        ),
    )


@router.post("/reset", response_model=DemoActionOut)
def post_demo_reset(
    payload: DemoResetIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Remove the demo dataset and nothing else.

    Shared entities (a subject, room or faculty member) are kept if any other
    academic context still uses them, so this cannot remove real data that
    happens to share a code.
    """
    _require_enabled()
    if not payload.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set confirm=true to reset the demo data.",
        )
    try:
        counts = reset_demo(db, quiet=True)
    except DemoDataDisabled as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    kept = counts.get("kept_in_use", 0)
    return DemoActionOut(
        ok=True,
        counts=counts,
        detail=(
            "Demo data removed."
            + (
                f" {kept} shared record(s) were kept because another academic "
                "context still uses them."
                if kept else ""
            )
        ),
    )
