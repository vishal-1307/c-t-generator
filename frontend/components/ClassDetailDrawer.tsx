"use client";

import { ArrowLeft, CircleCheck, Lock } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useAcademicContext } from "@/lib/academicContext";
import { EntityPicker } from "@/components/data/EntityPicker";
import { useAuth } from "@/lib/auth";
import { ApiError, api } from "@/lib/api";
import type {
  Room,
  AvailableFaculty,
  ChangeHistoryEntry,
  GridCell,
  ManualValidationResult,
  TimeSlot,
  LockPreview,
} from "@/lib/types";
import { Badge, Button, DetailList, Drawer, Select, Skeleton } from "@/components/ui";
import { cellRoom } from "@/components/TimetableGrid";

/**
 * The class detail panel, and the entry point for every manual edit.
 *
 * Every mutation goes preview -> inspect -> apply. The Apply button stays
 * disabled until a preview has come back `ok`, so an admin can never fire a
 * change the backend has not already agreed to - and the backend re-validates
 * anyway, so this is a usability guarantee layered on a correctness one, not a
 * substitute for it.
 */

type Mode = "detail" | "move" | "room" | "faculty";

export interface ClassRef {
  assignment_id: number;
  subject_code: string;
  subject_name: string | null;
  faculty_id: number | null;
  faculty_name: string | null;
  faculty_code: string | null;
  room_id: number | null;
  room_number: string | null;
  block: string | null;
  section_id: number | null;
  section_number: string | null;
  subject_id: number | null;
  locked: boolean;
  when: string;
}

export function cellToClassRef(cell: GridCell, when: string): ClassRef | null {
  if (!cell.assignment_id || !cell.subject_code) return null;
  return {
    assignment_id: cell.assignment_id,
    subject_code: cell.subject_code,
    subject_name: cell.subject_name,
    faculty_id: cell.faculty_id,
    faculty_name: cell.faculty_name,
    faculty_code: cell.faculty_code,
    room_id: cell.room_id,
    room_number: cell.room_number,
    block: cell.block,
    section_id: cell.section_id,
    section_number: cell.section_number,
    subject_id: cell.subject_id,
    locked: cell.locked,
    when,
  };
}

