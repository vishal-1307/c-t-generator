import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

/**
 * The home page is the product: the teaching data, the saved rooms, Analyze,
 * Generate, the table.
 *
 * The rooms are saved once and reused, so the page asks for Load.xlsx every
 * time and for Infra.xlsx only when none is saved - and it will not analyze a
 * load with no rooms to put it in. Nothing is asked for that the files do not
 * contain. Three things are impossible: generating without a clean analysis,
 * replacing rooms that timetables depend on without a tick, and clearing data
 * without one.
 *
 * Fake timers throughout: generation is followed by a polling interval, and
 * `advanceTimersByTimeAsync` both moves that clock and flushes the promises
 * each mocked call needs. (`waitFor` polls with a real timer and would hang.)
 */

const preview = vi.fn();
const apply = vi.fn();
const start = vi.fn();
const get = vi.fn();
const check = vi.fn();
const allocation = vi.fn();
const refresh = vi.fn();
const setCurrentId = vi.fn();
const infraGet = vi.fn();
const infraPreview = vi.fn();
const infraSave = vi.fn();
const clearPreview = vi.fn();
const dataClear = vi.fn();
let isAdmin = true;

class FakeApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

vi.mock("@/lib/api", () => ({
  api: {
    teacher: {
      preview: (...a: unknown[]) => preview(...a),
      apply: (...a: unknown[]) => apply(...a),
    },
    infrastructure: {
      get: (...a: unknown[]) => infraGet(...a),
      preview: (...a: unknown[]) => infraPreview(...a),
      save: (...a: unknown[]) => infraSave(...a),
    },
    data: {
      clearPreview: (...a: unknown[]) => clearPreview(...a),
      clear: (...a: unknown[]) => dataClear(...a),
    },
    runs: {
      start: (...a: unknown[]) => start(...a),
      get: (...a: unknown[]) => get(...a),
      check: (...a: unknown[]) => check(...a),
    },
    allocation: (...a: unknown[]) => allocation(...a),
    export: { allocationXlsxUrl: (f: { run_id?: number }) => `/xlsx?run_id=${f.run_id ?? ""}` },
    templates: { loadUrl: "/templates/LOAD_TEMPLATE.xlsx", infraUrl: "/templates/INFRA_TEMPLATE.xlsx" },
  },
  ApiError: FakeApiError,
}));
vi.mock("next/link", () => ({
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ isAdmin }) }));
vi.mock("@/lib/academicContext", () => ({
  useAcademicContext: () => ({ refresh, setCurrentId }),
}));

const Dashboard = (await import("@/app/page")).default;

const SUMMARY = {
  faculty: 7, subjects: 9, sections: 9, groups: 2, classes: 36, required_periods: 52,
  rooms: 30, classrooms: 18, labs: 12, byod_rooms: 21,
};

const SAVED_ROOMS = {
  available: true, filename: "Infra.xlsx", uploaded_at: "2026-09-14T08:00:00Z",
  summary: { rooms: 30, classrooms: 18, labs: 12, byod_rooms: 21 }, timetables: 2,
};
const NO_ROOMS = { available: false, filename: null, uploaded_at: null, summary: {}, timetables: 0 };

const READY = {
  filename: "Load.xlsx", sheets: [], outcomes: [], decisions: [], blockers: [],
  counts: { creates: 59 }, has_errors: false, can_apply: true, committed: false,
  warnings: ["No rows for 24012: section 2401 has 68 students, but only 24011 is in the file."],
  notes: [], summary: SUMMARY, room_changes: [],
  readiness: { ready: true, blockers: [], warnings: [], unassignable: [] },
  academic_context_id: null, dataset_label: "Upload 1 · 2026-09-11 · Load",
};

const RUN = {
  id: 11, academic_context_id: 7, version: 1, created_at: "2026-09-11T00:00:00Z",
  status: "RUNNING", publish_status: "DRAFT", published_at: null, objective_value: null,
  solve_time_seconds: null, message: null, weights: {}, penalties: {},
  assignment_count: 0, proven_optimal: false, warnings: [],
};

