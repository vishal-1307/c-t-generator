"use client";

import { ArrowLeft, Lock } from "lucide-react";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, api } from "@/lib/api";
import type { SubjectDetail } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  DataTable,
  DetailList,
  EmptyState,
  ErrorBanner,
  PageHeader,
  Skeleton,
} from "@/components/ui";

export default function SubjectDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const subjectId = Number(id);

  const [detail, setDetail] = useState<SubjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // Standard fetch-on-mount, same shape as useResource.ts - see that
    // file's comment for why this synchronous setState is intentional.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    api.subjects
      .detail(subjectId)
      .then((d) => {
        if (!cancelled) {
          setDetail(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [subjectId]);

  if (loading) {
    return (
      <>
        <PageHeader title="Subject" description="Loading…" />
        <Skeleton rows={6} />
      </>
    );
  }

  if (error || !detail) {
    return (
      <>
        <PageHeader title="Subject" />
        <ErrorBanner error={error ?? "Subject not found."} />
        <Link href="/subjects" className="text-sm text-ink-soft underline">
          <ArrowLeft aria-hidden="true" size={14} className="mr-1 inline" />Back to subjects
        </Link>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={`${detail.code} — ${detail.name}`}
        description={
          detail.type === "practical"
            ? `Practical · ${detail.session_length_hours}h × ${detail.sessions_per_week}/wk` +
              (detail.required_lab_type ? ` · requires ${detail.required_lab_type} lab` : "")
            : `Theory · ${detail.session_length_hours}h × ${detail.sessions_per_week}/wk`
        }
        actions={
          <Link href={`/subjects?edit=${detail.id}`}>
            <Button variant="secondary">Edit</Button>
          </Link>
        }
      />

      <div className="mb-4 grid grid-cols-3 gap-3">
        <Card>
          <div className="text-2xl font-semibold text-ink">{detail.periods_per_week}</div>
          <div className="text-xs text-ink-muted">Periods per week per section</div>
        </Card>
        <Card>
          <div className="text-2xl font-semibold text-ink">{detail.sections.length}</div>
          <div className="text-xs text-ink-muted">Sections taking this subject</div>
        </Card>
        <Card>
          <div className="text-2xl font-semibold text-ink">{detail.eligible_faculty.length}</div>
          <div className="text-xs text-ink-muted">Faculty eligible to teach</div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <Card title="Sections taking this subject">
            {detail.sections.length === 0 ? (
              <EmptyState>No section has this subject in its curriculum yet — add it on the Mappings page.</EmptyState>
            ) : (
              <DataTable
                rows={detail.sections}
                empty="No sections."
                columns={[
                  {
                    header: "Section",
                    cell: (s) => (
                      <Link href={`/sections/${s.id}`} className="font-mono text-xs underline">
                        {s.section_number}
                      </Link>
                    ),
                  },
                  { header: "Context", cell: (s) => <span className="text-xs text-ink-muted">{s.academic_context_label}</span> },
                  { header: "Strength", cell: (s) => s.strength },
                ]}
              />
            )}
          </Card>

          <Card title="Timetable usage">
            {detail.assignments.length === 0 ? (
              <EmptyState>Not yet scheduled anywhere.</EmptyState>
            ) : (
              <DataTable
                rows={detail.assignments.map((a, i) => ({ id: i, ...a }))}
                empty="No assignments."
                columns={[
                  { header: "Context", cell: (a) => <span className="text-xs text-ink-muted">{a.academic_context_label}</span> },
                  {
                    header: "Section",
                    cell: (a) => (
                      <Link href={`/sections/${a.section_id}`} className="text-xs underline">
                        {a.section_number}
                      </Link>
                    ),
                  },
                  {
                    header: "Faculty",
                    cell: (a) =>
                      a.faculty_id ? (
                        <Link href={`/faculty/${a.faculty_id}`} className="text-xs underline">
                          {a.faculty_name}
                        </Link>
                      ) : (
                        "—"
                      ),
                  },
                  {
                    header: "Room",
                    cell: (a) =>
                      a.room_id ? (
                        <Link href={`/rooms/${a.room_id}`} className="text-xs underline">
                          {a.room_number}
                        </Link>
                      ) : (
                        "—"
                      ),
                  },
                  {
                    header: "",
                    className: "text-right",
                    cell: (a) => (a.locked ? <Badge tone="amber"><Lock aria-hidden="true" size={11} /> locked</Badge> : null),
                  },
                ]}
              />
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Room requirement">
            <DetailList
              items={[
                ["Type", detail.type === "practical" ? "Lab (practical)" : "Theory room"],
                ["Lab type", detail.required_lab_type ?? "—"],
                [
                  "Fixed room override",
                  detail.fixed_room ? (
                    <Link key="fr" href={`/rooms/${detail.fixed_room.id}`} className="underline">
                      {detail.fixed_room.room_number}
                    </Link>
                  ) : (
                    "not pinned — normal eligible pool"
                  ),
                ],
              ]}
            />
            {detail.allowed_rooms.length > 0 && (
              <div className="mt-3">
                <div className="mb-1 text-xs font-medium text-ink-muted">Restricted to these rooms</div>
                <div className="flex flex-wrap gap-1">
                  {detail.allowed_rooms.map((r) => (
                    <Link key={r.id} href={`/rooms/${r.id}`}>
                      <Badge tone="slate">{r.room_number}</Badge>
                    </Link>
                  ))}
                </div>
              </div>
            )}
          </Card>

          <Card title="Eligible faculty">
            {detail.eligible_faculty.length === 0 ? (
              <EmptyState>No faculty mapped to teach this subject yet.</EmptyState>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {detail.eligible_faculty.map((f) => (
                  <Link key={f.id} href={`/faculty/${f.id}`}>
                    <Badge tone="blue">{f.faculty_code}</Badge>
                  </Link>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
