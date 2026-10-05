# Solver benchmarks

The baseline the redesign is measured against. Phase 6 changes what the solver
is asked to solve — subjects gain separate lecture and practical loads, so a
"mixed" subject becomes two scheduling pairs instead of one — and that must not
quietly cost the schedules that exist today.

**How to reproduce**

```bash
cd backend
.venv/Scripts/python.exe -m scripts.benchmark 4 8 12 20
```

Run it on an otherwise idle machine. CP-SAT is a portfolio search across
worker threads, and this project has measured the same code and data going from
`OPTIMAL` in under a second to `UNKNOWN` at 30s purely from contention with one
other solve. A number taken while the test suite is running is not a number.

**Only compare rows measured in the same sitting.** Absolute solve times here
are not portable across days, machine states, or thermal conditions. Measured
directly: the pre-Phase-6 code, re-run unchanged some weeks after its own
baseline was recorded, took 8.55s at 8 sections where the baseline table says
2.73s — a 3× difference from the same commit against the same data. Had that
been read as a regression in whatever change happened to be in flight, the
"fix" would have been for a bug that does not exist.

So a change is judged against the *old code re-measured now*, not against a
number in this file. The numbers below are kept because they describe shape —
how cost grows with sections, where the ceiling is — which does survive.
Anything claiming a specific slowdown or speed-up needs both sides measured
back to back.

---

## Baseline — before Phase 6 (commit `1ceede3`, Phase 0 indexes applied)

> Kept for its *shape* — pairs, candidate starts, how cost grows, where the
> ceiling is. Its absolute seconds are **not** a yardstick for later work: the
> same commit re-measured later gave 8.55s where this table says 2.73s. Use
> the same-sitting comparison in the Phase 6 section instead.

Synthetic data from `scripts/benchmark.py`: 9 × 50-minute periods, Monday to
Friday, shared faculty across sections, a deliberately scarce lab pool split
across two lab types, mixed section capacities, room and faculty pools growing
with section count. 8 search workers, `cp_model_probing_level=0`.

### Hard constraints only (90s budget)

| Sections | Pairs | Candidate starts | Status | Solve time | Wall | Peak heap |
|---:|---:|---:|---|---:|---:|---:|
| 4 | 28 | 3,680 | `OPTIMAL` | 2.50s | 3.42s | 1.9 MB |
| 8 | 56 | 7,360 | `OPTIMAL` | 2.73s | 5.48s | 3.7 MB |
| 12 | 84 | 15,840 | `OPTIMAL` | 12.19s | 14.93s | 6.3 MB |
| 20 | 140 | 43,600 | `OPTIMAL` | 25.16s | 35.89s | 16.2 MB |

Every result independently re-verified: `hard_constraint_violations = 0`.

### With the soft objective (60s budget)

| Sections | Status | Objective | Solve time |
|---:|---|---:|---:|
| 4 | `FEASIBLE` | 106 | 60.22s |

The objective phase spends its entire budget by design — it keeps improving
the schedule until the time runs out. `solve_time ≈ budget` is expected and is
not a timeout. This is why `SOLVER_MAX_SECONDS` is best read as "how long a
generation takes", not "the worst case".

---

## Phase 6 — the lecture/practical split

Both tables below were measured back to back in one sitting, which is the only
comparison that means anything (see the warning above). Reproduce with:

```bash
.venv/Scripts/python.exe -m scripts.benchmark 4 8 12 20
.venv/Scripts/python.exe -m scripts.benchmark 4 8 12 --mixed
```

`--mixed` expresses **the same weekly teaching** as mixed subjects: the two
standalone lab subjects are folded into two of the theory subjects, which then
own both a lecture and a practical. Same 22 periods a week per section, same
number of obligations — the only difference is that a subject can now be two of
them.

### Hard constraints only, same sitting

| Sections | Pairs | Starts | Pure subjects | Mixed subjects |
|---:|---:|---:|---:|---:|
| 4 | 28 | 3,680 | 1.91s | 1.77s |
| 8 | 56 | 7,360 | 8.75s | 7.87s |
| 12 | 84 | 15,840 | 34.46s | 31.06s |
| 20 | 140 | 43,600 | 59.28s | — |

`OPTIMAL` everywhere, `hard_constraint_violations = 0` everywhere, verified by
the independent checker.

**The split costs nothing by itself.** Identical pair counts, identical
candidate starts, and solve times that differ by less than the run-to-run
noise — in the direction of *faster*, which is how you know it is noise. The
cost of a mixed subject is the extra obligation it creates when it genuinely
has one, not the machinery that allows it. A dataset whose subjects each have a
single component builds the problem it always built.

### What the benchmark caught that the tests did not

The first mixed run came back `OPTIMAL` with **`hard_constraint_violations = 8`**
— two per section, one per mixed subject. The solver was right (4 lectures plus
one 2-period practical is 6 periods); the independent checker was measuring
weekly demand from the legacy single-load columns and reading 6 as "expected
4". The same stale assumption was in the readiness checks and the AI
diagnostics, where it undercounted demand and would have called a section's
load feasible when it is not.

