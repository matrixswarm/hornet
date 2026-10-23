# Changelog

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
