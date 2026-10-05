"use client";

import { Suspense, useState } from "react";
import { api } from "@/lib/api";
import { useAcademicContext } from "@/lib/academicContext";
import { useApi, useMutation } from "@/lib/useApi";
import { useUrlState } from "@/lib/urlState";
import type { Faculty, Paged, Room, Section, Subject } from "@/lib/types";
import { EntityPicker } from "@/components/data/EntityPicker";
import {
  Badge,
  Card,
  EmptyState,
  ErrorBanner,
  PageHeader,
  Skeleton,
} from "@/components/ui";

/**
 * The relationships the solver runs on: who can teach what, who studies what,
 * and which lab a practical is reserved for.
 *
 * Rewritten for institution scale. The previous version fetched every faculty
 * member, every subject, every section and every room, then rendered a
 * `select` of all unassigned subjects inside every single card - two hundred
 * teachers against three hundred subjects is sixty thousand option elements
 * built before the page can paint, none of which anybody reads, because a
 * human scrolling a three-hundred-item dropdown is not choosing, they are
 * hunting. Each tab now reads one page of owners, and searches for a subject
 * only once somebody opens a picker.
 *
 * Which tab you are on, what you searched for and which page you are reading
 * all live in the URL, so this screen survives a reload and can be handed to a
 * colleague mid-task.
 */

type Tab = "faculty" | "curriculum" | "labs";

const TABS: { id: Tab; label: string; blurb: string }[] = [
  {
    id: "faculty",
    label: "Faculty to subjects",
    blurb:
      "Which subjects each faculty member is qualified to teach. The solver only ever assigns a faculty to a subject listed here.",
  },
  {
    id: "curriculum",
    label: "Section to subjects",
    blurb:
      "The curriculum each section takes. Every subject listed here must be scheduled for that section.",
  },
  {
    id: "labs",
    label: "Lab to subject",
    blurb:
      "A lab may be reserved for one subject. This is an override: without it a practical still draws from every lab whose capability matches.",
  },
];

const PAGE_SIZE = 25;
const DEFAULTS = { tab: "faculty", q: "", page: "0" };

export default function MappingsPage() {
  return (
    <Suspense fallback={<Skeleton rows={6} />}>
      <MappingsView />
    </Suspense>
  );
}

