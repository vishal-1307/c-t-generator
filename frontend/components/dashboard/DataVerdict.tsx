"use client";

import { Info, TriangleAlert } from "lucide-react";
import type { Issues, Verdict } from "@/lib/dashboardState";
import { headline } from "@/lib/dashboardState";
import type { RoomChange } from "@/lib/types";
import { SummaryFigures } from "@/components/SummaryFigures";
import { SectionHeader, StatusBadge } from "@/components/ui";

/**
 * What the two files add up to.
 *
 * One heading answers the only question worth asking here - can a timetable
 * be built from this, yes or no - and the numbers under it are the same
 * either way, because they are a description of the files, not a celebration.
 */
export function DataVerdict({ verdict }: { verdict: Verdict }) {
  if (verdict.kind === "none") return null;
  const ready = verdict.kind === "ready";
  const s = verdict.summary;

  return (
    <section className="rise-enter rounded-md border border-line bg-surface p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-ink">
          {ready ? "Data Ready" : "Cannot generate yet."}
        </h2>
        <StatusBadge tone={ready ? "ok" : "blocker"}>
          {ready ? "Checked" : "Needs fixing"}
        </StatusBadge>
      </div>

      {s ? (
        <SummaryFigures
          figures={{
            sections: s.sections,
            faculty: s.faculty,
            subjects: s.subjects,
            classes: s.classes,
            requiredPeriods: s.required_periods,
            rooms: s.rooms,
            labs: s.labs,
          }}
        />
      ) : (
        <p className="text-sm text-ink-muted">
          The files could not be read far enough to count what is in them.
        </p>
      )}

      {ready && verdict.unchanged && (
        <p className="mt-4 text-xs text-ink-muted">
          These files match data already imported. Generating uses that dataset.
        </p>
      )}
    </section>
  );
}

/** Everything standing between these files and a timetable. */
export function ReadinessIssues({ issues }: { issues: Issues }) {
  const { rows, blockers, unassignable } = issues;
  if (rows.length === 0 && blockers.length === 0 && unassignable.length === 0) return null;

  return (
    <section className="space-y-4">
      {unassignable.length > 0 && (
        <div className="rounded-md border border-blocker-line bg-surface p-4">
          <SectionHeader
            title="No suitable room was available for:"
            description="Every room in the infrastructure was too small, lacked charging points, or was the wrong kind."
          />
          <ul className="space-y-2">
            {unassignable.map((u) => (
              <li
                key={`${u.section}-${u.subject_code}-${u.type}`}
                className="rounded-md bg-blocker-surface p-3 text-sm"
              >
                <div className="font-semibold text-ink">
                  {u.subject_code} · Section {u.section}
                </div>
                <div className="mt-0.5 text-xs text-ink-soft">
                  {u.type} · {u.strength} students{u.byod ? " · BYOD required" : ""}
                </div>
                <div className="mt-1 text-blocker">
                  <span className="font-medium">Reason:</span> {u.reason}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(rows.length > 0 || blockers.length > 0) && (
        <div className="rounded-md border border-blocker-line bg-surface p-4">
          <SectionHeader
            title="Errors - fix these first"
            description="Each one makes a valid timetable impossible."
          />
          <ul className="space-y-2 text-sm">
            {[...rows, ...blockers].map((p, i) => (
              <li key={i} className="rounded-md bg-blocker-surface p-2.5">
                <div className="font-medium text-ink">{p.where}</div>
                <div className="mt-0.5 text-ink-soft">{p.what}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

/**
 * The things worth knowing that do not stop anything.
 *
 * Folded away by default and counted on the summary line, because a note the
 * teacher has already read three times should not look like a problem the
 * fourth time.
 */
export function ImportNotes({
  notes,
  inferences = [],
  roomChanges,
  acknowledged,
  onAcknowledge,
}: {
  notes: string[];
  /** How the file was read where it does not say outright - expected, and
   *  shown quietly so it can be checked, not as something to fix. */
  inferences?: string[];
  roomChanges: RoomChange[];
  acknowledged: boolean;
  onAcknowledge: (v: boolean) => void;
}) {
  return (
    <section className="space-y-3">
      {inferences.length > 0 && (
        <details className="rounded-md border border-line bg-surface px-3 py-2.5 text-sm text-ink-soft">
          <summary className="flex cursor-pointer items-center gap-2 font-medium text-ink-soft">
            <Info aria-hidden="true" size={14} className="text-ink-muted" />
            How the file was read ({inferences.length})
          </summary>
          <ul className="mt-2 space-y-1.5">
            {inferences.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-ink-muted">
            A section numbered like 24011 or 24012 is read as a lab group of 2401 when 2401 is
            in the same file. If one of them is really a section of its own, give it a number
            that does not follow that pattern and upload the file again.
          </p>
        </details>
      )}
      {notes.length > 0 && (
        <details className="rounded-md border border-caution-line bg-caution-surface px-3 py-2.5 text-sm text-caution">
          <summary className="flex cursor-pointer items-center gap-2 font-medium">
            <TriangleAlert aria-hidden="true" size={14} />
            {notes.length} note{notes.length > 1 ? "s" : ""} about the files
          </summary>
          <ul className="mt-2 space-y-1.5 text-ink-soft">
            {notes.map((w, i) => (
              <li key={i} title={w}>
                {headline(w)}
              </li>
            ))}
          </ul>
        </details>
      )}

      {roomChanges.length > 0 && (
        <div className="rounded-md border border-line bg-surface p-4">
          <SectionHeader
            title={`${roomChanges.length} existing room${roomChanges.length > 1 ? "s" : ""} will change`}
            description="Anything already scheduled there is judged against the new values."
          />
          <ul className="space-y-1 text-sm">
            {roomChanges.map((r) => (
              <li key={r.room}>
                <span className="font-mono font-semibold">{r.room}</span>:{" "}
                {r.changes.map((c) => `${c.label} ${c.before} → ${c.after}`).join("; ")}
              </li>
            ))}
          </ul>
          <label className="mt-3 flex items-start gap-2 text-sm text-ink-soft">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={acknowledged}
              onChange={(e) => onAcknowledge(e.target.checked)}
            />
            <span>
              Apply {roomChanges.length > 1 ? "these changes" : "this change"} when generating.
            </span>
          </label>
        </div>
      )}
    </section>
  );
}

