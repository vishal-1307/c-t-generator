"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAcademicContext } from "@/lib/academicContext";
import type { DataSummary } from "@/lib/types";

/**
 * One way in to everything the timetable is built from.
 *
 * The entity screens were seven top-level menu entries, which made "is my data
 * ready?" a question you answered by opening seven pages and counting. The
 * summary endpoint already returns each entity's count and the gaps in it, so
 * the same question is answered here before anyone opens anything.
 *
 * Deliberately not a verdict: `/api/validate` decides whether a timetable can
 * be generated, and duplicating that judgement here would give two answers
 * that could disagree. What this shows is what exists and what looks unfinished.
 */

/** Where each entity in the summary is actually edited. */
const ROUTES: Record<string, string> = {
  faculty: "/faculty",
  subjects: "/subjects",
  rooms: "/rooms",
  sections: "/sections",
  timeslots: "/timeslots",
  availability: "/availability",
};

const ALSO = [
  { href: "/mappings", label: "Who teaches what", hint: "Which teacher takes which subject for which section" },
  {
    href: "/availability",
    label: "Availability",
    hint: "Periods when a teacher, room or section must not be scheduled",
  },
  {
    href: "/availability/search",
    label: "Free rooms & faculty",
    hint: "Who and what is not in use at a given time in the generated timetable",
  },
];

export default function DataPage() {
  const { currentId } = useAcademicContext();
  // The result carries the context it describes. Loading is then derived from
  // whether that matches the context now selected, rather than being switched
  // on synchronously when the effect runs - which would re-render twice on
  // every switch, and shows the previous semester's counts as though they were
  // this one's during the gap.
  const [result, setResult] = useState<{
    key: number | null;
    data?: DataSummary;
    error?: string;
  } | null>(null);

  const key = currentId ?? null;
  const loading = result === null || result.key !== key;
  const summary = !loading ? (result?.data ?? null) : null;
  const error = !loading ? (result?.error ?? null) : null;

  useEffect(() => {
    let cancelled = false;
    api
      .dataSummary(currentId ?? undefined)
      .then((data) => {
        if (!cancelled) setResult({ key, data });
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setResult({
            key,
            error: e instanceof Error ? e.message : "Could not load",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentId, key]);

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-ink">Data</h1>
        <p className="mt-1 text-sm text-ink-muted">
          What the uploaded files were turned into, one screen per kind of record, for
          checking or correcting a single value by hand. Counts are for the selected
          dataset where that matters; rooms, teachers and subjects are shared. To replace
          the data, upload new files on the Dashboard.
        </p>
      </header>

      {error && (
        <div className="mb-4 rounded-md border border-blocker-line bg-blocker-surface px-3 py-2 text-sm text-blocker">
          {error}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {loading
          ? Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-24 animate-pulse rounded-md bg-surface-hover" />
            ))
          : (summary?.entities ?? []).map((entity) => {
              const href = ROUTES[entity.key];
              const card = (
                <>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-sm font-medium text-ink">{entity.label}</span>
                    <span className="text-xl font-semibold text-ink">{entity.count}</span>
                  </div>
                  {entity.issues > 0 ? (
                    <p className="mt-2 text-xs text-caution">
                      {entity.issues} {entity.issue_label ?? "need attention"}
                    </p>
                  ) : (
                    <p className="mt-2 text-xs text-ink-faint">
                      {entity.detail ?? "Nothing outstanding"}
                    </p>
                  )}
                </>
              );
              return href ? (
                <Link
                  key={entity.key}
                  href={href}
                  className="rounded-md border border-line bg-surface p-4 transition-colors hover:border-line-strong hover:bg-surface-sunken"
                >
                  {card}
                </Link>
              ) : (
                <div key={entity.key} className="rounded-md border border-line bg-surface p-4">
                  {card}
                </div>
              );
            })}
      </div>

      <h2 className="mb-2 mt-8 text-sm font-semibold text-ink">Related</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {ALSO.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className="rounded-md border border-line bg-surface p-4 transition-colors hover:border-line-strong hover:bg-surface-sunken"
          >
            <div className="text-sm font-medium text-ink">{item.label}</div>
            <p className="mt-1 text-xs text-ink-muted">{item.hint}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}
