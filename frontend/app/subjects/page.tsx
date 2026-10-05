"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/useApi";
import { ServerTable } from "@/components/data/ServerTable";
import type { Subject, SubjectType } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Input,
  PageHeader,
  Select,
} from "@/components/ui";

const BLANK = {
  name: "",
  code: "",
  type: "theory" as SubjectType,
  session_length_hours: 1,
  sessions_per_week: 3,
  // Used only when type is "mixed": its lecture and practical components are
  // scheduled separately, usually in different kinds of room.
  lecture_sessions_per_week: 2,
  lecture_session_length: 1,
  practical_sessions_per_week: 2,
  practical_session_length: 2,
  required_lab_type: "",
  byod_required: false,
  charging_required: false,
};

export default function SubjectsPage() {
  return (
    <Suspense fallback={null}>
      <SubjectsPageInner />
    </Suspense>
  );
}

function SubjectsPageInner() {
  const { run, error, setError } = useMutation();
  const [form, setForm] = useState(BLANK);
  const [editingId, setEditingId] = useState<number | null>(null);

  const searchParams = useSearchParams();
  const openedEditId = useRef<string | null>(null);
  useEffect(() => {
    const editId = searchParams.get("edit");
    if (!editId || editId === openedEditId.current) return;
    // Fetched by id, not looked up among the loaded rows: the list is one page
    // of a paged query now, and the row being edited is often not on it.
    openedEditId.current = editId;
    void api.subjects
      .get(Number(editId))
      .then((row) => startEdit(row))
      .catch(() => undefined);
  }, [searchParams]);

  const reset = () => {
    setForm(BLANK);
    setEditingId(null);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const mixed = form.type === "mixed";
    const body = {
      ...form,
      name: form.name.trim(),
      code: form.code.trim(),
      required_lab_type:
        form.type === "theory" ? null : form.required_lab_type.trim() || null,
      // The per-component loads describe a mixed subject only; sending them
      // for a pure one would store a second, unused copy of its load.
      lecture_sessions_per_week: mixed ? form.lecture_sessions_per_week : null,
      lecture_session_length: mixed ? form.lecture_session_length : null,
      practical_sessions_per_week: mixed ? form.practical_sessions_per_week : null,
      practical_session_length: mixed ? form.practical_session_length : null,
    };
    const ok = await run(
      () =>
        editingId === null
          ? api.subjects.create(body)
          : api.subjects.update(editingId, body),
      ["subjects"],
    );
    if (ok) reset();
  }

  function startEdit(s: Subject) {
    setEditingId(s.id);
    setForm({
      name: s.name,
      code: s.code,
      type: s.type,
      session_length_hours: s.session_length_hours,
      sessions_per_week: s.sessions_per_week,
      lecture_sessions_per_week: s.lecture_sessions_per_week ?? 2,
      lecture_session_length: s.lecture_session_length ?? 1,
      practical_sessions_per_week: s.practical_sessions_per_week ?? 2,
      practical_session_length: s.practical_session_length ?? 2,
      required_lab_type: s.required_lab_type ?? "",
      byod_required: s.byod_required,
      charging_required: s.charging_required,
    });
  }

  return (
    <>
      <PageHeader
        title="Subjects"
        description="A subject's weekly demand is session length × sessions per week. Practicals must be pinned to a lab room before generating."
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="grid gap-6 md:grid-cols-[320px_1fr]">
        <Card title={editingId === null ? "Add subject" : "Edit subject"}>
          <form onSubmit={submit} className="space-y-3">
            <Input
              label="Name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Data Structures"
              required
            />
            <Input
              label="Code"
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
              placeholder="CS201"
              required
              hint="Must be unique."
            />
            <Select
              label="Type"
              value={form.type}
              onChange={(e) =>
                setForm({ ...form, type: e.target.value as SubjectType })
              }
            >
              <option value="theory">Theory</option>
              <option value="practical">Practical / Lab</option>
              <option value="mixed">Mixed (lecture + practical)</option>
            </Select>
            {form.type !== "mixed" && (
              <Select
                label="Session length"
                value={form.session_length_hours}
                onChange={(e) =>
                  setForm({ ...form, session_length_hours: Number(e.target.value) })
                }
                hint="Multi-hour sessions occupy contiguous periods on one day."
              >
                <option value={1}>1 hour</option>
                <option value={2}>2 hours</option>
                <option value={3}>3 hours</option>
              </Select>
            )}
            {form.type !== "mixed" ? (
              <Input
                label="Sessions per week"
                type="number"
                min={1}
                max={20}
                value={form.sessions_per_week}
                onChange={(e) =>
                  setForm({ ...form, sessions_per_week: Number(e.target.value) })
                }
                required
                hint={`Total demand: ${form.session_length_hours * form.sessions_per_week} periods/week.`}
              />
            ) : (
              <fieldset className="rounded-md border border-line p-3">
                <legend className="px-1 text-xs font-medium text-ink-soft">
                  Weekly load per component
                </legend>
                <p className="mb-2 text-xs text-ink-muted">
                  The lecture and practical are scheduled separately and usually
                  in different rooms, so each has its own load.
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <Input
                    label="Lectures / week"
                    type="number"
                    min={1}
                    max={20}
                    value={form.lecture_sessions_per_week}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        lecture_sessions_per_week: Number(e.target.value),
                      })
                    }
                  />
                  <Select
                    label="Lecture length"
                    value={form.lecture_session_length}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        lecture_session_length: Number(e.target.value),
                      })
                    }
                  >
                    <option value={1}>1 hour</option>
                    <option value={2}>2 hours</option>
                    <option value={3}>3 hours</option>
                  </Select>
                  <Input
                    label="Practicals / week"
                    type="number"
                    min={1}
                    max={20}
                    value={form.practical_sessions_per_week}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        practical_sessions_per_week: Number(e.target.value),
                      })
                    }
                  />
                  <Select
                    label="Practical length"
                    value={form.practical_session_length}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        practical_session_length: Number(e.target.value),
                      })
                    }
                  >
                    <option value={1}>1 hour</option>
                    <option value={2}>2 hours</option>
                    <option value={3}>3 hours</option>
                  </Select>
                </div>
                <p className="mt-2 text-xs text-ink-muted">
                  Total demand:{" "}
                  {form.lecture_sessions_per_week * form.lecture_session_length +
                    form.practical_sessions_per_week *
                      form.practical_session_length}{" "}
                  periods/week.
                </p>
                <p className="mt-2 rounded border border-caution-line bg-caution-surface px-2 py-1.5 text-xs text-caution">
                  Mixed subjects can be entered and imported now, but the
                  scheduler cannot yet place their two components separately, so
                  they block generation. Validation will say so.
                </p>
              </fieldset>
            )}
            {form.type !== "theory" && (
              <>
                <Input
                  label="Required lab type"
                  value={form.required_lab_type}
                  onChange={(e) =>
                    setForm({ ...form, required_lab_type: e.target.value })
                  }
                  placeholder="programming"
                  hint="Matched against a lab room's lab type. Leave blank if any suitable room will do."
                />
                <label className="flex items-center gap-2 text-sm text-ink-soft">
                  <input
                    type="checkbox"
                    checked={form.byod_required}
                    onChange={(e) =>
                      setForm({ ...form, byod_required: e.target.checked })
                    }
                  />
                  Students need their own devices
                </label>
                <label className="flex items-center gap-2 text-sm text-ink-soft">
                  <input
                    type="checkbox"
                    checked={form.charging_required}
                    onChange={(e) =>
                      setForm({ ...form, charging_required: e.target.checked })
                    }
                  />
                  Needs power at seats
                </label>
              </>
            )}
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
          <ServerTable<Subject>
            cacheKey="subjects"
            searchPlaceholder="Search code or name"
            fetcher={(params) => api.subjects.page(params)}
            empty="No subjects yet."
            columns={[
              {
                header: "Code",
                sortField: "code",
                cell: (s) => (
                  <Link
                    href={`/subjects/${s.id}`}
                    className="font-mono text-xs text-ink underline decoration-line-strong hover:decoration-ink"
                  >
                    {s.code}
                  </Link>
                ),
              },
              { header: "Name", sortField: "name", cell: (s) => s.name },
              {
                header: "Type",
                sortField: "type",
                cell: (s) => (
                  <Badge
                    tone={
                      s.type === "practical"
                        ? "amber"
                        : s.type === "mixed"
                          ? "violet"
                          : "blue"
                    }
                  >
                    {s.type}
                  </Badge>
                ),
              },
              {
                header: "Load",
                cell: (s) => <Load subject={s} />,
              },
              {
                header: "Faculty",
                cell: (s) =>
                  s.faculties.length === 0 ? (
                    <Badge tone="red">none</Badge>
                  ) : (
                    <span className="text-xs text-ink-soft">
                      {s.faculties.map((f) => f.faculty_code).join(", ")}
                    </span>
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
                      onClick={() => run(() => api.subjects.remove(s.id), ["subjects"])}
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
    </>
  );
}

/**
 * What this subject costs in a week.
 *
 * A mixed subject has two obligations with their own lengths, and the legacy
 * `session_length_hours x sessions_per_week` describes only one of them - it
 * reads 4 for a subject that actually occupies 6 periods. Showing the
 * components separately is the only honest summary, and it is also the thing
 * somebody checking a syllabus against the timetable needs to see.
 */
function Load({ subject }: { subject: Subject }) {
  if (subject.type === "mixed") {
    return (
      <span className="text-xs text-ink-soft">
        <span className="text-info">
          {subject.lecture_sessions_per_week ?? 0}x{subject.lecture_session_length ?? 1}h L
        </span>
        {" + "}
        <span className="text-caution">
          {subject.practical_sessions_per_week ?? 0}x{subject.practical_session_length ?? 1}h P
        </span>
        {" = "}
        <strong>{subject.periods_per_week}</strong>/wk
      </span>
    );
  }
  return (
    <span className="text-xs text-ink-soft">
      {subject.session_length_hours}h x {subject.sessions_per_week} ={" "}
      <strong>{subject.periods_per_week}</strong>/wk
    </span>
  );
}
