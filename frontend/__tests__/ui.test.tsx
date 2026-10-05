import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Alert, Button, DataTable, Metric, StatusBadge } from "@/components/ui";

/**
 * The contracts other screens depend on without knowing it.
 *
 * The first one is the whole point of the redesign: only a blocker interrupts.
 * If a passing server error ever starts announcing itself as an alert again,
 * it fails here rather than on a teacher's screen.
 */
describe("Alert", () => {
  it("interrupts for a blocker and waits its turn for everything else", () => {
    const { rerender } = render(<Alert tone="blocker" title="Wrong" />);
    expect(screen.getByRole("alert")).toBeTruthy();

    for (const tone of ["caution", "info", "ok"] as const) {
      rerender(<Alert tone={tone} title="Note" />);
      expect(screen.getByRole("status")).toBeTruthy();
      expect(screen.queryByRole("alert")).toBeNull();
    }
  });

  it("can be told to stay quiet", () => {
    render(<Alert tone="blocker" title="Wrong" live="off" />);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("Metric", () => {
  it("answers to its own label, so a figure can be asked for by name", () => {
    render(<Metric label="Hard Conflicts" value={0} />);
    const group = screen.getByRole("group", { name: "Hard Conflicts" });
    expect(group.textContent).toContain("0");
  });
});

describe("StatusBadge", () => {
  it("says its state in words, not only in colour", () => {
    render(<StatusBadge tone="blocker">Needs fixing</StatusBadge>);
    expect(screen.getByText("Needs fixing")).toBeTruthy();
  });
});

describe("Button", () => {
  it("has a touch-sized default and a bigger one for a primary action", () => {
    render(
      <>
        <Button>Ordinary</Button>
        <Button size="lg">Primary</Button>
      </>,
    );
    expect(screen.getByRole("button", { name: "Ordinary" }).className).toContain("h-9");
    expect(screen.getByRole("button", { name: "Primary" }).className).toContain("h-11");
  });
});

describe("DataTable", () => {
  const rows = [{ id: 1, code: "ECE181", room: "36-301" }];

  it("keeps a wide table's scrolling inside the table", () => {
    const { container } = render(
      <DataTable
        rows={rows}
        columns={[
          { header: "Code", cell: (r) => r.code, sticky: true },
          { header: "Room", cell: (r) => r.room },
        ]}
        empty="nothing"
        minWidth="64rem"
      />,
    );
    const region = container.querySelector("[data-scroll-region]");
    expect(region?.className).toContain("overflow-x-auto");
    // jsdom has no layout engine, so this is the class, not the pixels: what
    // it catches is a column reorder that leaves the sticky class stranded on
    // a cell in the middle of the table.
    expect(screen.getAllByRole("columnheader")[0].className).toContain("sticky left-0");
    expect(screen.getAllByRole("cell")[0].className).toContain("sticky left-0");
  });
});