That checker gates manual edits, so every mixed subject would have been
impossible to edit — the exact capability this phase adds. Ten unit tests
passed throughout: each asserted what the solver produced, and none asked the
independent checker whether it agreed. The end-to-end number did.

---

## What Phase 6 had to show, and what it showed

1. **No regression for existing data.** *Met.* Datasets with no mixed subjects
   produce the same pairs (28/56/84/140) and the same candidate starts
   (3,680/7,360/15,840/43,600) as the baseline. Stronger than the timings: the
   CP-SAT model itself was compared before and after and is identical —
   18,920 variables and 38,704 constraints at 8 sections, 33,300 and 72,096 at
   12, both sides. A subject with one component yields exactly one pair, which
   is the regression anchor and is asserted directly in
   `test_solver_session_types.py`.
2. **A stated cost for the new capability.** *Met, and it is smaller than
   expected.* The split itself costs nothing measurable — see the table
   above. Mixed subjects add cost only by adding obligations: a subject that
   gains a practical it did not have gains that practical's periods, which is
   arithmetic rather than overhead.
3. **Violations stay at zero**, verified by the independent checker rather
   than by the solver's own status. *Met — but only after the checker was
   fixed.* It was this criterion that failed first, and it failed loudly.
   Recording it as its own column rather than trusting `OPTIMAL` is what
   surfaced the bug.

## The real demo workbook

Not synthetic: the committed 14-sheet fixture, imported whole and then solved.
Measured on the same machine as the Phase 6 tables above.

| Context | Sections | Result |
|---|---:|---|
| BSC (Computer Science) | 4 | ready, solves |
| BTECH (ECE) | 4 | ready, solves |
| BCA (Computer Applications) | 11 | needs two more labs; then `FEASIBLE`, 407 assignments, 0 violations |

### The budget was not the problem

BCA found no solution at a 240s budget and a feasible one at 300s, against a
deployed `SOLVER_MAX_SECONDS` of 180. The obvious response - raise the limit -
is wrong, and measuring the deployed configuration is what showed it. Two
search workers is what the free instance runs:

| workers | objective | status | first solution |
|---:|---|---|---:|
| 8 | on | `FEASIBLE` | 101s |
| 8 | off | `OPTIMAL` | 100s |
| 2 | on | **`UNKNOWN`** | **never, in 300s** |
| 2 | off | `OPTIMAL` | 94s |

At the deployed worker count, with the objective in the model, the solver found
**nothing at all in five minutes**; without the objective it solved the same
data to optimality in under two. So the objective was not making the search
slower, it was making it look somewhere else - and no timeout would have fixed
that.

The fix is to solve for feasibility first, keep that timetable, and let the
objective improve on it from a warm start (`FEASIBILITY_SHARE` in
`solver/run.py`). At the exact deployed configuration - 2 workers, 180s:

| | before | after |
|---|---|---|
| status | `UNKNOWN` | `FEASIBLE` |
| first solution | never | 44.5s |
| rows written | 0 | 407 |

`SOLVER_MAX_SECONDS` stays at 180. The eleven-section context now returns a
valid timetable there, and a budget that runs out during optimisation costs
quality rather than the whole answer.

## Known ceiling

From earlier measurement (recorded in `TIMETABLE_LOGIC_SPEC.md`): 24 sections
solves with the objective on; 30 sections returns `UNKNOWN` at a 120s budget.
There is no measurement above 30, and the curve is superlinear. That ceiling is
per academic context — a context is one department-semester, and contexts scale
horizontally — so it is a context-sizing fact, not an institution-wide limit.

---

## Workbook importer

An onboarding workbook is read once, so the number that matters is whether it
finishes in a reasonable time and whether its cost grows with the data in a
sane way — not milliseconds.

**Reproduce**: `scripts/bench_workbook.py` (synthetic workbook at three sizes).

| Dataset | Rows | Analyse | Queries | Apply | Queries |
|---|---:|---:|---:|---:|---:|
| Demo (the real 14-sheet file) | 442 | 0.18s | 13 | 0.27s | 262 |
| One department | 2,536 | 0.35s | 13 | 5.7s | 1,124 |
| A faculty | 9,906 | 5.9s | 13 | 16.5s | 4,034 |

**Analysis is 13 queries at every size.** That is the property worth pinning:
each referenced table is prefetched once, so validating ten thousand rows costs
the same number of round trips as validating four hundred. Before the resolver
existed this was two to four queries *per row*, and doubled again because
applying re-runs the analysis.

Apply writes with one flush per sheet rather than one per row — ids are only
needed by *later* sheets, and sheets are applied in dependency order. That
halved both the query count and the wall time at the largest size.

### What is deliberately not optimised

Analysis time grows faster than row count (roughly 17× for 4× the rows at the
top end), which is Python-side parsing and validation, not database work. A
ten-thousand-row workbook is an unusually large one-off onboarding operation,
and six seconds to be told exactly what is wrong with it before anything is
written is a good trade. If real files turn out larger, the parse is the place
to look first.
