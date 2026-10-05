"use client";

import { ScanSearch, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AllocationTable } from "@/components/AllocationTable";
import { ClearDataPanel } from "@/components/dashboard/ClearDataPanel";
import { DataVerdict, ImportNotes, ReadinessIssues } from "@/components/dashboard/DataVerdict";
import { FileIntake } from "@/components/dashboard/FileIntake";
import {
  GenerateAction,
  GenerationFailure,
  GenerationProgress,
  GenerationResult,
} from "@/components/dashboard/GenerateAction";
import { InfrastructureCard } from "@/components/dashboard/InfrastructureCard";
import { ApiError, api } from "@/lib/api";
import { useAcademicContext } from "@/lib/academicContext";
import { useAuth } from "@/lib/auth";
import {
  actionFailureFrom,
  derivePhase,
  deriveVerdict,
  fileVerdict,
  humanReason,
  POLL_CEILING_MS,
  resultCounts,
  stagesFor,
  SUCCESS,
  TERMINAL,
  type ActionFailure,
  type Located,
} from "@/lib/dashboardState";
import type { ClearWhat, Infrastructure, Run, RunCheck, TeacherImportPreview } from "@/lib/types";
import { Alert, Button, PageHeader } from "@/components/ui";

/**
 * The whole product on one page: teaching data and rooms in, a timetable out.
 *
 *   Infrastructure (saved once) + Load.xlsx -> Analyze Files -> Data Ready ->
 *   Generate Timetable -> Timetable Generated Successfully -> the table, Export Excel.
 *
 * The rooms are saved on their own and reused, so a new semester means one
 * new file, not two. Nothing is asked for that the files do not contain: no
 * semester, no manual entry. Each teaching load becomes its own dataset.
 *
 * "Analyze Files" is a real check, not a parse: the backend imports the load
 * inside a transaction, runs the full readiness check against the saved rooms,
 * and rolls back. So "Data Ready" means a timetable can actually be built.
 *
 * This file is an orchestrator and nothing else. What state the page is in
 * lives in `lib/dashboardState.ts`, where the distinction that matters - the
 * files being wrong versus the last request failing - is a type rather than a
 * convention someone has to remember.
 */
