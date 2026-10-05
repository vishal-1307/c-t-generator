"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { TimetablePicker, type PickerItem } from "@/components/TimetablePicker";

export default function RoomTimetablesPage() {
  const load = useCallback(async (): Promise<PickerItem[]> => {
    const rooms = await api.rooms.list();
    // Faculty rooms are never schedulable, so they have no timetable to show -
    // listing them here would imply otherwise.
    return rooms
      .filter((r) => r.room_type !== "faculty")
      .map((r) => ({
        id: r.id,
        primary: r.room_number,
        secondary: [
          r.block ? `Block ${r.block}` : null,
          r.floor ? `Floor ${r.floor}` : null,
          `seats ${r.capacity}`,
        ]
          .filter(Boolean)
          .join(" · "),
        searchable: `${r.room_type} ${r.lab_type ?? ""}`,
        tags: [
          { label: r.room_type, tone: r.room_type === "lab" ? ("amber" as const) : ("blue" as const) },
          ...(r.lab_type ? [{ label: r.lab_type, tone: "slate" as const }] : []),
          ...(r.is_active ? [] : [{ label: "inactive", tone: "red" as const }]),
        ],
      }));
  }, []);

  return (
    <TimetablePicker
      title="Room timetables"
      description="Pick a room to see its occupancy across the week. Faculty rooms are excluded — they are never schedulable."
      hrefBase="/timetable/room"
      load={load}
      emptyMessage="No schedulable rooms yet."
      searchPlaceholder="Search room, block or lab type…"
    />
  );
}
