/**
 * What the dashboard is doing, as data.
 *
 * The page used to decide this inline, and the decisions drifted apart: a
 * server refusing a second solve ("a timetable is already being generated")
 * was painted in the same red as "these files are wrong", above a green
 * "Data Ready" and an enabled Generate button. Two different things - what
 * the *files* say, and what the last *request* did - were one `error` string.
 *
 * So they are two types here, and the split is the point:
 *
 *   Verdict        what the files add up to. Only this may be red, and when
 *                  it is blocked there is no Generate button to enable.
 *   ActionFailure  the last request came back badly. Never red, never a
 *                  verdict, and cannot arise unless something was pressed.
 *
 * No React in this file, so the machine can be tested without rendering it.
 */
import { MESSAGES, wasInterrupted } from "./failures";
import type {
  Run,
  RunCheck,
  TeacherImportPreview,
  TeacherImportSummary,
  Unassignable,
} from "./types";

/** A row the backend could not read, located well enough to go and fix. */
export type Located = { where: string; what: string };

export type Issues = {
  rows: Located[];
  blockers: Located[];
  unassignable: Unassignable[];
};

export type Verdict =
  | { kind: "none" }
  | {
      kind: "ready";
      summary: TeacherImportSummary;
      /** True when the import found nothing to change - the dataset it would
       *  have written already exists, so generating uses that one. */
      unchanged: boolean;
    }
  | { kind: "blocked"; summary: TeacherImportSummary | null; issues: Issues };

export type FileVerdict = { ok: boolean; detail: string };

export type StageId = "validate" | "import" | "solve" | "verify" | "save";
export type StageStatus = "pending" | "active" | "done" | "failed";
export type Stage = { id: StageId; label: string; status: StageStatus };

export type Phase =
  | { kind: "empty" }
  | { kind: "partial"; have: "load" | "infra" }
  | { kind: "chosen" }
  | { kind: "analyzing" }
  | { kind: "verdict" }
  | { kind: "generating" }
  | { kind: "succeeded" }
  | { kind: "failed" };

/**
 * The last thing that was pressed did not work.
 *
 * `recovery` says what the reader can do about it, and "wait" is the one that
 * matters: a 409 means a solve is already running, so offering Generate again
 * only earns a second 409.
 */
export type ActionFailure = {
  action: "analyze" | "generate";
  message: string;
  recovery: "retry" | "wait" | "signin";
};

export const TERMINAL = ["OPTIMAL", "FEASIBLE", "PARTIAL", "INFEASIBLE", "TIMEOUT"];
export const SUCCESS = ["OPTIMAL", "FEASIBLE"];

/** First sentence of a warning; the rest is detail, a hover away. */
export function headline(text: string): string {
  const cut = text.indexOf(". ");
  return cut > 0 ? text.slice(0, cut + 1) : text;
}

/**
 * What the files add up to.
 *
 * `ready` is deliberately hard to earn: every way the backend has of saying
 * "something is wrong" has to be silent at once. `has_errors` covers invalid
 * *and* duplicate rows, `blockers` covers a sheet rejected before it produced
 * an outcome at all, and `readiness.ready` covers everything the solver needs
 * that the files themselves cannot show.
 */
export function deriveVerdict(
  preview: TeacherImportPreview | null,
  fileProblems: Located[],
): Verdict {
  if (preview === null) {
    return fileProblems.length > 0
      ? { kind: "blocked", summary: null, issues: { rows: fileProblems, blockers: [], unassignable: [] } }
      : { kind: "none" };
  }

  const unassignable = preview.readiness?.unassignable ?? [];
  const rows: Located[] = [
    ...fileProblems,
    ...preview.outcomes.flatMap((o) =>
      o.problems.map((p) => ({ where: `${p.sheet}, row ${p.row_number}`, what: p.message })),
    ),
  ];
  const blockers: Located[] = [
    // A whole sheet refused before it could report a row - a missing required
    // column. Nothing else mentions it, so without this the page would show a
    // summary, a green tick and no explanation at all.
    ...preview.blockers.map((b) => ({ where: "The file could not be read", what: b })),
    ...(preview.readiness?.blockers ?? [])
      // A class with no room at all gets its own panel, with its reason.
      .filter((b) => !(unassignable.length > 0 && b.label === "Every section-subject has an eligible room"))
      .map((b) => ({ where: b.label, what: b.detail })),
  ];

  const ready =
    !preview.has_errors &&
    preview.readiness?.ready === true &&
    rows.length === 0 &&
    blockers.length === 0 &&
    unassignable.length === 0;

  if (!ready) return { kind: "blocked", summary: preview.summary ?? null, issues: { rows, blockers, unassignable } };
  return { kind: "ready", summary: preview.summary, unchanged: !preview.can_apply };
}

/**
 * Whether one file was read cleanly.
 *
 * Counts duplicates as well as invalid rows: two identical rooms are a
 * contradiction the file has to answer for, and a green tick over the top of
 * one is the interface lying about the thing it was asked to check.
 */
