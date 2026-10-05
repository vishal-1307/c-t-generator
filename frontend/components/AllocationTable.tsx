"use client";

import { Download, Search } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useAcademicContext } from "@/lib/academicContext";
import { ApiError, api } from "@/lib/api";
import type { Allocation, AllocationFilters, AllocationRow } from "@/lib/types";
import { Alert, Button, LoadingState, Select } from "@/components/ui";
import { DownloadLink } from "@/components/DownloadLink";

/**
 * The generated timetable as the teacher reads it: one row per class, with
 * the room it was given and the facts that made that room eligible.
 *
 * A phone gets cards and a desktop gets a table, and both are generated from
 * `FIELDS` below. That is the whole reason the list exists: two hand-written
 * renderings of the same thirteen columns drift the moment a fourteenth is
 * added, and the one that quietly loses a field is always the phone, because
 * nobody is looking at it when the column is added.
 */
export type Field = {
  key: string;
  label: string;
  cell: (r: AllocationRow) => ReactNode;
  /** What the card shows; defaults to `cell`. */
  card?: (r: AllocationRow) => ReactNode;
  className?: string;
  align?: "left" | "right";
};

export const FIELDS: Field[] = [
  { key: "day", label: "Day", cell: (r) => r.day, className: "whitespace-nowrap" },
  {
    key: "time",
    label: "Time",
    cell: (r) => `${r.start_time}–${r.end_time}`,
    className: "whitespace-nowrap font-mono text-xs",
  },
  { key: "faculty", label: "Faculty", cell: (r) => r.faculty_name, className: "whitespace-nowrap" },
  {
    key: "faculty_id",
    label: "Faculty ID",
    cell: (r) => r.faculty_id,
    className: "whitespace-nowrap font-mono text-xs",
  },
  {
    key: "subject_code",
    label: "Subject Code",
    cell: (r) => r.subject_code,
    className: "whitespace-nowrap font-semibold",
  },
  { key: "section", label: "Section", cell: (r) => r.section, className: "whitespace-nowrap" },
  { key: "students", label: "Students", cell: (r) => r.strength, align: "right" },
  {
    key: "room",
    label: "Room",
    cell: (r) => r.room,
    className: "whitespace-nowrap font-mono font-semibold",
  },
  { key: "block", label: "Block", cell: (r) => r.block },
  { key: "floor", label: "Floor", cell: (r) => r.floor || "—" },
  { key: "room_capacity", label: "Capacity", cell: (r) => r.room_capacity, align: "right" },
  { key: "byod", label: "BYOD", cell: (r) => (r.byod ? "Yes" : "No") },
  { key: "type", label: "Type", cell: (r) => r.class_type },
];

const NOTHING_YET = "No timetable has been generated yet.";

