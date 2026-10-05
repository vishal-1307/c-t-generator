"""Lock/unlock a semester assignment.

Extracted from ``routers/generate.py`` so every caller runs the *same* code
rather than two copies that can drift. The
router keeps its HTTP concerns (status codes, payload shape); everything that
decides what a lock means lives here.

A lock is a semester-level statement about a ``(context, section, subject)``
pair - "this faculty, this room, this slot survives regeneration" - not a
property of one weekly occurrence. That is why it is keyed the way it is, and
why locking is refused for a pair that has never been scheduled: there would
be nothing to preserve.

A subject with both a lecture and a practical component has one decision per
component. Locking such a subject locks **both** by default, because "keep
CAP460 where it is" plainly means all of it; pass ``session_type`` to pin only
one half.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import ChangeHistory, SectionSubjectAssignment, TimetableRun

logger = logging.getLogger("timetable.api")


class LockError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def latest_run_id(db: Session, context_id: int) -> int | None:
    run = (
        db.query(TimetableRun)
        .filter(TimetableRun.academic_context_id == context_id)
        .order_by(TimetableRun.created_at.desc(), TimetableRun.id.desc())
        .first()
    )
    return run.id if run else None


def get_assignment_rows(
    db: Session,
    *,
    context_id: int,
    section_id: int,
    subject_id: int,
    session_type: str | None = None,
) -> list[SectionSubjectAssignment]:
    """Every persisted decision for this pair, or just one component's.

    A list rather than one row because a subject can have two components, and
    picking whichever came back first would lock an arbitrary half of it.
    """
    query = db.query(SectionSubjectAssignment).filter(
        SectionSubjectAssignment.academic_context_id == context_id,
        SectionSubjectAssignment.section_id == section_id,
        SectionSubjectAssignment.subject_id == subject_id,
    )
    if session_type is not None:
        query = query.filter(SectionSubjectAssignment.session_type == session_type)
    return query.order_by(SectionSubjectAssignment.session_type).all()


def get_assignment_row(
    db: Session, *, context_id: int, section_id: int, subject_id: int
) -> SectionSubjectAssignment | None:
    """One decision for this pair, for callers that only need to know it exists."""
    rows = get_assignment_rows(
        db, context_id=context_id, section_id=section_id, subject_id=subject_id
    )
    return rows[0] if rows else None


def set_lock(
    db: Session,
    *,
    context_id: int,
    section_id: int,
    subject_id: int,
    locked: bool,
    session_type: str | None = None,
    user_id: int | None = None,
    user_role: str | None = None,
    actor: str | None = None,
) -> SectionSubjectAssignment:
    """Set the lock flag and record the change.

    Affects every component of the pair unless ``session_type`` names one.
    Raises ``LockError`` when the pair has no persisted assignment to lock.
    """
    rows = get_assignment_rows(
        db,
        context_id=context_id,
        section_id=section_id,
        subject_id=subject_id,
        session_type=session_type,
    )
    if not rows:
        raise LockError(
            "NO_PERSISTED_ASSIGNMENT",
            "No assignment exists yet for this section/subject - generate a "
            "timetable first",
        )

    # A component-wise comparison, not a single collapsed boolean: with two
    # components in different lock states (a lecture locked by an earlier
    # manual edit, its practical not), `any`/`all` over the whole set answers
    # a different question than "would this row change" and got it wrong -
    # a lock/unlock that genuinely changed one component's row could report,
    # and skip recording, no change at all.
    changed = any(r.locked != locked for r in rows)
    fully_locked_before = all(r.locked for r in rows)
    for row in rows:
        row.locked = locked
    row = rows[0]
    # A no-op still commits (harmless) but is not worth a history row - an
    # audit trail full of "locked -> locked" hides the real changes.
    if changed:
        run_id = latest_run_id(db, context_id)
        if run_id is not None:
            db.add(ChangeHistory(
                run_id=run_id,
                section_id=section_id,
                subject_id=subject_id,
                change_type="lock" if locked else "unlock",
                old_value="locked" if fully_locked_before else "unlocked",
                new_value="locked" if locked else "unlocked",
                actor=actor,
                user_id=user_id,
                user_role=user_role,
            ))
    db.commit()
    db.refresh(row)
    logger.info(
        "assignment %s context=%s section=%s subject=%s user=%s",
        "locked" if locked else "unlocked", context_id, section_id, subject_id,
        actor or user_id,
    )
    return row


@dataclass(frozen=True)
class LockPreview:
    """What locking or unlocking would do, before anything is written.

    Every other edit on a timetable - moving a class, changing its room,
    changing its teacher - is previewed before it is applied, and refuses to
    apply if the preview is stale. Locking used to be the exception, writing
    immediately with no preview at all.
    """

    would_change: bool
    currently_locked: bool
    will_be_locked: bool
    summary: str
    consequence: str
    # One entry per component: a mixed subject is two decisions, and "lock
    # CAP460" plainly means both of them.
    components: list[dict]


def preview(
    db: Session,
    *,
    context_id: int,
    section_id: int,
    subject_id: int,
    locked: bool,
    session_type: str | None = None,
) -> LockPreview:
    """Describe the effect of `set_lock` with the same arguments.

    Raises the same `LockError` for the same reasons, so a caller cannot get a
    clean preview for something that would then fail to apply.
    """
    rows = get_assignment_rows(
        db,
        context_id=context_id,
        section_id=section_id,
        subject_id=subject_id,
        session_type=session_type,
    )
    if not rows:
        raise LockError(
            "NO_PERSISTED_ASSIGNMENT",
            "No assignment exists yet for this section/subject - generate a "
            "timetable first",
        )

    first = rows[0]
    section = first.section
    subject = first.subject
    # Component-wise, for the same reason set_lock computes it this way (see
    # its comment): with components in different lock states, "would this
    # change anything" is not answerable by collapsing all of them into one
    # any()/all() first.
    would_change = any(r.locked != locked for r in rows)
    fully_locked_now = all(r.locked for r in rows)

    components = [
        {
            "session_type": r.session_type,
            "locked": bool(r.locked),
            "faculty_id": r.faculty_id,
            "room_id": r.room_id,
            "faculty_name": r.faculty.name if r.faculty else None,
            "room_number": r.room.room_code or r.room.room_number if r.room else None,
        }
        for r in rows
    ]

    label = f"{subject.code} for {section.section_number}"
    if not would_change:
        return LockPreview(
            would_change=False,
            currently_locked=fully_locked_now,
            will_be_locked=fully_locked_now,
            summary=f"{label} is already {'locked' if fully_locked_now else 'unlocked'}.",
            consequence="Nothing would change.",
            components=components,
        )

    return LockPreview(
        would_change=True,
        currently_locked=fully_locked_now,
        will_be_locked=locked,
        summary=f"{'Lock' if locked else 'Unlock'} {label}"
        + (f" ({len(components)} components)" if len(components) > 1 else ""),
        consequence=(
            "Regeneration will keep this faculty, room and slot exactly as they "
            "are. If the subject's weekly load later changes so the old "
            "placement no longer fits, the lock is reported as dropped rather "
            "than silently ignored."
            if locked
            else "Regeneration becomes free to move this class, change its room "
            "or change its faculty."
        ),
        components=components,
    )
