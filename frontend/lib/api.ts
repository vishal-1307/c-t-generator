import { describeFailure, type Failure } from "./failures";
import type {
  AcademicContext,
  AvailableFaculty,
  AvailableRoom,
  ChangeHistoryEntry,
  ChangeOutcome,
  DataSummary,
  DemoActionResult,
  DemoStatus,
  Faculty,
  FacultyDetail,
  FacultyUnavailability,
  Grid,
  ListQuery,
  LockPreview,
  ManualValidationResult,
  MasterTimetable,
  Paged,
  Room,
  RoomDetail,
  RoomUnavailability,
  Allocation,
  AllocationFilters,
  Run,
  RunCheck,
  RunSummary,
  Section,
  SectionAvailabilityCheck,
  SectionDetail,
  SectionSubjectAssignment,
  SectionUnavailability,
  SeedGridRequest,
  SlotAvailability,
  Subject,
  SubjectDetail,
  TimeSlot,
  Token,
  UnavailabilityCreate,
  User,
  ValidationReport,
  VersionDiff,
  TeacherImportContext,
  Infrastructure,
  InfrastructurePreview,
  ClearWhat,
  ClearPreview,
  ClearResult,
  TeacherImportPreview,
  AssistantChatMessage,
  AssistantChatResponse,
} from "./types";

/**
 * Where the API lives.
 *
 * `NEXT_PUBLIC_*` is inlined at build time, so an unset variable is baked into
 * the deployed bundle - it cannot be corrected at runtime. That makes the
 * fallback consequential: falling back to `127.0.0.1:8000` in a production
 * build would send every visitor's browser at *their own machine*, producing
 * connection errors that look like the server being down and are extremely
 * confusing to diagnose.
 *
 * So the fallback applies to development only. A production build without the
 * variable set fails loudly and immediately, naming the setting - a deployment
 * that cannot reach its API should say so, not appear broken at random.
 */
const BASE = (() => {
  const configured = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (configured) return configured.replace(/\/+$/, "");
  return "http://127.0.0.1:8000";
})();

/** Surfaces FastAPI's `detail` so forms can show the real reason a write failed. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** The parsed error body, when there was one. Some endpoints send
     *  structured context alongside `detail` - the grid-replacement refusal
     *  sends the counts it would delete - and a dialog should not have to
     *  re-derive that from the sentence. */
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Something went wrong that is about the connection or the session rather
 * than the thing asked for - the server is asleep or restarting, it failed,
 * or the sign-in expired. The notice panel listens here, so every page reports
 * these the same way without each one having to.
 */
type TroubleListener = (failure: Failure) => void;
const troubleListeners = new Set<TroubleListener>();

export function onApiTrouble(listener: TroubleListener): () => void {
  troubleListeners.add(listener);
  return () => {
    troubleListeners.delete(listener);
  };
}

/** The error a failed request throws, with its person-readable message. */
function failed(path: string, status: number, detail?: string, body?: unknown): ApiError {
  // A wrong password is a refusal, not an expired session.
  const signingIn = path === "/api/auth/login";
  const failure: Failure =
    signingIn && status === 401
      ? { kind: "request", message: detail ?? "Incorrect username or password" }
      : describeFailure(status, detail);
  // The sign-in page says these itself, next to the form.
  const quiet = signingIn || path === "/api/health";
  const tell =
    !quiet &&
    (failure.kind === "unreachable" ||
    failure.kind === "server" ||
      (failure.kind === "session" && authToken !== null));
  if (tell) for (const listener of troubleListeners) listener(failure);
  // A refusal keeps the server's own reason; a failure of the connection, the
  // server or the session gets the sentence written for it.
  const message =
    failure.kind === "request" || failure.kind === "busy" ? (detail ?? failure.message) : failure.message;
  return new ApiError(message, status, body);
}

