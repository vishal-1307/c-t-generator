"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { TimetablePicker, type PickerItem } from "@/components/TimetablePicker";

export default function FacultyTimetablesPage() {
  const load = useCallback(async (): Promise<PickerItem[]> => {
    const faculty = await api.faculty.list();
    return faculty.map((f) => ({
      id: f.id,
      primary: f.name,
      secondary: `${f.faculty_code}${f.department ? ` · ${f.department}` : ""} · ${f.subjects.length} subjects`,
      searchable: `${f.faculty_code} ${f.subjects.map((s) => s.code).join(" ")}`,
      tags: f.subjects.length === 0 ? [{ label: "no subjects", tone: "red" as const }] : undefined,
    }));
  }, []);

  return (
    <TimetablePicker
      title="Faculty timetables"
      description="Pick a faculty member to see their teaching week across every section."
      hrefBase="/timetable/faculty"
      load={load}
      emptyMessage="No faculty records yet."
      searchPlaceholder="Search name, code or subject…"
    />
  );
}
