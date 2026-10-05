"use client";

import { Download } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useAcademicContext } from "@/lib/academicContext";
import { api } from "@/lib/api";
import { roomLabel } from "@/lib/roomLabel";
import type { Faculty, Room, Run, Section } from "@/lib/types";
import { Card, EmptyState, PageHeader, Select } from "@/components/ui";
import { DownloadLink } from "@/components/DownloadLink";

/**
 * Export Center (Phase 9 PART 13). A dedicated place to grab a file when
 * there's no specific timetable page open - the timetable pages themselves
 * (master view, section/faculty/room grids) also carry their own export
 * buttons for exporting exactly what's on screen, filters included.
 */
export default function ExportCenterPage() {
  const { currentId } = useAcademicContext();
  const [run, setRun] = useState<Run | null>(null);
  const [sections, setSections] = useState<Section[]>([]);
  const [faculty, setFaculty] = useState<Faculty[]>([]);
  const [rooms, setRooms] = useState<Room[]>([]);

  useEffect(() => {
    api.runs
      .latest(currentId ?? undefined, true)
      .then(setRun)
      .catch(() => setRun(null));
    api.sections.listByContext(currentId ?? undefined).then(setSections).catch(() => setSections([]));
    api.faculty.list().then(setFaculty).catch(() => setFaculty([]));
    api.rooms.list().then((rs) => setRooms(rs.filter((r) => r.room_type !== "faculty"))).catch(() => setRooms([]));
  }, [currentId]);

  return (
    <>
      <PageHeader
        title="Export Timetable"
        description="Download the generated schedule for use in Excel or PDF."
      />

      {!run ? (
        <EmptyState>
          No timetable has been generated yet — upload the two files on the{" "}
          <Link href="/" className="underline">
            Dashboard
          </Link>{" "}
          first.
        </EmptyState>
      ) : (
        <>
        <div className="mb-8 rounded-md border border-line bg-surface p-5">
          <h2 className="text-base font-semibold text-ink">Timetable as Excel</h2>
          <p className="mt-1 max-w-prose text-sm text-ink-muted">
            One sheet, one row per class: day, time, faculty, subject, section, students, and
            the room with its block, floor, capacity and type.
          </p>
          <DownloadLink
            href={api.export.allocationXlsxUrl({ run_id: run.id })}
            className="mt-4 inline-flex h-11 items-center gap-2 rounded-md bg-accent px-5 text-sm font-medium text-accent-ink transition-colors hover:bg-accent-hover"
          >
            <Download aria-hidden="true" size={16} />
            Export Excel
          </DownloadLink>
        </div>
        <h2 className="mb-3 text-sm font-semibold text-ink">Other formats</h2>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Master timetable">
            <p className="mb-3 text-sm text-ink-muted">
              Every scheduled period, one row each, with the room&apos;s block, floor, capacity
              and BYOD.
            </p>
            <div className="flex flex-wrap gap-2">
              <ExportLink href={api.export.masterCsvUrl({ run_id: run.id })} label="CSV" />
              <ExportLink href={api.export.masterXlsxUrl({ run_id: run.id })} label="Excel" />
              <ExportLink href={api.export.masterPdfUrl({ run_id: run.id })} label="PDF (landscape)" />
            </div>
          </Card>

          <Card title="Full workbook">
            <p className="mb-3 text-sm text-ink-muted">
              One Excel file: master + assignments + a printable grid sheet for every
              section, faculty member and room used in this run.
            </p>
            <ExportLink href={api.export.runWorkbookUrl(run.id)} label="Download full workbook" />
          </Card>

          <Card title="Section timetable">
            <EntityExport
              items={sections.map((s) => ({ id: s.id, label: s.section_number, sublabel: `${s.strength} students` }))}
              urlFor={(id) => api.export.sectionPdfUrl(id, run.id)}
              emptyText="No sections in this context yet."
            />
          </Card>

          <Card title="Faculty timetable">
            <EntityExport
              items={faculty.map((f) => ({ id: f.id, label: f.name, sublabel: f.faculty_code }))}
              urlFor={(id) => api.export.facultyPdfUrl(id, run.id)}
              emptyText="No faculty yet."
            />
          </Card>

          <Card title="Room timetable">
            <EntityExport
              // room_code so two rooms numbered 101 are distinguishable in the
              // list; the block is already part of that label.
              items={rooms.map((r) => ({
                id: r.id,
                label: roomLabel(r),
                sublabel: r.room_type,
              }))}
              urlFor={(id) => api.export.roomPdfUrl(id, run.id)}
              emptyText="No rooms yet."
            />
          </Card>
        </div>
        </>
      )}
    </>
  );
}

function ExportLink({ href, label }: { href: string; label: string }) {
  return (
    <DownloadLink
      href={href}
      className="inline-flex h-9 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3 text-sm font-medium text-ink-soft transition-colors hover:bg-surface-hover"
    >
      <Download aria-hidden="true" size={14} />
      {label}
    </DownloadLink>
  );
}

function EntityExport({
  items,
  urlFor,
  emptyText,
}: {
  items: { id: number; label: string; sublabel?: string }[];
  urlFor: (id: number) => string;
  emptyText: string;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  if (items.length === 0) return <EmptyState>{emptyText}</EmptyState>;

  return (
    <div className="space-y-3">
      <Select
        value={selected ?? ""}
        onChange={(e) => setSelected(e.target.value ? Number(e.target.value) : null)}
      >
        <option value="">Choose…</option>
        {items.map((i) => (
          <option key={i.id} value={i.id}>
            {i.label}
            {i.sublabel ? ` — ${i.sublabel}` : ""}
          </option>
        ))}
      </Select>
      {selected !== null && <ExportLink href={urlFor(selected)} label="Download PDF" />}
    </div>
  );
}
