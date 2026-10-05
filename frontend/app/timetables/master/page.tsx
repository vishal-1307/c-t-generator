"use client";

import { ChevronLeft, ChevronRight, Download, Lock } from "lucide-react";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useAcademicContext } from "@/lib/academicContext";
import { useUrlState } from "@/lib/urlState";
import { ApiError, api } from "@/lib/api";
import type { MasterRow, MasterTimetable } from "@/lib/types";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBanner,
  Input,
  PageHeader,
  Select,
  Skeleton,
} from "@/components/ui";
import { ClassDetailDrawer, type ClassRef } from "@/components/ClassDetailDrawer";
import { DownloadLink } from "@/components/DownloadLink";

const PAGE_SIZE = 50;

/**
 * Every scheduled period of one timetable, in one filterable list.
 *
 * An administrator's tool, not the teacher's table: one row per `Assignment`,
 * so a two-period lab is two rows, which is what makes it the place to check
 * that nothing is missing, doubled or stale. It reads the same rows as the week
 * grids, the teacher's table and every export - nothing here reconstructs a
 * timetable of its own.
 *
 * Every filter, the search included, is answered by the server over the whole
 * timetable, so the count and the pages are always of what matches.
 */

interface Filters {
  section_id: string;
  faculty_id: string;
  subject_id: string;
  room_id: string;
  day_index: string;
  block: string;
  floor: string;
  room_type: string;
  locked: string;
}

const BLANK: Filters = {
  section_id: "",
  faculty_id: "",
  subject_id: "",
  room_id: "",
  day_index: "",
  block: "",
  floor: "",
  room_type: "",
  locked: "",
};

/**
 * The filters, the page, the search and the version all live in the address
 * bar, so a narrowed-down view can be sent to somebody, survives a reload, and
 * comes back intact after following a link out and pressing back. `run` is
 * set by "View" on the Versions page; without it this is the current
 * timetable. Module-level so the identity is stable across renders.
 */
const URL_DEFAULTS = { ...BLANK, page: "0", q: "", run: "" };
const FILTER_KEYS = Object.keys(BLANK) as (keyof Filters)[];

const PUBLISH_LABEL: Record<string, string> = {
  DRAFT: "Draft",
  VALIDATED: "Validated",
  PUBLISHED: "Published",
  ARCHIVED: "Archived",
};

function hhmm(t: string) {
  return t.slice(0, 5);
}

