"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAcademicContext } from "@/lib/academicContext";
import { useAuth } from "@/lib/auth";
import { ApiError, api } from "@/lib/api";
import { SummaryFigures } from "@/components/SummaryFigures";
import { wasInterrupted } from "@/lib/failures";
import { POLL_CEILING_MS } from "@/lib/dashboardState";
import type {
  PublishStatus,
  Run,
  RunCheck,
  RunStatus,
  SectionSubjectAssignment,
  ValidationReport,
} from "@/lib/types";
import { Badge, Button, Card, EmptyState, ErrorBanner, PageHeader, Skeleton } from "@/components/ui";

/**
 * Generate, and say plainly how it went.
 *
 * Generation is asynchronous: the POST returns a RUNNING run in well under a
 * second and this page polls it. The backend reports no sub-step progress, so
 * the steps are listed as what happens rather than ticked off one by one - a
 * ticked list the API cannot substantiate would be a percentage bar in
 * disguise.
 *
 * A run started somewhere else - the upload page starts one straight after
 * importing - is picked up and followed here, rather than shown as RUNNING
 * forever until someone reloads.
 */

const TERMINAL: RunStatus[] = ["OPTIMAL", "FEASIBLE", "PARTIAL", "INFEASIBLE", "TIMEOUT"];

/** What the backend does, in order. */
const STEPS = [
  "Validating",
  "Building schedule",
  "Solving",
  "Verifying result",
  "Saving timetable",
];

const FAILED: Record<string, { title: string; blurb: string }> = {
  PARTIAL: {
    title: "Only part of the timetable could be generated.",
    blurb: "What was scheduled is valid. The classes that could not be placed need a change to the data.",
  },
  INFEASIBLE: {
    title: "Unable to generate the timetable.",
    blurb: "The data as it stands cannot produce a timetable - something has to change first.",
  },
  TIMEOUT: {
    title: "Unable to generate the timetable in the time allowed.",
    blurb:
      "The search stopped before it found a timetable. That is not proof that none exists - try again, or generate fewer sections at once.",
  },
};

const PUBLISH_TONE: Record<PublishStatus, "slate" | "blue" | "green" | "amber"> = {
  DRAFT: "slate",
  VALIDATED: "blue",
  PUBLISHED: "green",
  ARCHIVED: "amber",
};

const SUCCESS: RunStatus[] = ["OPTIMAL", "FEASIBLE"];

