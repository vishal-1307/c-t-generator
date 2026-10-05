"use client";

import { ArrowLeft, ArrowRight } from "lucide-react";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, api } from "@/lib/api";
import type { RoomDetail } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  DetailList,
  EmptyState,
  ErrorBanner,
  PageHeader,
  Skeleton,
  StatusBanner,
} from "@/components/ui";

const TONE = { theory: "blue", lab: "amber", faculty: "slate" } as const;

export default function RoomDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const roomId = Number(id);

  const [detail, setDetail] = useState<RoomDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // Standard fetch-on-mount, same shape as useResource.ts - see that
    // file's comment for why this synchronous setState is intentional.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    api.rooms
      .detail(roomId)
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
  }, [roomId]);

  if (loading) {
    return (
      <>
        <PageHeader title="Room" description="Loading…" />
        <Skeleton rows={6} />
      </>
    );
  }

  if (error || !detail) {
    return (
      <>
        <PageHeader title="Room" />
        <ErrorBanner error={error ?? "Room not found."} />
        <Link href="/rooms" className="text-sm text-ink-soft underline">
          <ArrowLeft aria-hidden="true" size={14} className="mr-1 inline" />Back to rooms
        </Link>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={detail.room_number}
        description={
          <span className="flex items-center gap-2">
            <Badge tone={TONE[detail.room_type]}>{detail.room_type}</Badge>
            {detail.lab_type && <Badge tone="slate">{detail.lab_type}</Badge>}
            <span>
              {[detail.block, detail.floor].filter(Boolean).join(" / ")} · seats {detail.capacity}
            </span>
          </span>
        }
        actions={
          <>
            <Link href={`/rooms?edit=${detail.id}`}>
              <Button variant="secondary">Edit</Button>
            </Link>
            <Link href={`/availability?tab=room&id=${detail.id}`}>
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
          <div className="text-2xl font-semibold text-ink">{detail.utilization.percent}%</div>
          <div className="text-xs text-ink-muted">Weekly utilization</div>
        </Card>
        <Card>
          <div className="text-2xl font-semibold text-ink">{detail.utilization.occupied_periods}</div>
          <div className="text-xs text-ink-muted">Periods occupied / week</div>
        </Card>
        <Card>
          <div className="text-2xl font-semibold text-ink">{detail.utilization.teachable_periods}</div>
          <div className="text-xs text-ink-muted">Teachable periods / week</div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <Card title="Timetable">
            <p className="mb-2 text-sm text-ink-muted">This room&apos;s weekly occupancy.</p>
            <Link href={`/timetable/room/${detail.id}`}>
              <Button variant="secondary">View weekly timetable <ArrowRight aria-hidden="true" size={14} /></Button>
            </Link>
          </Card>

          <Card title="Utilization">
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-surface-hover">
              <div
                className="h-full rounded-full bg-accent"
                style={{ width: `${Math.min(100, detail.utilization.percent)}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-ink-muted">
              {detail.utilization.occupied_periods} of {detail.utilization.teachable_periods} teachable periods
              this week are booked, across every published timetable that uses this room.
            </p>
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Details">
            <DetailList
              items={[
                ["Block", detail.block || "—"],
                ["Floor", detail.floor ?? "—"],
                ["Capacity", String(detail.capacity)],
                ["Type", detail.room_type],
                ["Lab type", detail.lab_type ?? "—"],
                ["Active", detail.is_active ? "yes" : "no"],
                [
                  "Pinned subject",
                  detail.fixed_subject ? (
                    <Link key="fs" href={`/subjects/${detail.fixed_subject.id}`} className="underline">
                      {detail.fixed_subject.code}
                    </Link>
                  ) : (
                    "not pinned"
                  ),
                ],
              ]}
            />
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
