import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { stagesFor } from "@/lib/dashboardState";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));
vi.mock("@/lib/api", () => ({ fetchFile: vi.fn(), ApiError: Error }));

const { GenerationProgress } = await import("@/components/dashboard/GenerateAction");

/**
 * Progress shows the step in hand and the one after it, not five rows where
 * three are grey placeholders - and never a percentage, because the server
 * reports when a step ends, not how far through one it is.
 */
describe("generation progress", () => {
  const solving = stagesFor({ applied: true, runId: 7, status: "RUNNING", checked: false, saved: false });

  it("names the step in progress and the next one", () => {
    render(<GenerationProgress stages={solving} elapsed={42} />);
    const live = screen.getByRole("progressbar").parentElement!;
    expect(within(live).getByText("Step 3 of 5")).toBeTruthy();
    expect(within(live).getByText("Generating timetable")).toBeTruthy();
    expect(within(live).getByText("Next: Verifying result")).toBeTruthy();
    expect(within(live).getByText(/Data validated · Building schedule/)).toBeTruthy();
    expect(within(live).getByText("42s")).toBeTruthy();
  });

  it("never claims a percentage", () => {
    render(<GenerationProgress stages={solving} elapsed={3} />);
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBeNull();
    expect(document.body.textContent).not.toMatch(/%/);
  });

  it("keeps the full list a click away", () => {
    render(<GenerationProgress stages={solving} elapsed={3} />);
    expect(screen.getByText("Show all steps")).toBeTruthy();
    const steps = screen.getByRole("list");
    expect(within(steps).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Data validated - done",
      "Building schedule - done",
      "Generating timetable - in progress",
      "Verifying result - waiting",
      "Saving - waiting",
    ]);
  });
});
