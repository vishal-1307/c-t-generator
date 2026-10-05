import { describe, expect, it } from "vitest";

import {
  ADVANCED,
  PRIMARY,
  activeHref,
  activeSection,
  advancedFor,
  inAdvanced,
  type NavItem,
} from "@/components/shell/nav";

/**
 * Which navigation entry is highlighted is easy to get subtly wrong and hard
 * to notice: nothing breaks, the reader is just told they are somewhere they
 * are not. Two rules do the work, and both have a failure mode that looks
 * plausible on the screen.
 */

const ALL = [...PRIMARY, ...ADVANCED];

describe("the current destination", () => {
  it("prefers the longest match, so a nested route does not light up its parent", () => {
    // The failure this guards: /availability/search marking BOTH itself and
    // /availability, because both are prefixes of the path.
    expect(activeHref("/availability/search", ALL)).toBe("/availability/search");
    expect(activeHref("/availability", ALL)).toBe("/availability");
  });

  it("matches the dashboard exactly, never as a prefix of everything", () => {
    // "/" prefixes every path in existence, so a careless startsWith marks the
    // dashboard as current on every page in the app.
    expect(activeHref("/", ALL)).toBe("/");
    expect(activeHref("/rooms", ALL)).toBe("/rooms");
  });

  it("marks a detail page as its list page", () => {
    expect(activeHref("/faculty/12", ALL)).toBe("/faculty");
  });

  it("marks a week grid as the list it was picked from", () => {
    expect(activeHref("/timetable/faculty/1", ALL)).toBe("/timetables/faculty");
    expect(activeHref("/timetable/section/5", ALL)).toBe("/timetables/sections");
    expect(activeHref("/timetable/room/9", ALL)).toBe("/timetables/rooms");
  });

  it("returns nothing for a path the shell does not know", () => {
    expect(activeHref("/nowhere", ALL)).toBeNull();
  });
});

describe("the current section", () => {
  it("puts an entity page under Data even though Data is not in its path", () => {
    // The point of the regrouping: /rooms has nothing to do with /data as a
    // string, but that is the section a reader is in.
    expect(activeSection("/rooms", ALL)?.label).toBe("Data");
    expect(activeSection("/availability/search", ALL)?.label).toBe("Data");
  });

  it("puts the timetable views under Timetable", () => {
    expect(activeSection("/timetables/faculty", ALL)?.label).toBe("Timetable");
    expect(activeSection("/timetable", ALL)?.label).toBe("Timetable");
    // A week grid lives under /timetable/... and belongs to the same section.
    expect(activeSection("/timetable/section/5", ALL)?.label).toBe("Timetable");
  });

  it("keeps top-level destinations as their own section", () => {
    expect(activeSection("/generate", ALL)?.label).toBe("Generate");
    expect(activeSection("/export", ALL)?.label).toBe("Export");
    expect(activeSection("/", ALL)?.label).toBe("Dashboard");
  });
});

describe("the structure itself", () => {
  it("gives the everyday job four destinations, in the order they are done", () => {
    // Files on the Dashboard, generate, look, export. If this grows, that is
    // a decision to make deliberately: the point of the product is that the
    // menu alone explains how to use it.
    expect(PRIMARY.map((i) => i.label)).toEqual(["Dashboard", "Generate", "Timetable", "Export"]);
    expect(PRIMARY[0].href).toBe("/");
  });

  it("puts Validation first under Advanced, followed by the administrator's tools", () => {
    expect(ADVANCED.map((i) => i.label)).toEqual([
      "Validation", "Data", "Master Timetable", "Versions", "Users",
    ]);
    expect(PRIMARY.some((i) => i.href === "/validation")).toBe(false);
  });

  it("shows a non-administrator only the Advanced items they can use", () => {
    expect(advancedFor(false).map((i) => i.label)).toEqual(["Validation"]);
    expect(advancedFor(true)).toEqual(ADVANCED);
  });

  it("knows which pages belong to Advanced, so the group opens on them", () => {
    for (const path of ["/validation", "/data", "/rooms", "/availability/search",
                        "/timetables/master", "/versions", "/users", "/faculty/3"]) {
      expect(inAdvanced(path), path).toBe(true);
    }
    for (const path of ["/", "/generate", "/timetable", "/timetables/rooms",
                        "/timetable/section/5", "/export"]) {
      expect(inAdvanced(path), path).toBe(false);
    }
  });

  it("no longer offers the workbook importer, which duplicated the two-file upload", () => {
    const hrefs = ALL.flatMap((item) => [item.href, ...(item.children?.map((c) => c.href) ?? [])]);
    expect(hrefs).not.toContain("/import");
  });

  it("keeps the repair screens out of the teacher's menu", () => {
    const teacher = new Set(
      PRIMARY.flatMap((item) => [item.href, ...(item.children?.map((c) => c.href) ?? [])]),
    );
    for (const href of ["/data", "/faculty", "/rooms", "/mappings", "/versions", "/users",
                        "/timetables/master"]) {
      expect(teacher.has(href)).toBe(false);
    }
  });

  it("still reaches every screen the old nineteen-item menu did", () => {
    const reachable = new Set(
      ALL.flatMap((item: NavItem) => [
        item.href,
        ...(item.children?.map((c) => c.href) ?? []),
      ]),
    );
    // Exactly the destinations the previous sidebar listed, less the removed
    // workbook importer. Reorganising the menu must not quietly strand a page.
    for (const href of [
      "/",
      "/faculty",
      "/subjects",
      "/sections",
      "/rooms",
      "/timeslots",
      "/mappings",
      "/availability",
      "/availability/search",
      "/validation",
      "/generate",
      "/versions",
      "/timetables/master",
      "/timetables/sections",
      "/timetables/faculty",
      "/timetables/rooms",
      "/export",
      "/users",
    ]) {
      expect(reachable).toContain(href);
    }
  });
});

describe("the icons", () => {
  it("gives every destination one, so the rail, drawer and bar can all show it", () => {
    for (const item of ALL) {
      expect(typeof item.icon, item.label).not.toBe("undefined");
    }
    for (const child of ADVANCED.find((i) => i.label === "Data")?.children ?? []) {
      expect(typeof child.icon, child.label).not.toBe("undefined");
    }
  });
});