export default function DashboardPage() {
  const { isAdmin } = useAuth();
  const { refresh, setCurrentId } = useAcademicContext();

  const [infra, setInfra] = useState<Infrastructure | null>(null);
  const [infraLoading, setInfraLoading] = useState(true);
  const [clearing, setClearing] = useState<ClearWhat | null>(null);
  const [cleared, setCleared] = useState<string | null>(null);

  const [load, setLoad] = useState<File | null>(null);
  const [preview, setPreview] = useState<TeacherImportPreview | null>(null);
  const [fileProblems, setFileProblems] = useState<Located[]>([]);
  const [failure, setFailure] = useState<ActionFailure | null>(null);
  const [busy, setBusy] = useState<"analyze" | "generate" | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [check, setCheck] = useState<RunCheck | null>(null);
  const [checked, setChecked] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadInfra = useCallback(async () => {
    try {
      setInfra(await api.infrastructure.get());
    } catch {
      setInfra(null);
    } finally {
      setInfraLoading(false);
    }
  }, []);

  // What is saved, asked once on arrival; `loadInfra` asks again after a save
  // or a clear.
  useEffect(() => {
    let cancelled = false;
    api.infrastructure
      .get()
      .then((saved) => {
        if (!cancelled) setInfra(saved);
      })
      .catch(() => {
        if (!cancelled) setInfra(null);
      })
      .finally(() => {
        if (!cancelled) setInfraLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(
    () => () => {
      if (poll.current) clearInterval(poll.current);
    },
    [],
  );

  function reset() {
    setPreview(null);
    setFileProblems([]);
    setFailure(null);
    setAcknowledged(false);
    setRun(null);
    setCheck(null);
    setChecked(false);
  }

  function pickLoad(e: React.ChangeEvent<HTMLInputElement>) {
    setLoad(e.target.files?.[0] ?? null);
    setCleared(null);
    reset();
  }

  async function analyze() {
    if (!load) return;
    setBusy("analyze");
    reset();
    try {
      setPreview(await api.teacher.preview(load));
    } catch (err) {
      const rows = rowProblemsOf(err);
      setFileProblems(rows);
      setFailure(actionFailureFrom("analyze", err, rows.length > 0));
    } finally {
      setBusy(null);
    }
  }

  function follow(runId: number) {
    const startedAt = Date.now();
    let failures = 0;
    poll.current = setInterval(async () => {
      setElapsed(Math.round((Date.now() - startedAt) / 1000));
      if (Date.now() - startedAt > POLL_CEILING_MS) {
        if (poll.current) clearInterval(poll.current);
        poll.current = null;
        setBusy(null);
        setFailure({
          action: "generate",
          message:
            "This is taking longer than it should. The timetable may still finish - check again in a minute.",
          recovery: "wait",
        });
        return;
      }
      try {
        const latest = await api.runs.get(runId);
        failures = 0;
        setRun(latest);
        if (TERMINAL.includes(latest.status)) {
          if (poll.current) clearInterval(poll.current);
          poll.current = null;
          if (SUCCESS.includes(latest.status)) {
            try {
              setCheck(await api.runs.check(latest.id));
            } catch {
              setCheck(null);
            }
            setChecked(true);
          }
          setBusy(null);
        }
      } catch (e) {
        failures += 1;
        if (failures < 5) return; // a busy server can miss a poll
        if (poll.current) clearInterval(poll.current);
        poll.current = null;
        setBusy(null);
        setFailure(actionFailureFrom("generate", e, false));
      }
    }, 1500);
  }

  async function generate() {
    if (!load) return;
    setBusy("generate");
    setFailure(null);
    setElapsed(0);
    try {
      const applied = await api.teacher.apply(load);
      // The import is the same check again, for real this time. If it now
      // disagrees with the preview, that is a change of verdict, not a failed
      // action: the files are what changed their mind about.
      if (applied.has_errors || applied.academic_context_id === null || applied.readiness?.ready === false) {
        setPreview(applied);
        setBusy(null);
        return;
      }
      await refresh();
      setCurrentId(applied.academic_context_id);
      const started = await api.runs.start({ academic_context_id: applied.academic_context_id });
      setRun(started);
      follow(started.id);
    } catch (err) {
      setFailure(actionFailureFrom("generate", err, false));
      setBusy(null);
    }
  }

  function afterClear(what: ClearWhat) {
    setClearing(null);
    setCleared(
      what === "load"
        ? "The teaching data was cleared. The infrastructure is kept."
        : what === "infrastructure"
          ? "The infrastructure was cleared. The teaching data is kept - upload rooms again to generate."
          : "All data was cleared. Start by uploading the infrastructure.",
    );
    setLoad(null);
    reset();
    void refresh();
    void loadInfra();
  }

  const infraReady = !!infra?.available;
  const verdict = useMemo(() => deriveVerdict(preview, fileProblems), [preview, fileProblems]);
  const phase = derivePhase({ load, infraReady, busy, verdict, run });
  const notes = useMemo(
    () => [
      ...(preview?.warnings ?? []),
      ...(preview?.readiness?.warnings ?? []).map((w) => w.detail),
    ],
    [preview],
  );
  const roomChanges = preview?.room_changes ?? [];
  const summary = verdict.kind === "none" ? null : verdict.summary;
  const working = phase.kind === "generating" || phase.kind === "succeeded" || phase.kind === "failed";

  return (
    <div className="space-y-6">
      <PageHeader
        title="College Timetable Generator"
        description="Upload or manage your teaching and infrastructure files."
        actions={
          isAdmin &&
          !working && (
            <Button variant="ghost" onClick={() => setClearing("load")} disabled={busy !== null}>
              <Trash2 aria-hidden="true" size={14} />
              Clear Previous Data
            </Button>
          )
        }
      />

      {!isAdmin && (
        <Alert tone="info" title="Sign in to upload files.">
          <Link href="/login" className="font-medium underline">
            Sign in
          </Link>{" "}
          to upload and generate. A timetable that already exists can be read on the{" "}
          <Link href="/timetable" className="underline">
            Timetable
          </Link>{" "}
          page without an account.
        </Alert>
      )}

      {cleared && <Alert tone="ok" title={cleared} onDismiss={() => setCleared(null)} />}

      {clearing && (
        <ClearDataPanel
          initial={clearing}
          onClose={() => setClearing(null)}
          onCleared={afterClear}
        />
      )}

      {!working && (
        <section className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <FileIntake
              heading="Teaching Data"
              title="Load.xlsx"
              hint="Who teaches what, to which section, how many students, and how many classes a week."
              file={load}
              verdict={fileVerdict(preview, "load")}
              onChange={pickLoad}
              disabled={!isAdmin || busy !== null}
              templateHref={api.templates.loadUrl}
            />
            <InfrastructureCard
              infra={infra}
              loading={infraLoading}
              disabled={!isAdmin || busy !== null}
              onSaved={() => {
                setCleared(null);
                reset();
                void loadInfra();
              }}
              onRemove={() => setClearing("infrastructure")}
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button
              size="lg"
              variant={verdict.kind === "ready" ? "secondary" : "primary"}
              onClick={analyze}
              disabled={!isAdmin || !load || !infraReady || busy !== null}
              className="w-full sm:w-auto"
            >
              <ScanSearch aria-hidden="true" size={16} />
              {busy === "analyze" ? "Analyzing…" : "Analyze Files"}
            </Button>
            {phase.kind === "partial" && (
              <span className="text-sm text-ink-muted">
                {phase.have === "load"
                  ? "Save the infrastructure to analyze this teaching data."
                  : "Choose Load.xlsx to analyze it against the saved rooms."}
              </span>
            )}
          </div>

          {failure && failure.action === "analyze" && (
            <Alert tone="caution" title={failure.message} />
          )}
        </section>
      )}

      {phase.kind === "verdict" && (
        <section className="space-y-4">
          <DataVerdict verdict={verdict} />

          {verdict.kind === "blocked" && <ReadinessIssues issues={verdict.issues} />}

          <ImportNotes
            notes={notes}
            inferences={preview?.inferences ?? []}
            roomChanges={roomChanges}
            acknowledged={acknowledged}
            onAcknowledge={setAcknowledged}
          />

          {verdict.kind === "ready" && (
            <GenerateAction
              onGenerate={generate}
              disabled={busy !== null || (roomChanges.length > 0 && !acknowledged)}
              failure={failure?.action === "generate" ? failure : null}
              onRetry={() => {
                setFailure(null);
                void generate();
              }}
            />
          )}
        </section>
      )}

      {phase.kind === "generating" && (
        <GenerationProgress
          stages={stagesFor({
            applied: true,
            runId: run?.id ?? null,
            status: run?.status ?? null,
            checked,
            saved: false,
          })}
          elapsed={elapsed}
        />
      )}

      {phase.kind === "succeeded" && run && (
        <section className="space-y-5">
          <GenerationResult
            run={run}
            check={check}
            {...resultCounts(run, summary, check)}
            xlsxHref={api.export.allocationXlsxUrl({ run_id: run.id })}
            onStartOver={() => {
              setLoad(null);
              reset();
            }}
          />
          <AllocationTable runId={run.id} />
        </section>
      )}

      {phase.kind === "failed" && run && (
        <GenerationFailure
          reason={humanReason(run)}
          onTryAgain={() => {
            setRun(null);
            setCheck(null);
            setChecked(false);
            setFailure(null);
          }}
        />
      )}
    </div>
  );
}

/** The 422 that says which rows contradict each other. */
function rowProblemsOf(error: unknown): Located[] {
  if (!(error instanceof ApiError)) return [];
  const detail = (
    error.body as {
      detail?: { problems?: { file: string; row_number: number; message: string }[] };
    }
  )?.detail;
  return (detail?.problems ?? []).map((p) => ({
    where: p.row_number > 1 ? `${p.file}, row ${p.row_number}` : p.file,
    what: p.message,
  }));
}
