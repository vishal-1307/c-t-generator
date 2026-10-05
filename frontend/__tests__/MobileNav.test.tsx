import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

/**
 * The phone shell, which the app did not have at all before: a phone rendered
 * the fixed desktop sidebar and got roughly seventy pixels of page.
 *
 * These assert behaviour rather than appearance - whether it looks right at
 * 390px is a question for a browser, and the window in this environment
 * refuses to resize that small. What can be pinned here is the part that
 * silently rots: that the drawer lists every destination the desktop rail
 * does, and that it closes itself when you actually go somewhere.
 */

const pathname = { value: "/" };
const isAdmin = { value: false };

vi.mock("next/navigation", () => ({
  usePathname: () => pathname.value,
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ isAdmin: isAdmin.value }),
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const { MobileNav } = await import("@/components/shell/MobileNav");
const { PRIMARY } = await import("@/components/shell/nav");

beforeEach(() => {
  pathname.value = "/";
  isAdmin.value = false;
  localStorage.clear();
  sessionStorage.clear();
});

describe("the drawer", () => {
  it("offers every primary destination, so nothing is desktop-only", () => {
    render(<MobileNav open onClose={() => {}} />);
    for (const item of PRIMARY) {
      // The bottom bar shows four of these; the drawer must hold them all, or
      // a phone simply cannot reach the rest of the application.
      expect(
        screen.getAllByRole("link", { name: item.label }).length,
      ).toBeGreaterThan(0);
    }
  });

  it("reveals the current section's contents", () => {
    // The data screens are an administrator's repair path.
    isAdmin.value = true;
    pathname.value = "/rooms";
    render(<MobileNav open onClose={() => {}} />);
    expect(screen.getByRole("link", { name: "Rooms" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Subjects" })).toBeTruthy();
  });

  it("keeps admin destinations out of a non-admin's menu", () => {
    const { unmount } = render(<MobileNav open onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
    expect(screen.getByRole("link", { name: "Validation" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Users" })).toBeNull();
    unmount();
    sessionStorage.clear();

    isAdmin.value = true;
    render(<MobileNav open onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
    expect(screen.getAllByRole("link", { name: "Users" }).length).toBeGreaterThan(0);
  });

  it("folds Advanced behind an arrow, closed on an everyday page", () => {
    isAdmin.value = true;
    render(<MobileNav open onClose={() => {}} />);
    const toggle = screen.getByRole("button", { name: "Advanced" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("link", { name: "Validation" })).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    for (const label of ["Validation", "Data", "Master Timetable", "Versions", "Users"]) {
      expect(screen.getByRole("link", { name: label })).toBeTruthy();
    }

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("link", { name: "Master Timetable" })).toBeNull();
  });

  it("closes when the route changes, since arriving somewhere is the point", () => {
    const onClose = vi.fn();
    render(<MobileNav open onClose={onClose} />);
    expect(onClose).toHaveBeenCalled();
  });

  it("is not in the document at all when closed", () => {
    render(<MobileNav open={false} onClose={() => {}} />);
    expect(screen.queryByLabelText("Close navigation")).toBeNull();
    // The bottom bar, however, is always there - it is the navigation, not a
    // menu, and a phone with no visible way out of a page is a dead end.
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeTruthy();
  });
});

describe("the bottom bar", () => {
  it("marks the section you are in, not only the exact page", () => {
    pathname.value = "/timetables/faculty";
    render(<MobileNav open={false} onClose={() => {}} />);
    const bar = screen.getByRole("navigation", { name: "Primary" });
    const current = bar.querySelector('[aria-current="page"]');
    // The faculty view lives under Timetable, and that is what a reader
    // needs marked.
    expect(current?.textContent).toContain("Table");
  });
});
