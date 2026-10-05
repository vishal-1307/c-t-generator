import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

/**
 * The solve pins the backend's CPU for up to its whole time budget (see
 * backend/app/solver/run.py), which on a resource-constrained instance can
 * make one poll request in the middle of that window time out - a real but
 * transient hiccup, not the run having failed. The previous behaviour
 * treated the very first failed poll as fatal: it stopped polling and showed
 * "Cannot reach the API", even while the run was in fact still solving
 * correctly server-side, with no way to tell from the page short of a manual
 * reload. Confirmed live during a large (BCA, 88 section-subject pairs)
 * generation.
 *
 * These tests pin the fix: a handful of consecutive poll failures must be
 * tolerated before the page gives up, and a success in between must reset
 * that count rather than let failures accumulate across it.
 *
 * Fake timers are active for the whole test (not just the polling phase):
 * `start()`'s `setInterval` has to be *created* under the same fake clock
 * that later advances it, or advancing time never reaches it. `waitFor` is
 * avoided throughout for the same reason - it polls with a real `setTimeout`
 * internally and hangs once the global clock is faked; `advanceTimersByTimeAsync`
 * is used instead, which also flushes the microtasks each mocked API call's
 * promise needs to resolve.
 */

const runsStart = vi.fn();
const runsGet = vi.fn();
const runsLatest = vi.fn();
const runsCheck = vi.fn();
const validate = vi.fn();
const assignmentsList = vi.fn();

class MockApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

vi.mock("@/lib/api", () => ({
  ApiError: MockApiError,
  api: {
    runs: { start: (...a: unknown[]) => runsStart(...a), get: (...a: unknown[]) => runsGet(...a), latest: (...a: unknown[]) => runsLatest(...a), check: (...a: unknown[]) => runsCheck(...a) },
    validate: (...a: unknown[]) => validate(...a),
    assignments: { list: (...a: unknown[]) => assignmentsList(...a) },
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ canEdit: true }),
}));

vi.mock("@/lib/academicContext", () => ({
  useAcademicContext: () => ({
    currentId: 1,
    current: { id: 1, label: "2026-27 / Sem 5 / BCA (Computer Applications)" },
    contexts: [{ id: 1, label: "2026-27 / Sem 5 / BCA (Computer Applications)" }],
    loading: false,
  }),
}));

const { default: GeneratePage } = await import("@/app/generate/page");

const RUNNING = {
  id: 5, academic_context_id: 1, version: 2, created_at: "2026-09-02T00:00:00Z",
  status: "RUNNING", publish_status: "DRAFT", published_at: null,
  objective_value: null, solve_time_seconds: null, message: null,
  weights: {}, penalties: {}, assignment_count: 0, proven_optimal: false, warnings: [],
};
const OPTIMAL = { ...RUNNING, status: "OPTIMAL", proven_optimal: true };

beforeEach(() => {
  runsStart.mockReset().mockResolvedValue(RUNNING);
  runsGet.mockReset();
  runsLatest.mockReset().mockRejectedValue(new MockApiError("none yet", 404));
  runsCheck.mockReset().mockResolvedValue({ run_id: 5, violations: [], count: 0 });
  validate.mockReset().mockResolvedValue({ ready: true, checks: [] });
  assignmentsList.mockReset().mockResolvedValue([]);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

async function clickGenerate() {
  render(<GeneratePage />);
  // flush the mount-time load(): validation, assignments, the latest run and
  // the follow-up that decides whether to resume polling are separate hops
  for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(0);
  const button = screen.getByText("Generate Timetable") as HTMLButtonElement;
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  await vi.advanceTimersByTimeAsync(0); // flush start()'s await api.runs.start(...)
  expect(runsStart).toHaveBeenCalled();
}

it("tolerates a few consecutive poll failures without showing an error", async () => {
  runsGet.mockRejectedValue(new MockApiError("Cannot reach the API at http://x. Is the backend running?", 0));
  await clickGenerate();

  // 4 failed polls (1s apart) - one below the 5-failure threshold.
  for (let i = 0; i < 4; i++) {
    await vi.advanceTimersByTimeAsync(1000);
  }

  expect(screen.queryByText(/Cannot reach the API/)).toBeNull();
  expect(screen.getAllByText(/Solving/).length).toBeGreaterThan(0);
});

it("recovers cleanly once polling succeeds again after transient failures", async () => {
  let calls = 0;
  runsGet.mockImplementation(() => {
    calls += 1;
    if (calls <= 3) {
      return Promise.reject(new MockApiError("Cannot reach the API at http://x. Is the backend running?", 0));
    }
    return Promise.resolve(OPTIMAL);
  });
  // The terminal-status branch re-fetches "latest" via load() - it must
  // reflect the same reality runsGet just settled on, or this test would be
  // asserting against its own broken fixture rather than the page.
  runsLatest.mockResolvedValue(OPTIMAL);
  await clickGenerate();

  for (let i = 0; i < 4; i++) {
    await vi.advanceTimersByTimeAsync(1000);
  }
  await vi.advanceTimersByTimeAsync(0);

  expect(screen.queryByText(/Cannot reach the API/)).toBeNull();
  expect(screen.getByText("Timetable generated successfully.")).toBeTruthy();
});

it("still surfaces an error once failures persist past the threshold", async () => {
  runsGet.mockRejectedValue(new MockApiError("Cannot reach the API at http://x. Is the backend running?", 0));
  await clickGenerate();

  for (let i = 0; i < 5; i++) {
    await vi.advanceTimersByTimeAsync(1000);
  }
  await vi.advanceTimersByTimeAsync(0);

  expect(screen.getByText(/Cannot reach the API/)).toBeTruthy();
});

it("follows a run that was started elsewhere instead of leaving it at RUNNING", async () => {
  // The upload page starts a solve straight after importing, then comes here.
  // Before this page followed runs it had not started, that one sat at
  // RUNNING until somebody reloaded.
  runsLatest.mockResolvedValue(RUNNING);
  runsGet.mockResolvedValue({ ...OPTIMAL, assignment_count: 52 });

  render(<GeneratePage />);
  for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(0);
  expect(screen.getAllByText(/Solving/).length).toBeGreaterThan(0);

  runsLatest.mockResolvedValue({ ...OPTIMAL, assignment_count: 52 });
  await vi.advanceTimersByTimeAsync(1000);
  for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(0);

  expect(runsStart).not.toHaveBeenCalled();
  expect(screen.getByText("Timetable generated successfully.")).toBeTruthy();
  expect(runsCheck).toHaveBeenCalledWith(5);
});
