"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAcademicContext } from "@/lib/academicContext";
import { ApiError, api } from "@/lib/api";
import { roomLabel } from "@/lib/roomLabel";
import type { AvailableFaculty, AvailableRoom, Subject, TimeSlot } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Input,
  PageHeader,
  Select,
  Skeleton,
} from "@/components/ui";

/**
 * "What is free at this time?" - the lookup an admin does before retargeting
 * a class. Occupancy comes from the same two sources a manual move is
 * validated against (declared unavailability + the run's own assignments), so
 * anything listed here is genuinely a candidate.
 */
export default function AvailabilitySearchPage() {
  const { currentId } = useAcademicContext();
  const [slots, setSlots] = useState<TimeSlot[]>([]);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [runId, setRunId] = useState<number | null>(null);

  const [slotId, setSlotId] = useState<string>("");
  const [length, setLength] = useState("1");
  const [capacityMin, setCapacityMin] = useState("");
  const [roomType, setRoomType] = useState("");
  const [subjectId, setSubjectId] = useState("");

  const [rooms, setRooms] = useState<AvailableRoom[] | null>(null);
  const [faculty, setFaculty] = useState<AvailableFaculty[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([api.timeslots.list(), api.subjects.list()])
      .then(([ts, subs]) => {
        const teachable = ts.filter((s) => !s.is_lunch);
        setSlots(teachable);
        setSubjects(subs);
        setSlotId((prev) => prev || String(teachable[0]?.id ?? ""));
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  useEffect(() => {
    api.runs
      .latest(currentId ?? undefined, true)
      .then((r) => setRunId(r.id))
      .catch(() => setRunId(null));
  }, [currentId]);

  const search = useCallback(async () => {
    if (!slotId) return;
    setBusy(true);
    try {
      const common = {
        timeslot_id: Number(slotId),
        length: Number(length),
        run_id: runId ?? undefined,
      };
      const [r, f] = await Promise.all([
        api.availability.rooms({
          ...common,
          capacity_min: capacityMin ? Number(capacityMin) : undefined,
          room_type: roomType || undefined,
        }),
        api.availability.faculty({
          ...common,
          subject_id: subjectId ? Number(subjectId) : undefined,
        }),
      ]);
      setRooms(r);
      setFaculty(f);
      setError(null);
    } catch (e) {
      setRooms(null);
      setFaculty(null);
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [slotId, length, runId, capacityMin, roomType, subjectId]);

  const selectedSlot = useMemo(
    () => slots.find((s) => String(s.id) === slotId),
    [slots, slotId],
  );

  return (
    <>
      <PageHeader
        title="Free rooms & faculty"
        description="Rooms and faculty with no class in the generated timetable and no Unavailable restriction at a given day and time — the lookup to do before moving a class."
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <Card title="Search">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Select label="Day & time" value={slotId} onChange={(e) => setSlotId(e.target.value)}>
            {slots.map((s) => (
              <option key={s.id} value={s.id}>
                {s.day} P{s.period_index + 1} ({s.start_time.slice(0, 5)}–{s.end_time.slice(0, 5)})
              </option>
            ))}
          </Select>
          <Select label="Duration" value={length} onChange={(e) => setLength(e.target.value)}>
            <option value="1">1 period</option>
            <option value="2">2 periods</option>
            <option value="3">3 periods</option>
          </Select>
          <Input
            label="Min capacity"
            type="number"
            min={1}
            value={capacityMin}
            onChange={(e) => setCapacityMin(e.target.value)}
            placeholder="e.g. 60"
          />
          <Select label="Room type" value={roomType} onChange={(e) => setRoomType(e.target.value)}>
            <option value="">Any (never faculty rooms)</option>
            <option value="theory">Theory</option>
            <option value="lab">Lab</option>
          </Select>
          <Select
            label="Faculty eligible for"
            value={subjectId}
            onChange={(e) => setSubjectId(e.target.value)}
          >
            <option value="">Any subject</option>
            {subjects.map((s) => (
              <option key={s.id} value={s.id}>
                {s.code}
              </option>
            ))}
          </Select>
        </div>
        <div className="mt-3">
          <Button onClick={search} disabled={!slotId || busy}>
            {busy ? "Searching…" : "Search"}
          </Button>
          {runId === null && (
            <span className="ml-3 text-xs text-ink-faint">
              No timetable generated — only declared unavailability is considered.
            </span>
          )}
        </div>
      </Card>

      {(rooms || faculty) && (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card title={`Available rooms${selectedSlot ? ` · ${selectedSlot.day} P${selectedSlot.period_index + 1}` : ""}`}>
            {rooms === null ? (
              <Skeleton rows={3} />
            ) : rooms.length === 0 ? (
              <EmptyState>No rooms are free at that time with those filters.</EmptyState>
            ) : (
              <ul className="space-y-1.5">
                {rooms.map((r) => (
                  <li
                    key={r.id}
                    className="flex items-center justify-between rounded border border-line px-2.5 py-1.5 text-sm"
                  >
                    {/* room_code already carries the block, so "36-101" says
                        which 101 this is without a separate block column. */}
                    <span className="font-mono text-xs">{roomLabel(r)}</span>
                    <span className="flex items-center gap-1.5 text-xs text-ink-muted">
                      <Badge tone={r.room_type === "lab" ? "amber" : "blue"}>{r.room_type}</Badge>
                      {r.lab_type && <Badge tone="slate">{r.lab_type}</Badge>}
                      <span>seats {r.capacity}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Available faculty">
            {faculty === null ? (
              <Skeleton rows={3} />
            ) : faculty.length === 0 ? (
              <EmptyState>No faculty are free at that time with those filters.</EmptyState>
            ) : (
              <ul className="space-y-1.5">
                {faculty.map((f) => (
                  <li
                    key={f.id}
                    className="flex items-center justify-between rounded border border-line px-2.5 py-1.5 text-sm"
                  >
                    <span>{f.name}</span>
                    <span className="font-mono text-xs text-ink-faint">{f.faculty_code}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
