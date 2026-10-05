"""Pydantic v2 request/response schemas.

Field-level validation lives here (enums, ranges, non-empty strings).
Rules that depend on the *completeness* of the dataset - every practical has a
compatible lab, every section has a curriculum - deliberately do NOT live here.
They belong to the pre-solve validator, so partial data entry is never blocked.
"""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# 'mixed' means the subject has both a lecture and a practical component,
# scheduled independently and usually in different kinds of room.
SubjectType = Literal["theory", "practical", "mixed"]
SessionType = Literal["L", "P"]
RoomType = Literal["theory", "lab", "faculty"]
PublishStatus = Literal["DRAFT", "VALIDATED", "PUBLISHED", "ARCHIVED"]

NonEmptyStr = Annotated[str, Field(min_length=1, max_length=120)]
ShortStr = Annotated[str, Field(min_length=1, max_length=30)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------- AcademicContext


class AcademicContextBase(BaseModel):
    academic_year: Annotated[str, Field(min_length=1, max_length=20)]
    semester: Annotated[int, Field(gt=0, le=20)]
    program: ShortStr
    department: ShortStr


class AcademicContextCreate(AcademicContextBase):
    pass


class AcademicContextOut(ORMModel):
    id: int
    academic_year: str
    semester: int
    program: str
    department: str
    label: str


# --------------------------------------------------------------------------- Faculty


class FacultyBase(BaseModel):
    name: NonEmptyStr
    faculty_code: ShortStr
    department: Annotated[str, Field(max_length=60)] | None = None
    is_active: bool = True


class FacultyCreate(FacultyBase):
    pass


class FacultyUpdate(BaseModel):
    name: NonEmptyStr | None = None
    faculty_code: ShortStr | None = None
    department: Annotated[str, Field(max_length=60)] | None = None
    is_active: bool | None = None


class SubjectBrief(ORMModel):
    id: int
    name: str
    code: str
    type: SubjectType


class FacultyOut(ORMModel):
    id: int
    name: str
    faculty_code: str
    department: str | None = None
    is_active: bool
    subjects: list[SubjectBrief] = []


# --------------------------------------------------------------------------- Subject


class SubjectBase(BaseModel):
    name: NonEmptyStr
    code: ShortStr
    type: SubjectType
    session_length_hours: Annotated[int, Field(ge=1, le=3)] = 1
    sessions_per_week: Annotated[int, Field(ge=1, le=20)] = 1
    # Required when type == 'mixed', ignored otherwise: a mixed subject's two
    # components have their own weekly loads and are scheduled separately.
    lecture_sessions_per_week: Annotated[int, Field(ge=1, le=20)] | None = None
    lecture_session_length: Annotated[int, Field(ge=1, le=3)] | None = None
    practical_sessions_per_week: Annotated[int, Field(ge=1, le=20)] | None = None
    practical_session_length: Annotated[int, Field(ge=1, le=3)] | None = None
    # Room capabilities this subject needs. A software practical can run in a
    # classroom with power and students' own machines; an electronics one needs
    # the lab itself.
    byod_required: bool = False
    charging_required: bool = False
    # Only meaningful for practicals; matched against Room.lab_type.
    required_lab_type: Annotated[str, Field(max_length=40)] | None = None
    # Optional explicit single-room override (spec #15).
    fixed_room_id: int | None = None
    is_active: bool = True


class SubjectCreate(SubjectBase):
    pass


class SubjectUpdate(BaseModel):
    name: NonEmptyStr | None = None
    code: ShortStr | None = None
    type: SubjectType | None = None
    session_length_hours: Annotated[int, Field(ge=1, le=3)] | None = None
    sessions_per_week: Annotated[int, Field(ge=1, le=20)] | None = None
    lecture_sessions_per_week: Annotated[int, Field(ge=1, le=20)] | None = None
    lecture_session_length: Annotated[int, Field(ge=1, le=3)] | None = None
    practical_sessions_per_week: Annotated[int, Field(ge=1, le=20)] | None = None
    practical_session_length: Annotated[int, Field(ge=1, le=3)] | None = None
    byod_required: bool | None = None
    charging_required: bool | None = None
    required_lab_type: Annotated[str, Field(max_length=40)] | None = None
    fixed_room_id: int | None = None
    is_active: bool | None = None


class FacultyBrief(ORMModel):
    id: int
    name: str
    faculty_code: str


class RoomBrief(ORMModel):
    id: int
    room_number: str
    block: str
    floor: str | None = None
    room_code: str | None = None
    capacity: int
    room_type: RoomType
    lab_type: str | None = None
    byod: bool = False
    charging: bool = False


class SubjectOut(ORMModel):
    id: int
    name: str
    code: str
    type: SubjectType
    session_length_hours: int
    sessions_per_week: int
    periods_per_week: int
    lecture_sessions_per_week: int | None = None
    lecture_session_length: int | None = None
    practical_sessions_per_week: int | None = None
    practical_session_length: int | None = None
    byod_required: bool = False
    charging_required: bool = False
    required_lab_type: str | None = None
    fixed_room_id: int | None = None
    fixed_room: RoomBrief | None = None
    allowed_rooms: list[RoomBrief] = []
    faculties: list[FacultyBrief] = []
    is_active: bool


# --------------------------------------------------------------------------- Room


class RoomBase(BaseModel):
    room_number: ShortStr
    block: Annotated[str, Field(max_length=30)] = ""
    floor: Annotated[str, Field(max_length=20)] | None = None
    # The institution-wide label, e.g. "36-101". Derived from block and number
    # when omitted - room numbers repeat across buildings, this does not.
    room_code: Annotated[str, Field(max_length=40)] | None = None
    capacity: Annotated[int, Field(gt=0, le=1000)]
    # Capabilities that decide which practicals can run here.
    byod: bool = False
    charging: bool = False
    charging_sockets: Annotated[int, Field(ge=0, le=1000)] | None = None
    room_type: RoomType
    # Only meaningful when room_type == 'lab'.
    lab_type: Annotated[str, Field(max_length=40)] | None = None
    is_active: bool = True
    fixed_subject_id: int | None = None


class RoomCreate(RoomBase):
    pass


class RoomUpdate(BaseModel):
    room_number: ShortStr | None = None
    block: Annotated[str, Field(max_length=30)] | None = None
    floor: Annotated[str, Field(max_length=20)] | None = None
    room_code: Annotated[str, Field(max_length=40)] | None = None
    capacity: Annotated[int, Field(gt=0, le=1000)] | None = None
    byod: bool | None = None
    charging: bool | None = None
    charging_sockets: Annotated[int, Field(ge=0, le=1000)] | None = None
    room_type: RoomType | None = None
    lab_type: Annotated[str, Field(max_length=40)] | None = None
    is_active: bool | None = None
    fixed_subject_id: int | None = None


class RoomOut(ORMModel):
    id: int
    room_number: str
    block: str
    floor: str | None = None
    room_code: str | None = None
    capacity: int
    byod: bool = False
    charging: bool = False
    charging_sockets: int | None = None
    room_type: RoomType
    lab_type: str | None = None
    is_active: bool
    fixed_subject_id: int | None = None
    fixed_subject: SubjectBrief | None = None


# --------------------------------------------------------------------------- Section


class AllowedRoomsPayload(BaseModel):
    """Replace a subject's explicit room whitelist. Empty restores capability
    matching (capacity, room type, lab type)."""

    room_ids: list[int] = []


class SectionGroupBase(BaseModel):
    """A lab batch within a section - G1, G2 and so on. Legacy; not scheduled.

    Captured and displayed, never read by the solver. The representation that
    IS scheduled is a section with `parent_section_id` set. See
    models.SectionGroup for which to use when.
    """

    group_code: Annotated[str, Field(min_length=1, max_length=20)]
    strength: Annotated[int, Field(gt=0, le=1000)]


class SectionGroupOut(ORMModel, SectionGroupBase):
    id: int
    section_id: int


class SectionBase(BaseModel):
    academic_context_id: int
    section_number: ShortStr
    strength: Annotated[int, Field(gt=0, le=1000)]
    # The admission cohort, e.g. "BCA-2024". A label, not a scheduling scope.
    batch: Annotated[str, Field(max_length=40)] | None = None
    is_active: bool = True


class SectionCreate(SectionBase):
    pass


class SectionUpdate(BaseModel):
    section_number: ShortStr | None = None
    strength: Annotated[int, Field(gt=0, le=1000)] | None = None
    batch: Annotated[str, Field(max_length=40)] | None = None
    is_active: bool | None = None


class SectionOut(ORMModel):
    id: int
    academic_context_id: int
    academic_context: AcademicContextOut
    section_number: str
    strength: int
    batch: str | None = None
    is_active: bool
    subjects: list[SubjectBrief] = []
    # Legacy, and not scheduled - see SectionGroupBase.
    groups: list[SectionGroupOut] = []
    # Set when this section is a lab group of another one. The number comes
    # along because every screen that shows this wants to say "Group of 2401",
    # and an id alone would make each of them fetch the parent to find out.
    parent_section_id: int | None = None
    parent_section_number: str | None = None


# ------------------------------------------------------- Phase 9 entity detail
#
# One "/detail" endpoint per entity, additive to the plain CRUD *Out models
# above. Deliberately lean: the grid itself is already served by
# /api/timetable/{perspective}/{id} and availability by /api/availability/* -
# duplicating either here would create a second source of truth. What these
# add is what nothing else already computes: cross-context assignments,
# workload, and live occupancy.


class TimeSlotBrief(ORMModel):
    id: int
    day: str
    day_index: int
    period_index: int
    start_time: dt.time
    end_time: dt.time


class SectionBrief(ORMModel):
    id: int
    section_number: str
    strength: int
    academic_context_id: int
    academic_context_label: str


class SectionRowBrief(ORMModel):
    """A section as a picker sees it.

    Distinct from `SectionBrief`, which carries `academic_context_label` and so
    needs the context relationship loaded - the one thing a brief listing
    exists to avoid.
    """

    id: int
    section_number: str
    strength: int
    academic_context_id: int


class AssignmentBrief(BaseModel):
    """One (context, section, subject) persistent assignment - the semester-
    level faculty/room decision, not a single weekly occurrence."""

    academic_context_id: int
    academic_context_label: str
    section_id: int
    section_number: str
    subject_id: int
    subject_code: str
    subject_name: str
    periods_per_week: int
    faculty_id: int | None
    faculty_name: str | None
    room_id: int | None
    room_number: str | None
    locked: bool


class CurrentStatusOut(BaseModel):
    """What's true about this resource right now, derived from published runs
    and the live server clock - never fabricated, absent entirely outside
    teaching hours or when no published run exists."""

    state: Literal["occupied", "free", "inactive", "unknown"]
    detail: str


class FacultyDetailOut(ORMModel):
    id: int
    name: str
    faculty_code: str
    department: str | None
    is_active: bool
    subjects: list[SubjectBrief]
    assignments: list[AssignmentBrief]
    periods_per_week: int
    sections_taught: int
    blocked_slots: list[TimeSlotBrief]
    current_status: CurrentStatusOut


class SubjectDetailOut(ORMModel):
    id: int
    name: str
    code: str
    type: SubjectType
    session_length_hours: int
    sessions_per_week: int
    periods_per_week: int
    required_lab_type: str | None
    fixed_room: RoomBrief | None
    allowed_rooms: list[RoomBrief]
    eligible_faculty: list[FacultyBrief]
    sections: list[SectionBrief]
    assignments: list[AssignmentBrief]
    is_active: bool


class SectionDetailOut(ORMModel):
    id: int
    section_number: str
    strength: int
    is_active: bool
    academic_context: AcademicContextOut
    subjects: list[SubjectBrief]
    assignments: list[AssignmentBrief]
    rooms_used: list[RoomBrief]
    blocked_slots: list[TimeSlotBrief]


class RoomUtilizationOut(BaseModel):
    occupied_periods: int
    teachable_periods: int
    percent: float


class RoomDetailOut(ORMModel):
    id: int
    room_number: str
    block: str
    floor: str | None
    capacity: int
    room_type: RoomType
    lab_type: str | None
    is_active: bool
    fixed_subject: SubjectBrief | None
    blocked_slots: list[TimeSlotBrief]
    utilization: RoomUtilizationOut
    current_status: CurrentStatusOut


# --------------------------------------------------------------------------- TimeSlot


class TimeSlotBase(BaseModel):
    day: ShortStr
    day_index: Annotated[int, Field(ge=0, le=6)]
    period_index: Annotated[int, Field(ge=0, le=20)]
    start_time: dt.time
    end_time: dt.time
    is_lunch: bool = False


class TimeSlotCreate(TimeSlotBase):
    pass


class TimeSlotUpdate(BaseModel):
    day: ShortStr | None = None
    day_index: Annotated[int, Field(ge=0, le=6)] | None = None
    period_index: Annotated[int, Field(ge=0, le=20)] | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    is_lunch: bool | None = None


class TimeSlotOut(ORMModel):
    id: int
    day: str
    day_index: int
    period_index: int
    start_time: dt.time
    end_time: dt.time
    is_lunch: bool


class TimeSlotSeedRequest(BaseModel):
    """Generate a fresh weekly grid. Replaces any existing slots.

    Every generated slot is teachable. Lunch is a per-slot toggle set afterwards
    on the Time Slots page, so the generator never decides where the break goes.
    Defaults to Monday-Friday (the college's working week); pass an explicit `days`
    list to include Saturday if a future configuration needs it.

    Re-seeding over an existing grid destroys far more than the grid - every
    availability block and every generated assignment references a slot and is
    removed with it - so it requires `confirm`. Seeding an empty grid does not:
    there is nothing to lose, and first-time setup should not need a ceremony.
    """

    days: list[ShortStr] = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    periods: Annotated[int, Field(ge=1, le=15)] = 9
    start_hour: Annotated[int, Field(ge=0, le=23)] = 9
    start_minute: Annotated[int, Field(ge=0, le=59)] = 30
    period_minutes: Annotated[int, Field(ge=5, le=120)] = 50
    confirm: bool = False


class WorkbookSheetOut(BaseModel):
    """What one sheet of an uploaded workbook was taken to be.

    Every sheet is reported, including the ones nothing was read from - a
    silently skipped sheet is exactly the failure this replaces.
    """

    sheet: str
    status: Literal["detected", "ignored", "unknown", "empty"]
    entity: str | None = None
    label: str | None = None
    row_count: int = 0
    reason: str = ""
    confidence: float = 0.0
    unrecognised_columns: list[str] = []


class WorkbookProblemOut(BaseModel):
    """One thing wrong, located precisely enough to go and fix it."""

    sheet: str
    row_number: int
    identity: str
    message: str
    column: str | None = None
    suggestion: str | None = None


class WorkbookSheetOutcomeOut(BaseModel):
    """What one sheet would do if the import were applied."""

    sheet: str
    entity: str
    label: str
    creates: int
    updates: int
    unchanged: int
    invalid: int
    duplicates: int
    problems: list[WorkbookProblemOut] = []
    notes: list[str] = []


class WorkbookDecisionOut(BaseModel):
    """Something consequential the workbook implies, stated as its own choice."""

    kind: str
    label: str
    detail: str
    destructive: bool = False


class WorkbookPreviewOut(BaseModel):
    """The whole picture before anything is written."""

    filename: str
    sheets: list[WorkbookSheetOut]
    outcomes: list[WorkbookSheetOutcomeOut]
    decisions: list[WorkbookDecisionOut] = []
    blockers: list[str] = []
    counts: dict[str, int]
    has_errors: bool
    can_apply: bool
    committed: bool = False


class InfrastructureOut(BaseModel):
    """The saved room list, as the dashboard shows it."""

    available: bool
    filename: str | None = None
    uploaded_at: dt.datetime | None = None
    summary: dict[str, int] = {}
    # Timetables built on these rooms: what replacing or clearing them removes.
    timetables: int = 0


class InfrastructurePreviewOut(WorkbookPreviewOut):
    """What saving a room list would do. Written only when `committed`."""

    notes: list[str] = []
    summary: dict[str, int] = {}
    room_changes: list["TeacherRoomChangeOut"] = []
    # A different room list is saved now, so this one replaces it.
    replaces: bool = False
    # Room for room the one already saved: saving changes nothing.
    unchanged: bool = False
    timetables_removed: int = 0


class ClearPreviewOut(BaseModel):
    what: Literal["load", "infrastructure", "both"]
    confirmation: str
    counts: dict[str, int]
    total: int


class ClearRequest(BaseModel):
    what: Literal["load", "infrastructure", "both"]
    confirm: str


class ClearResultOut(BaseModel):
    what: Literal["load", "infrastructure", "both"]
    deleted: dict[str, int]
    total_rows: int


class TeacherImportPreviewOut(WorkbookPreviewOut):
    """A workbook preview, plus what had to be inferred to produce it.

    `warnings` are things worth checking before applying - a section's groups
    that do not add up to its size. `inferences` are how the file was read
    where it does not say outright - that 24011 is a lab group of 2401 -
    which is expected, and said so the reader can check it, not as a problem.
    """

    warnings: list[str] = []
    notes: list[str] = []
    inferences: list[str] = []
    summary: dict[str, int] = {}
    # Existing rooms the room list describes differently: one entry per room,
    # each with the fields that would change and their before/after values.
    room_changes: list[TeacherRoomChangeOut] = []
    # The full pre-generation check, run on the imported data. On a preview it
    # runs inside a transaction that is rolled back; null when the files
    # themselves have errors and there is nothing coherent to check.
    readiness: TeacherReadinessOut | None = None
    academic_context_id: int | None = None
    # The dataset this upload becomes, as people read it.
    dataset_label: str | None = None


class TeacherRoomFieldChangeOut(BaseModel):
    field: str
    label: str
    before: str
    after: str


class TeacherRoomChangeOut(BaseModel):
    room: str
    changes: list[TeacherRoomFieldChangeOut]


class TeacherReadinessItemOut(BaseModel):
    label: str
    detail: str


class TeacherUnassignableOut(BaseModel):
    """A class no room in the room list can take, and exactly why."""

    subject_code: str
    subject_name: str
    section: str
    strength: int
    byod: bool
    type: str
    reason: str


class TeacherReadinessOut(BaseModel):
    ready: bool
    blockers: list[TeacherReadinessItemOut] = []
    warnings: list[TeacherReadinessItemOut] = []
    # Classes that cannot get a room at all, one entry each, in the form a
    # teacher can act on - rather than folded into a blocker sentence.
    unassignable: list[TeacherUnassignableOut] = []


class RunCheckOut(BaseModel):
    """A run re-checked against every hard rule, straight from its rows."""

    run_id: int
    violations: list[str]
    count: int
    # The longest run of back-to-back periods any teacher has, and the most
    # allowed - so a result can say "longest teaching run: 5 of at most 6".
    longest_faculty_run: int = 0
    max_consecutive: int = 0


class EntityHealthOut(BaseModel):
    """One entity's headline count, and how many of its rows need attention."""

    key: str
    label: str
    count: int
    issues: int = 0
    issue_label: str | None = None
    detail: str | None = None


class DataSummaryOut(BaseModel):
    """What the data workspace shows before anyone opens an entity page.

    Counts and plain gaps only - `/api/validate` remains the single authority
    on whether a timetable can actually be generated.
    """

    academic_context_id: int | None
    entities: list[EntityHealthOut]
    total_issues: int


class GridReplacementImpactOut(BaseModel):
    """What re-seeding the grid would delete, counted before anything is.

    Carried in the 409 that refuses an unconfirmed replacement, so the refusal
    can state the actual cost rather than a general warning. The success
    response stays a plain slot list, unchanged for existing clients.
    """

    timeslots: int
    faculty_unavailability: int
    section_unavailability: int
    room_unavailability: int
    assignments: int
    runs_affected: int
    published_runs_affected: int


# --------------------------------------------------------------------------- Mapping payloads


class SubjectIdPayload(BaseModel):
    subject_id: int


class OptionalSubjectIdPayload(BaseModel):
    subject_id: int | None = None


class RoomIdPayload(BaseModel):
    room_id: int | None = None


class MessageOut(BaseModel):
    detail: str


# --------------------------------------------------------------------- Availability


class UnavailabilityCreate(BaseModel):
    timeslot_id: int
    reason: Annotated[str, Field(max_length=200)] | None = None


class FacultyUnavailabilityOut(ORMModel):
    id: int
    faculty_id: int
    timeslot_id: int
    timeslot: TimeSlotOut
    reason: str | None = None


class SectionUnavailabilityOut(ORMModel):
    id: int
    section_id: int
    timeslot_id: int
    timeslot: TimeSlotOut
    reason: str | None = None


class RoomUnavailabilityOut(ORMModel):
    id: int
    room_id: int
    timeslot_id: int
    timeslot: TimeSlotOut
    reason: str | None = None


# ----------------------------------------------------------- Validation report


class CheckOut(ORMModel):
    id: str
    label: str
    ok: bool
    detail: str
    severity: Literal["blocker", "warning"]
    offenders: list[str] = []


class ValidationReportOut(ORMModel):
    ready: bool
    blocker_count: int
    warning_count: int
    checks: list[CheckOut]
    stats: dict[str, int]


# ------------------------------------------------------- Phase 9 bulk import


class BulkFieldChangeOut(BaseModel):
    field: str
    old: str
    new: str


class BulkRowResultOut(BaseModel):
    row_number: int
    identity: str
    verdict: Literal["create", "update", "unchanged", "invalid", "duplicate"]
    message: str
    changes: list[BulkFieldChangeOut]


class BulkDeactivationOut(BaseModel):
    # None for junction/mapping and availability adapters, which have no
    # surrogate integer PK to report - the frontend keys these lists by
    # position/identity, not by this id.
    id: int | None = None
    identity: str


class BulkImportPreviewOut(BaseModel):
    entity: str
    mode: str
    filename: str
    columns: list[str]
    rows: list[BulkRowResultOut]
    deactivations: list[BulkDeactivationOut]
    counts: dict[str, int]
    has_errors: bool
    can_apply: bool
    committed: bool


class BulkEntitySpecOut(BaseModel):
    key: str
    label: str
    columns: list[str]
    required: list[str]
    description: str
    sample: list[dict[str, str]]


class ImportHistoryOut(ORMModel):
    id: int
    entity: str
    filename: str
    mode: str
    rows_processed: int
    rows_created: int
    rows_updated: int
    rows_unchanged: int
    rows_rejected: int
    rows_deactivated: int
    status: str
    message: str | None
    actor: str | None
    created_at: dt.datetime


# ------------------------------------------------------------ Generation runs


class GenerateRequest(BaseModel):
    """Every generation is scoped to one academic context. Sections default to
    all sections within it. Weights default to the configured values."""

    academic_context_id: int
    section_ids: list[int] | None = None
    max_seconds: Annotated[float, Field(gt=0, le=600)] | None = None
    weights: dict[str, int] | None = None
    # When True (default), a pair with an existing SectionSubjectAssignment is
    # restricted to that faculty/room rather than re-decided from scratch.
    respect_persisted: bool = True


class RunSummaryOut(ORMModel):
    id: int
    academic_context_id: int
    version: int
    created_at: dt.datetime
    status: str
    publish_status: PublishStatus
    published_at: dt.datetime | None = None
    objective_value: float | None = None
    solve_time_seconds: float | None = None
    message: str | None = None


class RunOut(RunSummaryOut):
    weights: dict[str, int] = {}
    penalties: dict[str, int] = {}
    assignment_count: int = 0
    # False means valid but the time limit stopped the search early.
    proven_optimal: bool = False
    # Things that happened during the solve which someone should know about
    # but which did not stop it. A lock that could not be honoured is the one
    # that matters: it means this timetable disagrees with an instruction that
    # was given deliberately, and saying nothing is how that goes unnoticed.
    warnings: list[str] = []


class ErrorOut(BaseModel):
    """Structured error body - never a bare stack trace (spec #42)."""

    code: str
    message: str
    likely_blockers: list[str] = []


# ---------------------------------------------------------- Locking / publishing


class LockAssignmentPayload(BaseModel):
    section_id: int
    subject_id: int


class LockPreviewPayload(BaseModel):
    """Which pair, and in which direction. `session_type` narrows the preview
    to one component of a subject that has both; omitted, it describes the
    whole subject, which is what locking does by default."""

    section_id: int
    subject_id: int
    lock: bool
    session_type: SessionType | None = None


class SectionSubjectAssignmentOut(ORMModel):
    id: int
    academic_context_id: int
    section_id: int
    subject_id: int
    faculty_id: int | None = None
    room_id: int | None = None
    locked: bool
    faculty: FacultyBrief | None = None
    room: RoomBrief | None = None


# ---------------------------------------------------------------- Grid views


class GridCellOut(BaseModel):
    period_index: int
    is_lunch: bool = False
    # Periods this cell spans. 1 = single, >1 = merged block start,
    # 0 = a continuation already covered by an earlier cell's span.
    span: int = 1
    continuation: bool = False
    # The underlying Assignment row this cell derives from - the anchor a
    # manual edit (POST /api/assignments/{id}/...) operates on. None for an
    # empty cell.
    assignment_id: int | None = None
    subject_id: int | None = None
    subject_code: str | None = None
    subject_name: str | None = None
    subject_type: SubjectType | None = None
    # Which component this class is. Only worth showing when a subject has
    # both, which is why the UI badges it conditionally.
    session_type: SessionType | None = None
    faculty_id: int | None = None
    faculty_name: str | None = None
    faculty_code: str | None = None
    room_id: int | None = None
    room_number: str | None = None
    block: str | None = None
    section_id: int | None = None
    section_number: str | None = None
    block_id: int | None = None
    locked: bool = False


class GridRowOut(BaseModel):
    day_index: int
    day: str
    cells: list[GridCellOut]


class GridOut(BaseModel):
    run_id: int
    run_status: str
    # Which version this is, and whether it is the one in force. A timetable
    # read without knowing that is a timetable you cannot act on: the draft and
    # the published version look identical on screen and differ in authority.
    version: int
    publish_status: str
    perspective: Literal["section", "faculty", "room", "master"]
    title: str
    subtitle: str
    periods: list[int]
    period_labels: dict[int, str]
    rows: list[GridRowOut]
    total_periods: int


class MasterRowOut(BaseModel):
    """One flattened scheduled class, for the filterable master view."""

    assignment_id: int
    timeslot_id: int
    day: str
    day_index: int
    period_index: int
    start_time: dt.time
    end_time: dt.time
    section_id: int
    section_number: str
    academic_year: str
    semester: int
    program: str
    department: str
    subject_id: int
    subject_code: str
    subject_name: str
    subject_type: SubjectType
    session_type: SessionType = "L"
    faculty_id: int
    faculty_name: str
    faculty_code: str
    room_id: int
    room_number: str
    block: str
    locked: bool
    # A class of two or three periods is that many rows sharing a block_id.
    block_id: int | None = None
    strength: int = 0
    class_type: str = "Theory"
    byod_required: bool = False
    room_code: str = ""
    floor: str = ""
    room_capacity: int = 0
    room_type: str = ""
    room_byod: bool = False


class MasterOptionOut(BaseModel):
    id: int | str
    label: str


class MasterOptionsOut(BaseModel):
    sections: list[MasterOptionOut] = []
    faculty: list[MasterOptionOut] = []
    subjects: list[MasterOptionOut] = []
    rooms: list[MasterOptionOut] = []
    days: list[MasterOptionOut] = []
    blocks: list[str] = []
    floors: list[str] = []
    room_types: list[MasterOptionOut] = []


class MasterTimetableOut(BaseModel):
    run_id: int
    run_status: str
    total: int
    rows: list[MasterRowOut]
    version: int | None = None
    publish_status: str | None = None
    dataset: str | None = None
    options: MasterOptionsOut | None = None


# --------------------------------------------------------- Phase 6: manual edits


class MoveRequest(BaseModel):
    target_timeslot_id: int
    lock_after: bool = True
    actor: Annotated[str, Field(max_length=120)] | None = None
    reason: Annotated[str, Field(max_length=500)] | None = None


class RoomChangeRequest(BaseModel):
    room_id: int
    lock_after: bool = True
    actor: Annotated[str, Field(max_length=120)] | None = None
    reason: Annotated[str, Field(max_length=500)] | None = None


class FacultyChangeRequest(BaseModel):
    faculty_id: int
    lock_after: bool = True
    actor: Annotated[str, Field(max_length=120)] | None = None
    reason: Annotated[str, Field(max_length=500)] | None = None


class ValidationResultOut(BaseModel):
    ok: bool
    issues: list[str] = []


class ChangeOutcomeOut(BaseModel):
    ok: bool
    issues: list[str] = []
    change_history_id: int | None = None


class ChangeHistoryOut(ORMModel):
    id: int
    run_id: int
    section_id: int
    subject_id: int
    change_type: str
    old_value: str
    new_value: str
    actor: str | None = None
    reason: str | None = None
    created_at: dt.datetime
    # Phase 10: the real, authenticated identity when one made this change -
    # None for pre-auth history or any call made without a token.
    user_id: int | None = None
    user_role: str | None = None
    user_username: str | None = None


# ----------------------------------------------------- Phase 7: availability queries


class AvailableRoomOut(ORMModel):
    id: int
    room_number: str
    block: str
    floor: str | None = None
    # Room numbers repeat across buildings, so 101 alone names several rooms.
    room_code: str | None = None
    capacity: int
    room_type: RoomType
    lab_type: str | None = None
    # What makes a room usable for a practical. These are hard constraints in
    # the solver, so somebody choosing a room by hand is choosing against the
    # same criteria and should be able to see them.
    byod: bool = False
    charging: bool = False
    charging_sockets: int | None = None


class AvailableFacultyOut(ORMModel):
    id: int
    name: str
    faculty_code: str
    department: str | None = None


class SlotAvailabilityOut(BaseModel):
    """One day/slot's occupancy snapshot for the master availability view."""

    timeslot_id: int
    day: str
    day_index: int
    period_index: int
    occupied_faculty_ids: list[int]
    available_faculty_ids: list[int]
    occupied_room_ids: list[int]
    available_room_ids: list[int]
    occupied_section_ids: list[int]
    available_section_ids: list[int]


class VersionDiffRowOut(BaseModel):
    section_number: str
    subject_code: str
    change: Literal["added", "removed", "moved", "faculty_changed", "room_changed"]
    detail: str


class VersionDiffOut(BaseModel):
    from_run_id: int
    to_run_id: int
    added: int
    removed: int
    moved: int
    faculty_changed: int
    room_changed: int
    rows: list[VersionDiffRowOut]


# ----------------------------------------------------------- Phase 10: auth

UserRole = Literal["admin", "scheduler", "faculty", "viewer"]


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(ORMModel):
    id: int
    username: str
    email: str | None
    role: UserRole
    is_active: bool
    faculty_id: int | None
    created_at: dt.datetime
    last_login_at: dt.datetime | None


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    username: Annotated[str, Field(min_length=3, max_length=60)]
    password: Annotated[str, Field(min_length=8, max_length=200)]
    email: Annotated[str, Field(max_length=200)] | None = None
    role: UserRole = "viewer"
    faculty_id: int | None = None


class UserUpdate(BaseModel):
    email: Annotated[str, Field(max_length=200)] | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    faculty_id: int | None = None
    password: Annotated[str, Field(min_length=8, max_length=200)] | None = None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class AssistantChatRequest(BaseModel):
    message: str
    academic_context_id: int | None = None
    run_id: int | None = None
    history: list[ChatMessage] = []


class AssistantChatResponse(BaseModel):
    reply: str
    suggested_actions: list[str] = []
    source: str = "gemini"

