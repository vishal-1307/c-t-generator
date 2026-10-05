// Mirrors backend/app/schemas.py. Keep the two in sync.

/** "mixed" means the subject has both a lecture and a practical component,
 *  scheduled independently and usually in different kinds of room. */
export type SubjectType = "theory" | "practical" | "mixed";

/** Which component of a subject a scheduled class belongs to. */
export type SessionType = "L" | "P";
export type RoomType = "theory" | "lab" | "faculty";
export type PublishStatus = "DRAFT" | "VALIDATED" | "PUBLISHED" | "ARCHIVED";

export interface SubjectBrief {
  id: number;
  name: string;
  code: string;
  type: SubjectType;
}

export interface FacultyBrief {
  id: number;
  name: string;
  faculty_code: string;
}

export interface RoomBrief {
  id: number;
  room_number: string;
  block: string;
  floor: string | null;
  room_code: string | null;
  capacity: number;
  room_type: RoomType;
  lab_type: string | null;
  byod: boolean;
  charging: boolean;
}

export interface AcademicContext {
  id: number;
  academic_year: string;
  semester: number;
  program: string;
  department: string;
  label: string;
}

export interface Faculty {
  id: number;
  name: string;
  faculty_code: string;
  department: string | null;
  subjects: SubjectBrief[];
}

export interface Subject {
  id: number;
  name: string;
  code: string;
  type: SubjectType;
  session_length_hours: number;
  sessions_per_week: number;
  /** Total across every component. For a mixed subject that is lecture +
   *  practical; for a pure one it is sessions x length, as before. */
  periods_per_week: number;
  /** Set only when type === "mixed" - the two components have their own
   *  weekly loads because they are scheduled separately. */
  lecture_sessions_per_week: number | null;
  lecture_session_length: number | null;
  practical_sessions_per_week: number | null;
  practical_session_length: number | null;
  /** Room capabilities this subject needs. A software practical can run in a
   *  classroom with power and students' own machines; an electronics one
   *  needs the lab itself. */
  byod_required: boolean;
  charging_required: boolean;
  /** Practicals only - matched against a lab room's lab_type. */
  required_lab_type: string | null;
  fixed_room_id: number | null;
  fixed_room: RoomBrief | null;
  allowed_rooms: RoomBrief[];
  faculties: FacultyBrief[];
}

export interface Room {
  id: number;
  /** Unique within its block, not globally - two buildings can both have a
   *  "101". Use room_code for a label that is unambiguous on its own. */
  room_number: string;
  block: string;
  floor: string | null;
  /** The institution-wide label, e.g. "36-101". */
  room_code: string | null;
  capacity: number;
  /** Whether students can work on their own machines here, and whether they
   *  can charge them - what lets a software practical use a classroom. */
  byod: boolean;
  charging: boolean;
  charging_sockets: number | null;
  room_type: RoomType;
  /** Only meaningful when room_type === "lab". */
  lab_type: string | null;
  is_active: boolean;
  fixed_subject_id: number | null;
  fixed_subject: SubjectBrief | null;
}

/** A lab batch within a section - G1, G2 and so on.
 *
 *  Recorded, not yet scheduled: the solver still treats a section as one
 *  indivisible unit, so a practical needs a room seating all of it. */
export interface SectionGroup {
  id: number;
  section_id: number;
  group_code: string;
  strength: number;
}

export interface Section {
  id: number;
  academic_context_id: number;
  academic_context: AcademicContext;
  section_number: string;
  strength: number;
  /** Admission cohort, e.g. "BCA-2024". A label, not a scheduling scope. */
  batch: string | null;
  subjects: SubjectBrief[];
  groups: SectionGroup[];
}

export interface TimeSlot {
  id: number;
  day: string;
  day_index: number;
  period_index: number;
  start_time: string;
  end_time: string;
  is_lunch: boolean;
}

export interface SeedGridRequest {
  days?: string[];
  periods?: number;
  start_hour?: number;
  start_minute?: number;
  period_minutes?: number;
  /** Required to replace an existing grid. Seeding an empty one does not need it. */
  confirm?: boolean;
}

/** What replacing the grid would delete. Returned with the 409 that refuses an
 *  unconfirmed replacement, so the dialog can state the real cost. */
export interface GridReplacementImpact {
  timeslots: number;
  faculty_unavailability: number;
  section_unavailability: number;
  room_unavailability: number;
  assignments: number;
  runs_affected: number;
  published_runs_affected: number;
}

export interface UnavailabilityCreate {
  timeslot_id: number;
  reason?: string | null;
}

