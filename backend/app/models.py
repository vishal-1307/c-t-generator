"""SQLAlchemy models.

Phase 4 rework, driven by TIMETABLE_LOGIC_SPEC.md. Two structural ideas carry
the whole schema:

* **AcademicContext** scopes a semester's Sections and TimetableRuns.  Rooms,
  Faculty and the Subject catalog are institution-wide resources reused across
  semesters, so they are *not* scoped - only the things that are specific to
  one semester's offering are.
* **SectionSubjectAssignment** is the persistence anchor (spec #7/#8): once the
  solver decides a faculty/room for a (context, section, subject), that choice
  is written here and re-read on every future regeneration, so persistence
  comes from the data model rather than from solver determinism. ``locked``
  additionally pins the exact slot(s), read from the referencing
  TimetableRun's Assignment rows - never duplicated into this table.

Availability is stored only as *exceptions* (blocked slots) for faculty,
section and room, so "available" is the default and there is exactly one
source of truth: absence of a block row, and absence of a clashing Assignment.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

SUBJECT_TYPES = ("theory", "practical", "mixed")
ROOM_TYPES = ("theory", "lab", "faculty")
PUBLISH_STATES = ("DRAFT", "VALIDATED", "PUBLISHED", "ARCHIVED")
# Phase 10: admin > scheduler > {faculty, viewer} in write privilege, though
# "greater than" isn't a strict total order here - faculty and viewer both
# have zero write privilege, they differ only in what they're shown.
USER_ROLES = ("admin", "scheduler", "faculty", "viewer")


# The program recorded for a dataset made by uploading the two files without
# naming a semester. Each such upload is its own dataset: see
# routers/teacher_import.py::_intake.
UPLOAD_PROGRAM = "Upload"


class AcademicContext(Base):
    """Scopes a timetable to one academic year, semester, program and
    department. Rooms/Faculty/Subjects are institution-wide and are not scoped
    - only Section and TimetableRun belong to a context, so the same room or
    faculty catalog is reused across semesters without duplication.
    """

    __tablename__ = "academic_context"

    id: Mapped[int] = mapped_column(primary_key=True)
    academic_year: Mapped[str] = mapped_column(String(20), nullable=False)
    semester: Mapped[int] = mapped_column(Integer, nullable=False)
    program: Mapped[str] = mapped_column(String(60), nullable=False)
    department: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )
    # The saved infrastructure this dataset's timetable is built on. Recorded
    # so that replacing the infrastructure knows exactly which timetables stop
    # being true, instead of guessing from room ids. Empty for datasets made
    # before infrastructure was saved on its own.
    infrastructure_id: Mapped[int | None] = mapped_column(
        ForeignKey("infrastructure.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "academic_year", "semester", "program", "department",
            name="uq_academic_context",
        ),
        CheckConstraint("semester > 0", name="ck_context_semester"),
    )

    @property
    def label(self) -> str:
        if self.program == UPLOAD_PROGRAM:
            # "Upload 2 · 2026-09-11 · Load" - which upload, when, from what.
            # Not "Sem 2": the number counts uploads that day, not semesters.
            return " · ".join(
                part for part in (
                    f"Upload {self.semester}", self.academic_year, self.department,
                ) if part
            )
        return f"{self.academic_year} / Sem {self.semester} / {self.program} ({self.department})"


class Infrastructure(Base):
    """The saved room list: one Infra.xlsx, kept so it is not uploaded twice.

    Rooms change rarely and teaching loads every semester, so the two are saved
    separately. There is at most one: every new teaching load is scheduled
    against it, and replacing it is a deliberate act that removes the
    timetables built on the rooms it replaces.
    """

    __tablename__ = "infrastructure"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )
    uploaded_by: Mapped[str | None] = mapped_column(String(80), nullable=True)


class InfrastructureRoom(Base):
    """Which rooms the saved infrastructure lists."""

    __tablename__ = "infrastructure_room"

    infrastructure_id: Mapped[int] = mapped_column(
        ForeignKey("infrastructure.id", ondelete="CASCADE"), primary_key=True
    )
    room_id: Mapped[int] = mapped_column(
        ForeignKey("room.id", ondelete="CASCADE"), primary_key=True
    )


class AcademicContextRoom(Base):
    """The rooms one intake's timetable may use.

    Rooms are institution-wide, and until this existed every intake could use
    every room on record. That is wrong as soon as a database holds more than
    one department's rooms - or, as the deployment did, an earlier dataset's:
    the department's real timetable was placed 40 periods out of 52 in rooms
    that are not in its room list at all.

    So the two-file import records the rooms its `Infra.xlsx` lists, and
    those are the rooms that intake's timetable is built from. An intake with
    no rows here - anything created before this table, or by the general
    importer - keeps using every room, exactly as before.
    """

    __tablename__ = "academic_context_room"

    academic_context_id: Mapped[int] = mapped_column(
        ForeignKey("academic_context.id", ondelete="CASCADE"), primary_key=True
    )
    room_id: Mapped[int] = mapped_column(
        ForeignKey("room.id", ondelete="CASCADE"), primary_key=True
    )


class FacultySubject(Base):
    """Which faculty are *eligible* to teach which subject (hard constraint 8).

    Eligibility is not the same as assignment: many faculty can be eligible for
    a subject, but only one is assigned per (context, section, subject) - see
    SectionSubjectAssignment.
    """

    __tablename__ = "faculty_subject"

    faculty_id: Mapped[int] = mapped_column(
        ForeignKey("faculty.id", ondelete="CASCADE"), primary_key=True
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="CASCADE"), primary_key=True
    )

    # Additive (Phase 10, no migration needed - not a column): lets the
    # bulk-import adapter navigate faculty_code/subject.code without a
    # hand-written join for every row.
    faculty: Mapped[Faculty] = relationship(lazy="selectin", viewonly=True)
    subject: Mapped[Subject] = relationship(lazy="selectin", viewonly=True)


class SectionSubject(Base):
    """Which subjects a section takes. One row == one scheduling 'pair'."""

    __tablename__ = "section_subject"

    section_id: Mapped[int] = mapped_column(
        ForeignKey("section.id", ondelete="CASCADE"), primary_key=True
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="CASCADE"), primary_key=True
    )
    # How many times a week *this section* takes the subject. The teaching load
    # states it per row, and two sections can take one subject a different
    # number of times. Empty means the subject's own count, which is what every
    # mapping made before this column existed has always used.
    sessions_per_week: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "sessions_per_week IS NULL OR sessions_per_week >= 1",
            name="ck_section_subject_spw",
        ),
    )

    section: Mapped[Section] = relationship(lazy="selectin", viewonly=True)
    subject: Mapped[Subject] = relationship(lazy="selectin", viewonly=True)


class SubjectAllowedRoom(Base):
    """Optional explicit room restriction for a subject (spec #15).

    Default eligibility is capacity + type/lab-type matching (spec #16). This
    table is empty for the overwhelming majority of subjects; when rows exist
    for a subject, they *narrow* its eligible pool to exactly those rooms.
    """

    __tablename__ = "subject_allowed_room"

    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="CASCADE"), primary_key=True
    )
    room_id: Mapped[int] = mapped_column(
        ForeignKey("room.id", ondelete="CASCADE"), primary_key=True
    )


class Faculty(Base):
    __tablename__ = "faculty"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    faculty_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    department: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Phase 10: same pattern Room.is_active already established - an inactive
    # faculty member is never offered to the scheduler (solver/data.py filters
    # on this), but is never deleted. Defaults True so every pre-existing row
    # is unaffected.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    subjects: Mapped[list[Subject]] = relationship(
        secondary="faculty_subject", back_populates="faculties", lazy="selectin"
    )
    unavailability: Mapped[list[FacultyUnavailability]] = relationship(
        back_populates="faculty", cascade="all, delete-orphan", lazy="selectin"
    )


class Subject(Base):
    __tablename__ = "subject"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    # 'theory' | 'practical' | 'mixed'.
    #
    # 'mixed' means the subject has both a lecture and a practical component,
    # usually taught in different kinds of room - CAP460 is two lectures and
    # four practical periods a week, the lectures in a classroom and the
    # practicals in a programming lab. That is the ordinary shape of a real
    # syllabus, and until it existed here such a subject could only be modelled
    # as two separate subjects with two separate codes, which no institution's
    # data actually looks like.
    #
    # Widening this column rather than adding a parallel `delivery_type`
    # deliberately: ten places read `type`, and two representations of one fact
    # would need keeping in step forever. The load fields below carry the
    # per-component detail that a single subject cannot express.
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    # The weekly load for a single-component subject. For 'mixed', the
    # per-component fields below are used instead - see app/domain.py, which is
    # the only place that decides which applies.
    session_length_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sessions_per_week: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Per-component weekly load. Meaningful only when type == 'mixed'; NULL
    # otherwise, so there is never a second, disagreeing copy of a pure
    # subject's load.
    lecture_sessions_per_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lecture_session_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    practical_sessions_per_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    practical_session_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Room capabilities this subject needs. A programming practical can run in
    # an ordinary classroom if students bring laptops and can charge them;
    # an electronics practical cannot. Enforced as room eligibility from
    # Phase 6 - stored here from Phase 3 so the data can be imported first.
    byod_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    charging_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Only meaningful when type == 'practical'. Matched against Room.lab_type
    # (spec #14) - replaces the old one-lab-pinned-to-one-subject model.
    required_lab_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Optional explicit single-room override (spec #15). When set, this is the
    # subject's only eligible room, bypassing capacity/type matching entirely -
    # it is the admin's word that this room is correct.
    # use_alter=True breaks the circular FK with Room.fixed_subject_id: SQLite
    # tolerates create-time forward references, but Postgres does not, so this
    # constraint is emitted as a separate ALTER TABLE after both tables exist.
    fixed_room_id: Mapped[int | None] = mapped_column(
        ForeignKey("room.id", ondelete="SET NULL", use_alter=True, name="fk_subject_fixed_room"),
        nullable=True,
    )
    # Phase 10: same pattern as Room/Faculty.is_active - an inactive subject
    # is excluded from pair-building (solver/data.py), never deleted.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint(
            "type IN ('theory','practical','mixed')", name="ck_subject_type"
        ),
        CheckConstraint("session_length_hours BETWEEN 1 AND 3", name="ck_subject_len"),
        CheckConstraint("sessions_per_week >= 1", name="ck_subject_spw"),
        # A mixed subject must say what its two components are. Without this a
        # row could claim to be mixed and carry no per-component load, and the
        # solver would have nothing to schedule.
        CheckConstraint(
            "type <> 'mixed' OR ("
            " lecture_sessions_per_week IS NOT NULL"
            " AND lecture_session_length IS NOT NULL"
            " AND practical_sessions_per_week IS NOT NULL"
            " AND practical_session_length IS NOT NULL)",
            name="ck_subject_mixed_has_both_loads",
        ),
        CheckConstraint(
            "lecture_session_length IS NULL"
            " OR lecture_session_length BETWEEN 1 AND 3",
            name="ck_subject_lecture_len",
        ),
        CheckConstraint(
            "practical_session_length IS NULL"
            " OR practical_session_length BETWEEN 1 AND 3",
            name="ck_subject_practical_len",
        ),
    )

    faculties: Mapped[list[Faculty]] = relationship(
        secondary="faculty_subject", back_populates="subjects", lazy="selectin"
    )
    sections: Mapped[list[Section]] = relationship(
        secondary="section_subject", back_populates="subjects", lazy="selectin"
    )
    fixed_room: Mapped[Room | None] = relationship(
        foreign_keys=[fixed_room_id], lazy="selectin"
    )
    allowed_rooms: Mapped[list[Room]] = relationship(
        secondary="subject_allowed_room", lazy="selectin"
    )

    @property
    def periods_per_week(self) -> int:
        """Total periods this subject consumes per week, per section taking it.

        Delegates so a mixed subject counts both of its components. `domain`
        is the single place that knows how a subject's load is composed.
        """
        from .domain import periods_per_week

        return periods_per_week(self)


class Room(Base):
    __tablename__ = "room"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Unique within its block, not globally: two buildings can each have a
    # "101", and they are different rooms. A global constraint here forced the
    # second building's rooms to be renamed to import them at all.
    room_number: Mapped[str] = mapped_column(String(30), nullable=False)
    block: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    floor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # The label people actually use, e.g. "36-101". Derived from block and
    # number when not supplied, and unique across the institution, so a
    # timetable can print one unambiguous name for a room.
    room_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    # Capabilities that decide whether a practical can run here. A programming
    # or database practical needs power and students' own machines, which an
    # ordinary classroom can provide; an electronics practical needs the lab
    # itself. Modelling these lets scarce labs go to the sessions that truly
    # need them. Enforced as room eligibility from Phase 6.
    byod: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    charging: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Informational. Deliberately not a constraint: "enough sockets for the
    # students who need one" would be a second capacity-like inequality and a
    # new constraint class, and nothing has yet shown it changes an outcome.
    charging_sockets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 'theory' | 'lab' | 'faculty'. Faculty rooms are never schedulable
    # teaching rooms (spec #13) - the solver's candidate generator excludes
    # them by this flag, never by inspecting the room's name.
    room_type: Mapped[str] = mapped_column(String(10), nullable=False)
    # Only meaningful when room_type == 'lab'. Matched against
    # Subject.required_lab_type.
    lab_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Legacy single-subject pin, retained as an explicit override path (equivalent
    # to Subject.fixed_room_id from the room's side). Most rooms leave this null;
    # default eligibility comes from room_type + lab_type + capacity, not this.
    fixed_subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subject.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint("room_type IN ('theory','lab','faculty')", name="ck_room_type"),
        CheckConstraint("capacity > 0", name="ck_room_capacity"),
        CheckConstraint(
            "charging_sockets IS NULL OR charging_sockets >= 0",
            name="ck_room_sockets",
        ),
        # Room numbers repeat across buildings; the pair does not.
        UniqueConstraint("block", "room_number", name="uq_room_block_number"),
        UniqueConstraint("room_code", name="uq_room_code"),
        # Room eligibility filters on exactly these three, for every pair of
        # every solve. Free to maintain at this table's size.
        Index("ix_room_active_type", "is_active", "room_type", "lab_type"),
    )

    fixed_subject: Mapped[Subject | None] = relationship(
        foreign_keys=[fixed_subject_id], lazy="selectin"
    )
    unavailability: Mapped[list[RoomUnavailability]] = relationship(
        back_populates="room", cascade="all, delete-orphan", lazy="selectin"
    )


class Section(Base):
    __tablename__ = "section"

    id: Mapped[int] = mapped_column(primary_key=True)
    academic_context_id: Mapped[int] = mapped_column(
        ForeignKey("academic_context.id", ondelete="CASCADE"), nullable=False
    )
    section_number: Mapped[str] = mapped_column(String(30), nullable=False)
    strength: Mapped[int] = mapped_column(Integer, nullable=False)
    # The admission cohort a section belongs to, e.g. "BCA-2024". Label only:
    # the scheduling scope is the academic context, not the batch.
    batch: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Phase 10: same pattern as Room/Faculty/Subject.is_active - an inactive
    # section is excluded from generation (solver/data.py), never deleted.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # The section this one is a lab group of, when it is one. NULL means "a
    # section in its own right", which is every row that existed before this
    # column did.
    #
    # A department that splits section 2401 into groups 24011 and 24012 gives
    # each group its own strength, its own lab subject and its own teacher - so
    # a group behaves like a section in every respect except one: its students
    # are also the parent's students. That single exception is what this column
    # records, and `solver/model._add_section_clash` is the only place it
    # changes a decision: a section and one of its groups cannot be taught at
    # the same time, while two groups of the same section can.
    #
    # Groups are sections rather than SectionGroup rows because everything else
    # then follows for free - room capacity is already checked against
    # `section.strength`, demand is already counted per section, and a lock is
    # already keyed by section. It also adds no solver pairs, where a separate
    # group dimension would have doubled the practicals.
    #
    # One level only: a group of a group has no meaning here, and validation
    # rejects it rather than letting the clash rule quietly miss a generation.
    parent_section_id: Mapped[int | None] = mapped_column(
        ForeignKey("section.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint("strength > 0", name="ck_section_strength"),
        UniqueConstraint(
            "academic_context_id", "section_number", name="uq_section_number_per_context"
        ),
        Index("ix_section_parent", "parent_section_id"),
    )

    academic_context: Mapped[AcademicContext] = relationship(lazy="selectin")
    subjects: Mapped[list[Subject]] = relationship(
        secondary="section_subject", back_populates="sections", lazy="selectin"
    )
    # The same mappings as rows, for what they carry beyond "takes": how many
    # times a week. Read-only - `subjects` is how a mapping is made.
    subject_links: Mapped[list[SectionSubject]] = relationship(
        viewonly=True, lazy="selectin"
    )
    unavailability: Mapped[list[SectionUnavailability]] = relationship(
        back_populates="section", cascade="all, delete-orphan", lazy="selectin"
    )
    groups: Mapped[list[SectionGroup]] = relationship(
        back_populates="section", cascade="all, delete-orphan", lazy="selectin"
    )
    parent: Mapped[Section | None] = relationship(
        remote_side=[id], back_populates="children", lazy="selectin"
    )
    children: Mapped[list[Section]] = relationship(
        back_populates="parent", lazy="selectin"
    )

    def sessions_per_week_for(self, subject_id: int) -> int | None:
        """This section's own weekly count for a subject, if the load gave one."""
        for link in self.subject_links:
            if link.subject_id == subject_id:
                return link.sessions_per_week
        return None

    @property
    def parent_section_number(self) -> str | None:
        """The parent's number, for screens that say "Group of 2401".

        A property rather than a column: it is the parent's fact, and copying
        it here would be one more thing to keep in step with a rename.
        """
        return self.parent.section_number if self.parent else None


class SectionGroup(Base):
    """A lab batch within a section - G1, G2 and so on. Legacy; see below.

    This is the older of two ways to say "half of section 2401 does the lab
    separately", and it is the one that is *not* scheduled. A SectionGroup row
    is captured and displayed; the solver never reads it. Nothing writes these
    rows except the `section_groups` import sheet, kept working for files that
    already use it.

    The representation that IS scheduled is `Section.parent_section_id`: the
    group is its own section, carrying its own strength, subjects and teacher,
    linked to the section it is part of. That arrived with the two-file teacher
    import, where the load sheet already names groups as sections (2401 with
    24011 and 24012), so making them sections cost nothing and made room
    capacity, demand counting and locking correct for a group without adding a
    single solver variable.

    Both can exist for one section, and they mean different things - so if you
    are reading a group off a section, check which one you want. New work
    should use `parent_section_id`.
    """

    __tablename__ = "section_group"

    id: Mapped[int] = mapped_column(primary_key=True)
    section_id: Mapped[int] = mapped_column(
        ForeignKey("section.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_code: Mapped[str] = mapped_column(String(20), nullable=False)
    strength: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("strength > 0", name="ck_section_group_strength"),
        UniqueConstraint("section_id", "group_code", name="uq_section_group_code"),
    )

    section: Mapped[Section] = relationship(back_populates="groups")


class TimeSlot(Base):
    __tablename__ = "timeslot"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(12), nullable=False)
    day_index: Mapped[int] = mapped_column(Integer, nullable=False)      # 0 = Monday
    period_index: Mapped[int] = mapped_column(Integer, nullable=False)   # 0-based within the day
    start_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    is_lunch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("day_index", "period_index", name="uq_timeslot_day_period"),
        CheckConstraint("day_index BETWEEN 0 AND 6", name="ck_timeslot_day_index"),
        CheckConstraint("period_index >= 0", name="ck_timeslot_period_index"),
    )


class FacultyUnavailability(Base):
    """An exception, not a schedule: a faculty member is blocked at this slot
    regardless of whether anything is assigned there. Availability is derived
    by combining this with the absence of a clashing Assignment - there is no
    separate "free time" table to keep in sync."""

    __tablename__ = "faculty_unavailability"

    id: Mapped[int] = mapped_column(primary_key=True)
    faculty_id: Mapped[int] = mapped_column(
        ForeignKey("faculty.id", ondelete="CASCADE"), nullable=False
    )
    timeslot_id: Mapped[int] = mapped_column(
        ForeignKey("timeslot.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        UniqueConstraint("faculty_id", "timeslot_id", name="uq_faculty_unavailable_slot"),
    )

    faculty: Mapped[Faculty] = relationship(back_populates="unavailability")
    timeslot: Mapped[TimeSlot] = relationship(lazy="selectin")


class SectionUnavailability(Base):
    """Same pattern as FacultyUnavailability, for a section."""

    __tablename__ = "section_unavailability"

    id: Mapped[int] = mapped_column(primary_key=True)
    section_id: Mapped[int] = mapped_column(
        ForeignKey("section.id", ondelete="CASCADE"), nullable=False
    )
    timeslot_id: Mapped[int] = mapped_column(
        ForeignKey("timeslot.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        UniqueConstraint("section_id", "timeslot_id", name="uq_section_unavailable_slot"),
    )

    section: Mapped[Section] = relationship(back_populates="unavailability")
    timeslot: Mapped[TimeSlot] = relationship(lazy="selectin")


class RoomUnavailability(Base):
    """Same pattern as FacultyUnavailability, for a room (e.g. maintenance)."""

    __tablename__ = "room_unavailability"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("room.id", ondelete="CASCADE"), nullable=False
    )
    timeslot_id: Mapped[int] = mapped_column(
        ForeignKey("timeslot.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        UniqueConstraint("room_id", "timeslot_id", name="uq_room_unavailable_slot"),
    )

    room: Mapped[Room] = relationship(back_populates="unavailability")
    timeslot: Mapped[TimeSlot] = relationship(lazy="selectin")


class SectionSubjectAssignment(Base):
    """The persistence anchor (spec #7/#8/#9).

    Once the solver decides a faculty and room for a (context, section,
    subject), that choice is written here. Every future regeneration for the
    same context reads this row first and restricts that pair's candidates to
    the recorded faculty/room, so persistence comes from the database, never
    from hoping the solver picks the same thing twice.

    ``locked`` additionally pins the exact day/slot(s). Those are NOT
    duplicated here - they are read from the most recent Assignment rows for
    this (section, subject) at solve time, keeping Assignment the single
    source of truth for placement while this table is the single source of
    truth for "who and where."
    """

    __tablename__ = "section_subject_assignment"

    id: Mapped[int] = mapped_column(primary_key=True)
    academic_context_id: Mapped[int] = mapped_column(
        ForeignKey("academic_context.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("section.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="CASCADE"), nullable=False
    )
    faculty_id: Mapped[int | None] = mapped_column(
        ForeignKey("faculty.id", ondelete="SET NULL"), nullable=True
    )
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("room.id", ondelete="SET NULL"), nullable=True
    )
    # Which component of the subject this decision is for. A subject's lecture
    # and its practical are decided separately and usually land in different
    # rooms, so one row per pair would force them to share one.
    session_type: Mapped[str] = mapped_column(
        String(1), nullable=False, default="L", server_default="L"
    )
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime,
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "academic_context_id", "section_id", "subject_id", "session_type",
            name="uq_section_subject_assignment",
        ),
    )

    section: Mapped[Section] = relationship(lazy="selectin")
    subject: Mapped[Subject] = relationship(lazy="selectin")
    faculty: Mapped[Faculty | None] = relationship(lazy="selectin")
    room: Mapped[Room | None] = relationship(lazy="selectin")


