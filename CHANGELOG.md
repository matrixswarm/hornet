# Changelog

## 0.17.0 — 2026-10-24

Opt-in periodic supervision resumes explicitly enrolled goals at validated
boundaries. Scans use no model requests. Each activation has a retained receipt,
frozen lifetime cap and cooldown; worker leases, pauses, human decisions,
unknown operations and existing budgets remain authoritative. A second boundary
check runs inside the execution lease. Watch mode prints changed observations;
single scans support external scheduling. The hive terminal shows enrollment and
activation counts. No OS scheduler is installed automatically.

Timed-out commands on Windows terminate their process tree before returning,
including interpreter children started by virtual-environment launchers. An
unconfirmed termination reports an error; it cannot become a successful check.

New goals independently derive each selected code call's expected return before
freezing its schema. The derivation sees the requirements and call inputs, not
the proposed schema or author rationale. The controller rejects incompatible
types, nested values and enums, returns specific feedback within the existing
budget, and retains rejected proposals. Uncertainty cannot become approval.
Saved approvals require matching evidence on resume; older goals retain their
original versioned review policy.

Autonomous goals and revisions accept caller-supplied `--acceptance-file` checks.
The controller freezes them before planning, uses them for the final goal gate,
and records their provenance in completion evidence. Cell checks still run;
successful cells cannot bypass a failed goal check. Resume uses the frozen copy,
and revisions retain inherited regression checks alongside any new criteria.

New goals can append a bounded repair job when a failed whole-goal or inherited
regression check has one unambiguous code-cell owner. `--max-goal-repairs` defaults
to 1 (0 disables, 2 allows two jobs). Original jobs, returns, checks and failures
remain intact; repairs preserve published dependency values and consume the
remaining shared budget. All final gates rerun before completion. Hive terminal,
questions and exact-cell resume follow the active repair. Ambiguous ownership,
unknown outcomes and exhausted budgets stay stopped for inspection.

## 0.16.1 — 2026-10-23

The worker provisioning helper supports Linux hosts as well as Windows, with
platform-specific tool discovery and private-file permissions. Both hosts use
the same fresh Ubuntu VM recipe, pinned SSH entry point and baseline source.
VirtualBox clones a temporary copy so the verified download remains unchanged.
The Linux launcher verifies the bundled contents, installs the included setup
dependency offline, and retains separate records for each readiness probe session.

Worker preflight verifies the fixed account/root, actual Git HEAD, matching
worker source and root-owned protected deployment before a coding turn. Empty
Git directories and manually created ready markers no longer establish readiness.
Misplaced model settings and API keys report the correct configuration location.
Missing operation workspaces return a structured refusal. Recovery continues to
refuse changed workspaces; setup repairs do not clear or replay failed runs.

Contract review distinguishes a cell's single typed return call from the separate
acceptance checks covering its required examples. Review instruction hashes keep
fresh proposals from reusing judgments made under older instructions; existing
frozen contracts and historical receipts remain intact.

## 0.16.0 — 2026-10-23

Named hive agents may be assigned to unfinished cells, with local or compatible
remote model endpoints and shared inference claims. The read-only hive terminal
shows goals, cells, recorded activity and an inverted tree; exact cell commands
resume an unfinished cell or create a linked revision from a completed one.

Autonomous goals can bind exact production files, use simpler literal-call return
contracts, request review of a root plan, and retain bounded recovery attempts.
Closeout receipts record verification and lease release. The controller keeps
failed attempts and frozen checks across resumes. Model roles and cells still run
sequentially on one worker; automatic crash restart and concurrent execution are
not included.

## 0.15.1 — 2026-10-22

First public GitHub packaging release: focused README, first-goal walkthrough,
license and project metadata, source distribution, CI and signed release assets. Windows test fixtures resolve temporary path
aliases consistently; Linux CI is pinned to Ubuntu 22.04 for Python 3.9 support.
The executable implementation is unchanged from the validated 0.15.0 release,
apart from its version string. Legacy experiments and local handoff files are
excluded from the public repository. Earlier signed 0.15.0 artifacts are preserved.

## 0.15.0 — 2026-10-22

Durable questions bound to exact goals, cells and checkpoints; separate answer and
resume commands; no inference while waiting; immutable question/cancellation
history. First-user coding trial completed with Gemma 12B in a new Ubuntu VM.

Earlier iterations introduced durable goals, typed cell returns, bounded context,
retained attempts, nested delegation, contract review, operation receipts, separate
goal workspaces and targeted revisions. Concurrent workers and a chat interface
remain future work.
