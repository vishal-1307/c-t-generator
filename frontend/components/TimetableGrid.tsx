"use client";

import { Lock } from "lucide-react";
import type { Grid, GridCell } from "@/lib/types";

/**
 * Renders a generated timetable as days x periods.
 *
 * Multi-hour blocks arrive from the API already merged: the first period
 * carries `span`, the rest are marked `continuation` and are skipped here. That
 * keeps contiguity a solver concern rather than something the UI re-derives.
 */

/**
 * Two tones, and only two.
 *
 * A per-subject palette was prettier and said nothing a reader could rely on:
 * eight hashed colours on one screen is decoration, and a reader who cannot
 * tell them apart loses nothing because there was nothing in them. What
 * distinguishes a class here is what it *is* - a lab is tinted and labelled,
 * a lecture is plain - so the tint is a second signal, never the only one.
 */
function toneFor(cell: { session_type?: string | null; subject_type?: string | null }): string {
  return isLab(cell)
    ? "border-accent-line bg-accent-surface text-ink"
    : "border-line bg-surface-sunken text-ink";
}

function isLab(cell: { session_type?: string | null; subject_type?: string | null }): boolean {
  return cell.session_type === "P" || cell.subject_type === "practical";
}

/**
 * A room as people name it: "36-301", not "301". Two blocks can both have a
 * room 301, and a timetable that says only the number sends someone to the
 * wrong building.
 */
export function cellRoom(cell: { room_number: string | null; block: string | null }): string | null {
  if (!cell.room_number) return null;
  return cell.block ? `${cell.block}-${cell.room_number}` : cell.room_number;
}



