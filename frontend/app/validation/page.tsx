"use client";

import { Check, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useAcademicContext } from "@/lib/academicContext";
import { ApiError, api } from "@/lib/api";
import type { ReadinessCheck, ValidationReport } from "@/lib/types";
import { Badge, Button, Card, ErrorBanner, PageHeader, Skeleton, Stat } from "@/components/ui";

/**
 * Readiness rules live server-side in `backend/app/validation.py`. This page
 * renders that output and nothing else - it never re-derives a verdict, so it
 * can never disagree with what the solver will actually refuse to run on.
 */

/** Where to go to fix each check. Keys match validation.py's check ids. */
const FIX_LINKS: Record<string, string> = {
  context_exists: "/sections",
  grid_exists: "/timeslots",
  sections_have_curriculum: "/mappings",
  subjects_have_faculty: "/mappings",
  faculty_not_overloaded: "/mappings",
  pairs_have_eligible_room: "/rooms",
  theory_rooms_exist: "/rooms",
  no_theory_pinned: "/rooms",
  lab_pressure: "/rooms",
  demand_fits_grid: "/subjects",
  multi_hour_blocks_fit: "/timeslots",
};

export default function ValidationPage() {
  const { currentId, current } = useAcademicContext();
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setReport(await api.validate(currentId ?? undefined));
      setError(null);
    } catch (e) {
      setReport(null);
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [currentId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const blockers = report?.checks.filter((c) => !c.ok && c.severity === "blocker") ?? [];
  const warnings = report?.checks.filter((c) => !c.ok && c.severity === "warning") ?? [];
  const passing = report?.checks.filter((c) => c.ok) ?? [];

  return (
    <>
      <PageHeader
        title="Validation"
        description={
          current
            ? `Pre-solve readiness for ${current.label}. Every rule the solver depends on, checked before it runs.`
            : "Pre-solve readiness checks."
        }
        actions={
          <Button variant="secondary" onClick={load} disabled={loading}>
            {loading ? "Checking…" : "Re-check"}
          </Button>
        }
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {loading && !report ? (
        <Skeleton rows={6} />
      ) : !report ? null : (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat
              label="Errors"
              value={blockers.length}
              hint={blockers.length ? "must be fixed" : "none"}
            />
            <Stat label="Warnings" value={warnings.length} hint="worth reviewing" />
            <Stat label="Passing" value={passing.length} hint="checks OK" />
            <Stat
              label="Teachable slots"
              value={report.stats.teachable_slots ?? 0}
              hint={`${report.stats.total_periods_demanded ?? 0} periods demanded`}
            />
          </div>

          {report.ready ? (
            <div className="mb-6 rounded-md border border-ok-line bg-ok-surface px-4 py-3 text-sm text-ok">
              <span className="font-medium">Ready to generate.</span>{" "}
              {report.stats.scheduled_pairs} section-subject pairs need{" "}
              {report.stats.total_periods_demanded} periods against{" "}
              {report.stats.teachable_slots} teachable slots per section.
              {warnings.length > 0 &&
                ` ${warnings.length} warning${warnings.length > 1 ? "s" : ""} worth reviewing.`}
              <div className="mt-2">
                <Link
                  href="/generate"
                  className="inline-flex h-8 items-center rounded-md bg-accent px-3 text-xs font-medium text-accent-ink transition-colors hover:bg-accent-hover"
                >
                  Go to Generate
                </Link>
              </div>
            </div>
          ) : (
            <div className="mb-6 rounded-md border border-blocker-line bg-blocker-surface px-4 py-3 text-sm text-blocker">
              <span className="font-medium">
                Generation is blocked by {blockers.length} error
                {blockers.length > 1 ? "s" : ""}.
              </span>{" "}
              Each one makes a valid timetable impossible.
            </div>
          )}

          <div className="space-y-4">
            {blockers.length > 0 && (
              <CheckGroup title="Errors" tone="red" checks={blockers} />
            )}
            {warnings.length > 0 && (
              <CheckGroup title="Warnings" tone="amber" checks={warnings} />
            )}
            {passing.length > 0 && (
              <CheckGroup title="Passing" tone="green" checks={passing} />
            )}
          </div>
        </>
      )}
    </>
  );
}

function CheckGroup({
  title,
  tone,
  checks,
}: {
  title: string;
  tone: "red" | "amber" | "green";
  checks: ReadinessCheck[];
}) {
  const dot = { red: "bg-blocker", amber: "bg-caution", green: "bg-ok" }[tone];
  return (
    <Card title={`${title} (${checks.length})`}>
      <ul className="space-y-2">
        {checks.map((c) => {
          const href = FIX_LINKS[c.id];
          const body = (
            <>
              <span
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-accent-ink ${dot}`}
                aria-hidden
              >
                {c.ok ? <Check size={10} strokeWidth={3} /> : <TriangleAlert size={10} strokeWidth={3} />}
              </span>
              <span className="flex-1">
                <span className={c.ok ? "text-ink-soft" : "font-medium text-ink"}>
                  {c.label}
                </span>
                <span className="ml-2 text-xs text-ink-muted">{c.detail}</span>
                {c.offenders.length > 0 && !c.ok && (
                  <span className="mt-1 flex flex-wrap gap-1">
                    {c.offenders.slice(0, 8).map((o, i) => (
                      <Badge key={i} tone="slate">
                        {o}
                      </Badge>
                    ))}
                    {c.offenders.length > 8 && (
                      <span className="text-[11px] text-ink-faint">
                        +{c.offenders.length - 8} more
                      </span>
                    )}
                  </span>
                )}
              </span>
            </>
          );
          return (
            <li key={c.id} className="flex items-start gap-3 text-sm">
              {href && !c.ok ? (
                <Link href={href} className="flex flex-1 items-start gap-3 hover:underline">
                  {body}
                </Link>
              ) : (
                body
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
