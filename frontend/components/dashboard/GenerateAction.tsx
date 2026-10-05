"use client";

import {
  CalendarClock,
  Check,
  CheckCircle2,
  Download,
  LoaderCircle,
  RotateCcw,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import type { ActionFailure, Stage } from "@/lib/dashboardState";
import type { Run, RunCheck } from "@/lib/types";
import { Alert, Button, Metric, MetricRow, StageChecklist, StatusBadge } from "@/components/ui";
import { DownloadLink } from "@/components/DownloadLink";

/**
 * The button, and anything the last press has to answer for.
 *
 * A failed request is reported *here*, next to the thing that failed, in
 * amber, as a status rather than an alert - never as a red banner over the
 * data, which is a different subject. When the server says a solve is already
 * running, pressing again can only earn the same refusal, so the button goes
 * with it.
 */
export function GenerateAction({
  onGenerate,
  disabled,
  failure,
  onRetry,
}: {
  onGenerate: () => void;
  disabled: boolean;
  failure: ActionFailure | null;
  onRetry: () => void;
}) {
  const waiting = failure?.recovery === "wait";

  return (
    <div className="space-y-3">
      <Button size="lg" onClick={onGenerate} disabled={disabled || waiting} className="w-full sm:w-auto">
        <CalendarClock aria-hidden="true" size={16} />
        Generate Timetable
      </Button>

      {failure && (
        <p
          role="status"
          className="flex flex-wrap items-center gap-2 text-sm text-caution"
        >
          <TriangleAlert aria-hidden="true" size={14} />
          {failure.message}
          {failure.recovery === "signin" ? (
            <Link href="/login" className="font-medium underline">
              Sign in
            </Link>
          ) : (
            <button type="button" onClick={onRetry} className="font-medium underline">
              {waiting ? "Check again" : "Try again"}
            </button>
          )}
        </p>
      )}
    </div>
  );
}

/**
 * What is happening, while it happens.
 *
 * One step at a time: the one in progress, with a moving bar, and the one
 * after it - not a list of five where three are grey placeholders. The server
 * reports when a step ends, not how far through one it is, so the bar says
 * "working", never a percentage; the seconds are real. The full list is a
 * click away for anyone who wants it.
 */
export function GenerationProgress({ stages, elapsed }: { stages: Stage[]; elapsed: number }) {
  const index = Math.max(
    0,
    stages.findIndex((s) => s.status === "active" || s.status === "failed"),
  );
  const current = stages[index];
  const next = stages.slice(index + 1).find((s) => s.status === "pending");
  const done = stages.filter((s) => s.status === "done").length;

  return (
    <section className="rise-enter rounded-md border border-line bg-surface p-5">
      <h2 className="text-base font-semibold text-ink">Generating the timetable</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Placing every class and choosing a room for it. This usually takes a minute or two.
      </p>

      <div className="mt-5" aria-live="polite">
        <div className="flex items-baseline justify-between gap-3 text-xs text-ink-muted">
          <span>
            Step {Math.min(done + 1, stages.length)} of {stages.length}
          </span>
          {elapsed > 0 && <span className="tabular-nums">{elapsed}s</span>}
        </div>
        {current && (
          <p key={current.id} className="fade-enter mt-1 flex items-center gap-2 text-base font-medium text-ink">
            <LoaderCircle aria-hidden="true" size={18} className="animate-spin text-accent" />
            {current.label}
          </p>
        )}
        <div
          className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-hover"
          role="progressbar"
          aria-label={current ? current.label : "Generating"}
          aria-valuetext={current ? `${current.label}, step ${done + 1} of ${stages.length}` : undefined}
        >
          <div className="indeterminate-bar h-full w-2/5 rounded-full bg-accent" />
        </div>
        <p className="mt-2 text-xs text-ink-muted">
          {done > 0 && (
            <span className="text-ok">
              <Check aria-hidden="true" size={12} className="mr-1 inline" />
              {stages.filter((s) => s.status === "done").map((s) => s.label).join(" · ")}
            </span>
          )}
          {done > 0 && next && <span className="text-ink-faint"> · </span>}
          {next && <span>Next: {next.label}</span>}
        </p>
      </div>

      <details className="mt-4 text-sm">
        <summary className="cursor-pointer text-xs text-ink-muted hover:text-ink">
          Show all steps
        </summary>
        <div className="mt-3">
          <StageChecklist stages={stages} />
        </div>
      </details>
    </section>
  );
}

/** It worked: what was produced, and the two things to do with it. */
export function GenerationResult({
  run,
  check,
  classes,
  periods,
  xlsxHref,
  onStartOver,
}: {
  run: Run;
  check: RunCheck | null;
  classes: number;
  periods: number;
  xlsxHref: string;
  onStartOver: () => void;
}) {
  return (
    <section className="rise-enter rounded-md border border-ok-line bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
          <CheckCircle2 aria-hidden="true" size={18} className="text-ok" />
          Timetable Generated Successfully
        </h2>
        {run.status === "OPTIMAL" && <StatusBadge tone="ok">Optimal</StatusBadge>}
      </div>

      <div className="mt-5">
        <MetricRow columns={4}>
          <Metric label="Classes" value={classes} />
          <Metric label="Periods" value={periods} />
          <Metric
            label="Hard Conflicts"
            value={check ? check.count : "…"}
            tone={check && check.count > 0 ? "blocker" : "ok"}
          />
          <Metric
            label="Longest teaching run"
            value={check?.longest_faculty_run ?? "…"}
            hint={check?.max_consecutive ? `periods in a row · at most ${check.max_consecutive}` : "periods in a row"}
          />
        </MetricRow>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <Link
          href="/timetable"
          className="inline-flex h-11 items-center gap-1.5 rounded-md bg-accent px-4 text-sm font-medium text-accent-ink transition-colors hover:bg-accent-hover"
        >
          <CalendarClock aria-hidden="true" size={16} />
          View Timetable
        </Link>
        <DownloadLink
          href={xlsxHref}
          className="inline-flex h-11 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-4 text-sm font-medium text-ink-soft transition-colors hover:bg-surface-hover"
        >
          <Download aria-hidden="true" size={16} />
          Export Excel
        </DownloadLink>
        <button
          type="button"
          onClick={onStartOver}
          className="inline-flex h-11 items-center gap-1.5 px-2 text-sm text-ink-muted underline-offset-2 hover:text-ink hover:underline"
        >
          <RotateCcw aria-hidden="true" size={14} />
          Start again with new files
        </button>
      </div>
    </section>
  );
}

/** It did not work, and why - in a sentence a person can act on. */
export function GenerationFailure({
  reason,
  onTryAgain,
}: {
  reason: string;
  onTryAgain: () => void;
}) {
  return (
    <Alert
      tone="blocker"
      title="Unable to generate the timetable."
      action={
        <Button variant="secondary" size="sm" onClick={onTryAgain}>
          Try again
        </Button>
      }
    >
      {reason}
    </Alert>
  );
}
