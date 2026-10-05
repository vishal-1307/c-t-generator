"use client";

import { useEffect, useRef, useState } from "react";
import { useApi } from "@/lib/useApi";
import type { Paged } from "@/lib/types";

/**
 * Choose one row out of a table that may have thousands.
 *
 * The pattern it replaces: a native `<select>` holding every candidate,
 * rendered once per owner. On the mappings screen that is one option element
 * per (faculty, subject) pair - two hundred teachers and three hundred
 * subjects is sixty thousand DOM nodes built before the page can paint, none
 * of which anybody reads, because a human scrolling a three-hundred-item
 * dropdown is not choosing, they are hunting.
 *
 * Typing narrows the list in SQL instead. Nothing is fetched until the picker
 * is opened, so a page with two hundred of these costs two hundred buttons.
 */
export function EntityPicker<T extends { id: number }>({
  prefix,
  search,
  label,
  onPick,
  placeholder = "Search…",
  buttonLabel = "+ add",
  disabledIds,
}: {
  /** Cache prefix, so a write elsewhere can invalidate these results. */
  prefix: string;
  search: (q: string) => Promise<Paged<T>>;
  label: (item: T) => string;
  onPick: (item: T) => void;
  placeholder?: string;
  buttonLabel?: string;
  /** Already chosen - shown, but not selectable again. */
  disabledIds?: Set<number>;
}) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);

  // A request per keystroke would be one per character of every search on a
  // page holding hundreds of these.
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(term), 200);
    return () => clearTimeout(timer);
  }, [term]);

  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    function onPointer(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const { data, loading, error } = useApi<Paged<T>>(
    open ? [prefix, "picker", debounced] : null,
    () => search(debounced),
  );

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-md border border-line-strong bg-surface px-2 py-1 text-xs text-ink-soft transition-colors hover:bg-surface-sunken hover:text-ink"
      >
        {buttonLabel}
      </button>
    );
  }

  const rows = data?.rows ?? [];
  const hidden = Math.max(0, (data?.total ?? 0) - rows.length);

  return (
    <div ref={box} className="relative">
      <input
        ref={input}
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        placeholder={placeholder}
        className="w-56 rounded-md border border-line-strong bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-ink"
      />
      <div className="absolute right-0 z-30 mt-1 max-h-64 w-72 overflow-y-auto rounded-md border border-line bg-surface py-1 shadow-lg">
        {error ? (
          <p className="px-3 py-2 text-xs text-blocker">{error}</p>
        ) : loading ? (
          <p className="px-3 py-2 text-xs text-ink-faint">Searching…</p>
        ) : rows.length === 0 ? (
          <p className="px-3 py-2 text-xs text-ink-faint">
            {debounced ? `Nothing matches “${debounced}”` : "Nothing to add"}
          </p>
        ) : (
          <>
            {rows.map((item) => {
              const already = disabledIds?.has(item.id) ?? false;
              return (
                <button
                  key={item.id}
                  type="button"
                  disabled={already}
                  onClick={() => {
                    onPick(item);
                    setOpen(false);
                    setTerm("");
                  }}
                  className={`block w-full px-3 py-1.5 text-left text-xs ${
                    already
                      ? "cursor-not-allowed text-ink-faint line-through"
                      : "text-ink-soft hover:bg-surface-hover hover:text-ink"
                  }`}
                >
                  {label(item)}
                </button>
              );
            })}
            {hidden > 0 && (
              // Silently showing the first page of a larger result set is how
              // somebody concludes a record does not exist.
              <p className="border-t border-line px-3 py-1.5 text-[11px] text-ink-faint">
                {hidden} more — keep typing to narrow
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
