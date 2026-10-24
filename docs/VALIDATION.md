# Validation scope

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
