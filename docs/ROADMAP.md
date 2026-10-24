# Hornet roadmap

Updated 2026-10-24. Released milestones and current development are distinguished
from planned capabilities below.

## Completed: setup and contract-review maintenance release

Version 0.16.1 is published on GitHub and PyPI. Its maintenance scope was:

- Make worker provisioning and the first-goal instructions dependable on Windows
  and Linux hosts; preserve existing workers and failed runs during recovery.
- Check the actual Git baseline, protected worker code and connection before
  spending model turns.
- Distinguish a cell's single typed result from the acceptance checks that cover
  the full requirement. Keep review evidence bound to its instruction version.
- Exercise the packaged first-user path, finish regression checks and document
  remaining limitations before preparing a release.

## 1. Establish a repeatable quality cycle

Aim for complete acceptance of a defined task. Do not equate a passing finite
test set with proof of correctness under every possible condition.

The cycle is define -> execute -> check -> retain evidence -> revise. Preserve
the goal, file scope, attempt budget and failed history throughout. Adaptation
must improve the product against the contract; changing the intended outcome
requires an explicit new contract.

The agreed [decision authority and working context design](DECISION-CONTEXT.md)
defines which requirements govern a decision, what belongs in the current cell's
prompt, and how identity and recovery keep work attached to the intended goal.
The note distinguishes existing mechanisms from planned context additions.

Current development, unreleased:

- Allow a user or supervising program to supply whole-goal acceptance checks
  before planning, through `autowork --acceptance-file`.
- Retain those checks through resume and inherit them in revisions. Keep model
  generated cell checks, typed returns and final evidence bindings.
- Exercise failure cases: a green cell with a failed goal gate, altered evidence,
  a lost model connection, a pending review and a regression in a revision.

See [supplied goal checks](GOAL-CONTROLLER.md#supplied-whole-goal-acceptance-checks-unreleased).

Live Gemma 12B trial, 2026-10-24, on the unreleased build:

- Repaired integer doubling, added clamping, then used an exact-cell revision to
  add list scaling. Supplied goal checks, inherited checks and 212 additional
  cases passed. The baseline, parent source, parent evidence and existing tests
  stayed unchanged. Resume used the frozen checks after the input file moved.
- The first goal needed two operator corrections before plan approval: Gemma
  repeatedly declared an object return for an integer call, and its fresh-context
  reviewer approved both mistakes. No implementation code was supplied by the
  operator. The revision completed without further correction.
- Across both goals, 24 model requests peaked at 4,073 prompt tokens per request.
  This was an assisted trial, not evidence of unattended reliability.

Follow-up hardening, unreleased: independently derive selected-call returns
without showing the proposed schema, then compare them mechanically before
general review and again when validating saved approvals. Preserve old review
policies and distinguish exact-value evidence from root-type-only evidence.

Fresh trial with that guard, also on 2026-10-24:

- The original goal and exact-cell revision completed without operator plan
  corrections or implementation edits. A repeated editing loop triggered the
  existing bounded handoff to a fresh context and then completed.
- Supplied checks, inherited checks and all 212 additional cases passed. Only
  `calculator.py` changed; baseline, parent source and parent evidence remained
  intact. The two stages used 37 model requests, peaking at 3,891 prompt tokens.
- The historical object-for-integer mistake was also replayed independently:
  the new guard rejected it and accepted the corrected schema.
- Model review remains fallible. One derivation invented a numeric return for
  an unspecified wrapper; its schema mismatch was rejected. Another confused
  the selected result example with an empty-list assertion and falsely rejected
  a valid check. A subsequent proposal was correctly reviewed without operator
  feedback. Retain these records and improve per-call context isolation; do not
  treat model-derived answers as ground truth.

Current development also appends bounded repair jobs after confirmed final goal
or inherited regression failures. Routing requires one unambiguous code leaf;
original criteria, dependency values, jobs and failed reports are retained.
Repairs use the existing goal budget and final gates rerun before completion.
See [final goal repair](GOAL-CONTROLLER.md#repair-a-failed-final-goal-gate-unreleased).
Multiple-owner integration repairs and final cell-check repairs remain future
work; those cases stop for inspection.

Controlled live repair trial, 2026-10-24:

- A deterministic fixture supplied a single-cell plan, narrow cell checks and
  deliberately faulty clamping code. The original whole-goal checks caught it.
  Gemma 12B performed only the appended repair; this trial did not measure
  autonomous planning or how often the model produces defects.
- On the final local package, Gemma read, edited, checked and finished in four
  requests, peaking at 3,587 prompt tokens. All final checks and 247 additional
  cases passed. Only `calculator.py` changed; the baseline, original cell history,
  published value and failed report remained intact. No repair code was supplied
  by the operator. A preceding trial also passed in four model requests.

The periodic goal supervisor requested on 2026-10-24 is implemented for 0.17.0.
It uses deterministic eligibility checks, explicit per-goal enrollment, lifetime
activation limits, cooldowns, a project lease and retained activation receipts.
It resumes known safe boundaries through the existing controller. Scans require
no model calls. Unknown outcomes, operator stops, pending decisions and active
workers remain blocked. See [SUPERVISOR.md](SUPERVISOR.md). Scheduling is opt-in;
no OS scheduler is installed. Model-assisted interpretation of ambiguous failures
remains future work and cannot authorize dispatch outside these mechanical rules.

Keep follow-up requests tied to the exact saved goal and cell. Compatible
upgrades use the existing `revise --cell` path and preserve prior checks. For an
intentional behavior change, a future contract-version workflow should identify
the specific superseded criteria and the reason, retaining the earlier contract
and product as history. Today `revise` preserves all inherited checks; an
incompatible change requires a separately reviewed new goal baseline.

Measure accepted outcomes, false completions in known failure scenarios,
recovery, source preservation and peak request context. Record which checks were
independent of the implementation model and which still relied on a model.

General computer tasks can reuse this lifecycle once each adapter defines its
allowed actions, observable postconditions, recovery rules and permission scope.
An unbounded background loop or a claim of zero defects is not a release target.

## 2. Prototype builders with durable contracts

Daniel's October 23 proposal is the reference design for this next capability:
an orchestrator selects a named builder, supplies an interface and destination,
and receives a UUID-bound contract. An assigned agent supplies implementations
through a return tool. The builder reconstructs and verifies the finished product.

Start with the Python Strategy pattern. See
[the builder design](CONTRACT-BUILDERS.md) for the lifecycle, proposed tools,
ownership rules and acceptance criteria.

The reusable unit is provision -> implement -> return -> assemble -> verify.
Language and artifact adapters supply the structure and checks. Preserve Hornet's
existing goal/cell identities, bounded contexts, failed attempts, revisions,
workspace boundaries and return manifests throughout.

### First deliverable

A local model creates two interchangeable Python strategies and a context that
uses their declared interface. Hornet assembles the returned bodies into an
importable package, verifies behavior, and supports a targeted method revision
without changing the original result.

Use one worker and sequential execution for this prototype. Design independent
submission areas now; concurrent execution needs its own implementation and
validation before it becomes a supported feature.

## 3. Expand after the prototype proves useful

- Add further patterns and language adapters based on completed user tasks.
- Add builder discovery by capability with only the selected schema in context.
- Extend the same contract/return lifecycle to other structured artifacts where
  assembly and acceptance can be defined explicitly.

Measure completion, retries, prompt size, assembly failures and revision
preservation. Do not treat generated structure or passing syntax checks as proof
of complete application behavior.
