# Validation scope

## Current development checkpoint — October 26, 2026

This checkpoint records the registry integration before the 0.18.0 release
commit. The development packages used 0.17.0 metadata and were never published
as replacements for that release. The final candidate updates version metadata,
starter instructions and documentation, and adds failure-report output to the
inherited-repair test without changing its checks or time limits. Release CI
must cover that final source; its results belong in the signed release record.

| Check | Result |
| --- | --- |
| Windows source suite, Python 3.9.13 | 752 passed |
| Windows source suite, Python 3.13.14 | 752 passed |
| Fresh installed wheel, Python 3.9 and 3.13 | Package contents, CLI and stdio allowlist passed |
| Complete installed suite, Python 3.13.14 | 752 passed in the clean repeat; no skips |
| Gemma 12B three-cell Strategy goal | Completed; stale write refused, interrupted write recovered |
| Linked single-file revision | Completed; original evidence and sibling files unchanged |

Both source runs matched the complete test discovery inventory, with unchanged
Python/JSON source and test files. The wheel's 100 Python/JSON package files match
the source byte for byte. Strict package metadata checks passed. These are local
Windows checks; GitHub's Windows/Ubuntu checks have not run for this candidate.

The first full installed-suite run retained two failures:

- Its separate test staging omitted `worker.py`, which one setup test reads as
  reference data. Restoring that file from the wheel fixed the staging error;
  all 157 tests in the affected group passed. Imports were checked to come from
  the installed package, and product and test code stayed unchanged.
- An inherited-revision repair test stopped at its final cell check. Its original
  detailed worker report was removed by test cleanup, so the cause is unresolved.
  The same test passed alone, in four simultaneous repeats, and in its complete
  200-test group, with the original assertions and time limits.
  The subsequent complete installed-suite run also passed all 752 tests in
  392.60 seconds, with package and test hashes unchanged and all Hornet imports
  verified to come from the installed wheel. Successful retries do not establish
  the cause of the first failure.

The live trials are focused evidence, not a reliability estimate. Earlier failed
plans, revisions and model calls remain retained. Final version selection,
release CI, signing and publication are still separate steps.

## Development evidence history

The following entries describe successive checkpoints. Test counts and statements
about the next required trial belong to their checkpoint; the summary above is
current. Failed attempts remain alongside the later successful runs.

## Unreleased registry integration — October 26, 2026

The registry adapter was exercised in an isolated Ubuntu 22.04 worker from a
Windows controller, using `google/gemma-4-12b` through LM Studio's JSON protocol.
The task was to repair an instance method while preserving its module-level
caller, sibling constant and existing tests. Caller-supplied acceptance checks
covered all 201 integers from -100 through 100, including exact integer types.

After adding source-bound API declarations for check authors, two consecutive
fresh goals completed with `--registry-only` and the entire reviewed cell catalog
available. The operator supplied the goal, file scope and independent acceptance
checks. Gemma supplied the plans, cell checks and registry tool selection.

| Run | Model requests | Tool calls | Peak prompt tokens | Elapsed |
| --- | ---: | ---: | ---: | ---: |
| First | 15 | 6 | 5,271 | 185.65 seconds |
| Repeat | 15 | 7 | 5,452 | 220.51 seconds |

Both runs passed the cell checks, existing suite and caller's final checks.
Return manifests and registry/operation receipts were reconstructed and checked
after completion. The original worker baseline remained unchanged; results stayed
in separate goal workspaces. The repeat initially invented `registry.update_method`.
Hornet rejected it, retained that failed call, and Gemma then discovered and
inspected `languages.coding.python.method.fill` before using it successfully.
The readback gate also required a current file observation before completion.

Earlier attempts remain part of the evidence. They included confusing registry
operations with product return functions, repeatedly searching for a project
class, and generating invalid checks such as calling an instance method without
an instance or treating a module function as a class attribute. Model review
missed those invalid checks. A supplied-plan trial succeeded before the two fully
planned runs; it is not counted as autonomous planning evidence. A native-protocol
request returned HTTP 400; the completed live trials used `lmstudio-json`.

