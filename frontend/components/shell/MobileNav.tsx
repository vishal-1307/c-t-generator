"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth";
import { AdvancedToggle, useAdvancedOpen } from "./AdvancedToggle";
import { PRIMARY, activeHref, activeSection, advancedFor, type NavItem } from "./nav";

/** The four reached most often, plus the drawer for everything else. */
const BOTTOM = ["/", "/generate", "/timetable", "/export"];


/**
 * The phone shell, which did not previously exist.
 *
 * There was no breakpoint variant, no drawer and no hamburger anywhere in the
 * app: a phone rendered the fixed 15rem sidebar and left roughly seventy
 * pixels for the page. Timetables are looked at on phones constantly - by
 * whoever is standing outside the wrong room - so this is not a nicety.
 *
 * A bottom bar for the few destinations that carry most of the traffic, and a
 * drawer holding the complete structure, so nothing is reachable only on a
 * desktop.
 */
export function MobileNav({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname();
  const { isAdmin } = useAuth();
  const advanced = advancedFor(isAdmin);
  const items = [...PRIMARY, ...advanced];
  const current = activeHref(pathname, items);
  const section = activeSection(pathname, items);
  const [advancedOpen, toggleAdvanced] = useAdvancedOpen(pathname);

  // Navigating is what a menu is for, so opening one and arriving somewhere
  // should leave it closed behind you.
  useEffect(() => {
    onClose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  // A drawer over a scrollable page that still scrolls the page underneath is
  // disorienting on a touch screen.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  const drawerItem = (item: NavItem) => (
    <li key={item.href}>
      <Link
        href={item.href}
        aria-current={current === item.href ? "page" : undefined}
        className={`flex items-center gap-2 rounded-md px-2 py-2.5 text-sm ${
          current === item.href ? "bg-accent-surface font-medium text-accent" : "text-ink-soft"
        }`}
      >
        <item.icon aria-hidden="true" size={16} className="shrink-0" />
        {item.label}
      </Link>
      {item.children && section?.href === item.href && (
        <ul className="mb-1 ml-2 border-l border-line pl-2">
          {item.children.map((child) => (
            <li key={child.href}>
              <Link
                href={child.href}
                aria-current={current === child.href ? "page" : undefined}
                className={`flex items-center gap-2 rounded-md px-2 py-2 text-[13px] ${
                  current === child.href
                    ? "bg-surface-hover font-medium text-ink"
                    : "text-ink-muted"
                }`}
              >
                {child.icon && <child.icon aria-hidden="true" size={14} className="shrink-0" />}
                {child.label}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </li>
  );

  return (
    <>
      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            onClick={onClose}
            className="absolute inset-0 bg-ink/40"
          />
          <nav
            aria-label="All destinations"
            className="absolute inset-y-0 left-0 w-[17rem] max-w-[85vw] overflow-y-auto bg-surface px-3 py-4 shadow-xl"
          >
            <div className="px-2 pb-2 text-sm font-semibold text-ink">Go to</div>
            <ul>
              {PRIMARY.map(drawerItem)}
            </ul>

            <div className="mt-4">
              <AdvancedToggle
                open={advancedOpen}
                onToggle={toggleAdvanced}
                controls="drawer-advanced"
              />
              {advancedOpen && <ul id="drawer-advanced" className="fade-enter">{advanced.map(drawerItem)}</ul>}
            </div>
          </nav>
        </div>
      )}

      <nav
        aria-label="Primary"
        className="fixed inset-x-0 bottom-0 z-30 flex h-(--spacing-bottomnav) items-stretch border-t border-line bg-surface md:hidden"
      >
        {BOTTOM.map((href) => {
          const item = PRIMARY.find((i) => i.href === href);
          if (!item) return null;
          const active = section?.href === item.href;
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={`flex flex-1 flex-col items-center justify-center gap-0.5 text-[11px] ${
                active ? "font-semibold text-ink" : "text-ink-muted"
              }`}
            >
              <Icon aria-hidden="true" size={18} />
              {item.short ?? item.label}
            </Link>
          );
        })}
      </nav>
    </>
  );
}
