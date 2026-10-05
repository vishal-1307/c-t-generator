"use client";

import { ArrowLeft, ArrowRight, Lock } from "lucide-react";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, api } from "@/lib/api";
import type { FacultyDetail } from "@/lib/types";
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
  StatusBanner,
} from "@/components/ui";

export default function FacultyDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const facultyId = Number(id);

  const [detail, setDetail] = useState<FacultyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // Standard fetch-on-mount, same shape as useResource.ts: the eventual
    // setState happens in a resolved microtask, not synchronously in the
    // effect body, and there is no external-system subscription to rewrite
    // this into.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    api.faculty
      .detail(facultyId)
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
  }, [facultyId]);

  if (loading) {
    return (
      <>
        <PageHeader title="Faculty" description="Loading…" />
        <Skeleton rows={6} />
      </>
    );
  }

  if (error || !detail) {
    return (
      <>
        <PageHeader title="Faculty" />
        <ErrorBanner error={error ?? "Faculty member not found."} />
        <Link href="/faculty" className="text-sm text-ink-soft underline">
          <ArrowLeft aria-hidden="true" size={14} className="mr-1 inline" />Back to faculty
        </Link>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={detail.name}
        description={`${detail.faculty_code}${detail.department ? ` · ${detail.department}` : ""}`}
        actions={
          <>
            <Link href={`/faculty?edit=${detail.id}`}>
              <Button variant="secondary">Edit</Button>
            </Link>
            <Link href={`/availability?tab=faculty&id=${detail.id}`}>
              <Button variant="secondary">Availability</Button>
            </Link>
          </>
        }
      />

      <div className="mb-4">
        <StatusBanner state={detail.current_status.state} detail={detail.current_status.detail} />
      </div>

      <div className="mb-4 grid grid-cols-3 gap-3">
        <Card>
          <div className="text-2xl font-semibold text-ink">{detail.periods_per_week}</div>
          <div className="text-xs text-ink-muted">Periods per week</div>
        </Card>
        <Card>
          <div className="text-2xl font-semibold text-ink">{detail.sections_taught}</div>
          <div className="text-xs text-ink-muted">Sections taught</div>
        </Card>
        <Card>
          <div className="text-2xl font-semibold text-ink">{detail.subjects.length}</div>
          <div className="text-xs text-ink-muted">Subjects eligible to teach</div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <Card title="Semester assignments">
            {detail.assignments.length === 0 ? (
              <EmptyState>No semester assignments yet — this faculty member has not been assigned to a section/subject pair by the scheduler.</EmptyState>
            ) : (
              <DataTable
                rows={detail.assignments.map((a, i) => ({ id: i, ...a }))}
                empty="No assignments."
                columns={[
                  {
                    header: "Context",
                    cell: (a) => <span className="text-xs text-ink-muted">{a.academic_context_label}</span>,
                  },
                  {
                    header: "Section",
                    cell: (a) => (
                      <Link href={`/sections/${a.section_id}`} className="text-xs underline">
                        {a.section_number}
                      </Link>
                    ),
                  },
                  {
                    header: "Subject",
                    cell: (a) => (
                      <Link href={`/subjects/${a.subject_id}`} className="font-mono text-xs underline">
                        {a.subject_code}
                      </Link>
                    ),
                  },
                  { header: "Room", cell: (a) => a.room_number ?? "—" },
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
            <p className="mb-2 text-sm text-ink-muted">
              This faculty member&apos;s weekly schedule across every assignment above.
            </p>
            <Link href={`/timetable/faculty/${detail.id}`}>
              <Button variant="secondary">View weekly timetable <ArrowRight aria-hidden="true" size={14} /></Button>
            </Link>
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Eligible subjects">
            {detail.subjects.length === 0 ? (
              <EmptyState>Not mapped to any subject yet.</EmptyState>
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

          <Card title="Declared unavailability">
            {detail.blocked_slots.length === 0 ? (
              <EmptyState>No blocked slots — available for every teaching period.</EmptyState>
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