function MasterTimetableView() {
  const { currentId } = useAcademicContext();
  const [data, setData] = useState<MasterTimetable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [urlState, setUrlState] = useUrlState(URL_DEFAULTS);
  const [target, setTarget] = useState<ClassRef | null>(null);

  const filters = useMemo(() => {
    const out = {} as Filters;
    for (const key of FILTER_KEYS) out[key] = urlState[key];
    return out;
  }, [urlState]);
  const page = Math.max(0, Number(urlState.page) || 0);
  const search = urlState.q;
  const runId = urlState.run ? Number(urlState.run) : undefined;

  // Typing is fast and the server is not: ask once the typing pauses.
  const [query, setQuery] = useState(search);
  useEffect(() => {
    const t = setTimeout(() => setQuery(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  /** What every request and every export is narrowed by. */
  const params = useMemo(() => {
    const out: Record<string, string | number | boolean | undefined> = runId
      ? { run_id: runId }
      : { academic_context_id: currentId ?? undefined };
    for (const [key, value] of Object.entries(filters)) {
      if (value !== "") out[key] = value;
    }
    if (query.trim()) out.q = query.trim();
    return out;
  }, [currentId, filters, query, runId]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      setData(
        await api.timetable.master({ ...params, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
      );
      setError(null);
    } catch (e) {
      setData(null);
      setError(
        e instanceof ApiError && e.status === 404
          ? null
          : e instanceof ApiError
            ? e.message
            : String(e),
      );
    } finally {
      setLoading(false);
    }
  }, [params, page]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchData();
  }, [fetchData]);

  function setFilter(key: keyof Filters, value: string) {
    // Narrowing the result set invalidates the page number - page 7 of a
    // filtered view is usually not there any more, and landing on an empty
    // page reads as "no results" rather than "wrong page".
    setUrlState({ [key]: value, page: "0" } as Partial<typeof URL_DEFAULTS>);
  }

  const options = data?.options;
  const activeFilterCount =
    Object.values(filters).filter((v) => v !== "").length + (search.trim() ? 1 : 0);
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const editable = data ? !["PUBLISHED", "ARCHIVED"].includes(data.publish_status ?? "") : true;

  function open(r: MasterRow) {
    setTarget({
      assignment_id: r.assignment_id,
      subject_code: r.subject_code,
      subject_name: r.subject_name,
      faculty_id: r.faculty_id,
      faculty_name: r.faculty_name,
      faculty_code: r.faculty_code,
      room_id: r.room_id,
      room_number: r.room_number,
      block: r.block,
      section_id: r.section_id,
      section_number: r.section_number,
      subject_id: r.subject_id,
      locked: r.locked,
      when: `${r.day} ${hhmm(r.start_time)}–${hhmm(r.end_time)}`,
    });
  }

  const exportLink =
    "inline-flex h-9 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3 text-sm text-ink-soft hover:bg-surface-sunken";

  return (
    <>
      <PageHeader
        title="Master Timetable"
        description="Every scheduled period of the timetable in one list. Filter it to check a teacher, a room, a day or a block."
        actions={
          data && data.total > 0 ? (
            <>
              <DownloadLink href={api.export.masterXlsxUrl(params)} className={exportLink}>
                <Download aria-hidden="true" size={14} />
                Excel
              </DownloadLink>
              <DownloadLink href={api.export.masterCsvUrl(params)} className={exportLink}>
                CSV
              </DownloadLink>
              <DownloadLink href={api.export.masterPdfUrl(params)} className={exportLink}>
                PDF
              </DownloadLink>
            </>
          ) : undefined
        }
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {data && (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
          <Badge tone={data.publish_status === "PUBLISHED" ? "green" : "slate"}>
            Version {data.version ?? "?"} · {PUBLISH_LABEL[data.publish_status ?? ""] ?? "Draft"}
          </Badge>
          {data.dataset && <span>{data.dataset}</span>}
          {runId && (
            <button
              type="button"
              onClick={() => setUrlState({ run: "", page: "0" })}
              className="underline hover:text-ink"
            >
              Show the current timetable instead
            </button>
          )}
        </div>
      )}

      {data && !editable && (
        <p className="mb-3 rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink-soft">
          This version is {PUBLISH_LABEL[data.publish_status ?? ""]?.toLowerCase()}, so its classes
          cannot be moved or changed here. Generate a new version to make a change, then publish it.
        </p>
      )}

      <div className="mb-4 rounded-md border border-line bg-surface p-3">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-5">
          <div className="col-span-2 lg:col-span-1">
            <Input
              label="Search"
              type="search"
              value={search}
              onChange={(e) => setUrlState({ q: e.target.value, page: "0" })}
              placeholder="Subject, section, teacher, room…"
            />
          </div>
          <Select label="Section" value={filters.section_id} onChange={(e) => setFilter("section_id", e.target.value)}>
            <option value="">All sections</option>
            {options?.sections.map((o) => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </Select>
          <Select label="Faculty" value={filters.faculty_id} onChange={(e) => setFilter("faculty_id", e.target.value)}>
            <option value="">All faculty</option>
            {options?.faculty.map((o) => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </Select>
          <Select label="Subject" value={filters.subject_id} onChange={(e) => setFilter("subject_id", e.target.value)}>
            <option value="">All subjects</option>
            {options?.subjects.map((o) => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </Select>
          <Select label="Room" value={filters.room_id} onChange={(e) => setFilter("room_id", e.target.value)}>
            <option value="">All rooms</option>
            {options?.rooms.map((o) => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </Select>
          <Select label="Day" value={filters.day_index} onChange={(e) => setFilter("day_index", e.target.value)}>
            <option value="">All days</option>
            {options?.days.map((o) => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </Select>
          <Select label="Block" value={filters.block} onChange={(e) => setFilter("block", e.target.value)}>
            <option value="">All blocks</option>
            {options?.blocks.map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </Select>
          <Select label="Floor" value={filters.floor} onChange={(e) => setFilter("floor", e.target.value)}>
            <option value="">All floors</option>
            {options?.floors.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </Select>
          <Select label="Room type" value={filters.room_type} onChange={(e) => setFilter("room_type", e.target.value)}>
            <option value="">All types</option>
            {options?.room_types.map((o) => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </Select>
          <Select label="Lock state" value={filters.locked} onChange={(e) => setFilter("locked", e.target.value)}>
            <option value="">Locked and unlocked</option>
            <option value="true">Locked only</option>
            <option value="false">Unlocked only</option>
          </Select>
        </div>
        {activeFilterCount > 0 && (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-ink-muted">
            <span>
              {activeFilterCount} filter{activeFilterCount === 1 ? "" : "s"} applied. The exports
              contain exactly what matches.
            </span>
            <Button variant="secondary" onClick={() => setUrlState({ ...BLANK, q: "", page: "0" })}>
              Clear filters
            </Button>
          </div>
        )}
      </div>

      {loading && !data ? (
        <Skeleton rows={8} />
      ) : !data ? (
        !error && (
          <EmptyState>
            No timetable has been generated yet. Upload the files on the Dashboard and generate one.
          </EmptyState>
        )
      ) : data.total === 0 ? (
        <EmptyState>
          {activeFilterCount > 0
            ? "No scheduled period matches these filters."
            : "This timetable has no scheduled periods."}
        </EmptyState>
      ) : (
        <>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-ink-muted">
            <span>
              {total} scheduled period{total === 1 ? "" : "s"}
              {activeFilterCount > 0 ? " match" : ""} · showing {page * PAGE_SIZE + 1}–
              {Math.min((page + 1) * PAGE_SIZE, total)}
            </span>
            {loading && <span className="text-ink-faint">Refreshing…</span>}
          </div>

          {/* relative: the visually hidden header text is absolutely positioned,
              and without a positioned container it escapes the scroll box and
              makes the whole page scroll sideways. */}
          <div className="relative overflow-x-auto rounded-md border border-line bg-surface">
            <table className="w-full min-w-[80rem] text-sm">
              <thead className="bg-surface-sunken text-left text-xs uppercase tracking-wide text-ink-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Day</th>
                  <th className="px-3 py-2 font-medium">Start</th>
                  <th className="px-3 py-2 font-medium">End</th>
                  <th className="px-3 py-2 font-medium">Subject code</th>
                  <th className="px-3 py-2 font-medium">Subject</th>
                  <th className="px-3 py-2 font-medium">Section</th>
                  <th className="px-3 py-2 font-medium">Faculty</th>
                  <th className="px-3 py-2 font-medium">Faculty ID</th>
                  <th className="px-3 py-2 font-medium">Room</th>
                  <th className="px-3 py-2 font-medium">Block</th>
                  <th className="px-3 py-2 font-medium">Floor</th>
                  <th className="px-3 py-2 text-right font-medium">Capacity</th>
                  <th className="px-3 py-2 font-medium">Type</th>
                  <th className="px-3 py-2 font-medium">BYOD</th>
                  <th className="px-3 py-2">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {data.rows.map((r) => (
                  <tr key={r.assignment_id} className="hover:bg-surface-sunken">
                    <td className="whitespace-nowrap px-3 py-2 text-ink">{r.day}</td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{hhmm(r.start_time)}</td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{hhmm(r.end_time)}</td>
                    <td className="whitespace-nowrap px-3 py-2 font-semibold text-ink">{r.subject_code}</td>
                    <td className="max-w-[14rem] truncate px-3 py-2 text-ink-soft" title={r.subject_name}>
                      {r.subject_name}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      {r.section_number}
                      <span className="ml-1 text-[11px] text-ink-faint">({r.strength})</span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-ink">{r.faculty_name}</td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{r.faculty_code}</td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-xs font-semibold">
                      {r.room_code || r.room_number}
                    </td>
                    <td className="px-3 py-2 text-xs">{r.block || "—"}</td>
                    <td className="px-3 py-2 text-xs">{r.floor || "—"}</td>
                    <td className="px-3 py-2 text-right text-xs tabular-nums">{r.room_capacity}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-xs">{r.class_type}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-xs">
                      {r.room_byod ? "Yes" : "No"}
                      {r.byod_required && <span className="ml-1 text-ink-faint">(required)</span>}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        {r.locked && (
                          <Badge tone="amber">
                            <Lock aria-hidden="true" size={11} />
                            Locked
                          </Badge>
                        )}
                        <Button variant="ghost" onClick={() => open(r)}>
                          Details
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="mt-3 flex items-center justify-between gap-2 text-sm">
              <Button
                variant="secondary"
                onClick={() => setUrlState({ page: String(Math.max(0, page - 1)) })}
                disabled={page === 0}
              >
                <ChevronLeft aria-hidden="true" size={14} />
                Previous
              </Button>
              <span className="text-xs text-ink-muted">
                Page {page + 1} of {totalPages}
              </span>
              <Button
                variant="secondary"
                onClick={() => setUrlState({ page: String(Math.min(totalPages - 1, page + 1)) })}
                disabled={page >= totalPages - 1}
              >
                Next
                <ChevronRight aria-hidden="true" size={14} />
              </Button>
            </div>
          )}
        </>
      )}

      <ClassDetailDrawer
        target={target}
        onClose={() => setTarget(null)}
        onChanged={() => {
          setTarget(null);
          void fetchData();
        }}
      />
    </>
  );
}

/**
 * `useSearchParams` makes the component below client-rendered up to the
 * nearest Suspense boundary, and a prerendered route without one fails the
 * production build outright. Development renders on demand and so never
 * suspends, which means the boundary is easy to omit and only hear about from
 * `next build`.
 */
export default function MasterTimetablePage() {
  return (
    <Suspense fallback={<Skeleton rows={8} />}>
      <MasterTimetableView />
    </Suspense>
  );
}