export function ClassDetailDrawer({
  target,
  onClose,
  onChanged,
}: {
  target: ClassRef | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [mode, setMode] = useState<Mode>("detail");
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<ChangeHistoryEntry[] | null>(null);

  // Reset to the detail view and reload history whenever a different class is
  // selected. Keyed on assignment_id rather than the object so re-rendering
  // the same class (e.g. after a refetch) does not throw away an open panel.
  // The reset is a genuine "new subject, start over" synchronisation, which is
  // what the lint rule below is warning about in the general case.
  const assignmentId = target?.assignment_id ?? null;
  useEffect(() => {
    if (assignmentId == null) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMode("detail");
    setError(null);
    setHistory(null);
    api.manualEdit
      .history(assignmentId)
      .then((h) => {
        if (!cancelled) setHistory(h);
      })
      .catch(() => {
        if (!cancelled) setHistory([]);
      });
    return () => {
      cancelled = true;
    };
  }, [assignmentId]);

  if (!target) return null;

  return (
    <Drawer
      open
      onClose={onClose}
      title={
        <span className="flex items-center gap-2">
          {target.subject_code}
          {target.locked && (
            <Badge tone="amber">
              <Lock aria-hidden="true" size={11} /> Locked
            </Badge>
          )}
        </span>
      }
      subtitle={target.subject_name ?? undefined}
    >
      {error && (
        <div className="mb-4 rounded-md border border-blocker-line bg-blocker-surface px-3 py-2 text-sm text-blocker">
          {error}
        </div>
      )}

      {mode === "detail" && (
        <div className="space-y-5">
          <DetailList
            items={[
              ["Faculty", target.faculty_name
                ? `${target.faculty_name}${target.faculty_code ? ` (${target.faculty_code})` : ""}`
                : "—"],
              ["Section", target.section_number ?? "—"],
              ["Room", cellRoom(target) ?? "—"],
              ["Block", target.block || "—"],
              ["Time", target.when],
            ]}
          />

          {target.locked ? (
            <div className="rounded-md border border-caution-line bg-caution-surface px-3 py-2 text-xs text-caution">
              This class is locked. Locked assignments are preserved during
              regeneration and cannot be changed manually until unlocked.
              <div className="mt-2">
                <LockButton target={target} lock={false} onDone={onChanged} setError={setError} />
              </div>
            </div>
          ) : (
            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-faint">
                Actions
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => setMode("move")}>
                  Move
                </Button>
                <Button variant="secondary" onClick={() => setMode("room")}>
                  Change room
                </Button>
                <Button variant="secondary" onClick={() => setMode("faculty")}>
                  Change faculty
                </Button>
                <LockButton target={target} lock onDone={onChanged} setError={setError} />
              </div>
              <p className="mt-2 text-[11px] text-ink-faint">
                Room and faculty changes apply to every session of this
                subject for this section (one room and one faculty per subject
                per week). A move affects only this occurrence.
              </p>
            </div>
          )}

          <ChangeHistoryList history={history} />
        </div>
      )}

      {mode === "move" && (
        <MovePanel target={target} onBack={() => setMode("detail")} onDone={onChanged} />
      )}
      {mode === "room" && (
        <RoomPanel target={target} onBack={() => setMode("detail")} onDone={onChanged} />
      )}
      {mode === "faculty" && (
        <FacultyPanel target={target} onBack={() => setMode("detail")} onDone={onChanged} />
      )}
    </Drawer>
  );
}

/* ------------------------------------------------------------ lock / unlock */

/**
 * Locking, shown before it happens.
 *
 * Moving a class, changing its room and changing its teacher have always been
 * previewed and applied in two steps. Locking was the one edit that just
 * happened - and it is the edit with the longest reach, because a lock changes
 * what every future regeneration is allowed to do, so it is previewed too.
 */