export interface FacultyUnavailability {
  id: number;
  faculty_id: number;
  timeslot_id: number;
  timeslot: TimeSlot;
  reason: string | null;
}

export interface SectionUnavailability {
  id: number;
  section_id: number;
  timeslot_id: number;
  timeslot: TimeSlot;
  reason: string | null;
}

export interface RoomUnavailability {
  id: number;
  room_id: number;
  timeslot_id: number;
  timeslot: TimeSlot;
  reason: string | null;
}

export interface ReadinessCheck {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
  severity: "blocker" | "warning";
  offenders: string[];
}

export interface ValidationReport {
  ready: boolean;
  blocker_count: number;
  warning_count: number;
  checks: ReadinessCheck[];
  stats: Record<string, number>;
}

export type RunStatus =
  | "RUNNING"
  | "OPTIMAL"
  | "FEASIBLE"
  | "PARTIAL"
  | "INFEASIBLE"
  | "TIMEOUT";

export interface RunSummary {
  id: number;
  academic_context_id: number;
  version: number;
  created_at: string;
  status: RunStatus;
  publish_status: PublishStatus;
  published_at: string | null;
  objective_value: number | null;
  solve_time_seconds: number | null;
  message: string | null;
}

export interface Run extends RunSummary {
  weights: Record<string, number>;
  penalties: Record<string, number>;
  assignment_count: number;
  /** False means valid, but the time limit stopped the search early. */
  proven_optimal: boolean;
  /** Things that happened during the solve which somebody should know about
   *  but which did not stop it. A lock that could not be honoured is the one
   *  that matters: the timetable disagrees with a deliberate instruction. */
  warnings: string[];
}

export interface SectionSubjectAssignment {
  id: number;
  academic_context_id: number;
  section_id: number;
  subject_id: number;
  faculty_id: number | null;
  room_id: number | null;
  locked: boolean;
  updated_at: string;
}

export interface LockAssignmentPayload {
  section_id: number;
  subject_id: number;
}

export interface GridCell {
  period_index: number;
  is_lunch: boolean;
  /** 1 = single period, >1 = start of a merged block, 0 = continuation. */
  span: number;
  continuation: boolean;
  /** The Assignment row behind this cell - the anchor for a manual edit. */
  assignment_id: number | null;
  subject_id: number | null;
  subject_code: string | null;
  subject_name: string | null;
  subject_type: SubjectType | null;
  /** Which component this class is. Worth showing only when a subject has
   *  both a lecture and a practical. */
  session_type: SessionType | null;
  faculty_id: number | null;
  faculty_name: string | null;
  faculty_code: string | null;
  room_id: number | null;
  room_number: string | null;
  block: string | null;
  section_id: number | null;
  section_number: string | null;
  block_id: number | null;
  locked: boolean;
}

export interface GridRow {
  day_index: number;
  day: string;
  cells: GridCell[];
}

export interface Grid {
  run_id: number;
  run_status: RunStatus;
  /** Which version this is, and whether it is the one in force. A draft and
   *  the published timetable look identical on screen and differ in
   *  authority, so both views say which they are showing. */
  version: number;
  publish_status: string;
  perspective: "section" | "faculty" | "room";
  title: string;
  subtitle: string;
  periods: number[];
  period_labels: Record<number, string>;
  rows: GridRow[];
  total_periods: number;
}

export interface MasterRow {
  assignment_id: number;
  timeslot_id: number;
  day: string;
  day_index: number;
  period_index: number;
  start_time: string;
  end_time: string;
  section_id: number;
  section_number: string;
  academic_year: string;
  semester: number;
  program: string;
  department: string;
  subject_id: number;
  subject_code: string;
  subject_name: string;
  subject_type: SubjectType;
  session_type: SessionType;
  faculty_id: number;
  faculty_name: string;
  faculty_code: string;
  room_id: number;
  room_number: string;
  block: string;
  locked: boolean;
  /** A class of two or three periods is that many rows sharing a block_id. */
  block_id: number | null;
  strength: number;
  class_type: string;
  byod_required: boolean;
  /** "36-309": the room as a person names it. */
  room_code: string;
  floor: string;
  room_capacity: number;
  room_type: string;
  room_byod: boolean;
}

export interface MasterOption {
  id: number | string;
  label: string;
}

export interface MasterTimetable {
  run_id: number;
  run_status: RunStatus;
  total: number;
  rows: MasterRow[];
  version: number | null;
  publish_status: PublishStatus | null;
  dataset: string | null;
  /** What each filter can be set to: the values that occur in this run. */
  options: {
    sections: MasterOption[];
    faculty: MasterOption[];
    subjects: MasterOption[];
    rooms: MasterOption[];
    days: MasterOption[];
    blocks: string[];
    floors: string[];
    room_types: MasterOption[];
  } | null;
}

