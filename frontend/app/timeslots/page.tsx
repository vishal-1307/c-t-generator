"use client";

import { useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useResource } from "@/lib/useResource";
import type { GridReplacementImpact, TimeSlot } from "@/lib/types";
import {
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Input,
  PageHeader,
} from "@/components/ui";

/** The college's working week is Monday-Friday; Saturday/Sunday remain selectable for
 * a future configuration that needs them, but are not part of the default. */
const ALL_DAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];
const DEFAULT_DAYS = ALL_DAYS.slice(0, 5);

const hhmm = (t: string) => t.slice(0, 5);

export default function TimeSlotsPage() {
  const { rows, loading, error, setError, refresh, mutate } = useResource<TimeSlot>(
    api.timeslots.list,
  );

  const [days, setDays] = useState<string[]>(DEFAULT_DAYS);
  const [periods, setPeriods] = useState(9);
  const [startHour, setStartHour] = useState(9);
  const [startMinute, setStartMinute] = useState(30);
  const [periodMinutes, setPeriodMinutes] = useState(50);
  /** Set when the server refuses a replacement, holding what it would delete.
   *  Rendering this rather than a generic warning is the whole point: the
   *  cascade reaches availability blocks and generated classes, which is not
   *  what "regenerate the grid" sounds like. */
  const [pendingImpact, setPendingImpact] = useState<GridReplacementImpact | null>(null);

  /** Group slots into a day-by-period matrix for display. */
  const grid = useMemo(() => {
    const byDay = new Map<number, { day: string; slots: TimeSlot[] }>();
    for (const s of rows) {
      if (!byDay.has(s.day_index)) byDay.set(s.day_index, { day: s.day, slots: [] });
      byDay.get(s.day_index)!.slots.push(s);
    }
    return [...byDay.entries()]
      .sort(([a], [b]) => a - b)
      .map(([, v]) => ({
        ...v,
        slots: [...v.slots].sort((a, b) => a.period_index - b.period_index),
      }));
  }, [rows]);

  /** The distinct period columns, taken from the first day. */
  const periodColumns = grid[0]?.slots ?? [];
  const teachable = rows.filter((s) => !s.is_lunch).length;

  /** Preview of what the generate form will produce. */
  const preview = useMemo(() => {
    const start = startHour * 60 + startMinute;
    const end = start + periods * periodMinutes;
    const fmt = (m: number) =>
      `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
    return { first: fmt(start), last: fmt(end), total: days.length * periods };
  }, [startHour, startMinute, periods, periodMinutes, days.length]);

  function toggleDay(day: string) {
    setDays((cur) =>
      cur.includes(day)
        ? cur.filter((d) => d !== day)
        : [...cur, day].sort((a, b) => ALL_DAYS.indexOf(a) - ALL_DAYS.indexOf(b)),
    );
  }

  const seedPayload = () => ({
    days,
    periods,
    start_hour: startHour,
    start_minute: startMinute,
    period_minutes: periodMinutes,
  });

  /** Ask the server first. An empty grid seeds straight away; a populated one
   *  comes back 409 carrying the counts, which become the confirmation prompt.
   *
   *  Called directly rather than through `mutate`, which reports errors but
   *  does not re-raise them - and this one needs inspecting, not displaying. */
  async function regenerate() {
    setPendingImpact(null);
    try {
      await api.timeslots.seed(seedPayload());
      setError(null);
      await refresh();
    } catch (e) {
      const impact =
        e instanceof ApiError && e.status === 409
          ? (e.body as { impact?: GridReplacementImpact })?.impact
          : undefined;
      if (impact) {
        setPendingImpact(impact);
        setError(null);
        return;
      }
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function confirmRegenerate() {
    setPendingImpact(null);
    await mutate(() => api.timeslots.seed({ ...seedPayload(), confirm: true }));
  }

  /** Toggle one slot's lunch flag. */
  const toggleLunch = (slot: TimeSlot) =>
    mutate(() => api.timeslots.update(slot.id, { is_lunch: !slot.is_lunch }));

  /** Toggle the same period across every day - the usual case for a lunch break. */
  async function toggleLunchColumn(periodIndex: number) {
    const column = rows.filter((s) => s.period_index === periodIndex);
    const makeLunch = column.some((s) => !s.is_lunch);
    await mutate(async () => {
      for (const slot of column) {
        if (slot.is_lunch !== makeLunch) {
          await api.timeslots.update(slot.id, { is_lunch: makeLunch });
        }
      }
    });
  }

  return (
    <>
      <PageHeader
        title="Time Slots"
        description="The weekly grid the solver schedules into. Click any slot to mark it as lunch — lunch slots are never scheduled."
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {pendingImpact && (
        <div className="mb-4 rounded-md border border-caution-line bg-caution-surface p-4">
          <h2 className="text-sm font-semibold text-caution">
            Replacing the grid will delete more than the grid
          </h2>
          <p className="mt-1 text-sm text-caution">
            Availability blocks and generated classes are tied to a time slot,
            so they are removed with it. This cannot be undone.
          </p>
          <ul className="mt-3 space-y-1 text-sm text-caution">
            <li>
              <strong>{pendingImpact.timeslots}</strong> time slots
            </li>
            <li>
              <strong>
                {pendingImpact.faculty_unavailability +
                  pendingImpact.section_unavailability +
                  pendingImpact.room_unavailability}
              </strong>{" "}
              availability blocks
            </li>
            <li>
              <strong>{pendingImpact.assignments}</strong> scheduled classes across{" "}
              <strong>{pendingImpact.runs_affected}</strong> timetable version
              {pendingImpact.runs_affected === 1 ? "" : "s"}
              {pendingImpact.published_runs_affected > 0 && (
                <>
                  {" — "}
                  <strong>{pendingImpact.published_runs_affected}</strong> of them
                  published
                </>
              )}
            </li>
          </ul>
          <div className="mt-4 flex gap-2">
            <Button variant="danger" onClick={confirmRegenerate}>
              Delete and regenerate
            </Button>
            <Button variant="secondary" onClick={() => setPendingImpact(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[300px_1fr]">
        <Card title="Generate grid">
          <div className="space-y-3">
            <div>
              <span className="mb-1.5 block text-xs font-medium text-ink-soft">
                Working days
              </span>
              <div className="flex flex-wrap gap-1">
                {ALL_DAYS.map((d) => (
                  <button
                    key={d}
                    type="button"
                    onClick={() => toggleDay(d)}
                    className={`rounded border px-2 py-1 text-xs transition-colors ${
                      days.includes(d)
                        ? "border-accent bg-accent text-accent-ink"
                        : "border-line-strong bg-surface text-ink-soft hover:bg-surface-sunken"
                    }`}
                  >
                    {d.slice(0, 3)}
                  </button>
                ))}
              </div>
            </div>
            <Input
              label="Periods per day"
              type="number"
              min={1}
              max={15}
              value={periods}
              onChange={(e) => setPeriods(Number(e.target.value))}
            />
            <div className="grid grid-cols-2 gap-2">
              <Input
                label="Start hour"
                type="number"
                min={0}
                max={23}
                value={startHour}
                onChange={(e) => setStartHour(Number(e.target.value))}
              />
              <Input
                label="Start minute"
                type="number"
                min={0}
                max={59}
                value={startMinute}
                onChange={(e) => setStartMinute(Number(e.target.value))}
              />
            </div>
            <Input
              label="Period length (minutes)"
              type="number"
              min={5}
              max={120}
              step={5}
              value={periodMinutes}
              onChange={(e) => setPeriodMinutes(Number(e.target.value))}
            />
            <p className="rounded bg-surface-sunken px-2 py-1.5 text-xs text-ink-soft">
              {preview.total} slots — {preview.first} to {preview.last}, back to back.
              All teachable until you mark a lunch slot.
            </p>
            <div className="pt-1">
              <Button onClick={regenerate} disabled={days.length === 0}>
                Regenerate grid
              </Button>
            </div>
          </div>
        </Card>

        <div>
          {loading ? (
            <p className="text-sm text-ink-muted">Loading…</p>
          ) : rows.length === 0 ? (
            <EmptyState>
              No time slots yet. Generate the weekly grid on the left.
            </EmptyState>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-ink-muted">
                <strong className="text-ink">{rows.length}</strong> slots across{" "}
                <strong className="text-ink">{grid.length}</strong> days —{" "}
                <strong className="text-ink">{teachable}</strong> teachable,{" "}
                <strong className="text-ink">{rows.length - teachable}</strong>{" "}
                marked lunch.
              </p>

              <div className="overflow-x-auto rounded-md border border-line bg-surface">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-line bg-surface-sunken">
                      <th className="px-3 py-2 text-left text-xs font-medium text-ink-muted">
                        Day
                      </th>
                      {periodColumns.map((p) => (
                        <th key={p.period_index} className="px-1 py-1 text-center">
                          <button
                            onClick={() => toggleLunchColumn(p.period_index)}
                            title="Mark this period as lunch on every day"
                            className="w-full rounded px-1 py-1 text-[10px] font-medium text-ink-muted hover:bg-line"
                          >
                            P{p.period_index + 1}
                            <span className="block font-mono text-[9px] text-ink-faint">
                              {hhmm(p.start_time)}
                            </span>
                          </button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {grid.map((d) => (
                      <tr key={d.day}>
                        <th className="whitespace-nowrap px-3 py-1.5 text-left text-xs font-medium text-ink-soft">
                          {d.day}
                        </th>
                        {d.slots.map((s) => (
                          <td key={s.id} className="p-0.5">
                            <button
                              onClick={() => toggleLunch(s)}
                              title={`${hhmm(s.start_time)}–${hhmm(s.end_time)} — click to ${
                                s.is_lunch ? "make teachable" : "mark as lunch"
                              }`}
                              className={`w-full rounded px-1 py-1.5 font-mono text-[10px] transition-colors ${
                                s.is_lunch
                                  ? "bg-caution-surface text-caution hover:bg-caution-surface"
                                  : "bg-info-surface text-info hover:bg-info-surface"
                              }`}
                            >
                              {s.is_lunch ? "LUNCH" : hhmm(s.start_time)}
                            </button>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <p className="text-xs text-ink-faint">
                Click a cell to toggle that single slot, or a{" "}
                <strong className="text-ink-muted">P#</strong> header to toggle that
                period across every day.
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