// Phase 10: the current JWT, held module-level so every request() call can
// attach it without every call site (or every component) needing to plumb
// it through. `lib/auth.tsx`'s AuthProvider is the only writer - it sets
// this on login/logout/token-refresh and keeps localStorage as the
// durable copy across page reloads.
let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

function authHeaders(): Record<string, string> {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {};
}

/**
 * A file from the API, fetched rather than navigated to.
 *
 * A plain link to an export sends the tab to the API when the file cannot be
 * made, and leaves the reader looking at a page of JSON with no way back.
 * Fetching it keeps them where they were and lets a failure be said properly.
 */
export async function fetchFile(url: string): Promise<{ blob: Blob; filename: string | null }> {
  const path = url.startsWith(BASE) ? url.slice(BASE.length).split("?")[0] : url;
  let res: Response;
  try {
    res = await fetch(url, { headers: authHeaders(), cache: "no-store" });
  } catch {
    throw failed(path, 0);
  }
  if (!res.ok) {
    const { detail, body } = await extractError(res);
    throw failed(path, res.status, detail, body);
  }
  const disposition = res.headers.get("content-disposition") ?? "";
  const named = /filename="?([^";]+)"?/i.exec(disposition);
  return { blob: await res.blob(), filename: named ? named[1] : null };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...authHeaders(), ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw failed(path, 0);
  }

  if (!res.ok) {
    const { detail, body } = await extractError(res);
    throw failed(path, res.status, detail, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function extractError(
  res: Response,
): Promise<{ detail: string; body?: unknown }> {
  let body: unknown;
  try {
    body = await res.json();
    const detail = (body as { detail?: unknown })?.detail;
    if (typeof detail === "string") return { detail, body };
    // Refusals that carry context alongside their sentence -
    // {message, problems} or {message, timetables} - still have a sentence.
    const message = (detail as { message?: unknown } | undefined)?.message;
    if (typeof message === "string") return { detail: message, body };
    // Pydantic validation errors arrive as a list of {loc, msg}.
    if (Array.isArray(detail)) {
      return {
        detail: detail
          .map((e) => {
            const field = Array.isArray(e.loc) ? e.loc.slice(1).join(".") : "";
            return field ? `${field}: ${e.msg}` : e.msg;
          })
          .join("; "),
        body,
      };
    }
  } catch {
    /* fall through to the status text */
  }
  return { detail: `${res.status} ${res.statusText}`, body };
}

async function uploadTeacherFiles<T>(
  path: string,
  load: File,
  infra: File | null,
  context?: TeacherImportContext,
): Promise<T> {
  const body = new FormData();
  body.append("load", load);
  // Omitted, the load is scheduled against the saved infrastructure.
  if (infra) body.append("infra", infra);
  // Optional: without it, the upload becomes a new dataset of its own.
  if (context) {
    body.append("academic_year", context.academic_year);
    body.append("semester", String(context.semester));
    body.append("program", context.program);
    body.append("department", context.department);
  }
  return postForm<T>(path, body);
}

async function postForm<T>(path: string, body: FormData): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { method: "POST", body, headers: authHeaders(), cache: "no-store" });
  } catch {
    throw failed(path, 0);
  }
  if (!res.ok) {
    const { detail, body } = await extractError(res);
    throw failed(path, res.status, detail, body);
  }
  return (await res.json()) as T;
}

