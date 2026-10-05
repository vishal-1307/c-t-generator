"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import type { Grid, GridCell } from "@/lib/types";
import { Badge, Card, ErrorBanner, PageHeader, Skeleton } from "@/components/ui";
import { GridLegend, TimetableGrid } from "@/components/TimetableGrid";
import { ClassDetailDrawer, cellToClassRef, type ClassRef } from "@/components/ClassDetailDrawer";
import { DownloadLink } from "@/components/DownloadLink";

/**
 * Shared shell for the section / faculty / room timetable pages.
 *
 * Clicking a class opens the detail drawer, which is also where every manual
 * edit happens. On a successful edit the grid is refetched rather than patched
 * locally - the backend stays the only source of truth, so no view can hold a
 * stale value after a change.
 */
export function GridPage({
  load,
  backHref,
  backLabel,
  editable = true,
  exportPdfUrl,
}: {
  load: () => Promise<Grid>;
  backHref: string;
  backLabel: string;
  editable?: boolean;
  /** Given the loaded grid (for its run_id), the printable PDF URL for this
   *  exact view - omitted where export doesn't apply. */
  exportPdfUrl?: (grid: Grid) => string;
}) {
  const [grid, setGrid] = useState<Grid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<ClassRef | null>(null);

  const fetchGrid = useCallback(async () => {
    try {
      setGrid(await load());
      setError(null);
    } catch (e) {
      setGrid(null);
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, [load]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchGrid();
  }, [fetchGrid]);

  const onCellClick = useCallback((cell: GridCell, when: string) => {
    setTarget(cellToClassRef(cell, when));
  }, []);

  const onChanged = useCallback(() => {
    setTarget(null);
    void fetchGrid();
  }, [fetchGrid]);

  if (error) {
    return (
      <>
        <PageHeader title="Timetable" />
        <ErrorBanner error={error} />
        <Link href={backHref} className="text-sm text-ink-soft underline">
          {backLabel}
        </Link>
      </>
    );
  }

  if (!grid) {
    return (
      <>
        <PageHeader title="Timetable" />
        <Skeleton rows={6} />
      </>
    );
  }

  const notProven = grid.run_status === "FEASIBLE";
  const partial = grid.run_status === "PARTIAL";

  return (
    <>
      <PageHeader
        title={grid.title}
        description={grid.subtitle}
        actions={
          <>
            {exportPdfUrl && (
              <DownloadLink
                href={exportPdfUrl(grid)}
                className="rounded-md border border-line-strong px-3 py-1.5 text-sm text-ink-soft hover:bg-surface-sunken"
              >
                Export PDF
              </DownloadLink>
            )}
            <Link
              href={backHref}
              className="rounded-md border border-line-strong px-3 py-1.5 text-sm text-ink-soft hover:bg-surface-sunken"
            >
              {backLabel}
            </Link>
          </>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
        {/* Which version, and whether it is the one in force. Without this a
            reader cannot tell an experiment from the timetable the institution
            is actually running - they look the same. */}
        <Badge tone={grid.publish_status === "PUBLISHED" ? "green" : "slate"}>
          v{grid.version} {grid.publish_status === "PUBLISHED" ? "published" : "draft"}
        </Badge>
        <span>
          {grid.total_periods} periods per week
        </span>
        {notProven && <Badge tone="amber">valid, not proven optimal</Badge>}
        {partial && <Badge tone="amber">partial timetable</Badge>}
        {editable && grid.total_periods > 0 && (
          <span className="text-ink-faint">· click a class to view or edit it</span>
        )}
      </div>

      <TimetableGrid grid={grid} onCellClick={editable ? onCellClick : undefined} />
      <GridLegend grid={grid} />

      {grid.total_periods === 0 && (
        <div className="mt-4">
          <Card>
            <p className="text-sm text-ink-muted">
              Nothing was scheduled here in this run.
            </p>
          </Card>
        </div>
      )}

      <ClassDetailDrawer
        target={target}
        onClose={() => setTarget(null)}
        onChanged={onChanged}
      />
    </>
  );
}
