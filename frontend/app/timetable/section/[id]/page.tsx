"use client";

import { use, useCallback } from "react";
import { api } from "@/lib/api";
import { useAcademicContext } from "@/lib/academicContext";
import { GridPage } from "@/components/GridPage";

export default function SectionTimetablePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { currentId } = useAcademicContext();
  const load = useCallback(
    () => api.timetable.section(Number(id), undefined, currentId ?? undefined),
    [id, currentId],
  );
  return (
    <GridPage
      load={load}
      backHref="/timetables/sections"
      backLabel="All sections"
      exportPdfUrl={(grid) => api.export.sectionPdfUrl(Number(id), grid.run_id)}
    />
  );
}
