import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * A download that fails keeps the reader where they were and says why - it
 * does not send the tab to a page of JSON.
 */

const fetchFile = vi.fn();
const notify = vi.fn();

class FakeApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

vi.mock("@/lib/api", () => ({
  fetchFile: (...a: unknown[]) => fetchFile(...a),
  ApiError: FakeApiError,
}));
vi.mock("@/lib/notices", () => ({ useNotices: () => ({ notify, dismiss: vi.fn() }) }));

const { DownloadLink } = await import("@/components/DownloadLink");

beforeEach(() => {
  fetchFile.mockReset();
  notify.mockReset();
});

describe("a download link", () => {
  it("is still a real link", () => {
    render(<DownloadLink href="http://api/export/x.xlsx">Export Excel</DownloadLink>);
    expect(screen.getByRole("link", { name: "Export Excel" }).getAttribute("href")).toBe(
      "http://api/export/x.xlsx",
    );
  });

  it("says why a file could not be made, and does not navigate", async () => {
    fetchFile.mockRejectedValue(new FakeApiError("That timetable version no longer exists.", 404));
    render(<DownloadLink href="http://api/export/run/9.xlsx">Workbook</DownloadLink>);
    const link = screen.getByRole("link", { name: "Workbook" });
    const click = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    link.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    await waitFor(() => expect(notify).toHaveBeenCalledTimes(1));
    expect(notify.mock.calls[0][0]).toMatchObject({
      tone: "error",
      title: "Could not download the file",
      detail: "That timetable version no longer exists.",
    });
  });

  it("leaves connection trouble to the notice the client already raised", async () => {
    fetchFile.mockRejectedValue(new FakeApiError("starting up", 503));
    render(<DownloadLink href="http://api/export/x.xlsx">Excel</DownloadLink>);
    fireEvent.click(screen.getByRole("link", { name: "Excel" }));
    await waitFor(() => expect(fetchFile).toHaveBeenCalled());
    expect(notify).not.toHaveBeenCalled();
  });

  it("saves the file under the name the server gave it", async () => {
    const created: HTMLAnchorElement[] = [];
    const original = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = original(tag);
      if (tag === "a") {
        created.push(el as HTMLAnchorElement);
        vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(() => {});
      }
      return el;
    });
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    fetchFile.mockResolvedValue({ blob: new Blob(["x"]), filename: "timetable_Upload-1_v2.xlsx" });

    render(<DownloadLink href="http://api/export/allocation.xlsx">Excel</DownloadLink>);
    fireEvent.click(screen.getByRole("link", { name: "Excel" }));
    await waitFor(() => expect(created.some((a) => a.download === "timetable_Upload-1_v2.xlsx")).toBe(true));
    expect(notify).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });
});