Regression coverage includes file and asset scope, closed schemas, stale source
hashes, missing contract inspection, unknown operations, lost write replies,
retained failed attempts, readback, strict-policy inheritance, receipt tampering,
context compaction and API evidence binding. The pre-API-evidence integration
suite passed 653 tests on Python 3.9. The final installed wheel separately passed
all 64 registry and API-evidence tests on both Python 3.9 and 3.13. A broader
205-test regression run on Python 3.13 passed after adding API evidence; the final
binding-tamper test is included in the installed 64-test runs. The wheel's 99
Python/JSON package files matched the source byte for byte. Package checks exercised
CLI policy parsing and the standalone stdio allowlist outside the source checkout.
These are local development checks, not a published release or GitHub CI result.

A subsequent three-file Strategy trial stopped in its first implementation cell
after repeated calls without contract inspection. No file was written, and the
planned stale-hash and interruption injections were not reached. The strict
worker prompt still recommended the disabled direct-edit tool. That guidance
was removed; the execution menu now exposes exact arguments for the two latest
inspected contracts. Four additional regression cases cover the prompt, menu
bounds, argument validation and reconstruction. A fresh multi-file trial is
required before claiming that this resolves the observed failure.
The resulting source passed all 662 tests on Python 3.9 and all 662 on Python
3.13, with source hashes unchanged throughout each run and the combined shard
inventory matching full test discovery. An intervening live retry timed out on
its first model request before any cell work; it is not a successful trial.

The next live run discovered and inspected its operation, rejected the harness's
stale hash, and completed a real write. A fresh controller process recovered the
confirmed worker receipt without repeating that write; the interrupted attempts,
receipt bytes and original worker baseline were preserved. The application still
failed: later edits mistyped hashes, a generated check confused a method with a
property, and method-body submissions included another function declaration.
The bounded attempt stopped without completing the goal. This is recovery
evidence, not successful multi-file implementation evidence.

Cell registry edits now accept the existing job-bound short read references,
method tools reject the accidental signature wrapper, and source facts/review
guidance explicitly distinguish ordinary method calls from property access.
The resulting 71 focused registry/API tests passed on Python 3.13. Another fresh
three-file trial reached both fault injections and confirmed cold recovery with
the original receipt and attempts preserved. It still stopped in the first cell
at its 20-turn limit: a generated check had exception cases accidentally nested
inside an earlier handler, and later edits repeatedly reused stale references.
The whole run used 33 model requests, 20 tool calls and at most 8,890 prompt tokens;
no multi-file success is claimed for that run.

The execution menu now offers exact current versions reconstructed from confirmed
reads and withholds existing-file edits until a fresh read. Check review renders
the parsed code without comments, and authors are guided toward independent
standard-library exception assertions. All 31 cell-registry and 34 contract-review
tests passed on Python 3.13 after these changes. The next trial used independent
exception assertions and recovered its interrupted write, but again froze a
method/property contradiction. The operator stopped it at a confirmed boundary
after 19 requests and 11 tool calls; peak prompt usage was 5,871 tokens. Its
failed contract and stop reason were retained.

A narrow syntax preflight now rejects direct scalar comparisons with known
ordinary methods before model review. Eight API-evidence tests passed on Python
3.13, including a real controller fixture that rejects the bad check and freezes
the corrected submission without relying on a model reviewer. A fresh full suite
and live trial are still needed for this subsequent source revision.

A following intake exhausted six proposals because the example return schema
contradicted the exact-result rule. The example now includes its singleton enum.
The next trial passed intake, reached both deliberate faults, recovered the
confirmed write and completed its first cell. It stopped in its second cell
after repeatedly supplying an indented sequential return. Its 40 model requests
and 26 tool calls peaked at 6,198 prompt tokens; the original baseline, interrupted
attempts and confirmed receipt remained unchanged. This was not a completed goal.

The method operation now shares its automatic body preparation with the optional
formatter, with exact-line diagnostics and explicit body-only examples. Equivalent
read handles now count toward the same repeated-failure budget. All 42 native
registry and 33 cell-registry tests passed on Python 3.13 after these changes,
including a controller case that retains three failures and writes nothing.
Full-suite and fresh live-trial validation of this revision remain pending.

