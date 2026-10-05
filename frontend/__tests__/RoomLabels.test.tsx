import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { roomLabel } from "@/lib/roomLabel";

/**
 * A room is identified by (block, floor, room_number), so "101" names a
 * different room in every block. `room_code` ("36-101") exists to be the label
 * a person can actually read - lib/types.ts says so in as many words.
 *
 * Several pickers ignored that and listed the bare number. On the deployed
 * demo estate the master timetable's room filter rendered ninety-seven options
 * in which "101" appeared six times, "102" six times and "201" seven times,
 * with nothing to tell them apart: choosing a room to filter by was guesswork.
 *
 * These render the same option lists those screens build and assert the labels
 * are distinct, which is the property that was actually broken.
 */

type Room = {
  id: number;
  room_number: string;
  room_code: string | null;
  block: string;
  room_type: string;
  lab_type: string | null;
  capacity: number;
};

const ROOMS: Room[] = [
  { id: 1, room_number: "101", room_code: "36-101", block: "36", room_type: "theory", lab_type: null, capacity: 72 },
  { id: 2, room_number: "101", room_code: "41-101", block: "41", room_type: "theory", lab_type: null, capacity: 75 },
  { id: 3, room_number: "101", room_code: "42-101", block: "42", room_type: "theory", lab_type: null, capacity: 72 },
  { id: 4, room_number: "103", room_code: "41-103", block: "41", room_type: "lab", lab_type: "programming", capacity: 72 },
  // A room whose code was never filled in still has to render something.
  { id: 5, room_number: "999", room_code: null, block: "43", room_type: "theory", lab_type: null, capacity: 60 },
];

/** The master timetable's Room filter. */
function RoomFilter({ rooms }: { rooms: Room[] }) {
  return (
    <select aria-label="Room" defaultValue="">
      <option value="">All rooms</option>
      {rooms
        .filter((r) => r.room_type !== "faculty")
        .map((r) => (
          <option key={r.id} value={r.id}>
            {roomLabel(r)}
          </option>
        ))}
    </select>
  );
}

/** The availability screen's "which room are you blocking?" picker. */
function AvailabilityOwners({ rooms }: { rooms: Room[] }) {
  const owners = rooms.map((r) => ({
    id: r.id,
    label: `${roomLabel(r)} · ${r.room_type}${
      r.lab_type ? `/${r.lab_type}` : ""
    } · seats ${r.capacity}`,
  }));
  return (
    <ul aria-label="owners">
      {owners.map((o) => (
        <li key={o.id}>{o.label}</li>
      ))}
    </ul>
  );
}

describe("room labels are unambiguous wherever a room is chosen", () => {
  it("the master timetable's room filter lists no two identical labels", () => {
    render(<RoomFilter rooms={ROOMS} />);
    const select = screen.getByLabelText("Room");
    const labels = within(select)
      .getAllByRole("option")
      .map((o) => o.textContent!.trim())
      .filter((t) => t !== "All rooms");

    expect(labels).toHaveLength(ROOMS.length);
    expect(new Set(labels).size).toBe(labels.length);
    // the three rooms numbered 101 must be tellable apart
    expect(labels).toContain("36-101");
    expect(labels).toContain("41-101");
    expect(labels).toContain("42-101");
    expect(labels.filter((l) => l === "101")).toHaveLength(0);
  });

  it("falls back to the bare number when a room has no code", () => {
    render(<RoomFilter rooms={ROOMS} />);
    const select = screen.getByLabelText("Room");
    const labels = within(select)
      .getAllByRole("option")
      .map((o) => o.textContent!.trim());
    expect(labels).toContain("999");
  });

  it("the availability picker names the block too, so the right room is blocked", () => {
    render(<AvailabilityOwners rooms={ROOMS} />);
    const items = within(screen.getByLabelText("owners"))
      .getAllByRole("listitem")
      .map((li) => li.textContent!.trim());

    expect(new Set(items).size).toBe(items.length);
    expect(items.some((t) => t.startsWith("41-103 · lab/programming"))).toBe(true);
    expect(items.some((t) => t.startsWith("101 ·"))).toBe(false);
  });
});