function LockButton({
  target,
  lock,
  onDone,
  setError,
}: {
  target: ClassRef;
  lock: boolean;
  onDone: () => void;
  setError: (s: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<LockPreview | null>(null);
  const { currentId: currentContextId } = useAcademicContext();
  const { canEdit } = useAuth();

  const ready =
    currentContextId != null &&
    target.section_id != null &&
    target.subject_id != null;

  async function ask() {
    if (!ready) {
      setError("Cannot lock: missing section/subject/context.");
      return;
    }
    setBusy(true);
    try {
      setPreview(
        await api.assignments.previewLock(
          currentContextId!,
          target.section_id!,
          target.subject_id!,
          lock,
        ),
      );
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    setBusy(true);
    try {
      const fn = lock ? api.assignments.lock : api.assignments.unlock;
      await fn(currentContextId!, target.section_id!, target.subject_id!);
      setError(null);
      setPreview(null);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (preview) {
    return (
      <div className="w-full rounded-md border border-line bg-surface-sunken p-3">
        <p className="text-sm font-medium text-ink">{preview.summary}</p>
        <p className="mt-1 text-xs text-ink-soft">{preview.consequence}</p>

        {preview.components.length > 1 && (
          <ul className="mt-2 space-y-0.5 text-xs text-ink-muted">
            {preview.components.map((c) => (
              <li key={c.session_type ?? "only"}>
                {c.session_type === "L"
                  ? "Lecture"
                  : c.session_type === "P"
                    ? "Practical"
                    : "Class"}
                : {c.faculty_name ?? "unassigned"}
                {c.room_number ? ` in ${c.room_number}` : ""}
              </li>
            ))}
          </ul>
        )}

        <div className="mt-3 flex gap-2">
          {preview.would_change ? (
            <Button onClick={apply} disabled={busy}>
              {busy ? "Working…" : lock ? "Confirm lock" : "Confirm unlock"}
            </Button>
          ) : null}
          <Button variant="secondary" onClick={() => setPreview(null)} disabled={busy}>
            {preview.would_change ? "Cancel" : "Close"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <Button
      variant={lock ? "secondary" : "primary"}
      onClick={ask}
      disabled={busy || !canEdit}
      title={canEdit ? undefined : "Sign in as a scheduler or admin to lock/unlock"}
    >
      {busy ? "Checking…" : lock ? "Lock" : "Unlock"}
    </Button>
  );
}

/* ------------------------------------------------------------------ preview */

function PreviewResult({ result }: { result: ManualValidationResult | null }) {
  if (!result) return null;
  if (result.ok) {
    return (
      <div className="rounded-md border border-ok-line bg-ok-surface px-3 py-2 text-sm text-ok">
        <span className="flex items-center gap-1.5">
          <CircleCheck aria-hidden="true" size={14} />
          Change is valid
        </span>
      </div>
    );
  }
  return (
    <div className="rounded-md border border-blocker-line bg-blocker-surface px-3 py-2 text-sm text-blocker">
      <div className="mb-1 font-medium">Cannot apply</div>
      <ul className="list-inside list-disc space-y-0.5">
        {result.issues.map((issue, i) => (
          <li key={i}>{issue}</li>
        ))}
      </ul>
    </div>
  );
}

function PanelShell({
  title,
  onBack,
  children,
}: {
  title: string;
  onBack: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <button onClick={onBack} className="text-xs text-ink-muted hover:text-ink">
        <span className="flex items-center gap-1">
          <ArrowLeft aria-hidden="true" size={12} />
          Back to details
        </span>
      </button>
      <div className="text-sm font-semibold text-ink">{title}</div>
      {children}
    </div>
  );
}

/* --------------------------------------------------------------------- move */

function MovePanel({
  target,
  onBack,
  onDone,
}: {
  target: ClassRef;
  onBack: () => void;
  onDone: () => void;
}) {
  const [slots, setSlots] = useState<TimeSlot[] | null>(null);
  const [slotId, setSlotId] = useState<number | null>(null);
  const [preview, setPreview] = useState<ManualValidationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { canEdit } = useAuth();

  useEffect(() => {
    api.timeslots
      .list()

      .then((all) => setSlots(all.filter((s) => !s.is_lunch)))
      .catch(() => setSlots([]));
  }, []);

  const runPreview = useCallback(async () => {
    if (slotId == null) return;
    setBusy(true);
    try {
      setPreview(await api.manualEdit.previewMove(target.assignment_id, slotId));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [slotId, target.assignment_id]);

  async function apply() {
    if (slotId == null) return;
    setBusy(true);
    try {
      await api.manualEdit.move(target.assignment_id, slotId);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PanelShell title="Move class" onBack={onBack}>
      {error && (
        <div className="rounded-md border border-blocker-line bg-blocker-surface px-3 py-2 text-sm text-blocker">
          {error}
        </div>
      )}
      <DetailList items={[["Current", target.when]]} />
      {slots === null ? (
        <Skeleton rows={1} />
      ) : (
        <Select
          label="New time"
          value={slotId ?? ""}
          onChange={(e) => {
            setSlotId(e.target.value ? Number(e.target.value) : null);
            setPreview(null);
          }}
        >
          <option value="">— choose a slot —</option>
          {slots.map((s) => (
            <option key={s.id} value={s.id}>
              {s.day} P{s.period_index + 1} ({s.start_time.slice(0, 5)}–{s.end_time.slice(0, 5)})
            </option>
          ))}
        </Select>
      )}
      <div className="flex gap-2">
        <Button variant="secondary" onClick={runPreview} disabled={slotId == null || busy || !canEdit}>
          Preview change
        </Button>
        <Button onClick={apply} disabled={!preview?.ok || busy || !canEdit}>
          {busy ? "Working…" : "Apply change"}
        </Button>
      </div>
      {!canEdit && (
        <p className="text-xs text-caution">Sign in as a scheduler or admin to make this change.</p>
      )}
      <PreviewResult result={preview} />
    </PanelShell>
  );
}

/* --------------------------------------------------------------------- room */

function RoomPanel({
  target,
  onBack,
  onDone,
}: {
  target: ClassRef;
  onBack: () => void;
  onDone: () => void;
}) {
  const [roomId, setRoomId] = useState<number | null>(null);
  const [preview, setPreview] = useState<ManualValidationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { canEdit } = useAuth();

  // No eager fetch. This used to read every room in the institution to fill a
  // dropdown - at nine hundred rooms that is a large response and an unusable
  // control, since nobody finds a room by scrolling nine hundred options. The
  // picker below queries as you type, and the backend's preview remains the
  // authority on whether the chosen room is actually eligible.
  const [chosen, setChosen] = useState<Room | null>(null);

  async function runPreview() {
    if (roomId == null) return;
    setBusy(true);
    try {
      setPreview(await api.manualEdit.previewRoom(target.assignment_id, roomId));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (roomId == null) return;
    setBusy(true);
    try {
      await api.manualEdit.changeRoom(target.assignment_id, roomId);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PanelShell title="Change room" onBack={onBack}>
      {error && (
        <div className="rounded-md border border-blocker-line bg-blocker-surface px-3 py-2 text-sm text-blocker">
          {error}
        </div>
      )}
      <DetailList items={[["Current room", cellRoom(target) ?? "—"]]} />

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-ink-muted">New room</span>
        {chosen ? (
          <span className="rounded bg-surface-hover px-2 py-1 text-xs text-ink">
            {chosen.room_code ?? chosen.room_number} · {chosen.room_type}
            {chosen.lab_type ? `/${chosen.lab_type}` : ""} · seats {chosen.capacity}
            {chosen.byod || chosen.charging
              ? ` · ${[chosen.byod ? "BYOD" : null, chosen.charging ? "power" : null]
                  .filter(Boolean)
                  .join(" + ")}`
              : ""}
          </span>
        ) : (
          <span className="text-xs text-ink-faint">none chosen</span>
        )}
        <EntityPicker<Room>
          prefix="rooms"
          buttonLabel={chosen ? "change" : "choose a room"}
          placeholder="Search room, block or lab type"
          search={(term) => api.rooms.page({ q: term || undefined, limit: 20 })}
          label={(r) =>
            `${r.room_code ?? r.room_number} · ${r.room_type}${
              r.lab_type ? "/" + r.lab_type : ""
            } · seats ${r.capacity}${
              r.byod || r.charging
                ? " · " +
                  [r.byod ? "BYOD" : null, r.charging ? "power" : null]
                    .filter(Boolean)
                    .join(" + ")
                : ""
            }`
          }
          onPick={(r) => {
            setChosen(r);
            setRoomId(r.id);
            setPreview(null);
          }}
        />
      </div>

      <div className="flex gap-2">
        <Button variant="secondary" onClick={runPreview} disabled={roomId == null || busy || !canEdit}>
          Preview change
        </Button>
        <Button onClick={apply} disabled={!preview?.ok || busy || !canEdit}>
          {busy ? "Working…" : "Apply change"}
        </Button>
      </div>
      {!canEdit && (
        <p className="text-xs text-caution">Sign in as a scheduler or admin to make this change.</p>
      )}
      <PreviewResult result={preview} />
      <p className="text-[11px] text-ink-faint">
        Applies to every session of {target.subject_code} for{" "}
        {target.section_number ?? "this section"} this week.
      </p>
    </PanelShell>
  );
}

/* ------------------------------------------------------------------ faculty */

function FacultyPanel({
  target,
  onBack,
  onDone,
}: {
  target: ClassRef;
  onBack: () => void;
  onDone: () => void;
}) {
  const [faculty, setFaculty] = useState<AvailableFaculty[] | null>(null);
  const [facultyId, setFacultyId] = useState<number | null>(null);
  const [preview, setPreview] = useState<ManualValidationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { canEdit } = useAuth();

  useEffect(() => {
    // Only faculty actually mapped to this subject - an arbitrary teacher can
    // never be assigned, and the UI should not imply otherwise.
    if (target.subject_id == null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFaculty([]);
      return;
    }
    api.subjects
      .get(target.subject_id)
      .then((subject) =>
         
        setFaculty(
          subject.faculties
            .filter((f) => f.id !== target.faculty_id)
            .map((f) => ({
              id: f.id,
              name: f.name,
              faculty_code: f.faculty_code,
              department: null,
            })),
        ),
      )
      .catch(() => setFaculty([]));
  }, [target.subject_id, target.faculty_id]);

  async function runPreview() {
    if (facultyId == null) return;
    setBusy(true);
    try {
      setPreview(await api.manualEdit.previewFaculty(target.assignment_id, facultyId));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (facultyId == null) return;
    setBusy(true);
    try {
      await api.manualEdit.changeFaculty(target.assignment_id, facultyId);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PanelShell title="Change faculty" onBack={onBack}>
      {error && (
        <div className="rounded-md border border-blocker-line bg-blocker-surface px-3 py-2 text-sm text-blocker">
          {error}
        </div>
      )}
      <DetailList items={[["Current faculty", target.faculty_name ?? "—"]]} />
      {faculty === null ? (
        <Skeleton rows={1} />
      ) : faculty.length === 0 ? (
        <div className="rounded-md border border-dashed border-line-strong px-3 py-4 text-center text-xs text-ink-muted">
          No other faculty is mapped to teach {target.subject_code}. Map one on
          the Mappings page first.
        </div>
      ) : (
        <Select
          label="New faculty"
          value={facultyId ?? ""}
          onChange={(e) => {
            setFacultyId(e.target.value ? Number(e.target.value) : null);
            setPreview(null);
          }}
          hint={`Only faculty eligible to teach ${target.subject_code} are listed.`}
        >
          <option value="">— choose a faculty member —</option>
          {faculty.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name} ({f.faculty_code})
            </option>
          ))}
        </Select>
      )}
      <div className="flex gap-2">
        <Button variant="secondary" onClick={runPreview} disabled={facultyId == null || busy || !canEdit}>
          Preview change
        </Button>
        <Button onClick={apply} disabled={!preview?.ok || busy || !canEdit}>
          {busy ? "Working…" : "Apply change"}
        </Button>
      </div>
      {!canEdit && (
        <p className="text-xs text-caution">Sign in as a scheduler or admin to make this change.</p>
      )}
      <PreviewResult result={preview} />
      <p className="text-[11px] text-ink-faint">
        Applies to every session of {target.subject_code} for{" "}
        {target.section_number ?? "this section"} this week.
      </p>
    </PanelShell>
  );
}

/* ------------------------------------------------------------------ history */

function ChangeHistoryList({ history }: { history: ChangeHistoryEntry[] | null }) {
  if (history === null) return <Skeleton rows={2} />;
  if (history.length === 0) return null;

  return (
    <div>
      <div className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-faint">
        Change history
      </div>
      <ul className="space-y-2 text-xs">
        {history.map((h) => (
          <li key={h.id} className="rounded border border-line px-2 py-1.5">
            <div className="flex items-center gap-2">
              <Badge tone="slate">{h.change_type}</Badge>
              <span className="text-ink-faint">
                {new Date(h.created_at).toLocaleString()}
              </span>
            </div>
            <div className="mt-1 text-ink-soft">
              {h.old_value} → <span className="font-medium text-ink">{h.new_value}</span>
            </div>
            {h.reason && <div className="mt-0.5 text-ink-faint">{h.reason}</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}