/* --------------------------------------------- manual edits (Phase 6 APIs) */

export interface ManualValidationResult {
  ok: boolean;
  issues: string[];
}

export interface ChangeOutcome {
  ok: boolean;
  issues: string[];
  change_history_id: number | null;
}

export type ChangeType = "move" | "room" | "faculty" | "lock" | "unlock";

export interface ChangeHistoryEntry {
  id: number;
  run_id: number;
  section_id: number;
  subject_id: number;
  change_type: ChangeType;
  old_value: string;
  new_value: string;
  actor: string | null;
  reason: string | null;
  created_at: string;
}

/* ------------------------------------------ availability queries (Phase 7) */

export interface AvailableRoom {
  id: number;
  room_number: string;
  block: string;
  floor: string | null;
  /** Room numbers repeat across buildings; this does not. */
  room_code: string | null;
  capacity: number;
  room_type: RoomType;
  lab_type: string | null;
  /** What makes a room usable for a practical - hard constraints in the
   *  solver, and therefore the same criteria a human is choosing against. */
  byod: boolean;
  charging: boolean;
  charging_sockets: number | null;
}

export interface AvailableFaculty {
  id: number;
  name: string;
  faculty_code: string;
  department: string | null;
}

export interface SlotAvailability {
  timeslot_id: number;
  day: string;
  day_index: number;
  period_index: number;
  occupied_faculty_ids: number[];
  available_faculty_ids: number[];
  occupied_room_ids: number[];
  available_room_ids: number[];
  occupied_section_ids: number[];
  available_section_ids: number[];
}

export interface SectionAvailabilityCheck {
  available: boolean;
  issues: string[];
}

/* ------------------------------------------- version comparison (Phase 7) */

export type DiffChange =
  | "added"
  | "removed"
  | "moved"
  | "faculty_changed"
  | "room_changed";

export interface VersionDiffRow {
  section_number: string;
  subject_code: string;
  change: DiffChange;
  detail: string;
}

export interface VersionDiff {
  from_run_id: number;
  to_run_id: number;
  added: number;
  removed: number;
  moved: number;
  faculty_changed: number;
  room_changed: number;
  rows: VersionDiffRow[];
}

/* ------------------------------------------------- entity detail (Phase 9) */

export interface TimeSlotBrief {
  id: number;
  day: string;
  day_index: number;
  period_index: number;
  start_time: string;
  end_time: string;
}

export interface SectionBrief {
  id: number;
  section_number: string;
  strength: number;
  academic_context_id: number;
  academic_context_label: string;
}

export interface AssignmentBrief {
  academic_context_id: number;
  academic_context_label: string;
  section_id: number;
  section_number: string;
  subject_id: number;
  subject_code: string;
  subject_name: string;
  periods_per_week: number;
  faculty_id: number | null;
  faculty_name: string | null;
  room_id: number | null;
  room_number: string | null;
  locked: boolean;
}

export type CurrentState = "occupied" | "free" | "inactive" | "unknown";

export interface CurrentStatus {
  state: CurrentState;
  detail: string;
}

export interface FacultyDetail {
  id: number;
  name: string;
  faculty_code: string;
  department: string | null;
  subjects: SubjectBrief[];
  assignments: AssignmentBrief[];
  periods_per_week: number;
  sections_taught: number;
  blocked_slots: TimeSlotBrief[];
  current_status: CurrentStatus;
}

export interface SubjectDetail {
  id: number;
  name: string;
  code: string;
  type: SubjectType;
  session_length_hours: number;
  sessions_per_week: number;
  periods_per_week: number;
  required_lab_type: string | null;
  fixed_room: RoomBrief | null;
  allowed_rooms: RoomBrief[];
  eligible_faculty: FacultyBrief[];
  sections: SectionBrief[];
  assignments: AssignmentBrief[];
}

export interface SectionDetail {
  id: number;
  section_number: string;
  strength: number;
  academic_context: AcademicContext;
  subjects: SubjectBrief[];
  assignments: AssignmentBrief[];
  rooms_used: RoomBrief[];
  blocked_slots: TimeSlotBrief[];
}

export interface RoomUtilization {
  occupied_periods: number;
  teachable_periods: number;
  percent: number;
}

export interface RoomDetail {
  id: number;
  room_number: string;
  block: string;
  floor: string | null;
  capacity: number;
  room_type: RoomType;
  lab_type: string | null;
  is_active: boolean;
  fixed_subject: SubjectBrief | null;
  blocked_slots: TimeSlotBrief[];
  utilization: RoomUtilization;
  current_status: CurrentStatus;
}

