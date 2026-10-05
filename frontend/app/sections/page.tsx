"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useAcademicContext } from "@/lib/academicContext";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/useApi";
import { ServerTable } from "@/components/data/ServerTable";
import type { Section } from "@/lib/types";
import {
  Button,
  Card,
  ErrorBanner,
  Input,
  PageHeader,
} from "@/components/ui";

const BLANK_CONTEXT = { academic_year: "", semester: 1, program: "", department: "" };

export default function SectionsPage() {
  return (
    <Suspense fallback={null}>
      <SectionsPageInner />
    </Suspense>
  );
}

function SectionsPageInner() {
  const { contexts, currentId, current, refresh: refreshContexts, setCurrentId } =
    useAcademicContext();
  const { run, error, setError } = useMutation();

  const [contextForm, setContextForm] = useState(BLANK_CONTEXT);
  const [showNewContext, setShowNewContext] = useState(false);
  const [form, setForm] = useState({ section_number: "", strength: 60 });
  const [editingId, setEditingId] = useState<number | null>(null);

  const searchParams = useSearchParams();
  const openedEditId = useRef<string | null>(null);
  useEffect(() => {
    const editId = searchParams.get("edit");
    if (!editId || editId === openedEditId.current) return;
    // By id rather than from the loaded rows: the list is one page of a paged
    // query now, and the row being edited is frequently not on it.
    openedEditId.current = editId;
    void api.sections
      .get(Number(editId))
      .then(startEdit)
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const reset = useCallback(() => {
    setForm({ section_number: "", strength: 60 });
    setEditingId(null);
  }, []);

  async function createContext(e: React.FormEvent) {
    e.preventDefault();
    try {
      const ctx = await api.academicContexts.create(contextForm);
      setContextForm(BLANK_CONTEXT);
      setShowNewContext(false);
      await refreshContexts();
      setCurrentId(ctx.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (currentId === null) {
      setError("Choose or create an academic context first.");
      return;
    }
    const ok = await run(
      () =>
        editingId === null
          ? api.sections.create({
              academic_context_id: currentId,
              section_number: form.section_number.trim(),
              strength: form.strength,
            })
          : api.sections.update(editingId, {
              section_number: form.section_number.trim(),
              strength: form.strength,
            }),
      ["sections"],
    );
    if (ok) reset();
  }

  function startEdit(s: Section) {
    setEditingId(s.id);
    setCurrentId(s.academic_context_id);
    setForm({ section_number: s.section_number, strength: s.strength });
  }

  // Show only the working context's sections; the sidebar selector is the
  // single place that choice is made.

  return (
    <>
      <PageHeader
        title="Sections"
        description="Every section belongs to one academic context. Theory rooms are not per-section — the solver picks from the eligible pool on the Rooms page."
        actions={
          <Button variant="secondary" onClick={() => setShowNewContext((v) => !v)}>
            {showNewContext ? "Cancel" : "New academic context"}
          </Button>
        }
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {(showNewContext || contexts.length === 0) && (
        <div className="mb-6">
          <Card title="Create academic context">
            <form onSubmit={createContext} className="grid gap-3 sm:grid-cols-4">
              <Input
                label="Academic year"
                value={contextForm.academic_year}
                onChange={(e) => setContextForm({ ...contextForm, academic_year: e.target.value })}
                placeholder="2026-27"
                required
              />
              <Input
                label="Semester"
                type="number"
                min={1}
                value={contextForm.semester}
                onChange={(e) => setContextForm({ ...contextForm, semester: Number(e.target.value) })}
                required
              />
              <Input
                label="Program"
                value={contextForm.program}
                onChange={(e) => setContextForm({ ...contextForm, program: e.target.value })}
                placeholder="BCA"
                required
              />
              <Input
                label="Department"
                value={contextForm.department}
                onChange={(e) => setContextForm({ ...contextForm, department: e.target.value })}
                placeholder="CSE"
                required
              />
              <div className="sm:col-span-4">
                <Button type="submit">Create context</Button>
              </div>
            </form>
          </Card>
        </div>
      )}

      {contexts.length > 0 && (
        <div className="grid gap-6 md:grid-cols-[320px_1fr]">
          <Card title={editingId === null ? "Add section" : "Edit section"}>
            <form onSubmit={submit} className="space-y-3">
              <p className="text-xs text-ink-muted">
                Adding to <span className="font-medium text-ink-soft">{current?.label ?? "—"}</span>
              </p>
              <Input
                label="Section number"
                value={form.section_number}
                onChange={(e) => setForm({ ...form, section_number: e.target.value })}
                placeholder="D2402"
                required
              />
              <Input
                label="Strength"
                type="number"
                min={1}
                value={form.strength}
                onChange={(e) => setForm({ ...form, strength: Number(e.target.value) })}
                required
                hint="Eligible rooms must seat at least this many."
              />
              <div className="flex gap-2 pt-1">
                <Button type="submit">{editingId === null ? "Add" : "Save"}</Button>
                {editingId !== null && (
                  <Button type="button" variant="secondary" onClick={reset}>
                    Cancel
                  </Button>
                )}
              </div>
            </form>
          </Card>

          <div className="min-w-0">
            <ServerTable<Section>
              cacheKey="sections"
              searchPlaceholder="Search section number"
              /* Scoped to the selected semester in SQL. This page used to
                 fetch every section in the institution and filter in the
                 browser, so the count it showed was a slice of a download. */
              fetcher={(params) => api.sections.pageByContext(currentId ?? undefined, params)}
              refreshToken={currentId}
              empty="No sections in this academic context yet."
              columns={[
                {
                  header: "Section",
                  sortField: "section_number",
                  cell: (s) => (
                    <Link
                      href={`/sections/${s.id}`}
                      className="font-mono text-xs text-ink underline decoration-line-strong hover:decoration-ink"
                    >
                      {s.section_number}
                    </Link>
                  ),
                },
                { header: "Strength", sortField: "strength", cell: (s) => s.strength },
                {
                  // Batches import from the workbook and are stored faithfully,
                  // but the solver still schedules a practical once for the
                  // whole section. Showing them without saying that would imply
                  // a capability the timetable does not have; hiding them would
                  // lose data somebody deliberately supplied.
                  header: "Lab groups",
                  key: "groups",
                  cell: (s) =>
                    s.groups.length === 0 ? (
                      <span className="text-xs text-ink-faint">-</span>
                    ) : (
                      <span
                        className="text-xs text-ink-muted"
                        title="Recorded from the workbook. Practicals are still scheduled for the whole section, in a room that seats all of it - groups are not yet scheduled separately."
                      >
                        {s.groups.map((g) => g.group_code).join(", ")}
                        <span className="ml-1 text-ink-faint">(not yet scheduled)</span>
                      </span>
                    ),
                },
                {
                  header: "Curriculum",
                  key: "curriculum",
                  cell: (s) =>
                    s.subjects.length === 0 ? (
                      <Link href="/mappings" className="text-xs text-blocker underline">
                        no subjects
                      </Link>
                    ) : (
                      <span className="text-xs text-ink-soft">
                        {s.subjects.length} subjects
                      </span>
                    ),
                },
                {
                  header: "Timetable",
                  key: "timetable",
                  cell: (s) => (
                    <Link
                      href={`/timetable/section/${s.id}`}
                      className="text-xs text-ink-soft underline hover:text-ink"
                    >
                      View
                    </Link>
                  ),
                },
                {
                  header: "",
                  key: "actions",
                  className: "text-right",
                  cell: (s) => (
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" onClick={() => startEdit(s)}>
                        Edit
                      </Button>
                      <Button
                        variant="danger"
                        onClick={() => run(() => api.sections.remove(s.id), ["sections"])}
                      >
                        Delete
                      </Button>
                    </div>
                  ),
                },
              ]}
            />
          </div>
        </div>
      )}
    </>
  );
}
