"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

/**
 * Screen state that lives in the address bar.
 *
 * Filters, the page you are on and which tab is open were all component state,
 * which meant a filtered timetable could not be sent to anyone, survived
 * neither a reload nor the back button, and produced the specific
 * frustration of narrowing a large table down, following a link, coming back,
 * and starting again.
 *
 * The address bar is the right place for it: it is already the browser's own
 * store of "where am I", and it makes every view addressable for free.
 *
 * Values are strings because that is what a query string holds; an empty
 * string means "not set" and is dropped from the URL, so a default view has a
 * clean address rather than a trail of empty parameters.
 */
export function useUrlState<T extends Record<string, string>>(defaults: T) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const state = useMemo(() => {
    const out = { ...defaults };
    for (const key of Object.keys(defaults) as (keyof T)[]) {
      const value = params.get(String(key));
      if (value !== null) out[key] = value as T[keyof T];
    }
    return out;
  }, [params, defaults]);

  const setState = useCallback(
    (patch: Partial<T>, options?: { replace?: boolean }) => {
      const next = new URLSearchParams(params.toString());
      for (const [key, value] of Object.entries(patch)) {
        // A value equal to its default is not worth carrying: it says nothing
        // the absence of the parameter does not already say.
        if (value === undefined || value === "" || value === defaults[key]) {
          next.delete(key);
        } else {
          next.set(key, String(value));
        }
      }
      const query = next.toString();
      const url = query ? `${pathname}?${query}` : pathname;
      // Replace by default: changing a filter is refining one view, not
      // travelling to a new one, and pushing every keystroke onto the history
      // stack turns the back button into an undo log nobody asked for.
      if (options?.replace === false) router.push(url, { scroll: false });
      else router.replace(url, { scroll: false });
    },
    [defaults, params, pathname, router],
  );

  return [state, setState] as const;
}
