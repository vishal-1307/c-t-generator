"""Timetable generation, run history, locking and publishing.

Generation is asynchronous. At the target scale a solve takes tens of seconds,
which exceeds the idle timeout of most hosting platforms, so the request starts
a worker and returns a run id to poll.

Publishing (spec #6/#9): PUBLISHED is the run in force for its academic
context. Publishing a new run archives whichever one was previously published
in that context, so at most one PUBLISHED run exists per context at a time -
no separate "ACTIVE" state duplicating that meaning.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import jobs, locking, versioning
from ..auth import require_scheduler_or_admin
from ..database import get_db
from ..models import (
    AcademicContext,
    Assignment,
    ChangeHistory,
    Section,
    SectionSubjectAssignment,
    TimetableRun,
    User,
)
from ..schemas import (
    GenerateRequest,
    LockAssignmentPayload,
    LockPreviewPayload,
    RunCheckOut,
    RunOut,
    RunSummaryOut,
    SectionSubjectAssignmentOut,
    VersionDiffOut,
    VersionDiffRowOut,
)
from ..validation import validate

router = APIRouter(prefix="/api", tags=["generate"])
logger = logging.getLogger("timetable.api")


def _to_out(db: Session, run: TimetableRun) -> RunOut:
    meta = json.loads(run.weights_json) if run.weights_json else {}
    return RunOut(
        id=run.id,
        academic_context_id=run.academic_context_id,
        version=run.version,
        created_at=run.created_at,
        status=run.status,
        publish_status=run.publish_status,
        published_at=run.published_at,
        objective_value=run.objective_value,
        solve_time_seconds=run.solve_time_seconds,
        message=run.message,
        weights=meta.get("weights") or {},
        penalties=meta.get("penalties") or {},
        assignment_count=db.query(Assignment)
        .filter(Assignment.run_id == run.id)
        .count(),
        proven_optimal=run.status == "OPTIMAL",
        warnings=json.loads(run.warnings_json) if run.warnings_json else [],
    )


def _reject_split_families(
    db: Session, academic_context_id: int, section_ids: list[int] | None
) -> None:
    """Refuse to schedule a lab group apart from the section it belongs to.

    A group's students are the parent's students, so the two must be placed
    against each other. Generating one without the other cannot do that: the
    absent side's classes are not in the model, so the result is a timetable
    that is internally consistent and still puts the same students in two rooms
    at once when it is read alongside the other run.

    Refusing is the honest option. Solving it anyway and warning would leave a
    plausible-looking timetable in the database for someone to publish.
    """
    if not section_ids:
        return
    wanted = set(section_ids)
    rows = (
        db.query(Section)
        .filter(Section.academic_context_id == academic_context_id)
        .all()
    )
    by_id = {s.id: s for s in rows}

    split: list[str] = []
    for section in rows:
        if section.id not in wanted:
            continue
        parent_id = section.parent_section_id
        if parent_id is not None and parent_id not in wanted:
            parent = by_id.get(parent_id)
            split.append(
                f"{section.section_number} is a lab group of "
                f"{parent.section_number if parent else parent_id}"
            )
    for section in rows:
        if section.id not in wanted or section.parent_section_id is not None:
            continue
        missing = [
            k.section_number
            for k in rows
            if k.parent_section_id == section.id and k.id not in wanted
        ]
        if missing:
            split.append(
                f"{section.section_number} has lab group(s) "
                f"{', '.join(missing)} left out"
            )

    if split:
        raise HTTPException(
            status_code=422,
            detail=(
                "A section and its lab groups have to be generated together, "
                "because they are the same students: "
                + "; ".join(split)
                + ". Include them all, or generate the whole context."
            ),
        )


@router.post("/generate", response_model=RunOut, status_code=202)
def start_generation(
    payload: GenerateRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    """Kick off a solve. Returns immediately; poll GET /api/runs/{id}."""
    ctx = db.get(AcademicContext, payload.academic_context_id)
    if ctx is None:
        raise HTTPException(
            status_code=404, detail=f"Academic context {payload.academic_context_id} not found"
        )
    if jobs.is_running():
        raise HTTPException(
            status_code=409,
            detail="A timetable is already being generated. Wait for it to finish.",
        )
    _reject_split_families(db, payload.academic_context_id, payload.section_ids)
    logger.info(
        "generation requested context=%s(%s) sections=%s candidate_check=pending",
        ctx.id, ctx.label, payload.section_ids or "all",
    )
    run = jobs.start(
        db,
        academic_context_id=payload.academic_context_id,
        section_ids=payload.section_ids,
        max_seconds=payload.max_seconds,
        weights=payload.weights or None,
        respect_persisted=payload.respect_persisted,
    )
    return _to_out(db, run)


@router.get("/runs", response_model=list[RunSummaryOut])
def list_runs(
    academic_context_id: int | None = None, limit: int = 20, db: Session = Depends(get_db)
):
    query = db.query(TimetableRun)
    if academic_context_id is not None:
        query = query.filter(TimetableRun.academic_context_id == academic_context_id)
    runs = query.order_by(TimetableRun.created_at.desc(), TimetableRun.id.desc()).limit(limit).all()
    return [
        RunSummaryOut(
            id=r.id,
            academic_context_id=r.academic_context_id,
            version=r.version,
            created_at=r.created_at,
            status=r.status,
            publish_status=r.publish_status,
            published_at=r.published_at,
            objective_value=r.objective_value,
            solve_time_seconds=r.solve_time_seconds,
            message=r.message,
        )
        for r in runs
    ]


@router.get("/runs/latest", response_model=RunOut)
def latest_run(
    academic_context_id: int | None = None,
    with_classes: bool = False,
    db: Session = Depends(get_db),
):
    """The newest run, whatever became of it - or, with ``with_classes``, the
    newest one that has a timetable in it, which is the run every timetable
    view shows."""
    from ..timetable_views import SHOWN_STATUSES

    query = db.query(TimetableRun)
    if with_classes:
        query = query.filter(TimetableRun.status.in_(SHOWN_STATUSES))
    if academic_context_id is not None:
        query = query.filter(TimetableRun.academic_context_id == academic_context_id)
    run = query.order_by(TimetableRun.created_at.desc(), TimetableRun.id.desc()).first()
    if run is None:
        raise HTTPException(status_code=404, detail="No timetable has been generated yet.")
    return _to_out(db, run)


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(TimetableRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return _to_out(db, run)


@router.get("/runs/{run_id}/check", response_model=RunCheckOut)
def check_run(run_id: int, db: Session = Depends(get_db)):
    """Every hard rule, re-derived from the run's rows. Reads only.

    What the result page reports as "hard conflicts". Taken from the rows
    rather than from anything the solver said about its own answer, so a bug
    in the model cannot certify itself. Unlike `POST .../validate` this changes
    nothing - it answers the question without moving the run's status.
    """
    from ..config import settings
    from ..timetable_checker import check_all, longest_faculty_run

    run = db.get(TimetableRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    violations = check_all(db, run_id)
    return RunCheckOut(
        run_id=run_id, violations=violations, count=len(violations),
        longest_faculty_run=longest_faculty_run(db, run_id),
        max_consecutive=max(0, settings.faculty_max_consecutive),
    )


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(
    run_id: int, db: Session = Depends(get_db), _user: User = Depends(require_scheduler_or_admin)
):
    run = db.get(TimetableRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.publish_status == "PUBLISHED":
        raise HTTPException(
            status_code=409,
            detail="Cannot delete the published timetable - archive or replace it first.",
        )
    db.delete(run)
    db.commit()


# ------------------------------------------------------------------ lifecycle


@router.post("/runs/{run_id}/validate", response_model=RunOut)
def validate_run(
    run_id: int, db: Session = Depends(get_db), _user: User = Depends(require_scheduler_or_admin)
):
    """DRAFT -> VALIDATED. Requires a valid solve and a clean pre-solve check."""
    run = db.get(TimetableRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.status not in ("OPTIMAL", "FEASIBLE"):
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} has solver status {run.status!r}, not a valid timetable",
        )
    report = validate(db, academic_context_id=run.academic_context_id)
    if not report.ready:
        raise HTTPException(
            status_code=409,
            detail=f"Data is not ready: {report.blocker_count} blocker(s) exist",
        )
    run.publish_status = "VALIDATED"
    db.commit()
    db.refresh(run)
    logger.info("run validated run_id=%s", run_id)
    return _to_out(db, run)


@router.post("/runs/{run_id}/publish", response_model=RunOut)
def publish_run(
    run_id: int, db: Session = Depends(get_db), user: User = Depends(require_scheduler_or_admin)
):
    """VALIDATED -> PUBLISHED. Archives any previously published run in the
    same context - at most one PUBLISHED run per context at a time.

    Part 18 concurrency note: two concurrent publish requests for the same
    context could, under a real MVCC database (Postgres at READ COMMITTED),
    both read "no currently published run" before either commits and both
    succeed - the archive-then-publish sequence alone does not prevent that.
    ``TimetableRun`` carries a partial unique index
    (``uq_one_published_run_per_context``, models.py) enforcing "at most one
    PUBLISHED row per context" at the database itself; whichever request
    commits second hits that constraint here and gets a clean 409 instead of
    leaving two runs simultaneously published. See TIMETABLE_LOGIC_SPEC.md's
    concurrency section for the full analysis.
    """
    from sqlalchemy.exc import IntegrityError

    run = db.get(TimetableRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.publish_status not in ("VALIDATED", "DRAFT"):
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is {run.publish_status}, cannot publish",
        )
    if run.status not in ("OPTIMAL", "FEASIBLE"):
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} has solver status {run.status!r}, not a valid timetable",
        )

    import datetime as _dt

    previous = (
        db.query(TimetableRun)
        .filter(
            TimetableRun.academic_context_id == run.academic_context_id,
            TimetableRun.publish_status == "PUBLISHED",
        )
        .all()
    )
    for p in previous:
        p.publish_status = "ARCHIVED"
    # Flush the archive step on its own before publishing this run - SQLite
    # (and Postgres without a DEFERRABLE constraint) checks a unique index
    # per statement, not only at commit, so archiving and publishing must
    # not land in the same flush: an arbitrary UPDATE ordering within one
    # flush could momentarily have both rows PUBLISHED and trip the partial
    # unique index even though the final state is valid.
    db.flush()

    run.publish_status = "PUBLISHED"
    run.published_at = _dt.datetime.now(_dt.UTC)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Another publish for this academic context just completed. "
            "Refresh and try again.",
        ) from None
    db.refresh(run)
    logger.info(
        "run published run_id=%s context=%s archived=%s",
        run_id, run.academic_context_id, [p.id for p in previous],
    )
    return _to_out(db, run)


# ---------------------------------------------------------------------- locking


@router.get(
    "/academic-contexts/{context_id}/assignments",
    response_model=list[SectionSubjectAssignmentOut],
)
def list_persisted_assignments(context_id: int, db: Session = Depends(get_db)):
    """The persistence table itself - what faculty/room is locked in for each
    section-subject pair in this context, and whether it is locked."""
    return (
        db.query(SectionSubjectAssignment)
        .filter(SectionSubjectAssignment.academic_context_id == context_id)
        .all()
    )


def _latest_run_id(db: Session, context_id: int) -> int | None:
    return locking.latest_run_id(db, context_id)


@router.post(
    "/academic-contexts/{context_id}/assignments/lock",
    response_model=SectionSubjectAssignmentOut,
)
def lock_assignment(
    context_id: int,
    payload: LockAssignmentPayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_scheduler_or_admin),
):
    """Lock a (section, subject)'s current faculty/room/slot so regeneration
    cannot change it. Requires the pair to already have a persisted assignment
    (i.e. it has been generated at least once).

    The rule itself lives in ``app/locking.py``.
    """
    try:
        return locking.set_lock(
            db, context_id=context_id, section_id=payload.section_id,
            subject_id=payload.subject_id, locked=True,
            user_id=user.id, user_role=user.role, actor=user.username,
        )
    except locking.LockError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None


@router.post("/academic-contexts/{context_id}/assignments/lock/preview")
def preview_lock_assignment(
    context_id: int,
    payload: LockPreviewPayload,
    db: Session = Depends(get_db),
    _user: User = Depends(require_scheduler_or_admin),
):
    """What locking or unlocking would do, without doing it.

    Parity with the other edits: moving a class, changing its room and changing
    its faculty are all previewed before they are applied. Locking was the
    exception, writing immediately with no preview. It now previews too.
    """
    try:
        result = locking.preview(
            db,
            context_id=context_id,
            section_id=payload.section_id,
            subject_id=payload.subject_id,
            locked=payload.lock,
            session_type=payload.session_type,
        )
    except locking.LockError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None
    return asdict(result)


@router.post(
    "/academic-contexts/{context_id}/assignments/unlock",
    response_model=SectionSubjectAssignmentOut,
)
def unlock_assignment(
    context_id: int,
    payload: LockAssignmentPayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_scheduler_or_admin),
):
    try:
        return locking.set_lock(
            db, context_id=context_id, section_id=payload.section_id,
            subject_id=payload.subject_id, locked=False,
            user_id=user.id, user_role=user.role, actor=user.username,
        )
    except locking.LockError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None


# ------------------------------------------------------------ version comparison


@router.get("/runs/{from_run_id}/diff/{to_run_id}", response_model=VersionDiffOut)
def diff_runs(from_run_id: int, to_run_id: int, db: Session = Depends(get_db)):
    """Spec #38: a basic structured comparison between two versions - added,
    removed, moved, and faculty/room-changed pairs.

    The comparison itself lives in ``app/versioning.py``.
    """
    try:
        diff = versioning.diff_runs(db, from_run_id, to_run_id)
    except versioning.UnknownRun as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    return VersionDiffOut(
        from_run_id=diff.from_run_id,
        to_run_id=diff.to_run_id,
        added=diff.added,
        removed=diff.removed,
        moved=diff.moved,
        faculty_changed=diff.faculty_changed,
        room_changed=diff.room_changed,
        rows=[
            VersionDiffRowOut(
                section_number=r.section_number,
                subject_code=r.subject_code,
                change=r.change,
                detail=r.detail,
            )
            for r in diff.rows
        ],
    )
