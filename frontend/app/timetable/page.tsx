"use client";

import { Building2, GraduationCap, UserRound } from "lucide-react";
import Link from "next/link";
import { AllocationTable } from "@/components/AllocationTable";
import { PageHeader } from "@/components/ui";

/**
 * The timetable, and the four ways of looking at it.
 *
 * The list of every class is the page rather than a destination behind a menu,
 * because it is what a teacher came for; the week grids are one click from it.
 * The master list of every period is an administrator's tool and lives under
 * Advanced, not here.
 * Open to anyone - reading a timetable needs no account.
 */
const VIEWS = [
  {
    href: "/timetables/sections",
    label: "By section",
    hint: "One section's week",
    icon: GraduationCap,
  },
  { href: "/timetables/faculty", label: "By teacher", hint: "One teacher's week", icon: UserRound },
  { href: "/timetables/rooms", label: "By room", hint: "One room's week", icon: Building2 },
];

export default function TimetablePage() {

  return (
    <div className="space-y-6">
      <PageHeader
        title="Generated Timetable"
        description="Faculty, section, time and classroom allocation."
      />

      <nav
        aria-label="Week views"
        className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3"
      >
        {VIEWS.map(({ href, label, hint, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className="flex items-center gap-3 rounded-md border border-line bg-surface px-3 py-2.5 transition-colors hover:border-line-strong hover:bg-surface-sunken"
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-surface-hover text-ink-muted">
              <Icon aria-hidden="true" size={16} />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-medium text-ink">{label}</span>
              <span className="block truncate text-xs text-ink-muted">{hint}</span>
            </span>
          </Link>
        ))}
      </nav>

      <AllocationTable />
    </div>
  );
}
