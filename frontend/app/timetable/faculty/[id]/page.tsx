"use client";

import { use, useCallback } from "react";
import { api } from "@/lib/api";
import { useAcademicContext } from "@/lib/academicContext";
import { GridPage } from "@/components/GridPage";

export default function FacultyTimetablePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { currentId } = useAcademicContext();
  const load = useCallback(
    () => api.timetable.faculty(Number(id), undefined, currentId ?? undefined),
    [id, currentId],
  );
  return (
    <GridPage
      load={load}
      backHref="/timetables/faculty"
      backLabel="All faculty"
      exportPdfUrl={(grid) => api.export.facultyPdfUrl(Number(id), grid.run_id)}
    />
  );
}
