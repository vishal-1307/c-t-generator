/**
 * What a failed request means, in words a person can act on.
 *
 * One table, used by the API client for every request, so a sleeping or
 * restarting server reads the same on every page - and never as "Is the
 * backend running?" with the API's address in it, which is a message for
 * whoever built the thing, not for whoever is using it.
 */

export type FailureKind =
  /** No answer, or the host answered for a server that is not up yet. */
  | "unreachable"
  /** The sign-in is no longer accepted. */
  | "session"
  /** A timetable is already being generated. */
  | "busy"
  /** The server answered, but could not do it. */
  | "server"
  /** The request itself was refused; the server's reason is the message. */
  | "request";

export type Failure = { kind: FailureKind; message: string };

/** Statuses a hosting platform returns while the application is starting. */
const WAKING = new Set([0, 502, 503, 504]);

export const MESSAGES = {
  unreachable:
    "The server is starting up or restarting. Try again in a moment - on first use this can take up to a minute.",
  session: "Your sign-in has expired. Sign in again to continue.",
  busy: "A timetable is already being generated. Wait for it to finish, then try again.",
  server: "The server had a problem completing that. Try again.",
} as const;

export function describeFailure(
  status: number,
  detail?: string | null,
): Failure {
  if (WAKING.has(status))
    return { kind: "unreachable", message: MESSAGES.unreachable };
  if (status === 401) return { kind: "session", message: MESSAGES.session };
  if (status === 409 && /being generated/i.test(detail ?? "")) {
    return { kind: "busy", message: MESSAGES.busy };
  }
  if (status >= 500) return { kind: "server", message: MESSAGES.server };
  return {
    kind: "request",
    message: detail?.trim() || "That did not work. Try again.",
  };
}

/**
 * A timetable run that ended because the server restarted mid-solve.
 *
 * The backend marks such a run INFEASIBLE (it has no timetable) and says why in
 * its message. That status alone reads as "the data cannot produce a
 * timetable", which is the opposite of the truth, so callers check this first.
 */
export function wasInterrupted(message: string | null | undefined): boolean {
  return /server restarted/i.test(message ?? "");
}
