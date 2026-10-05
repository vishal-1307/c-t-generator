"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import type { TimeSlot } from "@/lib/types";
import { ErrorBanner, Skeleton } from "@/components/ui";

/**
 * The day x period availability editor, shared by faculty, rooms and sections.
 *
 * Availability is stored as *exceptions* on the backend - a row exists only
 * for a blocked slot, and "available" is simply the absence of one. This grid
 * mirrors that exactly: clicking a cell creates or deletes one block row.
 *
 * The words are "Available" and "Unavailable", never "Free". This grid is a
 * restriction someone declared, not the timetable: "Free" reads as "has no
 * class then", which is a question for the timetable views, where an empty
 * period says "No Class".
 */

export interface BlockRow {
  id: number;
  timeslot_id: number;
  reason: string | null;
}

export function AvailabilityGrid({
  slots,
  blocks,
  onBlock,
  onUnblock,
  disabled = false,
  legendLabel = "Unavailable",
}: {
  slots: TimeSlot[];
  blocks: BlockRow[] | null;
  onBlock: (timeslotId: number) => Promise<void>;
  onUnblock: (blockId: number) => Promise<void>;
  disabled?: boolean;
  legendLabel?: string;
}) {
  const [busySlot, setBusySlot] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const blockBySlot = useMemo(() => {
    const map = new Map<number, BlockRow>();
    for (const b of blocks ?? []) map.set(b.timeslot_id, b);
    return map;
  }, [blocks]);

  const { days, periods, slotAt } = useMemo(() => {
    const dayMap = new Map<number, string>();
    const periodSet = new Set<number>();
    const at = new Map<string, TimeSlot>();
    for (const s of slots) {
      dayMap.set(s.day_index, s.day);
      periodSet.add(s.period_index);
      at.set(`${s.day_index}:${s.period_index}`, s);
    }
    return {
      days: [...dayMap.entries()].sort((a, b) => a[0] - b[0]),
      periods: [...periodSet].sort((a, b) => a - b),
      slotAt: at,
    };
  }, [slots]);

  const toggle = useCallback(
    async (slot: TimeSlot) => {
      if (disabled || slot.is_lunch) return;
      setBusySlot(slot.id);
      try {
        const existing = blockBySlot.get(slot.id);
        if (existing) await onUnblock(existing.id);
        else await onBlock(slot.id);
        setError(null);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setBusySlot(null);
      }
    },
    [blockBySlot, disabled, onBlock, onUnblock],
  );

  if (blocks === null) return <Skeleton rows={6} />;
  if (slots.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-line-strong px-4 py-8 text-center text-sm text-ink-muted">
        No time grid has been generated yet. Create one on the Time Slots page.
      </div>
    );
  }

  const periodLabel = (p: number) => {
    const any = slots.find((s) => s.period_index === p);
    return any ? `${any.start_time.slice(0, 5)}–${any.end_time.slice(0, 5)}` : "";
  };

  return (
    <>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <div className="relative overflow-x-auto rounded-md border border-line bg-surface">
        <table className="w-full min-w-[48rem] border-collapse text-sm">
          <thead>
            <tr className="border-b border-line bg-surface-sunken">
              <th className="px-3 py-2 text-left text-xs font-medium text-ink-muted">Day</th>
              {periods.map((p) => (
                <th key={p} className="px-2 py-2 text-center">
                  <div className="text-xs font-medium text-ink-soft">P{p + 1}</div>
                  <div className="font-mono text-[10px] font-normal text-ink-faint">
                    {periodLabel(p)}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {days.map(([dayIndex, dayName]) => (
              <tr key={dayIndex}>
                <th className="whitespace-nowrap px-3 py-1.5 text-left text-xs font-medium text-ink-soft">
                  {dayName}
                </th>
                {periods.map((p) => {
                  const slot = slotAt.get(`${dayIndex}:${p}`);
                  if (!slot) {
                    return <td key={p} className="p-1 text-center text-line">—</td>;
                  }
                  if (slot.is_lunch) {
                    return (
                      <td key={p} className="bg-surface-hover p-1 text-center">
                        <span className="text-[10px] uppercase tracking-wide text-ink-faint">
                          Lunch
                        </span>
                      </td>
                    );
                  }
                  const blocked = blockBySlot.has(slot.id);
                  const busy = busySlot === slot.id;
                  return (
                    <td key={p} className="p-1">
                      <button
                        type="button"
                        onClick={() => toggle(slot)}
                        disabled={disabled || busy}
                        aria-pressed={blocked}
                        aria-label={`${dayName} P${p + 1}: ${blocked ? "Unavailable" : "Available"}`}
                        className={`min-h-10 w-full rounded border px-1.5 py-2 text-[10px] font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-60 ${
                          blocked
                            ? "border-blocker-line bg-blocker-surface text-blocker hover:bg-blocker-surface"
                            : "border-line bg-surface text-ink-muted hover:bg-surface-sunken"
                        }`}
                      >
                        {busy ? "…" : blocked ? "Unavailable" : "Available"}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-ink-muted">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded border border-line bg-surface" /> Available:
          no restriction declared
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded border border-blocker-line bg-blocker-surface" /> {legendLabel}:
          must not be scheduled
        </span>
        <span>Click a period to switch it.</span>
      </div>
    </>
  );
}

/** Shared loader for the three availability owners. */
export function useAvailabilityData<T extends BlockRow>(
  ownerId: number | null,
  listSlots: () => Promise<TimeSlot[]>,
  listBlocks: (ownerId: number) => Promise<T[]>,
) {
  const [slots, setSlots] = useState<TimeSlot[]>([]);
  const [blocks, setBlocks] = useState<T[] | null>(null);

  const reload = useCallback(async () => {
    if (ownerId == null) {
      setBlocks([]);
      return;
    }
    const [s, b] = await Promise.all([listSlots(), listBlocks(ownerId)]);
    setSlots(s);
    setBlocks(b);
  }, [ownerId, listSlots, listBlocks]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload().catch(() => setBlocks([]));
  }, [reload]);

  return { slots, blocks, reload };
}