class TimetableRun(Base):
    """One invocation of the solver, scoped to one academic context.

    Two independent state dimensions, deliberately not conflated:

    * ``status`` - the *solver outcome*: RUNNING | OPTIMAL | FEASIBLE | PARTIAL
      | INFEASIBLE | TIMEOUT. Sourced from CP-SAT, meaningless as a
      publication state.
    * ``publish_status`` - the *lifecycle*: DRAFT -> VALIDATED -> PUBLISHED, or
      ARCHIVED once superseded. Only a run whose ``status`` is OPTIMAL/FEASIBLE
      can be validated or published. At most one PUBLISHED run exists per
      academic context at a time; publishing a new one archives the old.
    """

    __tablename__ = "timetable_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    academic_context_id: Mapped[int] = mapped_column(
        ForeignKey("academic_context.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )
    # RUNNING   - solver still working (async)
    # OPTIMAL   - valid, and proven best under the objective
    # FEASIBLE  - valid, but the time limit stopped the search early
    # PARTIAL   - valid for a subset; some pairs could not be placed
    # INFEASIBLE- proven impossible: nothing could be scheduled; see `message`
    # TIMEOUT   - ran out of time before finding one; NOT a proof that no
    #             timetable exists. Kept distinct from INFEASIBLE because the
    #             two call for opposite responses: fix the data, or allow more
    #             time / a smaller batch.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    publish_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    objective_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    solve_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    weights_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Things that happened during this solve which a person should know about
    # but which did not stop it - a lock that could not be honoured, above all.
    # Written inside the same transaction as the assignments, before the status,
    # so a finished run can never be missing the warnings that describe it.
    warnings_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "publish_status IN ('DRAFT','VALIDATED','PUBLISHED','ARCHIVED')",
            name="ck_run_publish_status",
        ),
        UniqueConstraint("academic_context_id", "version", name="uq_run_version_per_context"),
        # Phase 10 PART 18: a real DB-enforced constraint, not just the
        # archive-then-publish transaction in routers/generate.py. Under
        # SQLite's default single-writer locking that transaction already
        # can't race, but under Postgres (READ COMMITTED) two concurrent
        # publish requests for the same context could both read "no
        # currently published run" before either commits and both succeed -
        # this partial unique index makes the second one fail at the
        # database instead, which publish_run() then reports as a clean
        # 409 rather than a torn "two published runs" state.
        Index(
            "uq_one_published_run_per_context", "academic_context_id",
            unique=True,
            sqlite_where=text("publish_status = 'PUBLISHED'"),
            postgresql_where=text("publish_status = 'PUBLISHED'"),
        ),
    )

    academic_context: Mapped[AcademicContext] = relationship(lazy="selectin")
    assignments: Mapped[list[Assignment]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Assignment(Base):
    """Generated output: one row per (section, subject) per occupied timeslot.

    This is the single source of truth every timetable view derives from -
    section, faculty, room and master views are all queries over this table,
    never separately maintained copies (spec #33).
    """

    __tablename__ = "assignment"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("timetable_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("section.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[int] = mapped_column(ForeignKey("subject.id", ondelete="CASCADE"))
    faculty_id: Mapped[int] = mapped_column(
        ForeignKey("faculty.id", ondelete="CASCADE"), index=True
    )
    room_id: Mapped[int] = mapped_column(ForeignKey("room.id", ondelete="CASCADE"), index=True)
    timeslot_id: Mapped[int] = mapped_column(ForeignKey("timeslot.id", ondelete="CASCADE"))
    # Groups the periods of one multi-hour block together (HC9), so the UI can merge cells.
    block_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Which component of the subject this class is: 'L' or 'P'. A subject with
    # both is scheduled as two independent obligations, usually in different
    # kinds of room, so a class has to say which one it is.
    session_type: Mapped[str] = mapped_column(
        String(1), nullable=False, default="L", server_default="L"
    )

    __table_args__ = (
        # Every grid view joins on the slot, and the grid-replacement impact
        # count filters on it; it was the one FK here without an index.
        Index("ix_assignment_timeslot_id", "timeslot_id"),
        # The lookup the solver runs per locked pair when reproducing a lock
        # from the previous run's rows.
        Index("ix_assignment_run_section_subject", "run_id", "section_id", "subject_id"),
    )

    run: Mapped[TimetableRun] = relationship(back_populates="assignments")
    section: Mapped[Section] = relationship(lazy="selectin")
    subject: Mapped[Subject] = relationship(lazy="selectin")
    faculty: Mapped[Faculty] = relationship(lazy="selectin")
    room: Mapped[Room] = relationship(lazy="selectin")
    timeslot: Mapped[TimeSlot] = relationship(lazy="selectin")


class ChangeHistory(Base):
    """Phase 6 audit trail for manual timetable edits, hardened in Phase 10.

    Traceability, not user management (spec #20): one row per applied change
    (never per rejected attempt - a failed validation writes nothing). Kept
    intentionally simple - a free-text before/after description rather than a
    structured diff, since the shape of "what changed" differs by
    ``change_type`` (a move's old/new value is a day+time; a room change's is
    a room).

    ``actor`` predates authentication and stays as a free-text fallback
    (nullable, unrestricted) so nothing that ever wrote here breaks.
    ``user_id``/``user_role`` are additive (Phase 10): once a caller is
    authenticated, both are filled from the real ``User`` row rather than a
    self-reported string - a real identity, not a free-text claim of one.
    Both stay nullable so pre-auth history rows, and any future call made
    without a token, remain valid.
    """

    __tablename__ = "change_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("timetable_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("section.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="CASCADE"), nullable=False
    )
    # move | room | faculty | lock | unlock
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)
    old_value: Mapped[str] = mapped_column(String(300), nullable=False)
    new_value: Mapped[str] = mapped_column(String(300), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )
    # Phase 10 additive hardening - real identity, sourced from the
    # authenticated User at write time, never client-supplied.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    user_role: Mapped[str | None] = mapped_column(String(20), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "change_type IN ('move','room','faculty','lock','unlock')",
            name="ck_change_history_type",
        ),
    )

    user: Mapped[User | None] = relationship(lazy="selectin")

    @property
    def user_username(self) -> str | None:
        return self.user.username if self.user else None

    run: Mapped[TimetableRun] = relationship(lazy="selectin")
    section: Mapped[Section] = relationship(lazy="selectin")
    subject: Mapped[Subject] = relationship(lazy="selectin")


