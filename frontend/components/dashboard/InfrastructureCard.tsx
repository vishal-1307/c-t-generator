"use client";

import { Building2, CircleCheck, FileDown, RefreshCw, Trash2, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Infrastructure, InfrastructurePreview } from "@/lib/types";
import { Alert, Button, Skeleton } from "@/components/ui";
import { FileIntake } from "./FileIntake";

/**
 * The rooms, saved once.
 *
 * Rooms change rarely and teaching loads every semester, so this card is not
 * another file to choose every time. Saved, it says so and offers Replace and
 * Remove. Choosing a room list shows what saving it would do - how many
 * rooms, which existing rooms it changes, and whether it replaces rooms that
 * timetables were built on - before anything is written, and a replacement
 * that removes timetables needs its own tick.
 */
export function InfrastructureCard({
  infra,
  loading,
  disabled,
  onSaved,
  onRemove,
}: {
  infra: Infrastructure | null;
  loading: boolean;
  disabled: boolean;
  onSaved: () => void;
  onRemove: () => void;
}) {
  const [replacing, setReplacing] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<InfrastructurePreview | null>(null);
  const [busy, setBusy] = useState<"preview" | "save" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [problems, setProblems] = useState<{ where: string; what: string }[]>([]);
  const [acknowledged, setAcknowledged] = useState(false);

  function reset() {
    setFile(null);
    setPreview(null);
    setProblem(null);
    setProblems([]);
    setAcknowledged(false);
  }

  async function choose(e: React.ChangeEvent<HTMLInputElement>) {
    const chosen = e.target.files?.[0] ?? null;
    reset();
    setFile(chosen);
    if (!chosen) return;
    setBusy("preview");
    try {
      setPreview(await api.infrastructure.preview(chosen));
    } catch (err) {
      explain(err);
    } finally {
      setBusy(null);
    }
  }

  function explain(err: unknown) {
    const body = err instanceof ApiError
      ? (err.body as { detail?: { problems?: { file: string; row_number: number; message: string }[] } })
      : undefined;
    const rows = body?.detail?.problems ?? [];
    setProblems(rows.map((p) => ({ where: `${p.file}, row ${p.row_number}`, what: p.message })));
    setProblem(err instanceof Error ? err.message : "The room list could not be read.");
  }

  async function save() {
    if (!file) return;
    setBusy("save");
    setProblem(null);
    try {
      const saved = await api.infrastructure.save(file, acknowledged);
      if (!saved.committed && saved.has_errors) {
        setPreview(saved);
        return;
      }
      reset();
      setReplacing(false);
      onSaved();
    } catch (err) {
      explain(err);
    } finally {
      setBusy(null);
    }
  }

  const rowProblems = [
    ...problems,
    ...(preview?.outcomes ?? []).flatMap((o) =>
      o.problems.map((p) => ({ where: `${p.sheet}, row ${p.row_number}`, what: p.message })),
    ),
  ];
  const removes = preview?.replaces ? preview.timetables_removed : 0;
  const canSave =
    !!preview && !preview.has_errors && rowProblems.length === 0 && (removes === 0 || acknowledged);

  if (loading) {
    return (
      <div className="rounded-md border border-line bg-surface p-4">
        <Skeleton rows={3} />
      </div>
    );
  }

  const saved = infra?.available ? infra : null;
  const choosing = !saved || replacing;

  return (
    <div className="space-y-3">
      {saved && !replacing && (
        <div className="rounded-md border border-ok-line bg-surface p-4">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-ok-surface text-ok">
              <Building2 aria-hidden="true" size={18} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">
                Infrastructure
              </p>
              <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink">
                <CircleCheck aria-hidden="true" size={14} className="text-ok" />
                Infra.xlsx available
              </h3>
              <p className="mt-0.5 truncate text-xs text-ink-muted" title={saved.filename ?? ""}>
                {saved.filename}
                {saved.uploaded_at && ` · saved ${new Date(saved.uploaded_at).toLocaleDateString()}`}
              </p>
              <p className="mt-2 text-sm text-ink-soft">
                {saved.summary.rooms ?? 0} rooms · {saved.summary.classrooms ?? 0} classrooms ·{" "}
                {saved.summary.labs ?? 0} labs · {saved.summary.byod_rooms ?? 0} with charging points
              </p>
              <p className="mt-1 text-xs text-ink-muted">
                Kept for every teaching load - upload it again only when the rooms change.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="secondary" disabled={disabled} onClick={() => setReplacing(true)}>
                  <RefreshCw aria-hidden="true" size={14} />
                  Replace Infrastructure
                </Button>
                <Button variant="danger" disabled={disabled} onClick={onRemove}>
                  <Trash2 aria-hidden="true" size={14} />
                  Remove Infrastructure
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {choosing && (
        <FileIntake
          heading="Infrastructure"
          title="Infra.xlsx"
          hint={
            saved
              ? "A new room list replaces the saved one."
              : "The rooms: block, room, capacity, classroom or lab, BYOD. Saved once and reused."
          }
          file={file}
          onChange={choose}
          disabled={disabled || busy !== null}
          templateHref={api.templates.infraUrl}
        />
      )}

      {busy === "preview" && <p className="text-sm text-ink-muted">Reading the room list…</p>}

      {problem && rowProblems.length === 0 && <Alert tone="blocker" title={problem} />}
      {rowProblems.length > 0 && (
        <Alert tone="blocker" title="Fix these rows in the room list, then choose it again.">
          <ul className="mt-1 space-y-1">
            {rowProblems.map((p, i) => (
              <li key={i}>
                <span className="font-medium text-ink">{p.where}:</span> {p.what}
              </li>
            ))}
          </ul>
        </Alert>
      )}

      {preview && rowProblems.length === 0 && (
        <div className="space-y-3 rounded-md border border-line bg-surface p-4">
          <p className="text-sm text-ink-soft">
            <span className="font-semibold text-ink">{preview.summary.rooms ?? 0} rooms</span> ·{" "}
            {preview.summary.classrooms ?? 0} classrooms · {preview.summary.labs ?? 0} labs ·{" "}
            {preview.summary.byod_rooms ?? 0} with charging points
          </p>

          {preview.unchanged && (
            <p className="text-xs text-ink-muted">This is the room list already saved. Saving changes nothing.</p>
          )}

          {preview.room_changes.length > 0 && (
            <div className="text-sm">
              <p className="font-medium text-ink">
                {preview.room_changes.length} existing room{preview.room_changes.length > 1 ? "s" : ""} will change
              </p>
              <ul className="mt-1 space-y-0.5 text-ink-soft">
                {preview.room_changes.map((r) => (
                  <li key={r.room}>
                    <span className="font-mono font-semibold">{r.room}</span>:{" "}
                    {r.changes.map((c) => `${c.label} ${c.before} → ${c.after}`).join("; ")}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {removes > 0 && (
            <div className="rounded-md border border-caution-line bg-caution-surface p-3 text-sm text-caution">
              <p className="flex items-center gap-1.5 font-medium">
                <TriangleAlert aria-hidden="true" size={14} />
                Replacing the rooms removes {removes} timetable{removes > 1 ? "s" : ""}.
              </p>
              <p className="mt-1 text-ink-soft">
                They were built on the rooms being replaced. The teaching data is kept - generate
                again afterwards.
              </p>
              <label className="mt-2 flex items-start gap-2 text-ink-soft">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={acknowledged}
                  onChange={(e) => setAcknowledged(e.target.checked)}
                />
                <span>I understand, replace the rooms.</span>
              </label>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <Button onClick={save} disabled={!canSave || busy !== null || disabled}>
              {busy === "save" ? "Saving…" : saved ? "Save new infrastructure" : "Save Infrastructure"}
            </Button>
            {saved && (
              <Button
                variant="ghost"
                onClick={() => {
                  reset();
                  setReplacing(false);
                }}
              >
                Cancel
              </Button>
            )}
          </div>
        </div>
      )}

      {saved && replacing && !preview && !file && (
        <Button
          variant="ghost"
          onClick={() => {
            reset();
            setReplacing(false);
          }}
        >
          Cancel
        </Button>
      )}

      {!saved && !file && (
        <p className="flex items-center gap-1 text-xs text-ink-muted">
          <FileDown aria-hidden="true" size={12} />
          Upload the rooms first. They are kept, so they only have to be uploaded once.
        </p>
      )}
    </div>
  );
}