function MappingsView() {
  const [state, setState] = useUrlState(DEFAULTS);
  const tab = (TABS.find((t) => t.id === state.tab)?.id ?? "faculty") as Tab;
  const page = Math.max(0, Number(state.page) || 0);
  const active = TABS.find((t) => t.id === tab)!;
  const { run, error, setError } = useMutation();
  const setPage = (p: number) => setState({ page: String(p) });

  return (
    <>
      <PageHeader
        title="Mappings"
        description="The relationships that drive the solver. All three must be filled in before a timetable can be generated."
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="mb-4 flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setState({ tab: t.id, q: "", page: "0" })}
            className={`shrink-0 border-b-2 px-4 py-2 text-sm transition-colors ${
              tab === t.id
                ? "border-ink font-medium text-ink"
                : "border-transparent text-ink-muted hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <p className="mb-4 text-sm text-ink-muted">{active.blurb}</p>

      <input
        value={state.q}
        onChange={(e) => setState({ q: e.target.value, page: "0" })}
        placeholder={
          tab === "labs"
            ? "Search labs"
            : tab === "faculty"
              ? "Search faculty"
              : "Search sections"
        }
        className="mb-4 w-full max-w-sm rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-ink"
      />

      {tab === "faculty" ? (
        <FacultyTab q={state.q} page={page} setPage={setPage} run={run} />
      ) : tab === "curriculum" ? (
        <CurriculumTab q={state.q} page={page} setPage={setPage} run={run} />
      ) : (
        <LabsTab q={state.q} page={page} setPage={setPage} run={run} />
      )}
    </>
  );
}

type Run = (fn: () => Promise<unknown>, invalidate?: string[]) => Promise<boolean>;

interface TabProps {
  q: string;
  page: number;
  setPage: (p: number) => void;
  run: Run;
}

// --------------------------------------------------------------- the tabs

function FacultyTab({ q, page, setPage, run }: TabProps) {
  const { data, error, loading } = useApi<Paged<Faculty>>(
    ["faculty", "mappings", q, page],
    () =>
      api.faculty.page({
        q: q || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  );

  return (
    <Listing
      data={data}
      error={error}
      loading={loading}
      page={page}
      setPage={setPage}
      empty="No faculty match. Add some on the Faculty page."
    >
      {(f: Faculty) => (
        <OwnerCard
          key={f.id}
          title={
            <>
              <span className="font-mono text-xs text-ink-muted">{f.faculty_code}</span>{" "}
              {f.name}
            </>
          }
          warning={f.subjects.length === 0 ? "teaches nothing" : null}
          chips={f.subjects}
          onRemove={(id) => run(() => api.faculty.removeSubject(f.id, id), ["faculty"])}
          picker={
            <EntityPicker<Subject>
              prefix="subjects"
              search={(term) => api.subjects.page({ q: term || undefined, limit: 20, brief: true })}
              label={(s) => `${s.code} - ${s.name}`}
              disabledIds={new Set(f.subjects.map((s) => s.id))}
              onPick={(s) => run(() => api.faculty.addSubject(f.id, s.id), ["faculty"])}
              placeholder="Search subjects"
            />
          }
        />
      )}
    </Listing>
  );
}

function CurriculumTab({ q, page, setPage, run }: TabProps) {
  const { currentId } = useAcademicContext();
  const { data, error, loading } = useApi<Paged<Section>>(
    ["sections", "mappings", q, page, currentId],
    () =>
      api.sections.pageByContext(currentId ?? undefined, {
        q: q || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  );

  return (
    <Listing
      data={data}
      error={error}
      loading={loading}
      page={page}
      setPage={setPage}
      empty="No sections match in this semester. Add some on the Sections page."
    >
      {(s: Section) => (
        <OwnerCard
          key={s.id}
          title={
            <>
              <span className="font-mono text-xs">{s.section_number}</span>{" "}
              <span className="text-ink-muted">({s.strength} students)</span>
            </>
          }
          warning={s.subjects.length === 0 ? "no curriculum" : null}
          chips={s.subjects}
          onRemove={(id) => run(() => api.sections.removeSubject(s.id, id), ["sections"])}
          picker={
            <EntityPicker<Subject>
              prefix="subjects"
              search={(term) => api.subjects.page({ q: term || undefined, limit: 20, brief: true })}
              label={(sub) => `${sub.code} - ${sub.name}`}
              disabledIds={new Set(s.subjects.map((x) => x.id))}
              onPick={(sub) => run(() => api.sections.addSubject(s.id, sub.id), ["sections"])}
              placeholder="Search subjects"
            />
          }
        />
      )}
    </Listing>
  );
}

function LabsTab({ q, page, setPage, run }: TabProps) {
  const { data, error, loading } = useApi<Paged<Room>>(
    ["rooms", "mappings", q, page],
    () =>
      api.rooms.pageOfType("lab", {
        q: q || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  );

  return (
    <Listing
      data={data}
      error={error}
      loading={loading}
      page={page}
      setPage={setPage}
      empty="No labs match. Add a room of type lab on the Rooms page."
    >
      {(lab: Room) => <LabRow key={lab.id} lab={lab} run={run} />}
    </Listing>
  );
}

/**
 * A lab is reserved by searching for the subject rather than by choosing from
 * every subject in the catalogue. The pinned subject is shown by whatever the
 * row already knows - the list payload carries the id, and the code arrives
 * once somebody sets it here - which avoids a lookup request per lab.
 */
function LabRow({ lab, run }: { lab: Room; run: Run }) {
  const [justPinned, setJustPinned] = useState<string | null>(null);
  const pinned =
    justPinned ?? (lab.fixed_subject_id ? `subject #${lab.fixed_subject_id}` : null);

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <span className="font-mono text-xs">{lab.room_code ?? lab.room_number}</span>
          <span className="ml-2 text-xs text-ink-faint">
            seats {lab.capacity}
            {lab.lab_type ? ` · ${lab.lab_type}` : " · no capability recorded"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {pinned ? (
            <button
              onClick={() => {
                setJustPinned(null);
                void run(() => api.rooms.setFixedSubject(lab.id, null), ["rooms"]);
              }}
              title="Remove the reservation"
              className="inline-flex items-center gap-1 rounded bg-info-surface px-1.5 py-0.5 text-xs font-medium text-info hover:bg-info-surface"
            >
              {pinned} <span className="text-ink-faint">x</span>
            </button>
          ) : (
            <span className="text-xs text-ink-faint">not reserved</span>
          )}
          <EntityPicker<Subject>
            prefix="subjects"
            search={(term) => api.subjects.page({ q: term || undefined, limit: 20, brief: true })}
            label={(s) => `${s.code} - ${s.name}`}
            buttonLabel={pinned ? "change" : "+ reserve"}
            onPick={(s) => {
              setJustPinned(s.code);
              void run(() => api.rooms.setFixedSubject(lab.id, s.id), ["rooms"]);
            }}
            placeholder="Search subjects"
          />
        </div>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------ the pieces

/** One page of owners, with the paging controls and the states around it. */
function Listing<T extends { id: number }>({
  data,
  error,
  loading,
  page,
  setPage,
  empty,
  children,
}: {
  data: Paged<T> | undefined;
  error: string | null;
  loading: boolean;
  page: number;
  setPage: (p: number) => void;
  empty: string;
  children: (item: T) => React.ReactNode;
}) {
  if (error) return <ErrorBanner error={error} />;
  if (loading && !data) return <Skeleton rows={5} />;

  const rows = data?.rows ?? [];
  if (rows.length === 0) return <EmptyState>{empty}</EmptyState>;

  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <>
      <div className="space-y-3">{rows.map(children)}</div>
      <div className="mt-4 flex items-center justify-between text-xs text-ink-muted">
        <span>
          {page * PAGE_SIZE + 1}-{page * PAGE_SIZE + rows.length} of {total}
        </span>
        {pages > 1 && (
          <div className="flex gap-2">
            <button
              onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0}
              className="rounded-md border border-line-strong px-2 py-1 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              onClick={() => setPage(Math.min(pages - 1, page + 1))}
              disabled={page >= pages - 1}
              className="rounded-md border border-line-strong px-2 py-1 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        )}
      </div>
    </>
  );
}

function OwnerCard({
  title,
  warning,
  chips,
  onRemove,
  picker,
}: {
  title: React.ReactNode;
  warning: string | null;
  chips: { id: number; code: string; type: string }[];
  onRemove: (id: number) => void;
  picker: React.ReactNode;
}) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-48">
          <div className="text-sm">{title}</div>
          {warning && (
            <div className="mt-1">
              <Badge tone="red">{warning}</Badge>
            </div>
          )}
        </div>

        <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
          <div className="flex flex-wrap gap-1">
            {chips.map((c) => (
              <button
                key={c.id}
                onClick={() => onRemove(c.id)}
                title="Remove"
                className={`group inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium transition-colors ${
                  c.type === "practical"
                    ? "bg-caution-surface text-caution hover:bg-caution-surface"
                    : c.type === "mixed"
                      ? "bg-violet-50 text-violet-700 hover:bg-violet-100"
                      : "bg-info-surface text-info hover:bg-info-surface"
                }`}
              >
                {c.code}
                <span className="text-ink-faint group-hover:text-blocker">x</span>
              </button>
            ))}
          </div>
          {picker}
        </div>
      </div>
    </Card>
  );
}
