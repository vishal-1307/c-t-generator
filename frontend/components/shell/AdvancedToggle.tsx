"use client";

import { ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { inAdvanced, readAdvancedOpen, writeAdvancedOpen } from "./nav";

/**
 * Open or closed, for the rail and the drawer alike.
 *
 * Starts closed so the server render and the first client render agree, then
 * settles on the page's own answer: open on an Advanced page, otherwise
 * whatever the arrow was last left at.
 */
export function useAdvancedOpen(pathname: string): [boolean, () => void] {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOpen(inAdvanced(pathname) || readAdvancedOpen());
  }, [pathname]);

  function toggle() {
    setOpen((was) => {
      writeAdvancedOpen(!was);
      return !was;
    });
  }

  return [open, toggle];
}

/** The group's heading, which is also the arrow that folds it. */
export function AdvancedToggle({
  open,
  onToggle,
  controls,
}: {
  open: boolean;
  onToggle: () => void;
  controls: string;
}) {
  // One arrow that turns, rather than two icons swapped, so the change reads
  // as the group opening.
  const Arrow = ChevronRight;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls={controls}
      className="flex min-h-9 w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[11px] font-medium uppercase tracking-wide text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
    >
      <Arrow
        aria-hidden="true"
        size={14}
        className={`shrink-0 transition-transform duration-150 ${open ? "rotate-90" : ""}`}
      />
      Advanced
    </button>
  );
}
