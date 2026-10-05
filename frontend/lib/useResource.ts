"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";

/**
 * Load a whole list from the API, with refresh and a shared error channel.
 *
 * Superseded by `lib/useApi.ts` for anything that grows. The loader here is
 * frozen in a `useCallback` with no dependencies, so it can never see a
 * changed academic context - which is why the sections page used to fetch
 * every section in the institution and filter by semester in the browser - and
 * it has no notion of a page, so the search box could only ever search what
 * had already been downloaded.
 *
 * What it is still right for: a list that is small *by construction* rather
 * than small today. The timetable grid is the remaining caller - a week of
 * periods is forty-odd rows, every one of which the page draws - and paging
 * that would be ceremony around a complete answer.
 */
export function useResource<T>(loader: () => Promise<T[]>) {
  const [rows, setRows] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await loader());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Standard fetch-on-mount: `load`/`refresh` is stable (useCallback), and the
    // eventual setState happens in a resolved microtask, not synchronously in the
    // effect body. eslint-plugin-react-hooks' new set-state-in-effect rule flags this
    // shape regardless; there is no external-system subscription to rewrite it into.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  /** Run a write, surface its error, and refetch on success. */
  const mutate = useCallback(
    async (fn: () => Promise<unknown>): Promise<boolean> => {
      try {
        await fn();
        setError(null);
        await refresh();
        return true;
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
        return false;
      }
    },
    [refresh],
  );

  return { rows, loading, error, setError, refresh, mutate };
}
