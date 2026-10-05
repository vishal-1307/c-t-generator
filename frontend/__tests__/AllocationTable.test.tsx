import type { AllocationRow } from "@/lib/types";
import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * The teacher's table: one row per class with the room it got, four filters,
 * and an Excel link that carries the same filters - so the file holds exactly
 * what the table shows.
 */

const allocation = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    allocation: (...a: unknown[]) => allocation(...a),
    export: {
      allocationXlsxUrl: (f: Record<string, unknown>) =>
        "/api/export/allocation.xlsx?" + new URLSearchParams(
          Object.entries(f).map(([k, v]) => [k, String(v)])).toString(),
    },
  },
  ApiError: class extends Error { status = 404; },
}));

const context = { currentId: 5 as number | null };
vi.mock("@/lib/academicContext", () => ({
  useAcademicContext: () => ({ currentId: context.currentId }),
}));

const { AllocationTable } = await import("@/components/AllocationTable");

const row = (over: Record<string, unknown>) => ({
  day: "Monday", day_index: 0, start_time: "09:30", end_time: "10:20", periods: 1,
  faculty_id: "90001", faculty_name: "Faculty A", subject_code: "ECE181",
  subject_name: "Embedded systems", section: "2401", strength: 68, class_type: "Theory",
  byod: true, room: "36-301", block: "36", floor: "3", room_capacity: 72,
  room_type: "Classroom", room_byod: true, faculty_pk: 1, section_pk: 1, room_pk: 1,
  ...over,
});

const DATA = {
  run_id: 11, run_status: "OPTIMAL", version: 1, dataset: "Upload 1 · 2026-09-11 · Load",
  created_at: null, classes: 2, periods: 4,
  rows: [
    row({}),
    row({ day: "Tuesday", day_index: 1, start_time: "09:30", end_time: "12:00", periods: 3,
          subject_code: "ECE102", section: "24031", strength: 36, class_type: "Lab",
          room: "33-101", block: "33", floor: "", room_capacity: 36, room_type: "Lab",
          section_pk: 2, room_pk: 2 }),
  ],
  filters: {
    faculty: [{ id: 1, label: "Faculty A (90001)" }],
    sections: [{ id: 1, label: "2401" }, { id: 2, label: "24031" }],
    rooms: [{ id: 1, label: "36-301" }, { id: 2, label: "33-101" }],
    days: ["Monday", "Tuesday"],
  },
};

beforeEach(() => {
  allocation.mockReset().mockResolvedValue(DATA);
});

it("shows every class with its time, room and the room's facts", async () => {
  render(<AllocationTable />);
  await screen.findByRole("cell", { name: "36-301" });
  for (const h of ["Day", "Time", "Faculty", "Faculty ID", "Subject Code", "Section",
                   "Students", "Room", "Block", "Floor", "Capacity", "BYOD", "Type"]) {
    expect(screen.getAllByText(h).length).toBeGreaterThan(0);
  }
  // Scoped to the table: the phone's cards render the same class, so asking
  // the whole document for one time would find both renderings of it.
  const table = within(screen.getByRole("table"));
  expect(table.getByText("09:30–12:00")).toBeTruthy(); // a 3-period lab is one row
  expect(table.getByRole("cell", { name: "33-101" })).toBeTruthy();
});

it("loses nothing on a phone: every column is in the card too", async () => {
  const { FIELDS } = await import("@/components/AllocationTable");
  render(<AllocationTable />);
  await screen.findByRole("cell", { name: "36-301" });

  // The cards are a second rendering of the same rows, and the thing that can
  // go wrong with a second rendering is that it silently holds less. Assert
  // against the field list both are built from, so adding a column to the
  // table and forgetting the card fails here rather than on someone's phone.
  const card = within(screen.getAllByRole("listitem")[0]);
  for (const f of FIELDS) {
    const shown = String(f.cell(DATA.rows[0] as AllocationRow) ?? "");
    expect(card.getAllByText(shown, { exact: false }).length).toBeGreaterThan(0);
  }
});

it("filters on the server and exports what is filtered", async () => {
  render(<AllocationTable />);
  await screen.findByRole("cell", { name: "36-301" });
  fireEvent.change(screen.getByLabelText("Section"), { target: { value: "2" } });
  await waitFor(() =>
    expect(allocation).toHaveBeenLastCalledWith({ section_id: 2, academic_context_id: 5 }),
  );
  const link = screen.getByText("Export Excel").getAttribute("href") ?? "";
  expect(link).toContain("section_id=2");
  expect(link).toContain("run_id=11");
});

it("shows the current dataset's timetable, or exactly the run it is given", async () => {
  const { unmount } = render(<AllocationTable />);
  await screen.findByRole("cell", { name: "36-301" });
  expect(allocation).toHaveBeenLastCalledWith({ academic_context_id: 5 });
  unmount();
  render(<AllocationTable runId={11} />);
  await screen.findByRole("cell", { name: "36-301" });
  expect(allocation).toHaveBeenLastCalledWith({ run_id: 11 });
});

it("says so when nothing has been generated yet", async () => {
  const { ApiError } = await import("@/lib/api");
  allocation.mockRejectedValue(new (ApiError as unknown as new (m: string) => Error)("none"));
  render(<AllocationTable />);
  await screen.findByText("No timetable has been generated yet.");
});