export function AllocationTable({ runId }: { runId?: number }) {
  // With no run named this is the current dataset's timetable - the same run
  // the week grids, the master view and the exports show for it.
  const { currentId } = useAcademicContext();
  const [filters, setFilters] = useState<AllocationFilters>({});
  const [search, setSearch] = useState("");
  const [data, setData] = useState<Allocation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const query: AllocationFilters = {
    ...filters,
    ...(runId ? { run_id: runId } : currentId ? { academic_context_id: currentId } : {}),
  };
  const key = JSON.stringify(query);

  useEffect(() => {
    let cancelled = false;
    api
      .allocation(JSON.parse(key) as AllocationFilters)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setData(null);
          setError(
            e instanceof ApiError && e.status === 404
              ? NOTHING_YET
              : e instanceof Error
                ? e.message
                : String(e),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  function choose(name: keyof AllocationFilters, value: string) {
    setFilters((f) => {
      const next = { ...f };
      if (value === "") delete next[name];
      else (next as Record<string, string | number>)[name] = name === "day" ? value : Number(value);
      return next;
    });
  }

  // Search is the one filter done here rather than on the server: it spans
  // every column at once, and the rows are already in the browser.
  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return all;
    return all.filter((r) =>
      FIELDS.some((f) => String(f.cell(r) ?? "").toLowerCase().includes(q)),
    );
  }, [data, search]);

  if (loading && !data) return <LoadingState label="Loading the timetable" rows={5} />;
  if (error) {
    const nothingYet = error === NOTHING_YET;
    return (
      <Alert tone="info" title={nothingYet ? error : "The timetable could not be loaded right now."}>
        {nothingYet
          ? "Upload Load.xlsx and Infra.xlsx on the dashboard to generate one."
          : error}
      </Alert>
    );
  }
  if (!data) return null;

  const filtered = Object.keys(filters).length > 0 || search.trim() !== "";

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 items-end gap-3 sm:flex sm:flex-wrap">
        <label className="col-span-2 block text-xs font-medium text-ink-soft sm:col-span-1">
          Search
          <span className="relative mt-1 block">
            <Search
              aria-hidden="true"
              size={14}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint"
            />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Subject, teacher, room…"
              className="h-9 w-full rounded-md border border-line-strong bg-surface pl-8 pr-3 text-sm text-ink outline-none placeholder:text-ink-faint focus:border-accent sm:w-56"
            />
          </span>
        </label>

        <Filter
          label="Faculty"
          value={filters.faculty_id}
          onChange={(v) => choose("faculty_id", v)}
          options={data.filters.faculty.map((o) => [String(o.id), o.label])}
        />
        <Filter
          label="Section"
          value={filters.section_id}
          onChange={(v) => choose("section_id", v)}
          options={data.filters.sections.map((o) => [String(o.id), o.label])}
        />
        <Filter
          label="Day"
          value={filters.day}
          onChange={(v) => choose("day", v)}
          options={data.filters.days.map((d) => [d, d])}
        />
        <Filter
          label="Room"
          value={filters.room_id}
          onChange={(v) => choose("room_id", v)}
          options={data.filters.rooms.map((o) => [String(o.id), o.label])}
        />
        {filtered && (
          <Button
            variant="ghost"
            onClick={() => {
              setFilters({});
              setSearch("");
            }}
          >
            Clear filters
          </Button>
        )}

        <DownloadLink
          href={api.export.allocationXlsxUrl({ ...filters, run_id: data.run_id })}
          className="col-span-2 inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-line-strong bg-surface px-3 text-sm font-medium text-ink-soft transition-colors hover:bg-surface-hover sm:ml-auto"
        >
          <Download aria-hidden="true" size={16} />
          Export Excel
        </DownloadLink>
      </div>

      <p className="text-xs text-ink-muted">
        {rows.length} of {data.classes} classes{filtered ? " (filtered)" : ""} · {data.dataset} ·
        Version {data.version}
        {data.publish_status === "PUBLISHED" ? " (published)" : " (draft)"}
      </p>
      {!runId && data.publish_status === "PUBLISHED" && (data.newest_version ?? 0) > data.version && (
        <p className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink-soft">
          This is the published timetable. Version {data.newest_version} was generated since and
          is a draft - publish it on Advanced › Versions to show it here.
        </p>
      )}

      {/* The phone. Every field in FIELDS appears, so a column added to the
          table cannot go missing here. */}
      <ul className="space-y-3 md:hidden">
        {rows.map((r, i) => (
          <li
            key={`card-${r.day}-${r.start_time}-${r.section}-${r.subject_code}-${i}`}
            className="rounded-md border border-line bg-surface p-3"
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-base font-semibold text-ink">{r.subject_code}</span>
              <span className="font-mono text-xs text-ink-muted">
                {r.start_time}–{r.end_time}
              </span>
            </div>
            <div className="mt-0.5 text-xs font-medium uppercase tracking-wide text-ink-muted">
              {r.day}
            </div>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              {FIELDS.filter((f) => !["day", "time", "subject_code"].includes(f.key)).map((f) => (
                <div key={f.key} className="contents">
                  <dt className="text-ink-muted">{f.label}</dt>
                  <dd className="text-ink">{(f.card ?? f.cell)(r)}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>

      {/* Everything wider. The scroll is the region's, not the page's. */}
      <div
        data-scroll-region
        className="hidden overflow-x-auto overscroll-x-contain rounded-md border border-line bg-surface md:block"
      >
        <table className="w-full min-w-[64rem] border-separate border-spacing-0 text-sm">
          <thead>
            <tr>
              {FIELDS.map((f) => (
                <th
                  key={f.key}
                  scope="col"
                  className={`sticky top-0 z-10 whitespace-nowrap border-b border-line bg-surface-sunken px-3 py-2 text-left text-xs font-medium text-ink-muted ${
                    f.align === "right" ? "text-right" : ""
                  }`}
                >
                  {f.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={`${r.day}-${r.start_time}-${r.section}-${r.subject_code}-${i}`}
                className="group"
              >
                {FIELDS.map((f) => (
                  <td
                    key={f.key}
                    className={`border-b border-line px-3 py-2 align-middle group-hover:bg-surface-sunken ${
                      f.align === "right" ? "text-right" : ""
                    } ${f.className ?? ""}`}
                  >
                    {f.cell(r)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {rows.length === 0 && (
        <p className="rounded-md border border-dashed border-line-strong px-4 py-8 text-center text-sm text-ink-muted">
          Nothing matches those filters.
        </p>
      )}
    </div>
  );
}

function Filter({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string | number | undefined;
  options: [string, string][];
  onChange: (value: string) => void;
}) {
  return (
    <div className="min-w-0 sm:w-40">
      <Select
        label={label}
        value={value === undefined ? "" : String(value)}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">All</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </Select>
    </div>
  );
}