const TABLE = {
  run_id: 11, run_status: "OPTIMAL", version: 1, dataset: "Upload 1 · 2026-09-11 · Load",
  created_at: null, classes: 1, periods: 1,
  rows: [{
    day: "Monday", day_index: 0, start_time: "09:30", end_time: "10:20", periods: 1,
    faculty_id: "90001", faculty_name: "Faculty A", subject_code: "ECE181",
    subject_name: "Embedded systems", section: "2401", strength: 68, class_type: "Theory",
    byod: true, room: "36-301", block: "36", floor: "", room_capacity: 72,
    room_type: "Classroom", room_byod: true, faculty_pk: 1, section_pk: 1, room_pk: 1,
  }],
  filters: { faculty: [], sections: [], rooms: [], days: ["Monday"] },
};

const INFRA_PREVIEW = {
  filename: "Infra.xlsx", sheets: [], outcomes: [], decisions: [], blockers: [], counts: {},
  has_errors: false, can_apply: true, committed: false, notes: [],
  summary: { rooms: 25, classrooms: 15, labs: 10, byod_rooms: 18 }, room_changes: [],
  replaces: true, unchanged: false, timetables_removed: 2,
};

const flush = async (n = 6) => {
  for (let i = 0; i < n; i++) await vi.advanceTimersByTimeAsync(0);
};

function choose(label: RegExp, name: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { files: [new File(["x"], name)] } });
}

async function arrived() {
  render(<Dashboard />);
  await flush();
}

async function analyzed(result: unknown = READY) {
  preview.mockResolvedValue(result);
  await arrived();
  choose(/Load\.xlsx file/, "Load.xlsx");
  fireEvent.click(screen.getByRole("button", { name: /Analyze Files/ }));
  await flush();
}

const disabled = (name: RegExp) =>
  (screen.getByRole("button", { name }) as HTMLButtonElement).disabled;

/** A metric reads its own label, so a figure is asked for by name rather
 *  than by hunting the page for a number. */
const figure = (label: string) =>
  screen.getByRole("group", { name: label }).textContent?.replace(label, "").trim();

