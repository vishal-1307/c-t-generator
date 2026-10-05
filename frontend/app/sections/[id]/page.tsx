"use client";

import { ArrowLeft, ArrowRight, Lock } from "lucide-react";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, api } from "@/lib/api";
import type { SectionDetail } from "@/lib/types";
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

export default function SectionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const sectionId = Number(id);

  const [detail, setDetail] = useState<SectionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // Standard fetch-on-mount, same shape as useResource.ts - see that
    // file's comment for why this synchronous setState is intentional.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    api.sections
      .detail(sectionId)
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
  }, [sectionId]);

  if (loading) {
    return (
      <>
        <PageHeader title="Section" description="Loading…" />
        <Skeleton rows={6} />
      </>
    );
  }

  if (error || !detail) {
    return (
      <>
        <PageHeader title="Section" />
        <ErrorBanner error={error ?? "Section not found."} />
        <Link href="/sections" className="text-sm text-ink-soft underline">
          <ArrowLeft aria-hidden="true" size={14} className="mr-1 inline" />Back to sections
        </Link>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={detail.section_number}
        description={`${detail.academic_context.label} · ${detail.strength} students`}
        actions={
          <>
            <Link href={`/sections?edit=${detail.id}`}>
              <Button variant="secondary">Edit</Button>
            </Link>
            <Link href={`/availability?tab=section&id=${detail.id}`}>
              <Button variant="secondary">Availability</Button>
            </Link>
          </>
        }
      />

      <div className="mb-4 grid grid-cols-3 gap-3">
        <Card>
          <div className="text-2xl font-semibold text-ink">{detail.subjects.length}</div>
          <div className="text-xs text-ink-muted">Subjects in curriculum</div>
        </Card>
        <Card>
          <div className="text-2xl font-semibold text-ink">{detail.assignments.length}</div>
          <div className="text-xs text-ink-muted">Semester assignments</div>
        </Card>
        <Card>
          <div className="text-2xl font-semibold text-ink">{detail.rooms_used.length}</div>
          <div className="text-xs text-ink-muted">Rooms used</div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <Card title="Assigned faculty and rooms">
            {detail.assignments.length === 0 ? (
              <EmptyState>No semester assignments yet — generate a timetable for this context first.</EmptyState>
            ) : (
              <DataTable
                rows={detail.assignments.map((a, i) => ({ id: i, ...a }))}
                empty="No assignments."
                columns={[
                  {
                    header: "Subject",
                    cell: (a) => (
                      <Link href={`/subjects/${a.subject_id}`} className="font-mono text-xs underline">
                        {a.subject_code}
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
                  { header: "Load", cell: (a) => `${a.periods_per_week}/wk` },
                  {
                    header: "",
                    className: "text-right",
                    cell: (a) => (a.locked ? <Badge tone="amber"><Lock aria-hidden="true" size={11} /> locked</Badge> : null),
                  },
                ]}
              />
            )}
          </Card>

          <Card title="Timetable">
            <p className="mb-2 text-sm text-ink-muted">This section&apos;s weekly schedule.</p>
            <Link href={`/timetable/section/${detail.id}`}>
              <Button variant="secondary">View weekly timetable <ArrowRight aria-hidden="true" size={14} /></Button>
            </Link>
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Curriculum">
            {detail.subjects.length === 0 ? (
              <EmptyState>
                No subjects yet.{" "}
                <Link href="/mappings" className="underline">
                  Add on the Mappings page
                </Link>
                .
              </EmptyState>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {detail.subjects.map((s) => (
                  <Link key={s.id} href={`/subjects/${s.id}`}>
                    <Badge tone={s.type === "practical" ? "amber" : "blue"}>{s.code}</Badge>
                  </Link>
                ))}
              </div>
            )}
          </Card>

          <Card title="Rooms used">
            {detail.rooms_used.length === 0 ? (
              <EmptyState>No classes scheduled yet.</EmptyState>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {detail.rooms_used.map((r) => (
                  <Link key={r.id} href={`/rooms/${r.id}`}>
                    <Badge tone="slate">{r.room_number}</Badge>
                  </Link>
                ))}
              </div>
            )}
          </Card>

          <Card title="Declared unavailability">
            {detail.blocked_slots.length === 0 ? (
              <EmptyState>No blocked slots.</EmptyState>
            ) : (
              <DetailList
                items={detail.blocked_slots.map((s) => [
                  s.day,
                  `P${s.period_index + 1} (${s.start_time.slice(0, 5)}–${s.end_time.slice(0, 5)})`,
                ])}
              />
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
