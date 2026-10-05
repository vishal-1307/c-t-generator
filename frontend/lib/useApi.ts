"use client";

import useSWR, { mutate as globalMutate, type SWRConfiguration } from "swr";
import { useCallback, useState } from "react";
import { ApiError } from "./api";

/**
 * Reading from the API, with a cache that knows when it is stale.
 *
 * Every page used to carry its own copy of the same twenty lines - a rows
 * state, a loading flag, an error string, a fetch-on-mount effect - and the
 * copies drifted. `useResource` tried to share them but froze its loader in a
 * `useCallback(..., [])`, so the function could never see a changed academic
 * context; that is why the sections page fetched every section in the database
 * and filtered by context in the browser.
 *
 * Here the key *is* the request. Change the context and the key changes, so
 * the data changes with it; two components asking for the same thing in the
 * same tick make one request; a write invalidates by key prefix rather than by
 * each caller remembering to refetch.
 *
 * `swr` rather than a hand-rolled cache deliberately: deduplication, retry and
 * revalidation are individually easy and collectively where the bugs are.
 */

/** Key: null means "not ready to ask yet", and swr will not fetch. */
export type ApiKey = readonly [string, ...unknown[]] | null;

const DEFAULTS: SWRConfiguration = {
  // A timetable is not a live feed. Refetching because a window regained
  // focus mostly produces flicker and load, not news.
  revalidateOnFocus: false,
  shouldRetryOnError: false,
  dedupingInterval: 2000,
};

export function useApi<T>(
  key: ApiKey,
  fetcher: () => Promise<T>,
  options?: SWRConfiguration,
) {
  const { data, error, isLoading, mutate } = useSWR<T>(
    key,
    key ? fetcher : null,
    { ...DEFAULTS, ...options },
  );
  return {
    data,
    error: error ? message(error) : null,
    loading: isLoading,
    refresh: mutate,
  };
}

/**
 * Run a write, then let every affected read know it is out of date.
 *
 * The prefix is the first element of the key - `["faculty", …]` - so adding a
 * subject to a teacher refreshes every faculty list on the page regardless of
 * which page, search term or sort each of them happens to be showing. A caller
 * refetching only its own query is how one list on a screen ends up disagreeing
 * with another.
 */
export function useMutation() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (fn: () => Promise<unknown>, invalidate: string[] = []) => {
      setBusy(true);
      try {
        await fn();
        setError(null);
        await Promise.all(
          invalidate.map((prefix) =>
            globalMutate(
              (key) => Array.isArray(key) && key[0] === prefix,
              undefined,
              { revalidate: true },
            ),
          ),
        );
        return true;
      } catch (e) {
        setError(message(e));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  return { run, error, setError, busy };
}

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);
}