beforeEach(() => {
  isAdmin = true;
  for (const f of [preview, apply, start, get, check, allocation, refresh, setCurrentId,
    infraGet, infraPreview, infraSave, clearPreview, dataClear]) f.mockReset();
  refresh.mockResolvedValue(undefined);
  allocation.mockResolvedValue(TABLE);
  infraGet.mockResolvedValue(SAVED_ROOMS);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the dashboard", () => {
  it("asks for the teaching data, shows the saved rooms, and asks for nothing else", async () => {
    await arrived();
    expect(screen.getByText("College Timetable Generator")).toBeTruthy();
    expect(screen.getByText("Upload or manage your teaching and infrastructure files.")).toBeTruthy();
    expect(screen.getByText("Teaching Data")).toBeTruthy();
    expect(screen.getAllByText("No file selected")).toHaveLength(1);
    expect(screen.getByText("Infra.xlsx available")).toBeTruthy();
    // No semester, programme or department anywhere.
    expect(screen.queryByPlaceholderText("2025-26")).toBeNull();
    expect(screen.queryByText(/Semester/)).toBeNull();
  });

  it("analyzes a chosen load against the saved rooms - one file, no semester", async () => {
    await arrived();
    expect(disabled(/Analyze Files/)).toBe(true);
    choose(/Load\.xlsx file/, "Load.xlsx");
    expect(disabled(/Analyze Files/)).toBe(false);

    preview.mockResolvedValue(READY);
    fireEvent.click(screen.getByRole("button", { name: /Analyze Files/ }));
    await flush();
    expect(preview).toHaveBeenCalledTimes(1);
    expect(preview.mock.calls[0]).toHaveLength(1); // the load, and nothing else
  });

  it("offers a template for each file", async () => {
    infraGet.mockResolvedValue(NO_ROOMS);
    await arrived();
    const links = screen.getAllByText("Download the template").map((a) => a.closest("a")?.getAttribute("href"));
    expect(links).toEqual(["/templates/LOAD_TEMPLATE.xlsx", "/templates/INFRA_TEMPLATE.xlsx"]);
  });

  it("does not let someone who is not signed in upload", async () => {
    isAdmin = false;
    await arrived();
    expect(screen.getByText("Sign in")).toBeTruthy();
    expect((screen.getByLabelText(/Load\.xlsx file/) as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /Clear Previous Data/ })).toBeNull();
  });

  // ----------------------------------------------------------- the rooms

  it("with no rooms saved, asks for Infra.xlsx and will not analyze a load", async () => {
    infraGet.mockResolvedValue(NO_ROOMS);
    await arrived();
    expect(screen.getByLabelText(/Infra\.xlsx file/)).toBeTruthy();
    choose(/Load\.xlsx file/, "Load.xlsx");
    expect(disabled(/Analyze Files/)).toBe(true);
    expect(screen.getByText(/Save the infrastructure to analyze/)).toBeTruthy();
  });

  it("saves a first room list after showing what it holds", async () => {
    infraGet.mockResolvedValue(NO_ROOMS);
    infraPreview.mockResolvedValue({ ...INFRA_PREVIEW, replaces: false, timetables_removed: 0 });
    infraSave.mockResolvedValue({ ...INFRA_PREVIEW, committed: true, replaces: false });
    await arrived();

    choose(/Infra\.xlsx file/, "Infra.xlsx");
    await flush();
    expect(screen.getByText(/25 rooms/)).toBeTruthy();
    infraGet.mockResolvedValue(SAVED_ROOMS);
    fireEvent.click(screen.getByRole("button", { name: /Save Infrastructure/ }));
    await flush();
    expect(infraSave).toHaveBeenCalledWith(expect.any(File), false);
    expect(screen.getByText("Infra.xlsx available")).toBeTruthy();
  });

  it("shows the saved rooms with Replace and Remove", async () => {
    await arrived();
    expect(screen.getByText(/30 rooms · 18 classrooms · 12 labs · 21 with charging points/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Replace Infrastructure/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Remove Infrastructure/ })).toBeTruthy();
    expect(screen.queryByLabelText(/Infra\.xlsx file/)).toBeNull();
  });

  it("replacing rooms that timetables were built on needs a tick", async () => {
    infraPreview.mockResolvedValue(INFRA_PREVIEW);
    infraSave.mockResolvedValue({ ...INFRA_PREVIEW, committed: true });
    await arrived();

    fireEvent.click(screen.getByRole("button", { name: /Replace Infrastructure/ }));
    choose(/Infra\.xlsx file/, "Infra-2027.xlsx");
    await flush();
    expect(screen.getByText(/Replacing the rooms removes 2 timetables/)).toBeTruthy();
    expect(disabled(/Save new infrastructure/)).toBe(true);

    fireEvent.click(screen.getByRole("checkbox", { name: /replace the rooms/ }));
    expect(disabled(/Save new infrastructure/)).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /Save new infrastructure/ }));
    await flush();
    expect(infraSave).toHaveBeenCalledWith(expect.any(File), true);
  });

  // ------------------------------------------------------------ clearing

  it("clears data only after saying what goes and being ticked", async () => {
    clearPreview.mockResolvedValue({
      what: "load", confirmation: "CLEAR LOAD DATA",
      counts: { timetable_run: 1, section: 9, faculty: 7 }, total: 17,
    });
    dataClear.mockResolvedValue({ what: "load", deleted: {}, total_rows: 17 });
    await arrived();

    fireEvent.click(screen.getByRole("button", { name: /Clear Previous Data/ }));
    await flush();
    expect(clearPreview).toHaveBeenCalledWith("load");
    expect(
      screen.getByText("This deletes 1 timetable, 9 sections and lab groups, 7 faculty members."),
    ).toBeTruthy();
    expect(disabled(/Clear Load data/)).toBe(true);

    fireEvent.click(screen.getByRole("checkbox", { name: /permanently deletes/ }));
    fireEvent.click(screen.getByRole("button", { name: /Clear Load data/ }));
    await flush();
    expect(dataClear).toHaveBeenCalledWith("load", "CLEAR LOAD DATA");
    expect(screen.getByText("The teaching data was cleared. The infrastructure is kept.")).toBeTruthy();
  });

  it("Remove Infrastructure opens the clear panel on the rooms", async () => {
    clearPreview.mockResolvedValue({
      what: "infrastructure", confirmation: "CLEAR INFRASTRUCTURE",
      counts: { room: 30, timetable_run: 2 }, total: 32,
    });
    await arrived();
    fireEvent.click(screen.getByRole("button", { name: /Remove Infrastructure/ }));
    await flush();
    expect(clearPreview).toHaveBeenCalledWith("infrastructure");
    expect((screen.getByRole("radio", { name: /Infrastructure/ }) as HTMLInputElement).checked).toBe(true);
  });

  // ------------------------------------------------------------ analysis

  it("shows the file parsed, the data summary, and Generate", async () => {
    await analyzed();
    expect(screen.getAllByText("Parsed successfully")).toHaveLength(1);
    expect(screen.getByText("Data Ready")).toBeTruthy();
    expect(figure("Subjects")).toBe("9");
    expect(figure("Classes")).toBe("36");
    expect(figure("Required periods")).toBe("52");
    expect(screen.getByText(/a two-period lab is one class/)).toBeTruthy();
    expect(screen.getByText("30")).toBeTruthy();          // rooms
    expect(screen.getByRole("button", { name: /Generate Timetable/ })).toBeTruthy();
    // Warnings are one line each, tucked away by default.
    expect(screen.getByText(/1 note about the files/)).toBeTruthy();
  });

  it("names a class no room can take, with the reason, and offers no Generate", async () => {
    await analyzed({
      ...READY,
      readiness: {
        ready: false,
        blockers: [{ label: "Every section-subject has an eligible room", detail: "..." }],
        warnings: [],
        unassignable: [{
          subject_code: "ECE281", subject_name: "Internet of Things", section: "2401",
          strength: 68, byod: true, type: "Theory",
          reason: "No available BYOD-capable classroom with capacity >= 68.",
        }],
      },
    });
    expect(screen.getByText("No suitable room was available for:")).toBeTruthy();
    expect(screen.getByText("ECE281 · Section 2401")).toBeTruthy();
    expect(screen.getByText(/68 students · BYOD required/)).toBeTruthy();
    expect(screen.getByText(/No available BYOD-capable classroom with capacity >= 68\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Generate Timetable/ })).toBeNull();
  });

  it("needs a tick before changing rooms that already exist", async () => {
    await analyzed({
      ...READY,
      room_changes: [{ room: "36-307", changes: [
        { field: "capacity", label: "Capacity", before: "72", after: "60" }] }],
    });
    expect(screen.getByText(/Capacity 72 → 60/)).toBeTruthy();
    expect(disabled(/Generate Timetable/)).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    expect(disabled(/Generate Timetable/)).toBe(false);
  });

  it("names the file and row a contradiction came from", async () => {
    preview.mockRejectedValue(new FakeApiError("x", 422, {
      detail: { problems: [{ file: "Load.xlsx", row_number: 5, identity: "ECE182 / 24011",
        message: "section 24011 has 36 students here and 40 on row 3" }] },
    }));
    await arrived();
    choose(/Load\.xlsx file/, "Load.xlsx");
    fireEvent.click(screen.getByRole("button", { name: /Analyze Files/ }));
    await flush();
    expect(screen.getByText("Load.xlsx, row 5")).toBeTruthy();
    expect(screen.getByText(/40 on row 3/)).toBeTruthy();
  });

  it("says plainly when the load has no Classes Per Week column", async () => {
    preview.mockRejectedValue(new FakeApiError("x", 422, {
      detail: { problems: [{ file: "Load.xlsx", row_number: 1, identity: "Classes Per Week",
        message: "this file has no Classes Per Week column." }] },
    }));
    await arrived();
    choose(/Load\.xlsx file/, "Load.xlsx");
    fireEvent.click(screen.getByRole("button", { name: /Analyze Files/ }));
    await flush();
    // Named against the file, not a meaningless "row 1".
    expect(screen.getByText(/no Classes Per Week column/)).toBeTruthy();
    expect(screen.queryByText("Load.xlsx, row 1")).toBeNull();
    expect(screen.queryByRole("button", { name: /Generate Timetable/ })).toBeNull();
  });

  it("generates, then shows the result and the table on the same page", async () => {
    apply.mockResolvedValue({ ...READY, committed: true, academic_context_id: 7 });
    start.mockResolvedValue(RUN);
    get.mockResolvedValue({ ...RUN, status: "OPTIMAL", assignment_count: 52 });
    check.mockResolvedValue({ run_id: 11, violations: [], count: 0 });
    await analyzed();

    fireEvent.click(screen.getByRole("button", { name: /Generate Timetable/ }));
    await flush();
    expect(apply.mock.calls[0]).toHaveLength(1);
    expect(setCurrentId).toHaveBeenCalledWith(7);
    expect(start).toHaveBeenCalledWith({ academic_context_id: 7 });
    expect(screen.getByText(/Generating the timetable/)).toBeTruthy();

    await vi.advanceTimersByTimeAsync(1500);
    await flush(10);
    expect(screen.getByText("Timetable Generated Successfully")).toBeTruthy();
    expect(figure("Classes")).toBe("36");
    expect(figure("Hard Conflicts")).toBe("0");
    expect(screen.getByText("View Timetable").getAttribute("href")).toBe("/timetable");
    expect(screen.getAllByText("Export Excel")[0].getAttribute("href")).toBe("/xlsx?run_id=11");
    // The table itself, straight away.
    expect(screen.getAllByText("36-301").length).toBeGreaterThan(0);
  });

  it("says why when a timetable cannot be generated", async () => {
    apply.mockResolvedValue({ ...READY, committed: true, academic_context_id: 7 });
    start.mockResolvedValue(RUN);
    get.mockResolvedValue({ ...RUN, status: "INFEASIBLE", message: "Section 2401 has too many classes." });
    await analyzed();
    fireEvent.click(screen.getByRole("button", { name: /Generate Timetable/ }));
    await flush();
    await vi.advanceTimersByTimeAsync(1500);
    await flush(10);
    expect(screen.getByText("Unable to generate the timetable.")).toBeTruthy();
    expect(screen.getByText(/Section 2401 has too many classes\./)).toBeTruthy();
  });

  it("choosing a different file throws away an analysis that no longer describes it", async () => {
    await analyzed();
    expect(screen.getByText("Data Ready")).toBeTruthy();
    choose(/Load\.xlsx file/, "Load-v2.xlsx");
    expect(screen.queryByText("Data Ready")).toBeNull();
  });

  /*
   * The ways this page used to say "ready" when it was not, and the one way
   * it used to shout about something that was not the data's fault.
   */

  it("will not call a file parsed when rows in it are repeated", async () => {
    await analyzed({
      ...READY,
      has_errors: true,
      can_apply: false,
      readiness: null,
      outcomes: [
        { sheet: "Faculty", entity: "faculty", label: "Faculty", creates: 0, updates: 0,
          unchanged: 0, invalid: 0, duplicates: 1, notes: [],
          problems: [{ sheet: "Load.xlsx", row_number: 7, identity: "90001",
            message: "90001 appears twice", column: null, suggestion: null }] },
      ],
    });
    expect(screen.queryByText("Data Ready")).toBeNull();
    expect(screen.getByText("Cannot generate yet.")).toBeTruthy();
    expect(screen.getByText("1 row is repeated")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Generate Timetable/ })).toBeNull();
  });

  it("explains a file it could not read at all, instead of a bare summary", async () => {
    // A missing required column stops the sheet before it can report a row,
    // so this blocker is the only thing that knows anything is wrong.
    await analyzed({
      ...READY,
      has_errors: true,
      can_apply: false,
      readiness: null,
      outcomes: [],
      blockers: ["Faculty: missing required column(s) name."],
    });
    expect(screen.queryByText("Data Ready")).toBeNull();
    expect(screen.getByText("Faculty: missing required column(s) name.")).toBeTruthy();
    // On the Load card, and at the head of the error it explains.
    expect(screen.getAllByText("The file could not be read")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Generate Timetable/ })).toBeNull();
  });

  it("says so when the files match data already imported", async () => {
    await analyzed({ ...READY, can_apply: false });
    expect(screen.getByText("Data Ready")).toBeTruthy();
    expect(screen.getByText(/match data already imported/)).toBeTruthy();
    expect(disabled(/Generate Timetable/)).toBe(false);
  });

  it("a server refusing a second solve is not an error about the data", async () => {
    apply.mockResolvedValue({ ...READY, committed: true, academic_context_id: 7 });
    start.mockRejectedValue(
      new FakeApiError("A timetable is already being generated.", 409),
    );
    await analyzed();

    fireEvent.click(screen.getByRole("button", { name: /Generate Timetable/ }));
    await flush();

    // It is said where it happened, as a status, and it does not repaint the
    // verdict: the files are still fine, and they still say so.
    expect(screen.getByText("Data Ready")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    const notice = screen.getByRole("status");
    expect(notice.textContent).toContain("already being generated");
    // And pressing it again could only earn the same refusal.
    expect(disabled(/Generate Timetable/)).toBe(true);
  });

  it("keeps a blocked verdict red and a passing failure amber - never the reverse", async () => {
    await analyzed({
      ...READY,
      has_errors: false,
      readiness: {
        ready: false,
        blockers: [{ label: "Sections have a curriculum", detail: "2401 has no subjects" }],
        warnings: [],
        unassignable: [],
      },
    });
    // The data verdict is the one thing allowed to interrupt.
    expect(screen.getByText("Cannot generate yet.")).toBeTruthy();
    expect(screen.getByText("2401 has no subjects")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Generate Timetable/ })).toBeNull();
  });
});