export function TimetableGrid({
  grid,
  onCellClick,
}: {
  grid: Grid;
  /** When supplied, scheduled cells become buttons that open a detail panel. */
  onCellClick?: (cell: GridCell, when: string) => void;
}) {
  return (
    <>
      {/* A phone gets the same timetable read down the page. The grid below is
          a five-by-nine table with a 52rem floor, which on a 390px screen is a
          viewport-sized window onto a page-sized object - and a timetable is
          most often read on a phone, by somebody standing outside a room
          wondering whether they are in the right place. */}
      <div className="md:hidden">
        <DayList grid={grid} onCellClick={onCellClick} />
      </div>
      <div className="relative hidden overflow-x-auto rounded-md border border-line bg-surface md:block">
      <table className="w-full min-w-[52rem] border-collapse text-sm">
        <thead>
          <tr className="border-b border-line bg-surface-sunken">
            <th className="sticky left-0 z-10 bg-surface-sunken px-3 py-2 text-left text-xs font-medium text-ink-muted">
              Day
            </th>
            {grid.periods.map((p) => (
              <th key={p} className="px-2 py-2 text-center">
                <div className="text-xs font-medium text-ink-soft">P{p + 1}</div>
                <div className="font-mono text-[10px] font-normal text-ink-faint">
                  {grid.period_labels[p]}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {grid.rows.map((row) => (
            <tr key={row.day_index} className="h-14">
              <th className="sticky left-0 z-10 whitespace-nowrap bg-surface px-3 py-1.5 text-left text-xs font-medium text-ink-soft">
                {row.day}
              </th>
              {row.cells.map((cell) =>
                // Continuations are already covered by the previous cell's colSpan.
                cell.continuation ? null : (
                  <Cell
                    key={cell.period_index}
                    cell={cell}
                    perspective={grid.perspective}
                    when={`${row.day} ${grid.period_labels[cell.period_index] ?? `P${cell.period_index + 1}`}`}
                    onCellClick={onCellClick}
                  />
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </>
  );
}

/**
 * The same timetable as a list of days, for a screen too narrow to hold a
 * grid. Empty periods are omitted rather than drawn: on a phone the useful
 * question is "what is on, and when", and forty blank cells answer it worse
 * than nine filled ones.
 */
function DayList({
  grid,
  onCellClick,
}: {
  grid: Grid;
  onCellClick?: (cell: GridCell, when: string) => void;
}) {
  return (
    <div className="space-y-3">
      {grid.rows.map((row) => {
        const classes = row.cells.filter((c) => c.subject_code && !c.continuation);
        return (
          <section
            key={row.day_index}
            className="rounded-md border border-line bg-surface"
          >
            <h3 className="border-b border-line px-3 py-1.5 text-xs font-semibold text-ink-soft">
              {row.day}
              <span className="ml-2 font-normal text-ink-faint">
                {classes.length === 0
                  ? "nothing scheduled"
                  : `${classes.length} class${classes.length === 1 ? "" : "es"}`}
              </span>
            </h3>
            {classes.length > 0 && (
              <ul className="divide-y divide-line">
                {classes.map((cell) => {
                  const when = `${row.day} ${
                    grid.period_labels[cell.period_index] ??
                    `P${cell.period_index + 1}`
                  }`;
                  const inner = (
                    <div className="flex w-full items-start gap-3 px-3 py-2 text-left">
                      <div className="w-20 shrink-0">
                        <div className="text-[11px] font-medium text-ink-soft">
                          P{cell.period_index + 1}
                          {cell.span > 1 ? `-${cell.period_index + cell.span}` : ""}
                        </div>
                        <div className="font-mono text-[10px] text-ink-faint">
                          {grid.period_labels[cell.period_index]}
                        </div>
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1 text-sm font-semibold text-ink">
                          {cell.subject_code}
                          {cell.subject_type === "mixed" && cell.session_type && (
                            <span
                              className="rounded bg-surface-hover px-1 text-[10px] font-bold text-ink-soft"
                              title={
                                cell.session_type === "L" ? "Lecture" : "Practical"
                              }
                            >
                              {cell.session_type}
                            </span>
                          )}
                          {cell.locked && <span title="Locked">&#128274;</span>}
                        </div>
                        <div className="truncate text-xs text-ink-muted">
                          {cell.subject_name}
                        </div>
                        <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-ink-muted">
                          {grid.perspective !== "faculty" && cell.faculty_name && (
                            <span>{cell.faculty_name}</span>
                          )}
                          {grid.perspective !== "section" && cell.section_number && (
                            <span>{cell.section_number}</span>
                          )}
                          {grid.perspective !== "room" && cell.room_number && (
                            <span className="font-mono">{cellRoom(cell)}</span>
                          )}
                        </div>
                      </div>
                      {isLab(cell) && (
                        <span className="mt-1 shrink-0 rounded bg-accent-surface px-1.5 py-0.5 text-[10px] font-medium text-accent">
                          Lab
                        </span>
                      )}
                    </div>
                  );
                  return (
                    <li key={`${cell.period_index}`}>
                      {onCellClick ? (
                        <button
                          type="button"
                          onClick={() => onCellClick(cell, when)}
                          className="w-full hover:bg-surface-sunken focus:outline-none focus:ring-2 focus:ring-inset focus:ring-ring"
                        >
                          {inner}
                        </button>
                      ) : (
                        inner
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}



function Cell({
  cell,
  perspective,
  when,
  onCellClick,
}: {
  cell: GridCell;
  perspective: Grid["perspective"];
  when: string;
  onCellClick?: (cell: GridCell, when: string) => void;
}) {
  if (cell.is_lunch) {
    return (
      <td className="bg-surface-hover p-1 text-center align-middle">
        <span className="text-[10px] font-medium uppercase tracking-wide text-ink-faint">
          Lunch
        </span>
      </td>
    );
  }

  if (!cell.subject_code) {
    return (
      <td className="p-1 text-center align-middle">
        <span className="text-[10px] text-ink-faint">No Class</span>
      </td>
    );
  }

  // What identifies a class depends on who is looking at it: a section already
  // knows it is itself, a faculty member already knows what they teach.
  const secondary =
    perspective === "section"
      ? cell.faculty_name
      : perspective === "faculty"
        ? cell.section_number
        : `${cell.section_number} · ${cell.faculty_code}`;

  const body = (
    <>
      <div className="flex items-center justify-center gap-1 text-xs font-semibold leading-tight">
        {cell.subject_code}
        {/* Only for a subject that has both: badging every class L would be
            noise on the great majority that only ever have one component. */}
        {cell.subject_type === "mixed" && cell.session_type && (
          <span
            className="rounded bg-surface/70 px-1 text-[9px] font-bold leading-none text-ink-soft"
            title={cell.session_type === "L" ? "Lecture" : "Practical"}
          >
            {cell.session_type}
          </span>
        )}
        {cell.locked && (
          <span title="Locked" className="inline-flex text-caution">
            <Lock aria-hidden="true" size={10} />
            <span className="sr-only">Locked</span>
          </span>
        )}
      </div>
      {isLab(cell) && (
        <div className="text-[9px] font-medium uppercase tracking-wide text-accent">Lab</div>
      )}
      <div className="truncate text-[10px] leading-tight opacity-75">{secondary}</div>
      {perspective !== "room" && (
        <div className="font-mono text-[10px] leading-tight opacity-60">
          {cellRoom(cell)}
        </div>
      )}
    </>
  );

  const classes = `w-full rounded border px-1.5 py-1 text-center ${toneFor(cell)}`;
  const title = `${cell.subject_name ?? ""} — ${cell.faculty_name ?? ""} — ${cellRoom(cell) ?? ""}`;

  return (
    <td colSpan={Math.max(cell.span, 1)} className="p-1 align-middle">
      {onCellClick ? (
        <button
          type="button"
          onClick={() => onCellClick(cell, when)}
          title={title}
          className={`${classes} cursor-pointer transition-shadow hover:shadow-md focus:outline-none focus:ring-2 focus:ring-ring`}
        >
          {body}
        </button>
      ) : (
        <div className={classes} title={title}>
          {body}
        </div>
      )}
    </td>
  );
}

export function GridLegend({ grid }: { grid: Grid }) {
  const subjects = new Map<string, string>();
  for (const row of grid.rows) {
    for (const cell of row.cells) {
      if (cell.subject_code && !cell.continuation) {
        subjects.set(cell.subject_code, cell.subject_name ?? cell.subject_code);
      }
    }
  }
  if (subjects.size === 0) return null;

  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {[...subjects.entries()].sort().map(([code, name]) => (
        <span
          key={code}
          className="inline-flex items-center gap-1 rounded border border-line bg-surface px-1.5 py-0.5 text-[11px] text-ink-soft"
        >
          <strong>{code}</strong>
          <span className="opacity-70">{name}</span>
        </span>
      ))}
    </div>
  );
}
