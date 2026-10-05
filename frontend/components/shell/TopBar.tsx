"use client";

import { ChevronDown, Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useAcademicContext } from "@/lib/academicContext";
import { useAuth } from "@/lib/auth";

/**
 * The bar that stays put: what this is, and who you are signed in as.
 *
 * The teacher's pages have no dataset picker. Each upload of the two files is
 * its own dataset and the newest one is always current, so there is nothing
 * to choose - asking would make "which semester?" the first question again.
 * The picker survives on the administrator's pages, relabelled "Dataset",
 * for reopening an earlier upload.
 */
const TEACHER_PAGES = ["/", "/generate", "/timetable", "/export", "/login"];

function onTeacherPage(pathname: string): boolean {
  return TEACHER_PAGES.some((p) =>
    p === "/" ? pathname === "/" : pathname === p || pathname.startsWith(`${p}/`),
  );
}

export function TopBar({ onOpenMenu }: { onOpenMenu: () => void }) {
  const pathname = usePathname();
  const showSwitcher = !onTeacherPage(pathname);
  return (
    <header className="fixed inset-x-0 top-0 z-30 flex h-(--spacing-topbar) items-center gap-2 border-b border-line bg-surface px-3 sm:px-4">
      <button
        type="button"
        onClick={onOpenMenu}
        aria-label="Open navigation"
        className="-ml-1 rounded-md p-2 text-ink-soft hover:bg-surface-hover hover:text-ink md:hidden"
      >
        <Menu aria-hidden="true" size={18} />
      </button>

      {/* The product says its own name, truncated rather than wrapped when a
          phone is too narrow for all of it. */}
      <Link
        href="/"
        className="min-w-0 truncate text-sm font-semibold text-ink"
        title="College Timetable Generator"
      >
        College Timetable Generator
      </Link>

      <div className="ml-auto flex min-w-0 items-center gap-2">
        {showSwitcher && <ContextSwitcher />}
        <UserMenu />
      </div>
    </header>
  );
}

/**
 * Switching semester is an explicit act with a visible confirmation, not a
 * silent dropdown change: every figure on the screen behind it changes
 * meaning, and the reader should be able to see which one they landed on.
 */
function ContextSwitcher() {
  const { contexts, currentId, setCurrentId, loading } = useAcademicContext();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointer(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (loading) {
    return <div className="h-8 w-40 animate-pulse rounded-md bg-surface-hover" />;
  }

  if (contexts.length === 0) {
    return (
      <Link
        href="/"
        className="rounded-md border border-line-strong px-2.5 py-1.5 text-xs text-ink-soft hover:bg-surface-sunken"
      >
        No data yet — upload the two files
      </Link>
    );
  }

  const current = contexts.find((c) => c.id === currentId) ?? null;

  return (
    <div ref={ref} className="relative min-w-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex min-w-0 max-w-[46vw] items-center gap-1.5 rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-left text-xs text-ink hover:bg-surface-sunken sm:max-w-xs"
      >
        <span className="hidden shrink-0 text-ink-faint sm:inline">Dataset</span>
        <span className="truncate font-medium">
          {current ? current.label : "Choose one"}
        </span>
        <ChevronDown aria-hidden="true" size={12} className="shrink-0" />
      </button>

      {open && (
        <ul
          role="listbox"
          aria-label="Academic context"
          className="absolute right-0 z-40 mt-1 max-h-80 w-[min(20rem,90vw)] overflow-y-auto rounded-md border border-line bg-surface py-1 shadow-lg"
        >
          {contexts.map((c) => {
            const selected = c.id === currentId;
            return (
              <li key={c.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    setCurrentId(c.id);
                    setOpen(false);
                  }}
                  className={`block w-full px-3 py-2 text-left text-xs ${
                    selected
                      ? "bg-surface-sunken font-medium text-ink"
                      : "text-ink-soft hover:bg-surface-hover"
                  }`}
                >
                  {c.label}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();

  if (!user) {
    return (
      <Link
        href="/login"
        className="rounded-md border border-line-strong px-2.5 py-1.5 text-xs text-ink-soft hover:bg-surface-sunken"
      >
        Sign in
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <div className="hidden min-w-0 text-right sm:block">
        <div className="truncate text-xs font-medium text-ink">{user.username}</div>
        <div className="text-[11px] capitalize text-ink-faint">{user.role}</div>
      </div>
      <button
        type="button"
        onClick={logout}
        className="rounded-md px-2 py-1.5 text-xs text-ink-muted hover:bg-surface-hover hover:text-ink"
      >
        Sign out
      </button>
    </div>
  );
}