That next run completed the first cell and recovered its interrupted write, but
stopped in the second after `method_body` failed to match and an unrelated fallback
suggested a JSON editor for Python. All rejected edits were retained; baseline,
interrupted evidence and receipt bytes remained intact. The run used 32 requests,
21 tools and at most 6,570 prompt tokens. Search now splits capability separators,
and an unmatched query does not offer unrelated editors. A separate formatter
probe exposed changed whitespace inside multiline strings; body insertion now
preserves those contents and checks the complete parsed structure before commit.
All 47 native registry tests and 33 cell-registry tests pass on both Python 3.9
and 3.13 after these changes. A new complete live trial is still required.

The following run completed its first two cells, including the strategy cell in
eight tool calls. Both fault injections and cold recovery again preserved the
confirmed receipt, interrupted evidence and baseline. It stopped before the third
cell implementation: the controller recommended `TestCase.assertRaises` but did
not recognize exception-only checks as assertions. Six proposals received a
generic rejection. The run used 44 model requests and 21 tool calls, peaking at
7,540 prompt tokens; it did not complete the application. Cell and goal preflight
now share recognition of explicit unittest exception checks and name the rejected
check in feedback. Independent review and runtime checks remain required.

The next trial (`registry-shipping-20261026-060356`) reached both deliberate faults
and recovered the confirmed write without changing the retained attempts, receipt
or baseline. All three files reached passing cell checks, but the third cell did
not close: six repeated final summaries omitted the required manual readback and
the global 60-request limit was reached. The run used 34 tool calls with a peak
of 8,519 prompt tokens. Full-goal verification and integration were not reached.
Inspection also found an exact-int subclass defect that the cell checks had missed;
the independent final gate would reject it. No operator repair was applied.

The same trial exposed a blind-review ambiguity: `instance.quote(500)` lost whether
`instance` was a `FlatRate` or `WeightRate`. The reviewer rejected a correct flat
rate as if it used the weight formula. New review packets retain bounded receiver
construction and call history without exposing asserted answers. Committed registry
writes now perform their own separately recorded readback. Static API checks are
also conservative around constructors, inheritance and dynamic attribute access.
The following trial (`registry-shipping-20261026-062931`) reached both fault
injections and recovered the confirmed write and its automatic readback. It
stopped after 53 model requests and 36 tool calls, peaking at 9,479 prompt tokens.
The contract and strategy cells completed, but a valid zero weight failed in the
strategy implementation; its generated checks had missed this case. The service
cell detected the defect but could not edit the strategy file. It exhausted its
20-turn budget without completing the goal. The independent final checks also
covered an exact-int subclass defect in the contract. No operator repair was
applied, and the baseline, interrupted attempts and original receipt were preserved.

New flat goals now attach ready caller checks to upstream cell gates using declared
file ownership and dependency order. The final goal gate stays unchanged. Sixteen
regression tests passed on Python 3.9 and 3.13, covering upstream repair before
consumer execution, blocking consumers after failure, deterministic crash recovery,
transitive dependencies, unchanged write permissions, later writers, check limits,
legacy runs, nested deferral and evidence tampering. The existing 51 goal-contract,
goal-repair and tree tests also passed on both Python versions. Fresh whole-goal
and revision trials are still required.

The next trial (`registry-shipping-20261026-065618`) stopped before its first
worker tool call. The old review validator compared the combined authored and
caller check list against the author's reviewed submission, rejecting the new
appended checks. The run retained seven model requests, no writes and an unchanged
baseline; neither planned fault was reached. The validator now verifies caller
checks and their routing separately, then compares only the original authored
checks with their review receipt. Additional regression cases exercise the full
review-enabled controller and refuse changes to either authority. All 19 early
cell-check tests and all 41 contract-review tests passed on Python 3.9 and 3.13.

The subsequent trial (`registry-shipping-20261026-070207`) exercised both faults
and recovered the original write receipt without repeating that write. The first
cell repaired its exact-integer check after the caller gate rejected an integer
subclass. The strategy cell then treated the validator's zero return as false and
rejected zero-weight input. The early caller gate caught this before starting the
service cell. Repeated unsuccessful repairs stopped the run at 51 model requests
and 38 tool calls, with peak prompt usage of 9,613 tokens. The baseline, interrupted
attempts and confirmed receipt remained unchanged. This was not a completed goal;
the service cell, final integration and revision were not reached.

