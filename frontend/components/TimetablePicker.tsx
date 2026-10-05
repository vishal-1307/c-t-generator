"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { Badge, EmptyState, ErrorBanner, Input, PageHeader, Skeleton } from "@/components/ui";

/**
 * "Pick a section / faculty member / room, then see their timetable."
 *
 * One component for all three because the only difference is what a row is
 * called and where it links - duplicating this three times would just mean
 * three places to fix the next time the empty state or search behaviour
 * changes.
 */
export interface PickerItem {
  id: number;
  primary: string;
  secondary?: string;
  tags?: { label: string; tone?: "slate" | "blue" | "amber" | "green" | "red" }[];
  /** Extra text matched by the search box but not displayed. */
  searchable?: string;
}

export function TimetablePicker({
  title,
  description,
  hrefBase,
  load,
  emptyMessage,
  searchPlaceholder,
}: {
  title: string;
  description: string;
  hrefBase: string;
  load: () => Promise<PickerItem[]>;
  emptyMessage: string;
  searchPlaceholder: string;
}) {
  const [items, setItems] = useState<PickerItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    load()
       
      .then(setItems)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [load]);

  const filtered = useMemo(() => {
    if (!items) return [];
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((i) =>
      `${i.primary} ${i.secondary ?? ""} ${i.searchable ?? ""}`.toLowerCase().includes(q),
    );
  }, [items, query]);

  return (
    <>
      <PageHeader title={title} description={description} />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {items === null ? (
        <Skeleton rows={5} />
      ) : items.length === 0 ? (
        <EmptyState>{emptyMessage}</EmptyState>
      ) : (
        <>
          <div className="mb-4 max-w-sm">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder}
              aria-label={searchPlaceholder}
            />
          </div>
          {filtered.length === 0 ? (
            <EmptyState>No match for “{query}”.</EmptyState>
          ) : (
            <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((item) => (
                <li key={item.id}>
                  <Link
                    href={`${hrefBase}/${item.id}`}
                    className="block rounded-md border border-line bg-surface px-3 py-2.5 transition-colors hover:border-line-strong"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-ink">{item.primary}</span>
                      <div className="flex shrink-0 gap-1">
                        {item.tags?.map((t) => (
                          <Badge key={t.label} tone={t.tone}>
                            {t.label}
                          </Badge>
                        ))}
                      </div>
                    </div>
                    {item.secondary && (
                      <div className="mt-0.5 truncate text-xs text-ink-muted">
                        {item.secondary}
                      </div>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </>
  );
}
