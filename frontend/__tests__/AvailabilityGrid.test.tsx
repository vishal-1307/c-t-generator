import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AvailabilityGrid, type BlockRow } from "@/components/AvailabilityGrid";
import type { TimeSlot } from "@/lib/types";

/**
 * Availability is a restriction someone declared, not the timetable. A period
 * with no restriction says "Available" - never "Free", which reads as "has no
 * class then" and belongs to the timetable views, where it is "No Class".
 */

function slot(id: number, day: string, dayIndex: number, period: number): TimeSlot {
  return {
    id,
    day,
    day_index: dayIndex,
    period_index: period,
    start_time: `0${9 + period}:00:00`.slice(-8),
    end_time: `${10 + period}:00:00`,
    is_lunch: false,
  } as TimeSlot;
}

const SLOTS = [slot(1, "Monday", 0, 0), slot(2, "Monday", 0, 1)];

describe("the availability grid", () => {
  it("says Available where no restriction is declared, and Unavailable where one is", () => {
    const blocks: BlockRow[] = [{ id: 7, timeslot_id: 2, reason: null }];
    render(
      <AvailabilityGrid slots={SLOTS} blocks={blocks} onBlock={vi.fn()} onUnblock={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Monday P1: Available" }).textContent).toBe("Available");
    expect(screen.getByRole("button", { name: "Monday P2: Unavailable" }).textContent).toBe("Unavailable");
    expect(screen.queryByText("Free")).toBeNull();
    expect(screen.queryByText("Blocked")).toBeNull();
    // The legend explains both words.
    expect(screen.getByText(/no restriction declared/)).toBeTruthy();
    expect(screen.getByText(/must not be scheduled/)).toBeTruthy();
  });

  it("declares a restriction on an Available period and lifts one from an Unavailable period", async () => {
    const onBlock = vi.fn().mockResolvedValue(undefined);
    const onUnblock = vi.fn().mockResolvedValue(undefined);
    render(
      <AvailabilityGrid
        slots={SLOTS}
        blocks={[{ id: 7, timeslot_id: 2, reason: null }]}
        onBlock={onBlock}
        onUnblock={onUnblock}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Monday P1: Available" }));
    await waitFor(() => expect(onBlock).toHaveBeenCalledWith(1));
    fireEvent.click(screen.getByRole("button", { name: "Monday P2: Unavailable" }));
    await waitFor(() => expect(onUnblock).toHaveBeenCalledWith(7));
  });
});