export default function GeneratePage() {
  const { currentId, current, contexts, loading: ctxLoading } = useAcademicContext();
  const [run, setRun] = useState<Run | null>(null);
  const [check, setCheck] = useState<RunCheck | null>(null);
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [locked, setLocked] = useState<SectionSubjectAssignment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (poll.current) {
      clearInterval(poll.current);
      poll.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const verify = useCallback(async (finished: Run) => {
    if (!SUCCESS.includes(finished.status) || finished.assignment_count === 0) {
      setCheck(null);
      return;
    }
    try {
      setCheck(await api.runs.check(finished.id));
    } catch {
      setCheck(null);
    }
  }, []);

  const load = useCallback(
    async (ctxId: number) => {
      try {
        const [validation, assignments] = await Promise.all([
          api.validate(ctxId),
          api.assignments.list(ctxId).catch(() => [] as SectionSubjectAssignment[]),
        ]);
        setReport(validation);
        setLocked(assignments.filter((a) => a.locked));
        let latest: Run | null = null;
        try {
          latest = await api.runs.latest(ctxId);
        } catch {
          latest = null;
        }
        setRun(latest);
        if (latest && TERMINAL.includes(latest.status)) void verify(latest);
        setError(null);
        return latest;
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
        return null;
      }
    },
    [verify],
  );

  /**
   * Poll a run until it finishes.
   *
   * The solve pins the backend's CPU for up to its whole time budget, which
   * on a small instance can make a single poll time out - a transient hiccup,
   * not the run failing. A few consecutive failures are tolerated before the
   * page gives up; a success in between resets the count.
   */
  const follow = useCallback(
    (runId: number, ctxId: number) => {
      const startedAt = Date.now();
      setBusy(true);
      stopPolling();
      let consecutiveFailures = 0;
      const MAX_CONSECUTIVE_FAILURES = 5;
      poll.current = setInterval(async () => {
        setElapsed(Math.round((Date.now() - startedAt) / 1000));
        if (Date.now() - startedAt > POLL_CEILING_MS) {
          stopPolling();
          setBusy(false);
          setError(
            "This is taking longer than it should. The timetable may still finish - reload this page in a minute to check.",
          );
          return;
        }
        try {
          const latest = await api.runs.get(runId);
          consecutiveFailures = 0;
          setRun(latest);
          if (TERMINAL.includes(latest.status)) {
            stopPolling();
            setBusy(false);
            void load(ctxId);
          }
        } catch (e) {
          consecutiveFailures += 1;
          if (consecutiveFailures < MAX_CONSECUTIVE_FAILURES) return;
          stopPolling();
          setBusy(false);
          setError(e instanceof ApiError ? e.message : String(e));
        }
      }, 1000);
    },
    [load, stopPolling],
  );

  useEffect(() => {
    if (ctxLoading || currentId === null) return;
    let cancelled = false;
    // The state changes happen after awaited requests, not synchronously in
    // the effect body - the same fetch-on-mount shape the old page used.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(currentId).then((latest) => {
      if (!cancelled && latest?.status === "RUNNING") follow(latest.id, currentId);
    });
    return () => {
      cancelled = true;
    };
  }, [currentId, ctxLoading, load, follow]);

  async function start() {
    if (currentId === null) return;
    setElapsed(0);
    setCheck(null);
    try {
      const started = await api.runs.start({ academic_context_id: currentId });
      setRun(started);
      setError(null);
      follow(started.id, currentId);
    } catch (e) {
      setBusy(false);
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function lifecycle(action: "validate" | "publish") {
    if (!run) return;
    setLifecycleBusy(true);
    try {
      setRun(action === "validate" ? await api.runs.validateRun(run.id) : await api.runs.publish(run.id));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLifecycleBusy(false);
    }
  }

  const blockers = report?.checks.filter((c) => !c.ok && c.severity === "blocker") ?? [];
  const { canEdit } = useAuth();
  const canGenerate = currentId !== null && blockers.length === 0 && !busy && canEdit;
  const stats = report?.stats ?? {};

  if (ctxLoading) {
    return (
      <>
        <PageHeader title="Generate timetable" />
        <Skeleton rows={5} />
      </>
    );
  }

  if (contexts.length === 0) {
    return (
      <>
        <PageHeader title="Generate timetable" />
        <EmptyState>
          <p className="mb-3">Nothing has been uploaded yet.</p>
          <Link
            href="/"
            className="inline-flex h-9 items-center rounded-md bg-accent px-3 text-sm font-medium text-accent-ink transition-colors hover:bg-accent-hover"
          >
            Upload Load.xlsx and Infra.xlsx
          </Link>
        </EmptyState>
      </>
    );
  }

  // A run the server abandoned by restarting is recorded as having no
  // timetable, which on its own reads as a verdict on the data.
  const interrupted = wasInterrupted(run?.message);

  return (
    <>
      <PageHeader
        title="Generate timetable"
        description={current ? current.label : "Choose a semester at the top of the page."}
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {!canEdit && (
        <p className="mb-4 text-sm text-caution">
          Sign in as a scheduler or admin to generate a timetable.
        </p>
      )}

      {/* Readiness */}
      {blockers.length > 0 ? (
        <Card title="Not ready to generate">
          <p className="mb-2 text-sm text-ink-soft">Fix these first:</p>
          <ul className="space-y-2">
            {blockers.map((b) => (
              <li key={b.id} className="rounded border border-blocker-line bg-blocker-surface p-3 text-sm">
                <div className="font-medium text-blocker">{b.label}</div>
                <div className="mt-0.5 text-blocker">{b.detail}</div>
              </li>
            ))}
          </ul>
        </Card>
      ) : (
        report && (
          <Card title="Data ready to generate.">
            {current && (
              <p className="mb-3 text-xs text-ink-muted">For {current.label}</p>
            )}
            <SummaryFigures
              figures={{
                sections: stats.context_sections ?? 0,
                faculty: stats.context_faculty ?? 0,
                subjects: stats.context_subjects ?? 0,
                classes: stats.context_classes ?? 0,
                requiredPeriods: stats.context_required_periods ?? stats.total_periods_demanded ?? 0,
                rooms: stats.usable_rooms ?? 0,
                labs: stats.usable_labs ?? 0,
              }}
            />
            {locked.length > 0 && (
              <p className="mt-3 text-sm text-caution">
                {locked.length} class{locked.length > 1 ? "es are" : " is"} locked and will stay
                exactly where {locked.length > 1 ? "they are" : "it is"}.
              </p>
            )}
            <div className="mt-4">
              <Button onClick={start} disabled={!canGenerate}>
                {busy ? `Solving… ${elapsed}s` : "Generate Timetable"}
              </Button>
            </div>
          </Card>
        )
      )}

      {/* Progress */}
      {busy && (
        <Card title="Generating">
          <div className="flex items-center gap-2 text-sm text-ink-soft">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />
            Solving… {elapsed}s
          </div>
          <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-ink-muted">
            {STEPS.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ol>
          <p className="mt-3 text-xs text-ink-faint">
            The server reports when it has finished; it does not report how far along it is, so no
            percentage is shown.
          </p>
        </Card>
      )}

      {/* Result */}
      {run && !busy && TERMINAL.includes(run.status) && (
        <div className="mt-4 space-y-4">
          {SUCCESS.includes(run.status) ? (
            <div className="rounded-md border border-ok-line bg-ok-surface p-4">
              <p className="text-base font-semibold text-ok">
                Timetable generated successfully.
              </p>
              <dl className="mt-3 grid grid-cols-1 gap-2 text-sm text-ok sm:grid-cols-3">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-ok">Status</dt>
                  <dd className="font-semibold">{run.status}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-ok">Required periods</dt>
                  <dd className="font-semibold">{run.assignment_count}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-ok">Hard conflicts</dt>
                  <dd className="font-semibold">{check ? check.count : "checking…"}</dd>
                </div>
              </dl>
              {check && check.count > 0 && (
                <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-blocker">
                  {check.violations.slice(0, 10).map((v) => (
                    <li key={v}>{v}</li>
                  ))}
                </ul>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                <Link
                  href="/timetable"
                  className="inline-flex h-9 items-center rounded-md bg-accent px-3 text-sm font-medium text-accent-ink transition-colors hover:bg-accent-hover"
                >
                  View Timetable
                </Link>
                <Link
                  href="/export"
                  className="rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-sunken"
                >
                  Export
                </Link>
              </div>
              <p className="mt-3 text-xs text-ok">
                {run.solve_time_seconds != null && `Generated in ${run.solve_time_seconds.toFixed(1)}s. `}
                {run.status === "FEASIBLE" &&
                  "Every rule is met; the time limit stopped the search before a tidier arrangement was proven. "}
                Version {run.version}.
              </p>
            </div>
          ) : (
            <div className="rounded-md border border-blocker-line bg-blocker-surface p-4">
              <p className="text-base font-semibold text-blocker">
                {interrupted
                  ? "The timetable did not finish."
                  : (FAILED[run.status]?.title ?? "Unable to generate the timetable.")}
              </p>
              <p className="mt-1 text-sm text-blocker">
                {interrupted
                  ? "The server restarted while it was working. This is not a problem with the data - generate again."
                  : FAILED[run.status]?.blurb}
              </p>
              {run.message && !interrupted && (
                <p className="mt-3 text-sm text-blocker">
                  <span className="font-medium">Reason: </span>
                  {run.message}
                </p>
              )}
            </div>
          )}

          {run.warnings?.length > 0 && (
            <Card title="Worth knowing">
              <ul className="list-disc space-y-1 pl-5 text-sm text-caution">
                {run.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </Card>
          )}

          {SUCCESS.includes(run.status) && (
            <Card title="Share it">
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <Badge tone={PUBLISH_TONE[run.publish_status]}>{run.publish_status}</Badge>
                {run.publish_status === "PUBLISHED" ? (
                  <span className="text-ink-soft">
                    Published
                    {run.published_at ? ` ${new Date(run.published_at).toLocaleString()}` : ""}.
                  </span>
                ) : (
                  <Button
                    variant="secondary"
                    onClick={() => lifecycle("publish")}
                    disabled={lifecycleBusy || !canEdit}
                  >
                    Publish this timetable
                  </Button>
                )}
              </div>
            </Card>
          )}
        </div>
      )}

      {!run && !busy && blockers.length === 0 && report && (
        <p className="mt-4 text-sm text-ink-muted">No timetable has been generated for this semester yet.</p>
      )}
    </>
  );
}
