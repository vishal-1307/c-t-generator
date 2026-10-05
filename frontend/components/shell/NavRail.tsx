"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { AdvancedToggle, useAdvancedOpen } from "./AdvancedToggle";
import { PRIMARY, activeHref, activeSection, advancedFor, type NavItem } from "./nav";

/**
 * The desktop rail: the four everyday destinations, then the Advanced group
 * folded behind its arrow, each item revealing its contents only while you
 * are in it.
 *
 * Nineteen permanently-visible links meant scanning a list to find the one
 * screen you wanted, and the list was ordered by what the database contains
 * rather than by what anyone does. Nesting the entity screens under `Data`
 * keeps them one click away without making everyone read them on every page.
 */
export function NavRail() {
  const pathname = usePathname();
  const { isAdmin } = useAuth();
  const advanced = advancedFor(isAdmin);
  const items = [...PRIMARY, ...advanced];
  const current = activeHref(pathname, items);
  const section = activeSection(pathname, items);
  const [open, toggle] = useAdvancedOpen(pathname);

  return (
    <nav
      aria-label="Main"
      className="fixed left-0 top-(--spacing-topbar) hidden h-[calc(100dvh-var(--spacing-topbar))] w-56 shrink-0 overflow-y-auto border-r border-line bg-surface px-2 py-3 md:block"
    >
      <ul>
        {PRIMARY.map((item) => (
          <RailItem
            key={item.href}
            item={item}
            current={current}
            expanded={section?.href === item.href}
          />
        ))}
      </ul>

      <div className="mt-5">
        <AdvancedToggle open={open} onToggle={toggle} controls="rail-advanced" />
        {open && (
          <ul id="rail-advanced" className="fade-enter">
            {advanced.map((item) => (
              <RailItem
                key={item.href}
                item={item}
                current={current}
                expanded={section?.href === item.href}
              />
            ))}
          </ul>
        )}
      </div>
    </nav>
  );
}

function RailItem({
  item,
  current,
  expanded,
}: {
  item: NavItem;
  current: string | null;
  expanded: boolean;
}) {
  const active = current === item.href;
  return (
    <li>
      <Link
        href={item.href}
        aria-current={active ? "page" : undefined}
        className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors ${
          active
            ? "bg-accent-surface font-medium text-accent"
            : expanded
              ? "font-medium text-ink hover:bg-surface-hover"
              : "text-ink-soft hover:bg-surface-hover hover:text-ink"
        }`}
      >
        <item.icon aria-hidden="true" size={16} className="shrink-0" />
        {item.label}
      </Link>

      {expanded && item.children && (
        <ul className="mb-1 ml-2 border-l border-line pl-2">
          {item.children.map((child) => {
            const childActive = current === child.href;
            return (
              <li key={child.href}>
                <Link
                  href={child.href}
                  aria-current={childActive ? "page" : undefined}
                  className={`flex items-center gap-2 rounded-md px-2 py-1 text-[13px] transition-colors ${
                    childActive
                      ? "bg-surface-hover font-medium text-ink"
                      : "text-ink-muted hover:bg-surface-hover hover:text-ink"
                  }`}
                >
                  {child.icon && <child.icon aria-hidden="true" size={14} className="shrink-0" />}
                  {child.label}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}