/* --------------------------------------------------------- auth (Phase 10) */

export type UserRole = "admin" | "scheduler" | "faculty" | "viewer";

export interface User {
  id: number;
  username: string;
  email: string | null;
  role: UserRole;
  is_active: boolean;
  faculty_id: number | null;
  created_at: string;
  last_login_at: string | null;
}

export interface Token {
  access_token: string;
  token_type: "bearer";
  user: User;
}

/* ------------------------------------------------------- demo data (Phase 13) */

export interface DemoStatus {
  /** False when demo management is disabled - always so in production. */
  enabled: boolean;
  present: boolean;
  label: string | null;
  academic_context_id: number | null;
  sections: number;
  runs: number;
  detail: string;
}

export interface DemoActionResult {
  ok: boolean;
  counts: Record<string, number>;
  detail: string;
}


/** Query parameters accepted by every paginated list endpoint. */
export interface ListQuery {
  limit?: number;
  offset?: number;
  /** Identity fields only, and none of the row's relationships loaded.
   *  For pickers and typeaheads, where the rest is never read. */
  brief?: boolean;
  /** Case-insensitive substring match over the entity's name and code fields. */
  q?: string;
  /** Column name, optionally prefixed with '-' for descending. */
  sort?: string;
}

/** The envelope a list endpoint returns once any ListQuery parameter is sent. */
export interface Paged<T> {
  total: number;
  rows: T[];
}

/** One entity's headline count and how many of its rows need attention. */
export interface EntityHealth {
  key: string;
  label: string;
  count: number;
  issues: number;
  issue_label?: string | null;
  detail?: string | null;
}

export interface DataSummary {
  academic_context_id: number | null;
  entities: EntityHealth[];
  total_issues: number;
}


/* ------------------------------------------------ whole-workbook import */

/** What one sheet of an uploaded workbook was taken to be.
 *
 *  Every sheet is reported, including ones nothing is read from - a silently
 *  skipped sheet is how a user ends up believing a dataset imported when most
 *  of it did not. */
export interface WorkbookSheet {
  sheet: string;
  status: "detected" | "ignored" | "unknown" | "empty";
  entity: string | null;
  label: string | null;
  row_count: number;
  reason: string;
  confidence: number;
  unrecognised_columns: string[];
}

/** One thing wrong, located precisely enough to go and fix it. */
export interface WorkbookProblem {
  sheet: string;
  row_number: number;
  identity: string;
  message: string;
  column: string | null;
  suggestion: string | null;
}

export interface WorkbookSheetOutcome {
  sheet: string;
  entity: string;
  label: string;
  creates: number;
  updates: number;
  unchanged: number;
  invalid: number;
  duplicates: number;
  problems: WorkbookProblem[];
  notes: string[];
}

/** Something consequential the workbook implies, stated as its own choice -
 *  creating an academic context, or changing the weekly grid. */
export interface WorkbookDecision {
  kind: string;
  label: string;
  detail: string;
  destructive: boolean;
}

export interface WorkbookPreview {
  filename: string;
  sheets: WorkbookSheet[];
  outcomes: WorkbookSheetOutcome[];
  decisions: WorkbookDecision[];
  blockers: string[];
  counts: Record<string, number>;
  has_errors: boolean;
  can_apply: boolean;
  committed: boolean;
}

/** A workbook preview, plus what the teacher import had to conclude.
 *
 *  `warnings` are inferences, not readings - that 24011 is a lab group of
 *  2401, that a section's groups do not add up to its size. Neither stops an
 *  import and both change the timetable, so they belong next to the button.
 *  `notes` are columns the files carry that nothing here reads. */
export interface TeacherImportPreview extends WorkbookPreview {
  warnings: string[];
  notes: string[];
  /** How the file was read where it does not say outright (24011 as a lab
   *  group of 2401). Expected; not a warning. */
  inferences?: string[];
  summary: TeacherImportSummary;
  /** Rooms the database already has that the room list describes
   *  differently. Applying the import changes them, and anything else
   *  scheduled there is judged against the new values. */
  room_changes: RoomChange[];
  /** The full pre-generation check, run on the imported data inside a
   *  transaction that is then rolled back. Null when the files themselves
   *  have errors, since there is nothing coherent to check. */
  readiness: TeacherReadiness | null;
  /** The dataset the files were imported into. Set once `committed`. */
  academic_context_id: number | null;
  /** That dataset as people read it: "Upload 2 · 2026-09-11 · Load". */
  dataset_label: string | null;
}

