"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAcademicContext } from "@/lib/academicContext";
import { ApiError, api } from "@/lib/api";
import { roomLabel } from "@/lib/roomLabel";
import type { Faculty, Room, Section, TimeSlot } from "@/lib/types";
import {
  Badge,
  Card,
  EmptyState,
  ErrorBanner,
  PageHeader,
  Select,
  Skeleton,
} from "@/components/ui";
import { AvailabilityGrid, type BlockRow } from "@/components/AvailabilityGrid";

type Kind = "faculty" | "room" | "section";

const TABS: { id: Kind; label: string }[] = [
  { id: "faculty", label: "Faculty" },
  { id: "room", label: "Rooms" },
  { id: "section", label: "Sections" },
];

export default function AvailabilityPage() {
  const { currentId } = useAcademicContext();
  const [kind, setKind] = useState<Kind>("faculty");
  const [ownerId, setOwnerId] = useState<number | null>(null);

  const [faculty, setFaculty] = useState<Faculty[]>([]);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [sections, setSections] = useState<Section[]>([]);
  const [slots, setSlots] = useState<TimeSlot[]>([]);
  const [blocks, setBlocks] = useState<BlockRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingOwners, setLoadingOwners] = useState(true);

  useEffect(() => {
    Promise.all([
      api.faculty.list(),
      api.rooms.list(),
      api.sections.listByContext(currentId ?? undefined),
      api.timeslots.list(),
    ])
      .then(([f, r, s, t]) => {
         
        setFaculty(f);
        // Faculty rooms are never schedulable, so blocking their time is
        // meaningless - they are not offered here.
        setRooms(r.filter((room) => room.room_type !== "faculty"));
        setSections(s);
        setSlots(t);
        setError(null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoadingOwners(false));
  }, [currentId]);

  const owners = useMemo(() => {
    if (kind === "faculty")
      return faculty.map((f) => ({ id: f.id, label: `${f.name} (${f.faculty_code})` }));
    if (kind === "room")
      return rooms.map((r) => ({
        id: r.id,
        // room_code: blocking "101" is meaningless when three blocks have one.
        label: `${roomLabel(r)} · ${r.room_type}${r.lab_type ? `/${r.lab_type}` : ""} · seats ${r.capacity}`,
      }));
    return sections.map((s) => ({ id: s.id, label: `${s.section_number} (${s.strength})` }));
  }, [kind, faculty, rooms, sections]);

  // Reset the selection whenever the tab changes, defaulting to the first
  // owner of the new kind so the grid is never left pointing at an id from
  // a different entity type.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOwnerId(owners[0]?.id ?? null);
  }, [owners]);

  const listBlocks = useCallback(
    async (id: number): Promise<BlockRow[]> => {
      const client =
        kind === "faculty"
          ? api.faculty.unavailability
          : kind === "room"
            ? api.rooms.unavailability
            : api.sections.unavailability;
      const rows = await client.list(id);
      return rows.map((r) => ({ id: r.id, timeslot_id: r.timeslot_id, reason: r.reason }));
    },
    [kind],
  );

  const reload = useCallback(async () => {
    if (ownerId == null) {
      setBlocks([]);
      return;
    }
    setBlocks(null);
    try {
      setBlocks(await listBlocks(ownerId));
      setError(null);
    } catch (e) {
      setBlocks([]);
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, [ownerId, listBlocks]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload]);

  const client = useMemo(
    () =>
      kind === "faculty"
        ? api.faculty.unavailability
        : kind === "room"
          ? api.rooms.unavailability
          : api.sections.unavailability,
    [kind],
  );

  const onBlock = useCallback(
    async (timeslotId: number) => {
      if (ownerId == null) return;
      await client.add(ownerId, { timeslot_id: timeslotId });
      await reload();
    },
    [client, ownerId, reload],
  );

  const onUnblock = useCallback(
    async (blockId: number) => {
      if (ownerId == null) return;
      await client.remove(ownerId, blockId);
      await reload();
    },
    [client, ownerId, reload],
  );

  const selectedLabel = owners.find((o) => o.id === ownerId)?.label;

  return (
    <>
      <PageHeader
        title="Availability"
        description="Restrictions, not the timetable: periods when a faculty member, room or section must not be scheduled. Generating never uses an Unavailable period, and a manual move into one is refused."
      />
      <div className="mb-4 rounded-md border border-line bg-surface p-3 text-sm">
        <dl className="grid gap-2 sm:grid-cols-2">
          <div>
            <dt className="font-medium text-ink">Available</dt>
            <dd className="text-ink-muted">No availability restriction is declared for that period.</dd>
          </div>
          <div>
            <dt className="font-medium text-ink">Unavailable</dt>
            <dd className="text-ink-muted">
              The faculty member, room or section must not be scheduled then.
            </dd>
          </div>
        </dl>
        <p className="mt-2 text-xs text-ink-faint">
          Whether someone actually has a class in a period is shown on the Timetable pages, where an
          empty period reads &ldquo;No Class&rdquo;.
        </p>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="mb-4 flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setKind(t.id)}
            aria-pressed={kind === t.id}
            className={`border-b-2 px-4 py-2 text-sm transition-colors ${
              kind === t.id
                ? "border-accent font-medium text-ink"
                : "border-transparent text-ink-muted hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loadingOwners ? (
        <Skeleton rows={4} />
      ) : owners.length === 0 ? (
        <EmptyState>
          No {kind === "section" ? "sections in this academic context" : `${kind}s`} yet.
        </EmptyState>
      ) : (
        <div className="space-y-4">
          <div className="max-w-md">
            <Select
              label={`Select ${kind}`}
              value={ownerId ?? ""}
              onChange={(e) => setOwnerId(e.target.value ? Number(e.target.value) : null)}
            >
              {owners.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </Select>
          </div>

          <Card
            title={selectedLabel}
            actions={
              blocks && blocks.length > 0 ? (
                <Badge tone="red">{blocks.length} unavailable</Badge>
              ) : (
                <Badge tone="green">No restrictions</Badge>
              )
            }
          >
            {blocks && blocks.length === 0 && (
              <p className="mb-3 rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-soft">
                No restrictions are set for this {kind === "room" ? "room" : kind === "section" ? "section" : "teacher"}{" "}
                - every period is Available. Click a period to mark it Unavailable; the timetable
                will never use it.
              </p>
            )}
            <AvailabilityGrid
              slots={slots}
              blocks={blocks}
              onBlock={onBlock}
              onUnblock={onUnblock}
              legendLabel="Unavailable"
            />
          </Card>
        </div>
      )}
    </>
  );
}
