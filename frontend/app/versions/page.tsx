"use client";

import { Lock } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useAcademicContext } from "@/lib/academicContext";
import { useAuth } from "@/lib/auth";
import { ApiError, api } from "@/lib/api";
import type { PublishStatus, RunSummary, VersionDiff } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  PageHeader,
  Select,
  Skeleton,
} from "@/components/ui";

const PUBLISH_TONE: Record<PublishStatus, "slate" | "blue" | "green" | "amber"> = {
  DRAFT: "slate",
  VALIDATED: "blue",
  PUBLISHED: "green",
  ARCHIVED: "amber",
};

const STATUS_TONE: Record<string, "slate" | "blue" | "green" | "amber" | "red"> = {
  OPTIMAL: "green",
  FEASIBLE: "amber",
  PARTIAL: "amber",
  INFEASIBLE: "red",
  TIMEOUT: "amber",
  RUNNING: "blue",
};

export default function VersionsPage() {
  const { currentId, current } = useAcademicContext();
  const { canEdit } = useAuth();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const [fromId, setFromId] = useState<string>("");
  const [toId, setToId] = useState<string>("");
  const [diff, setDiff] = useState<VersionDiff | null>(null);
  const [diffBusy, setDiffBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRuns(await api.runs.list(currentId ?? undefined));
      setError(null);
    } catch (e) {
      setRuns([]);
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, [currentId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  async function act(id: number, action: "validate" | "publish" | "delete") {
    setBusyId(id);
    try {
      if (action === "validate") await api.runs.validateRun(id);
      else if (action === "publish") await api.runs.publish(id);
      else await api.runs.remove(id);
      setError(null);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function compare() {
    if (!fromId || !toId) return;
    setDiffBusy(true);
    try {
      setDiff(await api.runs.diff(Number(fromId), Number(toId)));
      setError(null);
    } catch (e) {
      setDiff(null);
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setDiffBusy(false);
    }
  }

  const solved = (runs ?? []).filter((r) => r.status === "OPTIMAL" || r.status === "FEASIBLE");

  return (
    <>
      <PageHeader
        title="Timetable versions"
        description={
          current
            ? `Every generated version for ${current.label}. Exactly one can be published at a time.`
            : "Generated timetable versions."
        }
        actions={
          <Link
            href="/generate"
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-ink hover:bg-accent-hover"
          >
            Generate new
          </Link>
        }
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {runs === null ? (
        <Skeleton rows={5} />
      ) : runs.length === 0 ? (
        <EmptyState>
          No timetable has been generated for this academic context yet.
        </EmptyState>
      ) : (
        <>
          <div className="relative overflow-x-auto rounded-md border border-line bg-surface">
            <table className="w-full min-w-[56rem] text-sm">
              <thead className="bg-surface-sunken text-left text-xs uppercase tracking-wide text-ink-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Version</th>
                  <th className="px-3 py-2 font-medium">Solver result</th>
                  <th className="px-3 py-2 font-medium">Lifecycle</th>
                  <th className="px-3 py-2 font-medium">Generated</th>
                  <th className="px-3 py-2 font-medium">Objective</th>
                  <th className="px-3 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {runs.map((r) => {
                  const published = r.publish_status === "PUBLISHED";
                  const canSolve = r.status === "OPTIMAL" || r.status === "FEASIBLE";
                  return (
                    <tr key={r.id} className={published ? "bg-ok-surface/40" : "hover:bg-surface-sunken"}>
                      <td className="px-3 py-2">
                        <span className="font-medium text-ink">v{r.version}</span>
                        <span className="ml-2 text-xs text-ink-faint">run #{r.id}</span>
                      </td>
                      <td className="px-3 py-2">
                        <Badge tone={STATUS_TONE[r.status] ?? "slate"}>{r.status}</Badge>
                        {r.status === "FEASIBLE" && (
                          <span className="ml-2 text-[11px] text-ink-faint">
                            valid, not proven optimal
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <Badge tone={PUBLISH_TONE[r.publish_status]}>{r.publish_status}</Badge>
                        {published && (
                          <span className="ml-2 text-[11px] text-ok">active</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-xs text-ink-muted">
                        {new Date(r.created_at).toLocaleString()}
                        {r.solve_time_seconds != null && (
                          <div className="text-ink-faint">
                            solved in {r.solve_time_seconds.toFixed(1)}s
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2 text-xs text-ink-muted">
                        {r.objective_value ?? "—"}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex justify-end gap-1">
                          {canSolve && r.publish_status === "DRAFT" && (
                            <Button
                              variant="ghost"
                              onClick={() => act(r.id, "validate")}
                              disabled={busyId === r.id || !canEdit}
                            >
                              Validate
                            </Button>
                          )}
                          {canSolve && !published && r.publish_status !== "ARCHIVED" && (
                            <Button
                              variant="secondary"
                              onClick={() => act(r.id, "publish")}
                              disabled={busyId === r.id || !canEdit}
                            >
                              Publish
                            </Button>
                          )}
                          <Link
                            href={`/timetables/master?run=${r.id}`}
                            className="rounded-md px-3 py-1.5 text-sm text-ink-muted hover:bg-surface-hover hover:text-ink"
                          >
                            View
                          </Link>
                          {published ? (
                            <span
                              className="px-3 py-1.5 text-sm text-ink-faint"
                              title="A published timetable is protected from deletion"
                            >
                              <Lock aria-hidden="true" size={14} />
                              <span className="sr-only">Protected from deletion</span>
                            </span>
                          ) : (
                            <Button
                              variant="danger"
                              onClick={() => act(r.id, "delete")}
                              disabled={busyId === r.id || !canEdit}
                            >
                              Delete
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {solved.length >= 2 && (
            <div className="mt-6">
              <Card title="Compare versions">
                <div className="flex flex-wrap items-end gap-3">
                  <div className="w-48">
                    <Select label="From" value={fromId} onChange={(e) => setFromId(e.target.value)}>
                      <option value="">— pick —</option>
                      {solved.map((r) => (
                        <option key={r.id} value={r.id}>
                          v{r.version} (run #{r.id})
                        </option>
                      ))}
                    </Select>
                  </div>
                  <div className="w-48">
                    <Select label="To" value={toId} onChange={(e) => setToId(e.target.value)}>
                      <option value="">— pick —</option>
                      {solved.map((r) => (
                        <option key={r.id} value={r.id}>
                          v{r.version} (run #{r.id})
                        </option>
                      ))}
                    </Select>
                  </div>
                  <Button onClick={compare} disabled={!fromId || !toId || diffBusy}>
                    {diffBusy ? "Comparing…" : "Compare"}
                  </Button>
                </div>

                {diff && (
                  <div className="mt-4">
                    <div className="mb-3 flex flex-wrap gap-2 text-xs">
                      <Badge tone="green">{diff.added} added</Badge>
                      <Badge tone="red">{diff.removed} removed</Badge>
                      <Badge tone="blue">{diff.moved} moved</Badge>
                      <Badge tone="amber">{diff.faculty_changed} faculty changed</Badge>
                      <Badge tone="slate">{diff.room_changed} room changed</Badge>
                    </div>
                    {diff.rows.length === 0 ? (
                      <p className="text-sm text-ink-muted">
                        These two versions are identical.
                      </p>
                    ) : (
                      <div className="max-h-96 overflow-y-auto rounded-md border border-line">
                        <table className="w-full text-sm">
                          <thead className="sticky top-0 bg-surface-sunken text-left text-xs uppercase tracking-wide text-ink-muted">
                            <tr>
                              <th className="px-3 py-2 font-medium">Section</th>
                              <th className="px-3 py-2 font-medium">Subject</th>
                              <th className="px-3 py-2 font-medium">Change</th>
                              <th className="px-3 py-2 font-medium">Detail</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-line">
                            {diff.rows.map((row, i) => (
                              <tr key={i}>
                                <td className="px-3 py-1.5 font-mono text-xs">{row.section_number}</td>
                                <td className="px-3 py-1.5 font-medium">{row.subject_code}</td>
                                <td className="px-3 py-1.5">
                                  <Badge
                                    tone={
                                      row.change === "added"
                                        ? "green"
                                        : row.change === "removed"
                                          ? "red"
                                          : row.change === "moved"
                                            ? "blue"
                                            : "amber"
                                    }
                                  >
                                    {row.change.replace("_", " ")}
                                  </Badge>
                                </td>
                                <td className="px-3 py-1.5 text-xs text-ink-soft">{row.detail}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </Card>
            </div>
          )}
        </>
      )}
    </>
  );
}
