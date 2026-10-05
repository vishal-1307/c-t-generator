"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/useApi";
import type { Faculty } from "@/lib/types";
import { ServerTable } from "@/components/data/ServerTable";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Input,
  PageHeader,
} from "@/components/ui";

export default function FacultyPage() {
  return (
    <Suspense fallback={null}>
      <FacultyPageInner />
    </Suspense>
  );
}

function FacultyPageInner() {
  const { run, error, setError } = useMutation();
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);

  // Deep link from a detail page: /faculty?edit=<id> opens that row's form.
  // Fetched by id rather than looked up in the loaded rows - now that the list
  // is one page of a paged query, the row being edited is frequently not on
  // the page in front of you, and the old lookup silently did nothing.
  const searchParams = useSearchParams();
  const opened = useRef<string | null>(null);
  useEffect(() => {
    const editId = searchParams.get("edit");
    if (!editId || editId === opened.current) return;
    opened.current = editId;
    let cancelled = false;
    api.faculty
      .get(Number(editId))
      .then((f) => {
        if (!cancelled) startEdit(f);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [searchParams]);

  const reset = () => {
    setName("");
    setCode("");
    setEditingId(null);
  };

  function startEdit(f: Faculty) {
    setEditingId(f.id);
    setName(f.name);
    setCode(f.faculty_code);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const body = { name: name.trim(), faculty_code: code.trim() };
    const ok = await run(
      () =>
        editingId === null
          ? api.faculty.create(body)
          : api.faculty.update(editingId, body),
      ["faculty"],
    );
    if (ok) reset();
  }

  return (
    <>
      <PageHeader
        title="Faculty"
        description="Teaching staff. Map each one to the subjects they can teach on the Mappings page."
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="grid gap-6 md:grid-cols-[320px_1fr]">
        <Card title={editingId === null ? "Add faculty" : "Edit faculty"}>
          <form onSubmit={submit} className="space-y-3">
            <Input
              label="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Dr. A. Sharma"
              required
            />
            <Input
              label="Faculty code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="FAC01"
              required
              hint="Must be unique."
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
          <ServerTable<Faculty>
            cacheKey="faculty"
            searchPlaceholder="Search name, code or department"
            fetcher={(params) => api.faculty.page(params)}
            empty="No faculty yet. Add the first one on the left."
            columns={[
              {
                header: "Code",
                sortField: "faculty_code",
                cell: (f) => (
                  <Link
                    href={`/faculty/${f.id}`}
                    className="font-mono text-xs text-ink underline decoration-line-strong hover:decoration-ink"
                  >
                    {f.faculty_code}
                  </Link>
                ),
              },
              { header: "Name", sortField: "name", cell: (f) => f.name },
              {
                header: "Teaches",
                cell: (f) =>
                  f.subjects.length === 0 ? (
                    <span className="text-xs text-ink-faint">unmapped</span>
                  ) : (
                    <div className="flex flex-wrap gap-1">
                      {f.subjects.map((s) => (
                        <Badge
                          key={s.id}
                          tone={
                            s.type === "practical"
                              ? "amber"
                              : s.type === "mixed"
                                ? "violet"
                                : "blue"
                          }
                        >
                          {s.code}
                        </Badge>
                      ))}
                    </div>
                  ),
              },
              {
                header: "",
                key: "actions",
                className: "text-right",
                cell: (f) => (
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" onClick={() => startEdit(f)}>
                      Edit
                    </Button>
                    <Button
                      variant="danger"
                      onClick={() => run(() => api.faculty.remove(f.id), ["faculty"])}
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
