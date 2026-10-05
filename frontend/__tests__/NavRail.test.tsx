import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

/**
 * The desktop rail's Advanced group: four everyday destinations always, the
 * coordinator's tools behind one arrow - open by itself on their own pages,
 * and remembered otherwise.
 */

const pathname = { value: "/" };
const isAdmin = { value: true };

vi.mock("next/navigation", () => ({ usePathname: () => pathname.value }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ isAdmin: isAdmin.value }) }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const { NavRail } = await import("@/components/shell/NavRail");

beforeEach(() => {
  pathname.value = "/";
  isAdmin.value = true;
  localStorage.clear();
  sessionStorage.clear();
});

function rail() {
  return within(screen.getByRole("navigation", { name: "Main" }));
}

describe("the rail", () => {
  it("shows the four everyday destinations and nothing else while Advanced is closed", () => {
    render(<NavRail />);
    const links = rail().getAllByRole("link").map((a) => a.textContent);
    expect(links).toEqual(["Dashboard", "Generate", "Timetable", "Export"]);
    expect(rail().getByRole("button", { name: "Advanced" }).getAttribute("aria-expanded")).toBe("false");
  });

  it("expands and collapses Advanced, with Validation first inside it", () => {
    render(<NavRail />);
    const toggle = rail().getByRole("button", { name: "Advanced" });
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const group = within(document.getElementById("rail-advanced")!);
    expect(group.getAllByRole("link").map((a) => a.textContent)).toEqual([
      "Validation", "Data", "Master Timetable", "Versions", "Users",
    ]);
    fireEvent.click(toggle);
    expect(document.getElementById("rail-advanced")).toBeNull();
  });

  it("remembers the arrow between pages", () => {
    const { unmount } = render(<NavRail />);
    fireEvent.click(rail().getByRole("button", { name: "Advanced" }));
    unmount();
    pathname.value = "/export";
    render(<NavRail />);
    expect(rail().getByRole("button", { name: "Advanced" }).getAttribute("aria-expanded")).toBe("true");
  });

  it("starts folded, and forgets the arrow at sign-in rather than across visits", async () => {
    const { forgetAdvancedOpen } = await import("@/components/shell/nav");
    const { unmount } = render(<NavRail />);
    fireEvent.click(rail().getByRole("button", { name: "Advanced" }));
    unmount();
    // Remembered for the tab, never for the next visit.
    expect(localStorage.getItem("timetable.nav.advanced-open")).toBeNull();
    forgetAdvancedOpen();
    render(<NavRail />);
    expect(rail().getByRole("button", { name: "Advanced" }).getAttribute("aria-expanded")).toBe("false");
  });

  it("opens on an Advanced page, marks it, and keeps the Data screens one click away", () => {
    pathname.value = "/rooms";
    render(<NavRail />);
    expect(rail().getByRole("button", { name: "Advanced" }).getAttribute("aria-expanded")).toBe("true");
    const current = rail().getByRole("link", { name: "Rooms" });
    expect(current.getAttribute("aria-current")).toBe("page");
    for (const label of ["Faculty", "Subjects", "Sections", "Time slots", "Who teaches what",
                         "Availability", "Free rooms & faculty"]) {
      expect(rail().getByRole("link", { name: label })).toBeTruthy();
    }
    expect(rail().queryByRole("link", { name: /workbook import/i })).toBeNull();
  });

  it("marks Validation, not an everyday item, on the Validation page", () => {
    pathname.value = "/validation";
    render(<NavRail />);
    expect(rail().getByRole("link", { name: "Validation" }).getAttribute("aria-current")).toBe("page");
    expect(rail().getByRole("link", { name: "Dashboard" }).getAttribute("aria-current")).toBeNull();
  });
});