Inspection showed that the strategy packet contained the dependency's result and
receipt but omitted its declared behavior: validation returns a nonnegative weight
unchanged, including zero. New goal packets now include completed direct providers'
assigned requirements and call inputs. The behavior text is deduplicated, file
permissions are unchanged, and saved legacy packets retain their original meaning.
All 75 focused dependency-context, mission, nested-tree and research tests passed
on Python 3.9 and 3.13. These include legacy packets, cold resume, retained provider
receipts, deduplicated behavior text, unchanged permissions and tamper rejection.
The behavior-context trial (`registry-shipping-20261026-072347`) completed its
contract and strategy cells. The strategy implementation used the validator's
returned weight and passed the zero-weight checks. Both injected faults recovered,
the confirmed write was not repeated, and retained attempts, receipts and the
baseline stayed unchanged. The service cell then repeatedly called instance
methods on their classes, without constructing instances. Executed checks rejected
those calls, and the unchanged-operation guard stopped the run at 56 requests and
41 tool calls (peak prompt: 9,547 tokens). Final integration and revision were not
reached. This is progress in one run, not proof that the context change alone
caused the different strategy result.

The next policy adds bounded, source-bound dependency signatures and method kinds
to the handoff. Function bodies are omitted, and the previous behavior-only policy
retains its original interpretation. A fresh live trial is required to assess it.
The initial signature handoff caused six research-to-code cases to exceed their
16,000-character worker context budget. The corrected policy captures only code
providers' files outside the code consumer's own scope. Research return data is
preserved without an extra API outline. All 43 dependency-context and research
tests passed on Python 3.9 and 3.13 with the original budgets, including mixed
inputs, malformed evidence, immutable recovery and unchanged file permissions.

The fresh trial (`registry-shipping-20261026-074821`) recovered both injected
faults and preserved the baseline, original attempts and confirmed write receipt.
It stopped in the contract cell at the model-turn limit: 26 total requests and
19 tool calls, with peak prompt usage of 9,744 tokens. The generated acceptance
check compared `instance.name` to a string after earlier method calls. Review
approved that invalid check; the worker repeatedly changed validation logic and
could not satisfy it. No cell completed, so this run did not reach dependency
API handoff, final integration or revision.

The precheck now retains an uncertainty hint across calls to declared ordinary
methods. A subsequent bare method/scalar comparison is rejected as ambiguous
before freezing, with a request to call the declared method or explicitly describe
intentional dynamic lookup. Unknown calls and rebinding discard the hint; dynamic
classes remain unknown. This is a bounded check-authoring rule, not proof of
runtime attribute types or general static analysis.
All 78 interface-fact, contract-review and dependency-context tests passed on
Python 3.9 and 3.13 after this change, including correction before freeze without
model review, intentional dynamic lookup, unknown side effects and legacy evidence.

The three-file trial `registry-shipping-20261026-075841` then completed all three
cells and the independent final goal checks. The operator supplied API skeletons,
file scope, requirements and acceptance checks; Gemma supplied the plan, generated
checks, registry discovery and implementation. All cells required registry edits.
The run used 46 model requests and 26 tool calls, with a peak of 9,239 prompt
tokens. A deliberate stale-source hash was rejected, and a fresh controller
recovered the confirmed write after interruption without repeating it. Original
attempts, receipt bytes and the worker baseline were preserved. The three changed
files remained in the isolated goal workspace; they were not integrated into
the baseline. Final results were `"shipping-v1"`, `215` and `230`.

This run also recorded an independent derivation that returned 500 for a weight
quote while its own calculation said 215. The proposed assertion was correct.
The later proposal and derivation agreed, and executed final checks passed.
Mismatch feedback now identifies each value's source and asks the author to
recheck the original requirements; it does not make the model oracle infallible.

The first targeted revision of that completed goal stopped before implementation
after 11 requests and no tool calls (peak prompt: 4,906 tokens). Its exception
check repeatedly defined a unittest class without invoking it. Preflight rejected
the check, but the feedback did not clearly explain that acceptance checks run
as scripts without automatic test discovery. Author/reviewer guidance and the
error now explain this and show an executed module-level exception assertion.
The failed revision, completed parent evidence and baseline remain unchanged.
A new revision trial is required to assess the corrected guidance.

