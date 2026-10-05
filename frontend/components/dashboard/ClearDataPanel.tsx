"use client";

import { Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ClearPreview, ClearWhat } from "@/lib/types";
import { Alert, Button, Skeleton } from "@/components/ui";

/**
 * Clearing old data, deliberately slow to do.
 *
 * Three separate choices, because they are three different intentions: a new
 * semester's teaching (keep the rooms), a rebuilt campus (keep the teaching),
 * or a clean start. The panel says in plain numbers what the choice deletes,
 * and the button stays disabled until the teacher has ticked that they
 * understand it cannot be undone. The confirmation phrase the API requires is
 * sent by the panel, not typed - the tick is the human's confirmation.
 */
const CHOICES: { what: ClearWhat; label: string; keeps: string; example: string }[] = [
  {
    what: "load",
    label: "Load data",
    keeps: "Keeps the infrastructure (rooms).",
    example: "For a new semester: the teachers, subjects and sections change, the rooms do not.",
  },
  {
    what: "infrastructure",
    label: "Infrastructure",
    keeps: "Keeps the teaching data.",
    example: "When the rooms themselves have changed.",
  },
  {
    what: "both",
    label: "Both",
    keeps: "Keeps only the user accounts.",
    example: "To start completely fresh.",
  },
];

// Rows a teacher recognises, in the words they would use: [one, many].
const PLAIN: Record<string, [string, string]> = {
  timetable_run: ["timetable", "timetables"],
  academic_context: ["uploaded teaching dataset", "uploaded teaching datasets"],
  section: ["section or lab group", "sections and lab groups"],
  subject: ["subject", "subjects"],
  faculty: ["faculty member", "faculty members"],
  room: ["room", "rooms"],
};

export function ClearDataPanel({
  initial = "load",
  onClose,
  onCleared,
}: {
  initial?: ClearWhat;
  onClose: () => void;
  onCleared: (what: ClearWhat) => void;
}) {
  const [what, setWhat] = useState<ClearWhat>(initial);
  const [preview, setPreview] = useState<ClearPreview | null>(null);
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function choose(next: ClearWhat) {
    // A tick given for one choice is not consent to another.
    setWhat(next);
    setPreview(null);
    setUnderstood(false);
    setError(null);
  }

  useEffect(() => {
    let cancelled = false;
    api.data
      .clearPreview(what)
      .then((p) => {
        if (!cancelled) setPreview(p);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [what]);

  async function clear() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      await api.data.clear(what, preview.confirmation);
      onCleared(what);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const lines = preview
    ? Object.entries(PLAIN)
        .map(([table, [one, many]]) => {
          const n = preview.counts[table] ?? 0;
          return [n === 1 ? one : many, n] as const;
        })
        .filter(([, n]) => n > 0)
    : [];

  return (
    <section
      aria-labelledby="clear-data-title"
      className="rounded-md border border-blocker-line bg-surface p-5"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="clear-data-title" className="text-base font-semibold text-ink">
            Clear Previous Data
          </h2>
          <p className="mt-0.5 text-sm text-ink-muted">What would you like to clear?</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded p-1 text-ink-faint hover:bg-surface-hover hover:text-ink"
        >
          <X aria-hidden="true" size={16} />
        </button>
      </div>

      <fieldset className="mt-4 grid gap-2 sm:grid-cols-3">
        <legend className="sr-only">What to clear</legend>
        {CHOICES.map((c) => (
          <label
            key={c.what}
            className={`flex cursor-pointer flex-col rounded-md border p-3 text-sm transition-colors ${
              what === c.what ? "border-blocker bg-blocker-surface" : "border-line hover:bg-surface-hover"
            }`}
          >
            <span className="flex items-center gap-2 font-medium text-ink">
              <input
                type="radio"
                name="clear-what"
                value={c.what}
                checked={what === c.what}
                onChange={() => choose(c.what)}
              />
              {c.label}
            </span>
            <span className="mt-1 text-xs text-ink-soft">{c.keeps}</span>
            <span className="mt-1 text-xs text-ink-muted">{c.example}</span>
          </label>
        ))}
      </fieldset>

      <div className="mt-4">
        {!preview && !error && <Skeleton rows={1} />}
        {preview && (
          <p className="text-sm text-ink-soft">
            {lines.length === 0
              ? "There is nothing to clear."
              : `This deletes ${lines.map(([words, n]) => `${n} ${words}`).join(", ")}.`}
          </p>
        )}
        {error && <Alert tone="blocker" title={error} />}
      </div>

      <label className="mt-4 flex items-start gap-2 text-sm text-ink-soft">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={understood}
          onChange={(e) => setUnderstood(e.target.checked)}
          disabled={!preview || preview.total === 0}
        />
        <span>I understand this permanently deletes it.</span>
      </label>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          variant="danger"
          onClick={clear}
          disabled={!preview || preview.total === 0 || !understood || busy}
        >
          <Trash2 aria-hidden="true" size={14} />
          {busy ? "Clearing…" : `Clear ${CHOICES.find((c) => c.what === what)?.label}`}
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </section>
  );
}