/** What the two files add up to, counted from the files. */
export interface TeacherImportSummary {
  faculty: number;
  subjects: number;
  sections: number;
  groups: number;
  /** Class sessions a week - a three-period lab is one class. */
  classes: number;
  required_periods: number;
  rooms: number;
  classrooms: number;
  labs: number;
  byod_rooms: number;
}

export interface RoomFieldChange {
  field: "capacity" | "room_type" | "byod";
  label: string;
  before: string;
  after: string;
}

export interface RoomChange {
  room: string;
  changes: RoomFieldChange[];
}

export interface TeacherReadiness {
  ready: boolean;
  blockers: { label: string; detail: string }[];
  warnings: { label: string; detail: string }[];
  /** Classes no room in the room list can take, and the reason. */
  unassignable: Unassignable[];
}

export interface Unassignable {
  subject_code: string;
  subject_name: string;
  section: string;
  strength: number;
  byod: boolean;
  type: "Theory" | "Lab";
  reason: string;
}

/** One class session and the room it was given. */
export interface AllocationRow {
  day: string;
  day_index: number;
  start_time: string;
  end_time: string;
  periods: number;
  faculty_id: string;
  faculty_name: string;
  subject_code: string;
  subject_name: string;
  section: string;
  strength: number;
  class_type: "Theory" | "Lab";
  byod: boolean;
  room: string;
  block: string;
  floor: string;
  room_capacity: number;
  room_type: string;
  room_byod: boolean;
  faculty_pk: number;
  section_pk: number;
  room_pk: number;
}

export interface AllocationFilters {
  run_id?: number;
  /** Which upload's timetable, when no run is named. */
  academic_context_id?: number;
  faculty_id?: number;
  section_id?: number;
  day?: string;
  room_id?: number;
}

export interface Allocation {
  run_id: number;
  run_status: string;
  version: number;
  publish_status?: PublishStatus;
  academic_context_id?: number;
  /** The dataset's newest version, when it is newer than the one shown. */
  newest_version?: number;
  dataset: string;
  created_at: string | null;
  classes: number;
  periods: number;
  rows: AllocationRow[];
  filters: {
    faculty: { id: number; label: string }[];
    sections: { id: number; label: string }[];
    rooms: { id: number; label: string }[];
    days: string[];
  };
}

/** A generated timetable re-checked against every hard rule, from its rows. */
export interface RunCheck {
  run_id: number;
  violations: string[];
  count: number;
  /** The longest run of back-to-back periods any teacher has. */
  longest_faculty_run?: number;
  /** The most allowed (0 = no cap). */
  max_consecutive?: number;
}

/** Which intake the uploaded files describe. Neither file says, and a section
 *  cannot exist without one. */
/** The saved room list. */
export interface Infrastructure {
  available: boolean;
  filename: string | null;
  uploaded_at: string | null;
  summary: Partial<Pick<TeacherImportSummary, "rooms" | "classrooms" | "labs" | "byod_rooms">>;
  /** Timetables built on these rooms - what replacing or clearing them removes. */
  timetables: number;
}

export interface InfrastructurePreview extends WorkbookPreview {
  notes: string[];
  summary: Partial<Pick<TeacherImportSummary, "rooms" | "classrooms" | "labs" | "byod_rooms">>;
  room_changes: RoomChange[];
  /** A different room list is saved now; this one replaces it. */
  replaces: boolean;
  /** Room for room the one already saved. */
  unchanged: boolean;
  timetables_removed: number;
}

export type ClearWhat = "load" | "infrastructure" | "both";

export interface ClearPreview {
  what: ClearWhat;
  /** The exact phrase the clear request must carry. */
  confirmation: string;
  counts: Record<string, number>;
  total: number;
}

export interface ClearResult {
  what: ClearWhat;
  deleted: Record<string, number>;
  total_rows: number;
}

export interface TeacherImportContext {
  academic_year: string;
  semester: number;
  program: string;
  department: string;
}

/** What locking or unlocking a pair would do, described before it happens. */
export interface LockPreview {
  would_change: boolean;
  currently_locked: boolean;
  will_be_locked: boolean;
  summary: string;
  consequence: string;
  /** One entry per component: a mixed subject is two decisions, and locking
   *  the subject locks both. */
  components: {
    session_type: string | null;
    locked: boolean;
    faculty_name: string | null;
    room_number: string | null;
  }[];
}

export interface AssistantChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface AssistantChatResponse {
  reply: string;
  suggested_actions: string[];
  source: string;
}

