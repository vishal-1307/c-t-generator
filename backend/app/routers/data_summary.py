"""One request that answers "is my data ready?".

Finding that out meant visiting up to ten pages and counting by eye. This
returns the counts and the obvious gaps for each entity, so a data workspace
can show it at a glance and link straight to whatever needs attention.

It is deliberately **not** a second opinion on readiness. `/api/validate` is
the authority on whether a timetable can be generated, and duplicating its
twelve checks here would create two sources of truth that drift. This reports
what exists and what is plainly missing; validation decides what that means.

Counts and EXISTS only, aggregated to one query per entity, so the endpoint
stays cheap enough to load on every visit to the workspace whatever the size
of the institution.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
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
from ..schemas import DataSummaryOut, EntityHealthOut

router = APIRouter(prefix="/api/data", tags=["data"])


def _tally(condition) -> Any:
    """Count the rows matching `condition` within the same pass.

    Every figure below could be its own `SELECT count(*)`, which is how this
    started and which cost fourteen round trips per page view. Conditional
    aggregation answers "how many, and how many of those are a problem" in one
    query per entity.
    """
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


@router.get("/summary", response_model=DataSummaryOut)
def data_summary(
    academic_context_id: int | None = Query(
        None, description="Scopes sections and their curriculum coverage."
    ),
    db: Session = Depends(get_db),
):
    """Per-entity counts, plus how many rows of each need attention."""

    # --- faculty: mapped to at least one subject, or they can teach nothing.
    teaches_nothing = ~(
        select(1)
        .select_from(FacultySubject)
        .where(FacultySubject.faculty_id == Faculty.id)
        .exists()
    )
    faculty_total, faculty_unmapped = db.execute(
        select(func.count(), _tally(teaches_nothing))
        .select_from(Faculty)
        .where(Faculty.is_active.is_(True))
    ).one()

    # --- subjects: a subject nobody can teach cannot be scheduled.
    has_no_teacher = ~(
        select(1)
        .select_from(FacultySubject)
        .where(FacultySubject.subject_id == Subject.id)
        .exists()
    )
    subject_total, subject_no_faculty = db.execute(
        select(func.count(), _tally(has_no_teacher))
        .select_from(Subject)
        .where(Subject.is_active.is_(True))
    ).one()

    # --- rooms: faculty rooms are not teaching space and are counted apart,
    #     because "we have 200 rooms" is misleading when 60 are offices.
    teaching_rooms, lab_rooms, office_rooms = db.execute(
        select(
            _tally(Room.room_type != "faculty"),
            _tally(Room.room_type == "lab"),
            _tally(Room.room_type == "faculty"),
        )
        .select_from(Room)
        .where(Room.is_active.is_(True))
    ).one()

    # --- sections: scoped to the context being worked on.
    section_where = [Section.is_active.is_(True)]
    if academic_context_id is not None:
        section_where.append(Section.academic_context_id == academic_context_id)
    has_no_curriculum = ~(
        select(1)
        .select_from(SectionSubject)
        .where(SectionSubject.section_id == Section.id)
        .exists()
    )
    section_total, section_no_curriculum = db.execute(
        select(func.count(), _tally(has_no_curriculum))
        .select_from(Section)
        .where(*section_where)
    ).one()

    # --- the week itself. Lunch is never scheduled, so the teachable count is
    #     the one that actually bounds what can be timetabled.
    slot_total, teachable_slots = db.execute(
        select(func.count(), _tally(TimeSlot.is_lunch.is_(False))).select_from(TimeSlot)
    ).one()

    availability_blocks = db.scalar(
        select(
            select(func.count()).select_from(FacultyUnavailability).scalar_subquery()
            + select(func.count()).select_from(SectionUnavailability).scalar_subquery()
            + select(func.count()).select_from(RoomUnavailability).scalar_subquery()
        )
    ) or 0

    entities = [
        EntityHealthOut(
            key="faculty", label="Faculty", count=faculty_total,
            issues=faculty_unmapped,
            issue_label="not mapped to any subject" if faculty_unmapped else None,
        ),
        EntityHealthOut(
            key="subjects", label="Subjects", count=subject_total,
            issues=subject_no_faculty,
            issue_label="have no eligible faculty" if subject_no_faculty else None,
        ),
        EntityHealthOut(
            key="rooms", label="Teaching rooms", count=teaching_rooms,
            issues=0,
            detail=f"{lab_rooms} labs"
            + (f", {office_rooms} faculty rooms excluded" if office_rooms else ""),
        ),
        EntityHealthOut(
            key="sections", label="Sections", count=section_total,
            issues=section_no_curriculum,
            issue_label="have no subjects assigned" if section_no_curriculum else None,
        ),
        EntityHealthOut(
            key="timeslots", label="Time slots", count=slot_total,
            issues=1 if slot_total == 0 else 0,
            issue_label="no weekly grid has been created" if slot_total == 0 else None,
            detail=f"{teachable_slots} teachable" if slot_total else None,
        ),
        EntityHealthOut(
            key="availability", label="Unavailable periods", count=availability_blocks,
            issues=0,
            detail="declared restrictions on faculty, rooms or sections",
        ),
    ]

    return DataSummaryOut(
        academic_context_id=academic_context_id,
        entities=entities,
        total_issues=sum(e.issues for e in entities),
    )
