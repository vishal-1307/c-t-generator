import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

/**
 * Signing in against a server that may be asleep.
 *
 * The rule: a server that does not answer is never reported as a wrong
 * password, and a slow answer says it is slow rather than looking frozen.
 */

const login = vi.fn();
const health = vi.fn();
const replace = vi.fn();

class FakeApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

vi.mock("@/lib/api", () => ({
  api: { health: (...a: unknown[]) => health(...a) },
  ApiError: FakeApiError,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ login, user: null }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const LoginPage = (await import("@/app/login/page")).default;

const flush = async (n = 4) => {
  for (let i = 0; i < n; i++) await vi.advanceTimersByTimeAsync(0);
};

function signIn() {
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret" } });
  fireEvent.click(screen.getByRole("button", { name: /Sign in/ }));
}

beforeEach(() => {
  for (const f of [login, health, replace]) f.mockReset();
  health.mockResolvedValue({ status: "ok" });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("signing in", () => {
  it("wakes the server once as the page opens", async () => {
    render(<LoginPage />);
    await flush();
    expect(health).toHaveBeenCalledTimes(1);
  });

  it("says it is connecting when the server is slow, instead of looking frozen", async () => {
    login.mockReturnValue(new Promise(() => {})); // never answers
    render(<LoginPage />);
    signIn();
    await flush();
    expect(screen.getByRole("button", { name: /Signing in/ })).toBeTruthy();

    await vi.advanceTimersByTimeAsync(3000);
    await flush();
    expect(screen.getByRole("button", { name: /Connecting to server/ })).toBeTruthy();
    expect(screen.getByText(/The server is waking up/)).toBeTruthy();
  });

  it("does not call an unreachable server a wrong password, and offers to try again", async () => {
    login.mockRejectedValueOnce(new FakeApiError("Cannot reach the API", 0));
    render(<LoginPage />);
    signIn();
    await flush();
    expect(screen.getByText("Couldn't reach the server.")).toBeTruthy();
    expect(screen.getByText(/were not rejected/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();

    login.mockResolvedValueOnce(undefined);
    fireEvent.click(screen.getByRole("button", { name: /Try again/ }));
    await flush();
    expect(login).toHaveBeenCalledTimes(2);
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("does say so when the password really is wrong", async () => {
    login.mockRejectedValue(new FakeApiError("Incorrect username or password", 401));
    render(<LoginPage />);
    signIn();
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("Incorrect username or password");
    expect(screen.queryByText("Couldn't reach the server.")).toBeNull();
  });
});

describe("the password field", () => {
  it("can be shown and hidden again", async () => {
    render(<LoginPage />);
    const field = screen.getByLabelText("Password") as HTMLInputElement;
    expect(field.type).toBe("password");
    const toggle = screen.getByRole("button", { name: "Show password" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(toggle);
    expect(field.type).toBe("text");
    const hide = screen.getByRole("button", { name: "Hide password" });
    expect(hide.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(hide);
    expect(field.type).toBe("password");
  });
});

describe("a server still starting", () => {
  it.each([502, 503, 504])("reads %i as starting up, not as a wrong password", async (status) => {
    login.mockRejectedValue(new FakeApiError("Bad Gateway", status));
    render(<LoginPage />);
    signIn();
    await flush();
    expect(screen.getByText("Couldn't reach the server.")).toBeTruthy();
  });
});
