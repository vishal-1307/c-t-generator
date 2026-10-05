import { describe, expect, it } from "vitest";
import { MESSAGES, describeFailure, wasInterrupted } from "@/lib/failures";
import { actionFailureFrom, humanReason } from "@/lib/dashboardState";
import type { Run } from "@/lib/types";

/**
 * One table for what a failed request means. The rule it exists for: a server
 * that is asleep or restarting is said as exactly that, everywhere - never as
 * "the server had a problem", never as a wrong password, and never with the
 * API's address in it.
 */

describe("describing a failed request", () => {
  it.each([0, 502, 503, 504])("reads %i as the server starting up", (status) => {
    const failure = describeFailure(status, "Bad Gateway");
    expect(failure.kind).toBe("unreachable");
    expect(failure.message).toBe(MESSAGES.unreachable);
    expect(failure.message).not.toMatch(/http|api|backend/i);
  });

  it("reads 401 as an expired sign-in", () => {
    expect(describeFailure(401, "Not authenticated")).toEqual({
      kind: "session",
      message: MESSAGES.session,
    });
  });

  it("reads a 409 about generation as busy, and any other 409 as the server's reason", () => {
    expect(describeFailure(409, "A timetable is already being generated.").kind).toBe("busy");
    expect(describeFailure(409, "version 1 is published and cannot be changed")).toEqual({
      kind: "request",
      message: "version 1 is published and cannot be changed",
    });
  });

  it("keeps the server's own reason for a refusal, and a sentence for a failure", () => {
    expect(describeFailure(422, "Only .csv, .xlsx files are accepted").message).toBe(
      "Only .csv, .xlsx files are accepted",
    );
    expect(describeFailure(500, "Traceback ...")).toEqual({ kind: "server", message: MESSAGES.server });
  });
});

describe("a run the server abandoned", () => {
  const interrupted = {
    id: 1,
    status: "INFEASIBLE",
    message: "This run did not finish - the server restarted while it was solving, most likely...",
  } as unknown as Run;

  it("is recognised from its message", () => {
    expect(wasInterrupted(interrupted.message)).toBe(true);
    expect(wasInterrupted("Section 2401 has too many classes.")).toBe(false);
    expect(wasInterrupted(null)).toBe(false);
  });

  it("is not described as a problem with the data", () => {
    const said = humanReason(interrupted);
    expect(said).toMatch(/server restarted/);
    expect(said).toMatch(/not a problem with the files/);
    expect(said).not.toMatch(/No timetable can satisfy/);
  });
});

describe("the dashboard's failed action", () => {
  it("tells someone a waking server is waking, not failing", () => {
    for (const status of [0, 502, 503]) {
      const f = actionFailureFrom("generate", { status }, false);
      expect(f?.message).toBe(MESSAGES.unreachable);
      expect(f?.recovery).toBe("retry");
    }
    expect(actionFailureFrom("generate", { status: 500 }, false)?.message).toBe(MESSAGES.server);
  });
});
