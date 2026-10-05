"use client";

import { use, useCallback } from "react";
import { api } from "@/lib/api";
import { useAcademicContext } from "@/lib/academicContext";
import { GridPage } from "@/components/GridPage";

export default function RoomTimetablePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { currentId } = useAcademicContext();
  const load = useCallback(
    () => api.timetable.room(Number(id), undefined, currentId ?? undefined),
    [id, currentId],
  );
  return (
    <GridPage
      load={load}
      backHref="/timetables/rooms"
      backLabel="All rooms"
      exportPdfUrl={(grid) => api.export.roomPdfUrl(Number(id), grid.run_id)}
    />
  );
}