The next revision (`20261026T122037.930279Z`) passed check authoring but stopped
after 27 requests and 20 tool calls (peak prompt: 9,465 tokens). It repeatedly
selected body replacement for an existing similarly named function instead of
adding the new batch function. Ticket checks failed, and the bounded repetition
guard stopped the attempt. Parent evidence and the baseline stayed unchanged.
The worker prompt now distinguishes new definitions from body replacements,
requires exact symbol names and directs discovery toward the required operation
category. A regression check exercises multiline insertion of a new function
while preserving the similarly named existing function. No failed run was reset.

The following revision (`20261026T122829.412890Z`) still stopped after 25 requests
and 17 tool calls (peak prompt: 9,387 tokens). The added operation guidance alone
did not resolve the repeated body-edit selection. Inspection showed that the
worker prompt ended by repeating the parent cell's old imperative, result call
and return value. The new goal and plan were correct, but the presentation put
two assignments in the same handoff. This is a plausible source of confusion,
not proof of the model's reasoning. Revision briefs now retain only historical
identity/receipt references and preservation interfaces, followed by the current
cell's exact task, file scope and return call. Complete prior evidence stays
unchanged in revision.json. A fresh trial is needed to assess this change.

The next revision (`20261026T123603.177458Z`) completed. Gemma added
`weight_quotes(weights)` through a discovered registry insertion operation,
returned `[200, 215, 230]` for `[0, 5, 10]`, and passed the new acceptance checks
and inherited regressions. Only shipping_strategies.py changed; both sibling
files, the parent goal's evidence and the original worker baseline were unchanged.
The run used 23 model requests and 9 tool calls, peaking at 8,673 prompt tokens
and taking 289.56 seconds. The operator supplied the revision goal and independent
acceptance checks, not the plan, implementation or chosen operation. No faults
were injected into this revision. It initially selected body replacement twice,
then discovered and inspected the insertion tool itself. Earlier planning/review
errors and all failed revisions remain retained. This single successful revision
supports the workflow; it does not isolate which guidance change caused success
or establish a success rate.

These focused successful goals do not establish general model reliability. Declaration
maps omit implementation behavior and do not infer dynamic or inherited APIs.
Generated checks and their reviewers can still be wrong. Runtime budgets and
verification gates remain necessary. General multi-file contract assembly, concurrent
controllers and general autonomous OOP construction are separate work.

## 0.17.0 supervision and contract hardening

The supervisor's regression cases cover enrollment, activation caps and cooldowns,
unchanged observations, real OS lock contention, active worker leases, retained
crash receipts, pause/continue/stop, exact question and plan decisions, source
changes, unknown operations, and a pause arriving between scan and dispatch.
Scans validate saved evidence without model requests or publishing missing files.
The release manifest records the exact final package, suite and installation checks.

In the October 24 development trial, a fixture created a single defective cell
and deliberately interrupted the first repair model request before any repair
tool ran. The supervisor withheld dispatch while paused, then resumed the existing
repair after an explicit continue. Gemma 12B completed the repair in four actual
model requests, with peak prompt usage of 3,681 tokens. All 247 additional checks
passed. Only `calculator.py` changed; the original cell history, failure report,
published return value and baseline remained intact. Two subsequent dispatch scans
did not restart the completed goal. Planning and initial defect creation in this
trial were deterministic fixtures; they were not Gemma-generated work.

Independent return-contract derivation, caller-frozen goal checks and bounded
final-gate repair have separate regression coverage and earlier local trials.
These focused results do not establish general model reliability or zero defects.
Execution remains sequential. The supervisor does not resolve ambiguous worker
operations, control a fleet of remote controllers or install an OS scheduler.

## 0.16.1 setup and contract-review maintenance

This release adds Linux host setup, verifies the actual worker deployment before
coding, and separates planning review from acceptance-check review. Regression
checks cover malformed deployments, retained setup attempts, protected source
images, exact literal returns, incomplete acceptance checks and review-version
changes across resume.

