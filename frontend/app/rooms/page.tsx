"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/useApi";
import { ServerTable } from "@/components/data/ServerTable";
import type { Room, RoomType, Subject } from "@/lib/types";
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
  room_number: "",
  block: "",
  floor: "",
  room_code: "",
  capacity: 60,
  room_type: "theory" as RoomType,
  lab_type: "",
  byod: false,
  charging: false,
  charging_sockets: "" as number | "",
  is_active: true,
  fixed_subject_id: null as number | null,
};

const TONE: Record<RoomType, "blue" | "amber" | "slate"> = {
  theory: "blue",
  lab: "amber",
  faculty: "slate",
};

export default function RoomsPage() {
  return (
    <Suspense fallback={null}>
      <RoomsPageInner />
    </Suspense>
  );
}

function RoomsPageInner() {
  const { run, error, setError } = useMutation();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [form, setForm] = useState(BLANK);
  const [editingId, setEditingId] = useState<number | null>(null);

  useEffect(() => {
    api.subjects.list().then(setSubjects).catch(() => setSubjects([]));
  }, []);

  const searchParams = useSearchParams();
  const openedEditId = useRef<string | null>(null);
  useEffect(() => {
    const editId = searchParams.get("edit");
    if (!editId || editId === openedEditId.current) return;
    // By id rather than from the loaded rows: the list is one page now.
    openedEditId.current = editId;
    void api.rooms.get(Number(editId)).then(startEdit).catch(() => undefined);
  }, [searchParams]);

  const practicals = subjects.filter((s) => s.type === "practical");
  const canPin = form.room_type !== "faculty";

  const reset = () => {
    setForm(BLANK);
    setEditingId(null);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const body = {
      ...form,
      room_number: form.room_number.trim(),
      block: form.block.trim(),
      floor: form.floor.trim() || null,
      // Left blank, the server derives it from block and number.
      room_code: form.room_code.trim() || null,
      charging_sockets:
        form.charging_sockets === "" ? null : Number(form.charging_sockets),
      lab_type: form.room_type === "lab" ? form.lab_type.trim() || null : null,
      // A faculty room can never carry a subject pin.
      fixed_subject_id: canPin ? form.fixed_subject_id : null,
    };
    const ok = await run(
      () =>
        editingId === null
          ? api.rooms.create(body)
          : api.rooms.update(editingId, body),
      ["rooms"],
    );
    if (ok) reset();
  }

  function startEdit(r: Room) {
    setEditingId(r.id);
    setForm({
      room_number: r.room_number,
      block: r.block,
      floor: r.floor ?? "",
      room_code: r.room_code ?? "",
      capacity: r.capacity,
      room_type: r.room_type,
      lab_type: r.lab_type ?? "",
      byod: r.byod,
      charging: r.charging,
      charging_sockets: r.charging_sockets ?? "",
      is_active: r.is_active,
      fixed_subject_id: r.fixed_subject_id,
    });
  }

  return (
    <>
      <PageHeader
        title="Rooms"
        description="Theory and lab rooms are drawn into an eligible pool by type/capability/capacity - not pinned per section. Faculty rooms are never schedulable. An explicit pin overrides the pool for the rare subject that must use one specific room."
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="grid gap-6 md:grid-cols-[320px_1fr]">
        <Card title={editingId === null ? "Add room" : "Edit room"}>
          <form onSubmit={submit} className="space-y-3">
            <Input
              label="Room number"
              value={form.room_number}
              onChange={(e) => setForm({ ...form, room_number: e.target.value })}
              placeholder="A101"
              required
            />
            <div className="grid grid-cols-2 gap-2">
              <Input
                label="Block"
                value={form.block}
                onChange={(e) => setForm({ ...form, block: e.target.value })}
                placeholder="A"
              />
              <Input
                label="Floor"
                value={form.floor}
                onChange={(e) => setForm({ ...form, floor: e.target.value })}
                placeholder="1"
              />
            </div>
            <Input
              label="Capacity"
              type="number"
              min={1}
              value={form.capacity}
              onChange={(e) => setForm({ ...form, capacity: Number(e.target.value) })}
              required
              hint="Must be at least the strength of any section placed here."
            />
            <Select
              label="Type"
              value={form.room_type}
              onChange={(e) =>
                setForm({
                  ...form,
                  room_type: e.target.value as RoomType,
                  fixed_subject_id: null,
                })
              }
            >
              <option value="theory">Theory</option>
              <option value="lab">Lab</option>
              <option value="faculty">Faculty (never scheduled)</option>
            </Select>
            {form.room_type === "lab" && (
              <Input
                label="Lab type"
                value={form.lab_type}
                onChange={(e) => setForm({ ...form, lab_type: e.target.value })}
                placeholder="COMPUTING"
                hint="Matched against a practical subject's required lab type."
              />
            )}
            {form.room_type !== "faculty" && (
              <fieldset className="rounded-md border border-line p-3">
                <legend className="px-1 text-xs font-medium text-ink-soft">
                  Practical capabilities
                </legend>
                <p className="mb-2 text-xs text-ink-muted">
                  A software practical can run in an ordinary classroom when
                  students can use their own machines and charge them. Recording
                  this keeps scarce labs for the sessions that truly need them.
                </p>
                <label className="flex items-center gap-2 text-sm text-ink-soft">
                  <input
                    type="checkbox"
                    checked={form.byod}
                    onChange={(e) => setForm({ ...form, byod: e.target.checked })}
                  />
                  Students can use their own devices (BYOD)
                </label>
                <label className="mt-1.5 flex items-center gap-2 text-sm text-ink-soft">
                  <input
                    type="checkbox"
                    checked={form.charging}
                    onChange={(e) =>
                      setForm({ ...form, charging: e.target.checked })
                    }
                  />
                  Power available at seats
                </label>
                {form.charging && (
                  <div className="mt-2">
                    <Input
                      label="Charging sockets"
                      type="number"
                      value={String(form.charging_sockets)}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          charging_sockets:
                            e.target.value === "" ? "" : Number(e.target.value),
                        })
                      }
                      hint="Recorded for reference. Not used as a scheduling limit."
                    />
                  </div>
                )}
              </fieldset>
            )}
            {canPin && (
              <Select
                label="Pinned subject (override)"
                value={form.fixed_subject_id ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    fixed_subject_id: e.target.value ? Number(e.target.value) : null,
                  })
                }
                hint="Optional. Leave unpinned to use normal type/capability matching."
              >
                <option value="">— not pinned —</option>
                {(form.room_type === "lab" ? practicals : subjects).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.code} — {s.name}
                  </option>
                ))}
              </Select>
            )}
            <label className="flex items-center gap-2 text-sm text-ink-soft">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              Active (offered to the scheduler)
            </label>
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
          <ServerTable<Room>
            cacheKey="rooms"
            searchPlaceholder="Search number, block, floor or lab type"
            fetcher={(params) => api.rooms.page(params)}
            empty="No rooms yet."
            columns={[
              {
                header: "Room",
                sortField: "room_number",
                cell: (r) => (
                  <Link
                    href={`/rooms/${r.id}`}
                    className="font-mono text-xs text-ink underline decoration-line-strong hover:decoration-ink"
                  >
                    {/* The institution-wide label: room numbers repeat across
                        buildings, so 101 alone identifies several rooms. */}
                    {r.room_code ?? r.room_number}
                  </Link>
                ),
              },
              {
                header: "Block / Floor",
                sortField: "block",
                cell: (r) => [r.block, r.floor].filter(Boolean).join(" / ") || "-",
              },
              { header: "Capacity", sortField: "capacity", cell: (r) => r.capacity },
              {
                header: "Type",
                sortField: "room_type",
                cell: (r) => (
                  <div className="flex items-center gap-1">
                    <Badge tone={TONE[r.room_type]}>{r.room_type}</Badge>
                    {r.lab_type && <Badge tone="slate">{r.lab_type}</Badge>}
                  </div>
                ),
              },
              {
                // Why a room is or is not eligible for a practical. These
                // decide it as hard constraints, and a registrar adding a lab
                // that still cannot be used deserves to see what it lacks.
                header: "Facilities",
                key: "facilities",
                cell: (r) => (
                  <div className="flex flex-wrap gap-1">
                    {r.byod && <Badge tone="green">BYOD</Badge>}
                    {r.charging && (
                      <Badge tone="green">
                        power{r.charging_sockets ? ` x${r.charging_sockets}` : ""}
                      </Badge>
                    )}
                    {!r.byod && !r.charging && (
                      <span className="text-xs text-ink-faint">-</span>
                    )}
                  </div>
                ),
              },
              {
                header: "Reserved for",
                key: "pinned",
                cell: (r) =>
                  r.fixed_subject ? (
                    <Badge tone="green">{r.fixed_subject.code}</Badge>
                  ) : (
                    <span className="text-xs text-ink-faint">-</span>
                  ),
              },
              {
                header: "Active",
                key: "active",
                cell: (r) =>
                  r.is_active ? <Badge tone="green">yes</Badge> : <Badge tone="red">no</Badge>,
              },
              {
                header: "",
                key: "actions",
                className: "text-right",
                cell: (r) => (
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" onClick={() => startEdit(r)}>
                      Edit
                    </Button>
                    <Button
                      variant="danger"
                      onClick={() => run(() => api.rooms.remove(r.id), ["rooms"])}
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
