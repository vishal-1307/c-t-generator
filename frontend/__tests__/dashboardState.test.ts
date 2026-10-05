import { describe, expect, it } from "vitest";
import {
  actionFailureFrom,
  derivePhase,
  deriveVerdict,
  fileVerdict,
  humanReason,
  stagesFor,
} from "@/lib/dashboardState";
import type { Run, TeacherImportPreview } from "@/lib/types";

/**
 * The rule this file exists to hold down:
 *
 *   a request that failed is not a verdict about the data.
 *
 * Everything else here is the arithmetic of "is this ready", which used to be
 * spread through a render function where each new way of being wrong had to be
 * remembered separately - and one of them, a repeated row, was not.
 */

const OUTCOME = {
  sheet: "Rooms", entity: "rooms", label: "Rooms",
  creates: 1, updates: 0, unchanged: 0, invalid: 0, duplicates: 0,
  problems: [], notes: [],
};

const SUMMARY = {
  faculty: 7, subjects: 9, sections: 9, groups: 2, classes: 36,
  required_periods: 52, rooms: 30, classrooms: 18, labs: 12, byod_rooms: 21,
};

function preview(over: Partial<TeacherImportPreview> = {}): TeacherImportPreview {
  return {
    filename: "Load.xlsx", sheets: [], outcomes: [OUTCOME], decisions: [],
    blockers: [], counts: {}, has_errors: false, can_apply: true, committed: false,
    warnings: [], notes: [], summary: SUMMARY, room_changes: [],
    readiness: { ready: true, blockers: [], warnings: [], unassignable: [] },
    academic_context_id: null, dataset_label: "Upload 1",
    ...over,
  } as TeacherImportPreview;
}

const RUN = { id: 1, status: "OPTIMAL", assignment_count: 52, message: null } as unknown as Run;

describe("the data verdict", () => {
  it("is ready only when every way of being wrong is silent", () => {
    expect(deriveVerdict(preview(), []).kind).toBe("ready");
  });

  it("is blocked by a repeated row, which reports no invalid rows at all", () => {
    const v = deriveVerdict(
      preview({
        has_errors: true,
        outcomes: [{ ...OUTCOME, duplicates: 1 }],
      }),
      [],
    );
    expect(v.kind).toBe("blocked");
  });

  it("is blocked by a sheet that was rejected before it could report a row", () => {
    const v = deriveVerdict(
      preview({ has_errors: true, readiness: null, outcomes: [], blockers: ["Rooms: missing required column(s) capacity."] }),
      [],
    );
    expect(v.kind).toBe("blocked");
    if (v.kind !== "blocked") return;
    // And it says so, rather than leaving a summary with nothing to explain it.
    expect(v.issues.blockers[0].what).toContain("missing required column");
  });

  it("blames the file the rejected sheet came from, and not the other one", () => {
    const p = preview({ has_errors: true, readiness: null, blockers: ["Rooms: missing required column(s) capacity."] });
    expect(fileVerdict(p, "infra").ok).toBe(false);
    expect(fileVerdict(p, "load").ok).toBe(true);
  });

  it("counts a duplicate against the file it is in", () => {
    const p = preview({ has_errors: true, outcomes: [{ ...OUTCOME, duplicates: 2 }] });
    expect(fileVerdict(p, "infra")).toEqual({ ok: false, detail: "2 rows are repeated" });
    expect(fileVerdict(p, "load").ok).toBe(true);
  });

  it("knows an import with nothing to change is still ready", () => {
    const v = deriveVerdict(preview({ can_apply: false }), []);
    expect(v.kind).toBe("ready");
    if (v.kind === "ready") expect(v.unchanged).toBe(true);
  });
});

describe("an action that failed", () => {
  it("is never produced for the 422 that carries row problems", () => {
    expect(actionFailureFrom("analyze", new Error("422"), true)).toBeNull();
  });

  it("tells someone to wait when a solve is already running, and not to retry", () => {
    const f = actionFailureFrom("generate", { status: 409, message: "busy" }, false);
    expect(f?.recovery).toBe("wait");
    expect(f?.message).toContain("already being generated");
  });

  it("cannot change what the data verdict is", () => {
    // The proof is structural: a failure is not an input to the verdict at all.
    const v = deriveVerdict(preview(), []);
    const failed = actionFailureFrom("generate", { status: 409 }, false);
    expect(failed).not.toBeNull();
    expect(deriveVerdict(preview(), []).kind).toBe(v.kind);
    expect(v.kind).toBe("ready");
  });
});

describe("the phase", () => {
  const base = { load: null, infraReady: false, busy: null, verdict: { kind: "none" }, run: null } as const;

  it("walks empty -> partial -> chosen as the load and the saved rooms arrive", () => {
    const f = new File(["x"], "Load.xlsx");
    expect(derivePhase({ ...base }).kind).toBe("empty");
    expect(derivePhase({ ...base, load: f })).toEqual({ kind: "partial", have: "load" });
    expect(derivePhase({ ...base, infraReady: true })).toEqual({ kind: "partial", have: "infra" });
    expect(derivePhase({ ...base, load: f, infraReady: true }).kind).toBe("chosen");
  });

  it("is generating while a run is not terminal, and succeeded once it is", () => {
    const running = { ...RUN, status: "RUNNING" } as Run;
    expect(derivePhase({ ...base, run: running }).kind).toBe("generating");
    expect(derivePhase({ ...base, run: RUN }).kind).toBe("succeeded");
    expect(derivePhase({ ...base, run: { ...RUN, status: "INFEASIBLE" } as Run }).kind).toBe("failed");
  });
});

describe("the stages", () => {
  it("marks nothing done that has not actually happened", () => {
    const stages = stagesFor({ applied: true, runId: null, status: null, checked: false, saved: false });
    expect(stages.map((s) => s.status)).toEqual(["done", "active", "pending", "pending", "pending"]);
  });

  it("fails the solve step rather than pretending it finished", () => {
    const stages = stagesFor({ applied: true, runId: 1, status: "INFEASIBLE", checked: false, saved: false });
    expect(stages.find((s) => s.id === "solve")?.status).toBe("failed");
  });
});

describe("why a run produced no timetable", () => {
  it("says it in a sentence, and adds what the solver said", () => {
    const said = humanReason({ ...RUN, status: "INFEASIBLE", message: "Section 2401 has too many classes." } as Run);
    expect(said).toContain("No timetable can satisfy every rule");
    expect(said).toContain("Section 2401");
  });

  it("does not call a timeout a proof that none exists", () => {
    expect(humanReason({ ...RUN, status: "TIMEOUT", message: null } as Run)).toContain("ran out of time");
  });
});
