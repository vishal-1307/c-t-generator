/**
 * The information architecture, defined once.
 *
 * Two groups, because there are two jobs.
 *
 * `PRIMARY` is the everyday one: manage the files on the Dashboard, generate,
 * look at the timetable, export it. Four words, in the order they are done -
 * someone who has never seen the application should be able to follow it from
 * the menu alone.
 *
 * `ADVANCED` is the coordinator's toolbox, folded away behind one arrow: the
 * full list of checks, the screens for correcting one imported value by hand,
 * every class in one filterable list, the version history, user accounts.
 * Nothing there is needed for the main job, so none of it competes with it.
 * No URL moved - every page still answers on the path it always did.
 *
 * Desktop and mobile both read these lists, because two hand-maintained
 * copies of a navigation menu diverge - usually in the direction of the phone
 * quietly missing whatever was added last.
 */

import {
  BookOpen,
  Building2,
  CalendarCheck,
  CalendarDays,
  CalendarPlus,
  Clock3,
  Database,
  FileDown,
  GitBranch,
  GitCompare,
  LayoutDashboard,
  List,
  Search,
  ShieldCheck,
  UserRound,
  Users,
  UsersRound,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  /** One icon per destination, the same on the rail, the drawer and the bar. */
  icon: LucideIcon;
  /** Shown on the phone's bottom bar, which has no room for long words. */
  short?: string;
  /** Only an administrator sees it. */
  adminOnly?: boolean;
  /** Sub-destinations, revealed when the section is current. */
  children?: { href: string; label: string; icon?: LucideIcon }[];
};

export const PRIMARY: NavItem[] = [
  { href: "/", label: "Dashboard", short: "Home", icon: LayoutDashboard },
  { href: "/generate", label: "Generate", icon: CalendarPlus },
  {
    href: "/timetable",
    label: "Timetable",
    short: "Table",
    icon: CalendarDays,
    children: [
      { href: "/timetable", label: "All classes" },
      { href: "/timetables/sections", label: "By section" },
      { href: "/timetables/faculty", label: "By teacher" },
      { href: "/timetables/rooms", label: "By room" },
    ],
  },
  { href: "/export", label: "Export", icon: FileDown },
];

export const ADVANCED: NavItem[] = [
  { href: "/validation", label: "Validation", icon: ShieldCheck },
  {
    href: "/data",
    label: "Data",
    icon: Database,
    adminOnly: true,
    children: [
      { href: "/faculty", label: "Faculty", icon: UserRound },
      { href: "/subjects", label: "Subjects", icon: BookOpen },
      { href: "/sections", label: "Sections", icon: UsersRound },
      { href: "/rooms", label: "Rooms", icon: Building2 },
      { href: "/timeslots", label: "Time slots", icon: Clock3 },
      { href: "/mappings", label: "Who teaches what", icon: GitBranch },
      { href: "/availability/search", label: "Free rooms & faculty", icon: Search },
      { href: "/availability", label: "Availability", icon: CalendarCheck },
    ],
  },
  { href: "/timetables/master", label: "Master Timetable", icon: List, adminOnly: true },
  { href: "/versions", label: "Versions", icon: GitCompare, adminOnly: true },
  { href: "/users", label: "Users", icon: Users, adminOnly: true },
];

/** The Advanced items this reader may see. */
export function advancedFor(isAdmin: boolean): NavItem[] {
  return ADVANCED.filter((item) => isAdmin || !item.adminOnly);
}

/** Every destination the shell knows, including the ones nested under Data. */
function allHrefs(items: NavItem[]): string[] {
  return items.flatMap((item) => [
    item.href,
    ...(item.children?.map((c) => c.href) ?? []),
  ]);
}

// A week grid lives at /timetable/section/5, but it was picked from the
// "By section" list at /timetables/sections - and that is where the reader is.
const GRID_LISTS: [RegExp, string][] = [
  [/^\/timetable\/section\//, "/timetables/sections"],
  [/^\/timetable\/faculty\//, "/timetables/faculty"],
  [/^\/timetable\/room\//, "/timetables/rooms"],
];

/**
 * The current destination is the *longest* entry that prefixes the path, so
 * `/availability/search` highlights itself rather than also lighting up its
 * shorter sibling `/availability`.
 */
export function activeHref(pathname: string, items: NavItem[]): string | null {
  const grid = GRID_LISTS.find(([pattern]) => pattern.test(pathname));
  if (grid && allHrefs(items).includes(grid[1])) return grid[1];
  const candidates = allHrefs(items).filter((href) =>
    href === "/"
      ? pathname === "/"
      : pathname === href || pathname.startsWith(`${href}/`),
  );
  if (candidates.length === 0) return null;
  return candidates.reduce((a, b) => (b.length > a.length ? b : a));
}

/**
 * Which top-level section the path belongs to.
 *
 * Distinct from `activeHref`: on `/rooms` the current destination is `/rooms`,
 * but the section is `Data`, and that is what the rail has to mark so a reader
 * can see where they are in the structure rather than only what they clicked.
 */
export function activeSection(pathname: string, items: NavItem[]): NavItem | null {
  const grid = GRID_LISTS.find(([pattern]) => pattern.test(pathname));
  const path = grid ? grid[1] : pathname;
  let best: NavItem | null = null;
  let bestLength = -1;
  for (const item of items) {
    for (const href of [item.href, ...(item.children?.map((c) => c.href) ?? [])]) {
      const matches =
        href === "/"
          ? path === "/"
          : path === href || path.startsWith(`${href}/`);
      if (matches && href.length > bestLength) {
        best = item;
        bestLength = href.length;
      }
    }
  }
  return best;
}

/** Whether the path is one of the Advanced destinations. */
export function inAdvanced(pathname: string): boolean {
  const section = activeSection(pathname, [...PRIMARY, ...ADVANCED]);
  return section !== null && ADVANCED.includes(section);
}

/**
 * Whether the Advanced group is open.
 *
 * Folded by default so the everyday menu stays four words long, and folded
 * again at every sign-in. It opens by itself while you are on one of its
 * pages - a menu that hides the page you are on leaves you with no idea where
 * you are - and otherwise remembers what you did with the arrow for as long as
 * the tab is open. Only that long: remembering it across visits is what made
 * it look as if it opened by itself.
 */
export const ADVANCED_STORAGE_KEY = "timetable.nav.advanced-open";

export function readAdvancedOpen(): boolean {
  try {
    return sessionStorage.getItem(ADVANCED_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeAdvancedOpen(open: boolean): void {
  try {
    sessionStorage.setItem(ADVANCED_STORAGE_KEY, open ? "1" : "0");
  } catch {
    // Storage disabled: the arrow still works for this page view.
  }
}

/** Fold the group again - on signing in or out. */
export function forgetAdvancedOpen(): void {
  try {
    sessionStorage.removeItem(ADVANCED_STORAGE_KEY);
    // An older build kept it here, across visits.
    localStorage.removeItem(ADVANCED_STORAGE_KEY);
  } catch {
    /* nothing to forget */
  }
}
