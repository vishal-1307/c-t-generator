import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * The property that matters about this table is not how it looks but where the
 * work happens.
 *
 * Every entity screen used to download its whole table and filter in the
 * browser. The failure that caused was quiet rather than loud: the search box
 * searched only what had already arrived, so typing a subject code that lived
 * further down the table reported, truthfully and uselessly, that nothing
 * matched. These assert that searching, sorting and paging are all requests.
 */

const params = new URLSearchParams();
const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/subjects",
  useSearchParams: () => params,
}));

vi.mock("@/lib/useApi", async () => {
  const react = await import("react");
  return {
    // A deliberately trivial stand-in: the real hook is swr, and what these
    // tests are about is the query this component asks for, not caching.
    useApi: (key: unknown, fetcher: () => Promise<unknown>) => {
      const [data, setData] = react.useState<unknown>(undefined);
      react.useEffect(() => {
        let live = true;
        void fetcher().then((d) => {
          if (live) setData(d);
        });
        return () => {
          live = false;
        };
      }, [JSON.stringify(key)]);
      return { data, error: null, loading: data === undefined, refresh: vi.fn() };
    },
    useMutation: () => ({ run: vi.fn(), error: null, setError: vi.fn(), busy: false }),
  };
});

const { ServerTable } = await import("@/components/data/ServerTable");

interface Row {
  id: number;
  code: string;
}

beforeEach(() => {
  replace.mockClear();
  for (const key of [...params.keys()]) params.delete(key);
});

function renderTable(fetcher: (p: Record<string, unknown>) => Promise<unknown>) {
  return render(
    <ServerTable<Row>
      cacheKey="subjects"
      fetcher={fetcher as never}
      empty="nothing here"
      columns={[
        { header: "Code", sortField: "code", cell: (r) => r.code },
        { header: "Id", cell: (r) => r.id },
      ]}
    />,
  );
}

describe("where the work happens", () => {
  it("asks the server for a page rather than the whole table", async () => {
    const fetcher = vi.fn().mockResolvedValue({ total: 3000, rows: [{ id: 1, code: "A" }] });
    renderTable(fetcher);

    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    const sent = fetcher.mock.calls[0][0];
    expect(sent.limit).toBeGreaterThan(0);
    expect(sent.offset).toBe(0);
  });

  it("reports the server's total, not the number of rows it received", async () => {
    const fetcher = vi.fn().mockResolvedValue({ total: 3000, rows: [{ id: 1, code: "A" }] });
    renderTable(fetcher);
    // A count taken from the page would say "1 of 1" while 2,999 rows waited
    // out of sight, which is how somebody concludes a record is missing.
    await waitFor(() => expect(screen.getByText(/of 3000/)).toBeTruthy());
  });

  it("sends the search term to the server", async () => {
    const fetcher = vi.fn().mockResolvedValue({ total: 0, rows: [] });
    renderTable(fetcher);
    await waitFor(() => expect(fetcher).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: "SUB0299" },
    });

    // Debounced, then written to the URL - which is what re-runs the query.
    await waitFor(
      () => {
        expect(replace).toHaveBeenCalled();
        expect(String(replace.mock.calls.at(-1)?.[0])).toContain("q=SUB0299");
      },
      { timeout: 2000 },
    );
  });

  it("puts the sort in the URL so a sorted view can be shared", async () => {
    const fetcher = vi.fn().mockResolvedValue({ total: 2, rows: [{ id: 1, code: "A" }] });
    renderTable(fetcher);
    await waitFor(() => expect(screen.getByText("A")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: /Code/ }));
    await waitFor(() => {
      expect(String(replace.mock.calls.at(-1)?.[0])).toContain("sort=code");
    });
  });

  it("does not offer a sort on a column the API cannot sort by", async () => {
    const fetcher = vi.fn().mockResolvedValue({ total: 1, rows: [{ id: 1, code: "A" }] });
    renderTable(fetcher);
    await waitFor(() => expect(screen.getByText("A")).toBeTruthy());
    // "Id" has no sortField: the server whitelists sort columns and refuses
    // unknown ones, so offering a control that cannot work would be a lie.
    expect(screen.queryByRole("button", { name: /Id/ })).toBeNull();
  });
});