In the October 23 pre-release trial after the review fix, Gemma 12B completed the
calculator goal in 14 model requests and 4 worker tool calls. Peak prompt usage
was 3,438 tokens. Independent checks passed inputs -3, 0, 7, -100 and 101; only
`calculator.py` changed and the original worker baseline was preserved. This
trial used Windows with a fresh Ubuntu VM and the local preview source.

The release's `VALIDATION.json` records the separate installed-candidate trial,
final package hashes and exact source commit. Linux host orchestration is covered
by simulated setup tests; the fresh release-candidate VM trial uses a Windows host.
The earlier user-returned Ubuntu trial used an unsigned development preview.
None of these focused trials establishes general model reliability.

Upgrades do not migrate an existing VM or its runs. Keep the old environment,
worker and evidence together and use the matching starter for a fresh deployment.

## 0.16.0 goal-controller iteration

This release adds named model agents, a read-only hive terminal, exact cell
resume and linked revision commands, frozen file scopes, simpler literal-call
return contracts, root-plan review, bounded recovery and closeout receipts.
Execution remains sequential. The terminal observes local saved records; it
does not orchestrate controllers across machines or restart a crashed process.

The October 23 live Gemma 12B/Qwen 9B invoice trial completed a two-cell goal
in 24 model turns and 9 tool calls. Both cells were verified; the original VM
baseline and test fixture were unchanged. Independent checks of the exported
two-module result passed. This is one focused trial, not a reliability estimate.

The signed release's `VALIDATION.json` records the exact source commit, package
hashes and local checks. GitHub CI runs Python 3.9 and 3.13 on Windows and
Ubuntu, plus an installed-wheel job. Inspect those workflow results for the
release commit; the live trial was separate from CI.

## Public release 0.15.1

This release adds publication metadata, the AGPL-3.0-only license, documentation,
CI, a source distribution and a starter bundle. Executable source matches the
frozen 0.15.0 baseline; the sole implementation edit is the version string in
`hornet/__init__.py`. The VM setup helpers and demo are unchanged. Three test
fixtures now resolve temporary paths so Windows short names such as `RUNNER~1`
and their long names identify the same test files and injected failure points.
No checkpoint validation or test assertion was relaxed.

The GitHub checks exercise Python 3.9 and 3.13 on Windows and Ubuntu. A separate
job builds the wheel and source distribution, validates package metadata, installs
the wheel in a fresh environment and runs the tests outside the source checkout.
Check the actual workflow results for the current commit. These checks use
simulated model and worker responses and do not require a live model or VM.

The signed release's `VALIDATION.json` records the local checks completed for its
exact artifacts. Historical live-model evidence below belongs to 0.15.0. A new
model trial was not needed for packaging-only changes and is not claimed here.

## Earlier 0.15.0 first-user trial

See [validation-0.15.0.json](validation-0.15.0.json). The package's 383-test suite
passed in source environments on Python 3.9 and 3.13, a fresh installed-wheel
environment and an Ubuntu worker on Python 3.10.

The first-user walkthrough was then exercised with a fresh Python 3.13 environment
on the existing Windows host and a newly provisioned Ubuntu 22.04 VM. It covered:

- Offline wheel installation, dependency checks and local diagnostics.
- Passing and failing file contracts, and refusal to overwrite an existing project.
- Worker setup refusal for existing configuration and an incorrect image checksum.
- A real Gemma 12B coding goal: 16 model requests, 5 tool calls, peak input 3,084
  tokens, only `calculator.py` changed, checked values `[-6, 0, 14]`.
- Preservation of the original worker baseline and inspection of the completed goal.
- Refusal to resume an already completed goal, with its checkpoint unchanged.

The initial report harness incorrectly expected completed-goal resume to succeed;
that report error was corrected and the refusal rechecked. Separate earlier Gemma
and Qwen trials exercised durable questions and explicit answer/resume behavior.

## Limits

One small live coding goal does not establish general model reliability. Generated
contracts and reviewers can be wrong. Unknown operations stop for inspection;
resume is not a guarantee against every power-loss or external-effect scenario.
The dedicated VM is the execution boundary, and goal directories share its account.
Concurrent workers, a conversational interface and universal zero-drift guarantees
are not part of this release. Review exported changes before integration.