function crud<T, TCreate, TUpdate = Partial<TCreate>>(resource: string) {
  return {
    /** The whole table, as it always has been. Prefer `page()` for anything
     *  that could grow: at institution scale this ships every row. */
    list: () => request<T[]>(`/api/${resource}`),
    /** One searchable, sortable page. Sending any of these parameters makes
     *  the API answer with `{total, rows}` instead of a bare array. */
    page: (params: ListQuery = {}) =>
      request<Paged<T>>(`/api/${resource}${qs({ ...params, limit: params.limit ?? 50 })}`),
    get: (id: number) => request<T>(`/api/${resource}/${id}`),
    create: (body: TCreate) =>
      request<T>(`/api/${resource}`, { method: "POST", body: JSON.stringify(body) }),
    update: (id: number, body: TUpdate) =>
      request<T>(`/api/${resource}/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    remove: (id: number) =>
      request<void>(`/api/${resource}/${id}`, { method: "DELETE" }),
  };
}

/** Build a query string from defined params only. */
function qs(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const out = search.toString();
  return out ? `?${out}` : "";
}

/** Shared by faculty/section/room availability - identical shape, different owner. */
function availability<T>(ownerResource: string) {
  return {
    list: (ownerId: number) =>
      request<T[]>(`/api/${ownerResource}/${ownerId}/unavailability`),
    add: (ownerId: number, body: UnavailabilityCreate) =>
      request<T>(`/api/${ownerResource}/${ownerId}/unavailability`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    remove: (ownerId: number, blockId: number) =>
      request<void>(`/api/${ownerResource}/${ownerId}/unavailability/${blockId}`, {
        method: "DELETE",
      }),
  };
}

function gridQuery(runId?: number, contextId?: number): string {
  const params = new URLSearchParams();
  if (runId) params.set("run_id", String(runId));
  if (contextId) params.set("academic_context_id", String(contextId));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export const api = {
  health: () => request<{ status: string; database: string }>("/api/health"),

  /** Phase 10: login and user management. Every other namespace below
   *  automatically carries whatever token setAuthToken() last set. */
  auth: {
    login: (username: string, password: string) =>
      request<Token>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
    me: () => request<User>("/api/auth/me"),
    users: {
      list: () => request<User[]>("/api/auth/users"),
      /** One searchable page of accounts. An institution issues a login per
       *  member of staff, so this list is not inherently small. */
      page: (params: ListQuery = {}) =>
        request<Paged<User>>(
          `/api/auth/users${qs({ ...params, limit: params.limit ?? 50 })}`,
        ),
      create: (body: { username: string; password: string; role: string; email?: string | null; faculty_id?: number | null }) =>
        request<User>("/api/auth/users", { method: "POST", body: JSON.stringify(body) }),
      update: (id: number, body: Partial<{ email: string | null; role: string; is_active: boolean; faculty_id: number | null; password: string }>) =>
        request<User>(`/api/auth/users/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    },
  },

  /**
   * The demo dataset. Admin-only, and disabled entirely in production - the
   * backend enforces both, so these calls are a convenience over that
   * guarantee rather than the guarantee itself.
   */
  demo: {
    status: () => request<DemoStatus>("/api/demo/status"),
    seed: () => request<DemoActionResult>("/api/demo/seed", { method: "POST" }),
    reset: (confirm: boolean) =>
      request<DemoActionResult>("/api/demo/reset", {
        method: "POST",
        body: JSON.stringify({ confirm }),
      }),
  },

  /** Server-side pre-solve readiness checks - the single source of truth. */
  validate: (academicContextId?: number) =>
    request<ValidationReport>(
      `/api/validate${academicContextId ? `?academic_context_id=${academicContextId}` : ""}`,
    ),

  /** Per-entity counts and obvious gaps, for the data workspace.
   *
   *  Counts and plain gaps only - `validate` remains the authority on whether
   *  a timetable can actually be generated. */
  dataSummary: (academicContextId?: number) =>
    request<DataSummary>(`/api/data/summary${qs({ academic_context_id: academicContextId })}`),

  academicContexts: crud<
    AcademicContext,
    { academic_year: string; semester: number; program: string; department: string }
  >("academic-contexts"),

  runs: {
    /** Starts a solve and returns immediately with a RUNNING run to poll. */
    start: (body: {
      academic_context_id: number;
      section_ids?: number[] | null;
      max_seconds?: number;
      weights?: Record<string, number>;
      respect_persisted?: boolean;
    }) =>
      request<Run>("/api/generate", { method: "POST", body: JSON.stringify(body) }),
    get: (id: number) => request<Run>(`/api/runs/${id}`),
    /** The newest run - or, with `withClasses`, the newest one that has a
     *  timetable in it, which is the run every timetable view shows. */
    latest: (academicContextId?: number, withClasses = false) =>
      request<Run>(
        `/api/runs/latest${qs({
          academic_context_id: academicContextId,
          with_classes: withClasses ? "true" : undefined,
        })}`,
      ),
    list: (academicContextId?: number) =>
      request<RunSummary[]>(
        `/api/runs${academicContextId ? `?academic_context_id=${academicContextId}` : ""}`,
      ),
    remove: (id: number) => request<void>(`/api/runs/${id}`, { method: "DELETE" }),
    /** DRAFT -> VALIDATED. */
    validateRun: (id: number) =>
      request<Run>(`/api/runs/${id}/validate`, { method: "POST" }),
    /** VALIDATED/DRAFT -> PUBLISHED. Archives any previously published run in the context. */
    publish: (id: number) =>
      request<Run>(`/api/runs/${id}/publish`, { method: "POST" }),
    /** Structured comparison between two versions. */
    diff: (fromRunId: number, toRunId: number) =>
      request<VersionDiff>(`/api/runs/${fromRunId}/diff/${toRunId}`),
    /** Every hard rule re-checked from the run's rows. Reads only. */
    check: (id: number) => request<RunCheck>(`/api/runs/${id}/check`),
  },

  /** Manual timetable operations (Phase 6). Every mutation has a matching
   *  preview that validates without writing - the UI always previews first. */
  manualEdit: {
    previewMove: (assignmentId: number, target_timeslot_id: number) =>
      request<ManualValidationResult>(`/api/assignments/${assignmentId}/move/preview`, {
        method: "POST",
        body: JSON.stringify({ target_timeslot_id }),
      }),
    move: (assignmentId: number, target_timeslot_id: number, reason?: string) =>
      request<ChangeOutcome>(`/api/assignments/${assignmentId}/move`, {
        method: "POST",
        body: JSON.stringify({ target_timeslot_id, reason }),
      }),
    previewRoom: (assignmentId: number, room_id: number) =>
      request<ManualValidationResult>(`/api/assignments/${assignmentId}/room/preview`, {
        method: "POST",
        body: JSON.stringify({ room_id }),
      }),
    changeRoom: (assignmentId: number, room_id: number, reason?: string) =>
      request<ChangeOutcome>(`/api/assignments/${assignmentId}/room`, {
        method: "POST",
        body: JSON.stringify({ room_id, reason }),
      }),
    previewFaculty: (assignmentId: number, faculty_id: number) =>
      request<ManualValidationResult>(`/api/assignments/${assignmentId}/faculty/preview`, {
        method: "POST",
        body: JSON.stringify({ faculty_id }),
      }),
    changeFaculty: (assignmentId: number, faculty_id: number, reason?: string) =>
      request<ChangeOutcome>(`/api/assignments/${assignmentId}/faculty`, {
        method: "POST",
        body: JSON.stringify({ faculty_id, reason }),
      }),
    history: (assignmentId: number) =>
      request<ChangeHistoryEntry[]>(`/api/assignments/${assignmentId}/history`),
    runHistory: (runId: number) =>
      request<ChangeHistoryEntry[]>(`/api/assignments/history/run/${runId}`),
  },

  /** "Which resources are free?" queries (Phase 7). */
  availability: {
    rooms: (params: {
      timeslot_id: number;
      length?: number;
      run_id?: number;
      capacity_min?: number;
      room_type?: string;
      lab_type?: string;
      block?: string;
    }) => request<AvailableRoom[]>(`/api/availability/rooms${qs(params)}`),
    faculty: (params: {
      timeslot_id: number;
      length?: number;
      run_id?: number;
      subject_id?: number;
      department?: string;
    }) => request<AvailableFaculty[]>(`/api/availability/faculty${qs(params)}`),
    sectionCheck: (
      sectionId: number,
      params: { timeslot_id: number; length?: number; run_id?: number },
    ) =>
      request<SectionAvailabilityCheck>(
        `/api/sections/${sectionId}/availability/check${qs(params)}`,
      ),
    sectionFreeSlots: (sectionId: number, params: { length?: number; run_id?: number }) =>
      request<TimeSlot[]>(`/api/sections/${sectionId}/availability/free-slots${qs(params)}`),
    /** Occupied vs available faculty/rooms/sections at one slot, in one call. */
    atSlot: (params: {
      timeslot_id: number;
      run_id?: number;
      academic_context_id?: number;
    }) => request<SlotAvailability>(`/api/timetable/availability${qs(params)}`),
  },

  timetable: {
    // A section implies its own academic context, but a teacher and a room do
    // not - they belong to the institution. Without contextId those two views
    // fall back to the newest run anywhere, which shows a teacher a run from
    // another programme and reads as "nothing scheduled".
    section: (id: number, runId?: number, contextId?: number) =>
      request<Grid>(`/api/timetable/section/${id}${gridQuery(runId, contextId)}`),
    faculty: (id: number, runId?: number, contextId?: number) =>
      request<Grid>(`/api/timetable/faculty/${id}${gridQuery(runId, contextId)}`),
    room: (id: number, runId?: number, contextId?: number) =>
      request<Grid>(`/api/timetable/room/${id}${gridQuery(runId, contextId)}`),
    /** The filterable admin view. Pass any subset of filters as query params. */
    master: (filters: Record<string, string | number | boolean | undefined> = {}) => {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(filters)) {
        if (value !== undefined) params.set(key, String(value));
      }
      const qs = params.toString();
      return request<MasterTimetable>(`/api/timetable/master${qs ? `?${qs}` : ""}`);
    },
  },

  faculty: {
    ...crud<Faculty, { name: string; faculty_code: string; department?: string | null }>(
      "faculty",
    ),
    /** Declare that this faculty member can teach this subject (hard constraint 4). */
    addSubject: (id: number, subject_id: number) =>
      request<Faculty>(`/api/faculty/${id}/subjects`, {
        method: "POST",
        body: JSON.stringify({ subject_id }),
      }),
    removeSubject: (id: number, subjectId: number) =>
      request<Faculty>(`/api/faculty/${id}/subjects/${subjectId}`, { method: "DELETE" }),
    unavailability: availability<FacultyUnavailability>("faculty"),
    /** Phase 9: eligibility + every semester assignment + workload + live status. */
    detail: (id: number) => request<FacultyDetail>(`/api/faculty/${id}/detail`),
  },

  subjects: {
    ...crud<
      Subject,
      {
        name: string;
        code: string;
        type: string;
        session_length_hours: number;
        sessions_per_week: number;
        required_lab_type?: string | null;
        fixed_room_id?: number | null;
      }
    >("subjects"),
    detail: (id: number) => request<SubjectDetail>(`/api/subjects/${id}/detail`),
  },

  rooms: {
    ...crud<
      Room,
      {
        room_number: string;
        block: string;
        floor?: string | null;
        capacity: number;
        room_type: string;
        lab_type?: string | null;
        is_active?: boolean;
        fixed_subject_id: number | null;
      }
    >("rooms"),
    /** One page of rooms of a single kind. `room_type` narrows the query in
     *  SQL, so `total` counts the labs rather than the whole building. */
    pageOfType: (room_type: string, params: ListQuery = {}) =>
      request<Paged<Room>>(
        `/api/rooms${qs({ ...params, room_type, limit: params.limit ?? 50 })}`,
      ),
    /** Pin a room to one subject as an explicit override, or pass null to unpin. */
    setFixedSubject: (id: number, subject_id: number | null) =>
      request<Room>(`/api/rooms/${id}/fixed-subject`, {
        method: "PUT",
        body: JSON.stringify({ subject_id }),
      }),
    unavailability: availability<RoomUnavailability>("rooms"),
    /** Phase 9: utilization + occupied/free/inactive right now. */
    detail: (id: number) => request<RoomDetail>(`/api/rooms/${id}/detail`),
  },

  sections: {
    ...crud<
      Section,
      { academic_context_id: number; section_number: string; strength: number }
    >("sections"),
    /** List sections, optionally scoped to one academic context. */
    listByContext: (academicContextId?: number) =>
      request<Section[]>(
        `/api/sections${academicContextId ? `?academic_context_id=${academicContextId}` : ""}`,
      ),
    /** One page of sections within a context - the context filter is applied
     *  in SQL, so `total` describes that semester and not the institution. */
    pageByContext: (academicContextId: number | undefined, params: ListQuery = {}) =>
      request<Paged<Section>>(
        `/api/sections${qs({
          ...params,
          academic_context_id: academicContextId,
          limit: params.limit ?? 50,
        })}`,
      ),
    addSubject: (id: number, subject_id: number) =>
      request<Section>(`/api/sections/${id}/subjects`, {
        method: "POST",
        body: JSON.stringify({ subject_id }),
      }),
    removeSubject: (id: number, subjectId: number) =>
      request<Section>(`/api/sections/${id}/subjects/${subjectId}`, { method: "DELETE" }),
    unavailability: availability<SectionUnavailability>("sections"),
    detail: (id: number) => request<SectionDetail>(`/api/sections/${id}/detail`),
  },

  /** The semester persistence + locking layer (spec #7/#8/#9). */
  assignments: {
    list: (academicContextId: number) =>
      request<SectionSubjectAssignment[]>(
        `/api/academic-contexts/${academicContextId}/assignments`,
      ),
    /** What locking or unlocking would do, before it is done - parity with
     *  the move/room/faculty edits, which have always been previewed. */
    previewLock: (
      academicContextId: number,
      section_id: number,
      subject_id: number,
      lock: boolean,
    ) =>
      request<LockPreview>(
        `/api/academic-contexts/${academicContextId}/assignments/lock/preview`,
        { method: "POST", body: JSON.stringify({ section_id, subject_id, lock }) },
      ),
    lock: (academicContextId: number, section_id: number, subject_id: number) =>
      request<SectionSubjectAssignment>(
        `/api/academic-contexts/${academicContextId}/assignments/lock`,
        { method: "POST", body: JSON.stringify({ section_id, subject_id }) },
      ),
    unlock: (academicContextId: number, section_id: number, subject_id: number) =>
      request<SectionSubjectAssignment>(
        `/api/academic-contexts/${academicContextId}/assignments/unlock`,
        { method: "POST", body: JSON.stringify({ section_id, subject_id }) },
      ),
  },

  timeslots: {
    ...crud<
      TimeSlot,
      {
        day: string;
        day_index: number;
        period_index: number;
        start_time: string;
        end_time: string;
        is_lunch: boolean;
      }
    >("timeslots"),
    /** Regenerates the whole week, replacing any existing slots. Defaults to Mon-Fri. */
    seed: (body: SeedGridRequest) =>
      request<TimeSlot[]>(`/api/timeslots/seed`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },

  /** The two-file teacher import: a teaching load and a room list. */
  teacher: {
    /** What importing this load would do. Writes nothing. With no room list,
     *  it is checked against the saved infrastructure. */
    preview: (load: File, infra: File | null = null, context?: TeacherImportContext) =>
      uploadTeacherFiles<TeacherImportPreview>("/api/teacher/preview", load, infra, context),
    /** Imports the load (and a room list, if given) in one transaction, or nothing. */
    apply: (load: File, infra: File | null = null, context?: TeacherImportContext) =>
      uploadTeacherFiles<TeacherImportPreview>("/api/teacher/apply", load, infra, context),
  },

  /** The saved room list every teaching load is scheduled against. */
  infrastructure: {
    get: () => request<Infrastructure>("/api/infrastructure"),
    /** What saving this room list would do. Writes nothing. */
    preview: (file: File) => {
      const body = new FormData();
      body.append("infra", file);
      return postForm<InfrastructurePreview>("/api/infrastructure/preview", body);
    },
    /** Save it. Replacing a different saved list that timetables were built
     *  on is refused (409) unless `confirmReplace`. */
    save: (file: File, confirmReplace = false) => {
      const body = new FormData();
      body.append("infra", file);
      if (confirmReplace) body.append("confirm_replace", "true");
      return postForm<InfrastructurePreview>("/api/infrastructure", body);
    },
  },

  /** Clearing old data, one half at a time or both. */
  data: {
    clearPreview: (what: ClearWhat) =>
      request<ClearPreview>(`/api/data/clear?what=${encodeURIComponent(what)}`),
    clear: (what: ClearWhat, confirm: string) =>
      request<ClearResult>("/api/data/clear", {
        method: "POST",
        body: JSON.stringify({ what, confirm }),
      }),
  },

  /** Blank spreadsheets with example rows, served by the frontend itself. */
  templates: {
    loadUrl: "/templates/LOAD_TEMPLATE.xlsx",
    infraUrl: "/templates/INFRA_TEMPLATE.xlsx",
  },

  /** The generated timetable, one row per class. With no run, the latest. */
  allocation: (filters: AllocationFilters = {}) =>
    request<Allocation>(`/api/allocation${qs(filters as Record<string, string | number | undefined>)}`),

  /** Phase 9: Excel/CSV/PDF export. Every URL is a direct GET the browser can
   *  navigate to (Content-Disposition triggers the download) - no fetch/blob
   *  plumbing needed, and every filter mirrors `api.timetable.master`. */
  export: {
    /** The teacher's timetable as one Excel sheet. With no run, the latest. */
    allocationXlsxUrl: (filters: AllocationFilters = {}) =>
      `${BASE}/api/export/allocation.xlsx${qs(filters as Record<string, string | number | undefined>)}`,
    masterCsvUrl: (filters: Record<string, string | number | boolean | undefined> = {}) =>
      `${BASE}/api/export/master.csv${qs(filters)}`,
    masterXlsxUrl: (filters: Record<string, string | number | boolean | undefined> = {}) =>
      `${BASE}/api/export/master.xlsx${qs(filters)}`,
    masterPdfUrl: (filters: Record<string, string | number | boolean | undefined> = {}) =>
      `${BASE}/api/export/master.pdf${qs(filters)}`,
    /** Full workbook for one run: master + assignments + one grid sheet per
     *  section/faculty/room used in it. */
    runWorkbookUrl: (runId: number) => `${BASE}/api/export/run/${runId}.xlsx`,
    sectionPdfUrl: (id: number, runId?: number) =>
      `${BASE}/api/export/section/${id}.pdf${qs({ run_id: runId })}`,
    facultyPdfUrl: (id: number, runId?: number) =>
      `${BASE}/api/export/faculty/${id}.pdf${qs({ run_id: runId })}`,
    roomPdfUrl: (id: number, runId?: number) =>
      `${BASE}/api/export/room/${id}.pdf${qs({ run_id: runId })}`,
  },

  /** AI Assistant powered by Gemini and verified schedule facts. */
  assistant: {
    chat: (
      message: string,
      academicContextId?: number,
      runId?: number,
      history?: AssistantChatMessage[],
    ) =>
      request<AssistantChatResponse>("/api/assistant/chat", {
        method: "POST",
        body: JSON.stringify({
          message,
          academic_context_id: academicContextId,
          run_id: runId,
          history: history ?? [],
        }),
      }),
  },
};
