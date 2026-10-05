import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Failure } from "@/lib/failures";

/**
 * The notice panel: the one place connection, server and session trouble is
 * said, whichever page it happened on.
 */

const listeners = new Set<(f: Failure) => void>();

vi.mock("@/lib/api", () => ({
  onApiTrouble: (listener: (f: Failure) => void) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const { INFO_MS, NoticesProvider, useNotices } = await import("@/lib/notices");

function trouble(failure: Failure) {
  act(() => {
    for (const listener of listeners) listener(failure);
  });
}

function Raise({ tone }: { tone: "error" | "info" }) {
  const { notify } = useNotices();
  return (
    <button type="button" onClick={() => notify({ tone, title: `A ${tone} notice` })}>
      raise {tone}
    </button>
  );
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  listeners.clear();
});

describe("notices", () => {
  it("say a sleeping server once, however many requests failed, until dismissed", () => {
    render(
      <NoticesProvider>
        <p>page</p>
      </NoticesProvider>,
    );
    for (let i = 0; i < 5; i++) {
      trouble({ kind: "unreachable", message: "The server is starting up or restarting." });
    }
    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(1);
    expect(alerts[0].textContent).toContain("Can't reach the server");

    act(() => {
      vi.advanceTimersByTime(INFO_MS * 5);
    });
    expect(screen.getAllByRole("alert")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("offers a way back in when the sign-in expired", () => {
    render(
      <NoticesProvider>
        <p>page</p>
      </NoticesProvider>,
    );
    trouble({ kind: "session", message: "Your sign-in has expired." });
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/login");
  });

  it("leaves refusals to the page that asked", () => {
    render(
      <NoticesProvider>
        <p>page</p>
      </NoticesProvider>,
    );
    trouble({ kind: "request", message: "Only .xlsx files are accepted" });
    trouble({ kind: "busy", message: "Already generating" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("lets information go by itself, and announces it politely", () => {
    render(
      <NoticesProvider>
        <Raise tone="info" />
      </NoticesProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "raise info" }));
    expect(screen.getByRole("status").textContent).toContain("A info notice");
    act(() => {
      vi.advanceTimersByTime(INFO_MS + 10);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("does nothing, rather than throwing, outside the provider", () => {
    render(<Raise tone="error" />);
    fireEvent.click(screen.getByRole("button", { name: "raise error" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