class ImportHistory(Base):
    """Phase 9 audit trail for bulk data imports.

    Answers "when was room data last refreshed, and what did that import
    actually do?" - the question an administrator asks when a timetable looks
    wrong and they suspect stale reference data. One row per *applied* import;
    a preview writes nothing.
    """

    __tablename__ = "import_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Which importer ran - "rooms", and later the other entity types.
    entity: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    # add_update | full_sync - see app/bulk_import.py.
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    rows_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_deactivated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # applied | rejected - "rejected" records an attempt refused wholesale
    # (e.g. invalid rows present and the caller did not allow partial apply).
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )

    __table_args__ = (
        CheckConstraint("status IN ('applied','rejected')", name="ck_import_history_status"),
    )


class User(Base):
    """Phase 10 authentication/authorization.

    Passwords are never stored in plain text - only a bcrypt hash
    (``app/auth.py``). ``faculty_id`` is only meaningful when ``role ==
    'faculty'``: it ties a login to one institution-wide ``Faculty`` row so
    "view my own timetable" has a concrete resource to mean. It is nullable
    and unrelated to any other role.
    """

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    faculty_id: Mapped[int | None] = mapped_column(
        ForeignKey("faculty.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "role IN ('admin','scheduler','faculty','viewer')", name="ck_user_role"
        ),
    )

    faculty: Mapped[Faculty | None] = relationship(lazy="selectin")