export function fileVerdict(
  preview: TeacherImportPreview | null,
  which: "load" | "infra",
): FileVerdict {
  if (!preview) return { ok: true, detail: "" };
  // A sheet rejected before it produced an outcome names itself first
  // ("Rooms: missing required column(s) capacity."). Only the file that sheet
  // came from is at fault; blaming both would send the teacher to reread a
  // file that is fine.
  const rejected = preview.blockers.filter((b) => {
    const fromInfra = b.trimStart().toLowerCase().startsWith("rooms");
    return which === "infra" ? fromInfra : !fromInfra;
  });
  if (rejected.length > 0) return { ok: false, detail: "The file could not be read" };

  const mine = preview.outcomes.filter((o) =>
    which === "infra" ? o.entity === "rooms" : o.entity !== "rooms",
  );
  const invalid = mine.reduce((n, o) => n + o.invalid, 0);
  const duplicates = mine.reduce((n, o) => n + o.duplicates, 0);
  if (invalid > 0) return { ok: false, detail: "Some rows could not be read" };
  if (duplicates > 0) {
    return {
      ok: false,
      detail: `${duplicates} row${duplicates > 1 ? "s are" : " is"} repeated`,
    };
  }
  return { ok: true, detail: "Parsed successfully" };
}

export function derivePhase(p: {
  load: File | null;
  /** The rooms are saved: the infrastructure is reused, not chosen each time. */
  infraReady: boolean;
  busy: "analyze" | "generate" | null;
  verdict: Verdict;
  run: Run | null;
}): Phase {
  if (p.run !== null && TERMINAL.includes(p.run.status) && p.busy === null) {
    return SUCCESS.includes(p.run.status) ? { kind: "succeeded" } : { kind: "failed" };
  }
  if (p.busy === "generate" || (p.run !== null && !TERMINAL.includes(p.run.status))) {
    return { kind: "generating" };
  }
  if (p.busy === "analyze") return { kind: "analyzing" };
  if (p.verdict.kind !== "none") return { kind: "verdict" };
  if (p.load && p.infraReady) return { kind: "chosen" };
  if (p.load) return { kind: "partial", have: "load" };
  if (p.infraReady) return { kind: "partial", have: "infra" };
  return { kind: "empty" };
}

const STAGE_LABELS: Record<StageId, string> = {
  validate: "Data validated",
  import: "Building schedule",
  solve: "Generating timetable",
  verify: "Verifying result",
  save: "Saving",
};

/**
 * The steps of a generation, each one a real event.
 *
 * Nothing here is timed or guessed: a step is done when a request came back.
 * A percentage would have to be invented, and an invented percentage is a
 * progress bar that lies politely.
 */
export function stagesFor(p: {
  applied: boolean;
  runId: number | null;
  status: string | null;
  checked: boolean;
  saved: boolean;
}): Stage[] {
  const done = (s: StageId): StageStatus => {
    switch (s) {
      case "validate":
        return p.applied ? "done" : "active";
      case "import":
        return p.runId !== null ? "done" : p.applied ? "active" : "pending";
      case "solve":
        if (p.status !== null && TERMINAL.includes(p.status)) {
          return SUCCESS.includes(p.status) ? "done" : "failed";
        }
        return p.runId !== null ? "active" : "pending";
      case "verify":
        if (p.checked) return "done";
        return p.status !== null && SUCCESS.includes(p.status) ? "active" : "pending";
      case "save":
        if (p.saved) return "done";
        return p.checked ? "active" : "pending";
    }
  };
  return (["validate", "import", "solve", "verify", "save"] as StageId[]).map((id) => ({
    id,
    label: STAGE_LABELS[id],
    status: done(id),
  }));
}

/**
 * How long to keep waiting on a run before saying it is taking longer than
 * it should. Generous: the server's own time limit is three minutes on the
 * free tier, and a sleeping server can add one more before it starts.
 */
export const POLL_CEILING_MS = 8 * 60 * 1000;

/** Why a finished run has no timetable in it, in a sentence. */
export function humanReason(run: Run): string {
  if (wasInterrupted(run.message)) {
    return (
      "The server restarted while the timetable was being generated, so it did not finish. " +
      "This is not a problem with the files - generate again."
    );
  }
  const opening =
    run.status === "INFEASIBLE"
      ? "No timetable can satisfy every rule in these files at once."
      : run.status === "TIMEOUT"
        ? "The search ran out of time before a complete timetable was found."
        : "Only part of the timetable could be built.";
  // The server's own message for a timeout already says it ran out of time.
  if (run.status === "TIMEOUT" && run.message) return run.message;
  return run.message ? `${opening} ${run.message}` : opening;
}

/** Classes and periods, from whichever of the two knows. */
export function resultCounts(
  run: Run,
  summary: TeacherImportSummary | null,
  check: RunCheck | null,
): { classes: number; periods: number; conflicts: number | null } {
  return {
    classes: summary?.classes ?? run.assignment_count,
    periods: run.assignment_count,
    conflicts: check ? check.count : null,
  };
}

/**
 * A failed request, described for a person.
 *
 * Returns null for the 422 that carries row problems: that is the files being
 * wrong, which is a verdict, and routing it here is exactly how a data
 * problem used to end up drawn as a passing server hiccup.
 */
export function actionFailureFrom(
  action: ActionFailure["action"],
  err: unknown,
  hasRowProblems: boolean,
): ActionFailure | null {
  if (hasRowProblems) return null;
  const status = (err as { status?: number })?.status ?? 0;
  const said = err instanceof Error ? err.message : String(err);

  if (status === 409) {
    return {
      action,
      message:
        "A timetable is already being generated for this data. Wait for it to finish, then try again.",
      recovery: "wait",
    };
  }
  if (status === 401 || status === 403) {
    return { action, message: "Your sign-in has expired. Sign in again to continue.", recovery: "signin" };
  }
  if (status === 0 || status === 502 || status === 503 || status === 504) {
    return { action, message: MESSAGES.unreachable, recovery: "retry" };
  }
  if (status >= 500) {
    return { action, message: MESSAGES.server, recovery: "retry" };
  }
  return { action, message: said || "That did not work. Try again.", recovery: "retry" };
}
