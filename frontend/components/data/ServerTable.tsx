"use client";

import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useEffect, useState } from "react";
import { useApi } from "@/lib/useApi";
import { useUrlState } from "@/lib/urlState";
import type { ListQuery, Paged } from "@/lib/types";
import { Column, DataTable, ErrorBanner, Skeleton } from "@/components/ui";

/**
 * A table whose searching, sorting and paging happen in the database.
 *
 * The entity screens each fetched their whole table and did the rest in the
 * browser. That is fine for the twenty rows a demo has and untenable for the
 * three thousand subjects a university has: the response alone is megabytes
 * before a single row is drawn, and the search box only ever searched what had
 * already been downloaded - so typing a code that existed on page nine
 * truthfully reported nothing found.
 *
 * The presentational `DataTable` is unchanged and still owns how a row looks.
 * This adds only the part that has to talk to the server.
 *
 * Search term, sort column and page live in the URL, so a filtered view can be
 * shared and survives a reload.
 */
export function ServerTable<T extends { id: number }>({
  cacheKey,
  fetcher,
  columns,
  empty,
  searchPlaceholder = "Search",
  pageSize = 50,
  refreshToken,
  toolbar,
}: {
  /** Cache prefix; a write invalidates every table sharing it. */
  cacheKey: string;
  fetcher: (params: ListQuery) => Promise<Paged<T>>;
  columns: SortableColumn<T>[];
  empty: React.ReactNode;
  searchPlaceholder?: string;
  pageSize?: number;
  /** Changes when something outside the table has edited the data. */
  refreshToken?: unknown;
  toolbar?: React.ReactNode;
}) {
  const [state, setState] = useUrlState(DEFAULTS);
  const page = Math.max(0, Number(state.page) || 0);
  const [typed, setTyped] = useState(state.q);

  // The URL is the source of truth, but writing to it on every keystroke would
  // put a request behind each character.
  useEffect(() => {
    if (typed === state.q) return;
    const timer = setTimeout(() => setState({ q: typed, page: "0" }), 250);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typed]);

  const { data, error, loading } = useApi<Paged<T>>(
    [cacheKey, "table", state.q, state.sort, page, refreshToken],
    () =>
      fetcher({
        q: state.q || undefined,
        sort: state.sort || undefined,
        limit: pageSize,
        offset: page * pageSize,
      }),
  );

  const total = data?.total ?? 0;
  const rows = data?.rows ?? [];
  const pages = Math.max(1, Math.ceil(total / pageSize));

  function toggleSort(field: string) {
    const next =
      state.sort === field ? `-${field}` : state.sort === `-${field}` ? "" : field;
    setState({ sort: next, page: "0" });
  }

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={searchPlaceholder}
          className="w-full max-w-xs rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-ink"
        />
        {toolbar}
        <span className="ml-auto text-xs text-ink-faint">
          {loading && !data
            ? "Loading"
            : total === 0
              ? "No matches"
              : `${page * pageSize + 1}-${page * pageSize + rows.length} of ${total}`}
        </span>
      </div>

      <ErrorBanner error={error} />

      {loading && !data ? (
        <Skeleton rows={6} />
      ) : (
        <DataTable
          rows={rows}
          empty={state.q ? `Nothing matches "${state.q}"` : empty}
          columns={columns.map((c, i) => ({
            ...c,
            key: c.sortField ?? (typeof c.header === "string" ? c.header : String(i)),
            header: c.sortField ? (
              <button
                type="button"
                onClick={() => toggleSort(c.sortField!)}
                className="-my-2 inline-flex min-h-9 items-center gap-1 uppercase tracking-wide hover:text-ink"
              >
                {c.header}
                {state.sort === c.sortField ? (
                  <ArrowUp aria-hidden="true" size={12} />
                ) : state.sort === `-${c.sortField}` ? (
                  <ArrowDown aria-hidden="true" size={12} />
                ) : (
                  <ArrowUpDown aria-hidden="true" size={12} className="text-ink-faint" />
                )}
              </button>
            ) : (
              c.header
            ),
          })) as Column<T>[]}
        />
      )}

      {pages > 1 && (
        <div className="mt-3 flex items-center justify-between text-xs">
          <button
            onClick={() => setState({ page: String(Math.max(0, page - 1)) })}
            disabled={page === 0}
            className="rounded-md border border-line-strong px-2 py-1 text-ink-soft disabled:opacity-40"
          >
            Previous
          </button>
          <span className="text-ink-faint">
            Page {page + 1} of {pages}
          </span>
          <button
            onClick={() => setState({ page: String(Math.min(pages - 1, page + 1)) })}
            disabled={page >= pages - 1}
            className="rounded-md border border-line-strong px-2 py-1 text-ink-soft disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}
    </>
  );
}

const DEFAULTS = { q: "", sort: "", page: "0" };

/**
 * A column that may be sorted by. `sortField` is the name the API accepts, and
 * it is whitelisted server-side - an unknown one is refused with the list of
 * what is allowed rather than ignored, so a typo here fails loudly.
 */
export interface SortableColumn<T> {
  header: React.ReactNode;
  cell: (row: T) => React.ReactNode;
  className?: string;
  sortField?: string;
  /** Stable identity, needed when the header is not text. */
  key?: string;
}
