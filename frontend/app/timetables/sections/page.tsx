"use client";

import { useCallback } from "react";
import { useAcademicContext } from "@/lib/academicContext";
import { api } from "@/lib/api";
import { TimetablePicker, type PickerItem } from "@/components/TimetablePicker";

export default function SectionTimetablesPage() {
  const { currentId } = useAcademicContext();

  const load = useCallback(async (): Promise<PickerItem[]> => {
    const sections = await api.sections.listByContext(currentId ?? undefined);
    return sections.map((s) => ({
      id: s.id,
      primary: s.section_number,
      secondary: `${s.strength} students · ${s.subjects.length} subjects`,
      searchable: s.subjects.map((sub) => sub.code).join(" "),
      tags: s.subjects.length === 0 ? [{ label: "no subjects", tone: "red" as const }] : undefined,
    }));
  }, [currentId]);

  return (
    <TimetablePicker
      title="Section timetables"
      description="Pick a section to see its weekly schedule. Click any class to view details or make a manual change."
      hrefBase="/timetable/section"
      load={load}
      emptyMessage="No sections in this academic context yet."
      searchPlaceholder="Search sections or subject codes…"
    />
  );
}
