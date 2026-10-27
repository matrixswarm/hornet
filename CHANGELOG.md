# Changelog

## 0.18.1 — 2026-10-27

Worker setup diagnostics maintenance release.

- Identify missing or invalid `identity_file` and `known_hosts_file` fields;
  explicitly reject the mistaken `identity` and `known_hosts` spellings.
- Report missing credential paths on the controller, including resolved paths,
  and explain that copying keys into the VM does not fix local configuration.
- Reject an unprovisioned account name before SSH dispatch. Preserve the existing
  required `hornet` account, pinned host checking and connection binding format.
- Report unreadable host pins and path-resolution errors without exposing file
  contents. Preserve current-directory resolution for relative credential paths.
- Document `worker-status`, JSON/text errors and shell-wrapper exit codes; add
  regressions for the reported setup loop and valid configurations.
- Refresh the starter version. No automatic worker or saved-run migration;
  tagged-ticket, math and restart-controller experiments remain outside this release.

## 0.18.0 — 2026-10-26

Registry integration for coding cells.

- Include the inherited-repair test's final-check evidence in failure output,
  before temporary fixtures are removed, to diagnose intermittent failures.
- Include completed direct dependencies' declared behavior in consumer task
  packets, deduplicating verbatim requirements without expanding histories or
  file permissions. Add source-bound Python signatures and method kinds without
  implementation bodies. Freeze the policy and preserve older saved packets.
- Flag ambiguous bare method/scalar assertions after earlier declared-method calls
  before freezing generated checks; require an explicit return call or dynamic
  lookup without claiming unknown runtime types are proven.
- Package the repaired capability catalog, explicit aliases, native tools and
  stdio MCP gateway without prototype paths, databases or conversation files.
- Offer 17 reviewed transformations and two recipes to new coding cells, with
  repeatable `--registry-asset` selection and exact cell file scope.
- Require contract inspection, guard source versions, prepare isolated candidates
  and commit through the existing durable worker operation journal.
- Retain registry receipts in return manifests, require committed-file readback,
  stop repeated unchanged failures, and preserve operation recovery across a
  lost reply. Bind resumed cells to their registry implementation and permissions.
- Preserve canonical IDs, hashes and receipt references when active context is
  shortened. Document the agent procedure and adapter extension boundary.
- Add `--registry-only` to require discovered registry operations for cell edits,
  retain that policy through repairs/revisions, and reject direct-write bypasses.
  Give unmatched searches operation-word guidance and include registry calls in the
  existing stagnation detector.
- Supply source-bound Python API declarations to new registry-only check authors
  and their reviewers, preserving the distinction between module functions and
  instance methods without exposing implementation bodies as expected answers.
  Retain this evidence and validate its binding on resume.
- Remove direct-edit guidance from registry-only worker prompts. Build the
  execution menu from the two latest inspected tool contracts, with exact
  canonical IDs and argument schemas; rebuild it from retained observations on
  resume. File scope, hash guards and operation receipts remain authoritative.
- Distinguish adding a function from replacing an existing body in worker guidance;
  preserve exact requested symbol names and discover the appropriate operation.
- Keep historical revision tasks and return values in retained provenance;
  present the current cell's task, file scope and exact return call last in the
  worker handoff so the prior assignment does not compete with the follow-up.
- Support job-bound short `read_ref` handles in cell registry writes while keeping
  full hashes in durable operations. Reject accidental method signatures supplied
  as bodies, and clarify method calls versus property access in check review.
- Offer exact current file versions from confirmed reads in registry execution
  menus. Present parsed check code to reviewers
  and recommend independent standard-library exception assertions to check authors.
- Reject direct scalar assertions on known ordinary method objects before
  freezing generated checks, using the captured API declarations. Preserve the
  rejected submission and allow correction within the existing role budget.
- Prepare and parse Python bodies automatically in method insertion; report
  line-specific indentation errors before writing. Add the same optional pure
  body preparation to `files.text.indent` and document it in the formatting recipe.
- Count equivalent confirmed read handles as the same failed edit against the
  same bytes; rereading cannot reset the identical-failure budget.
- Split capability search labels at underscores, hyphens and dots, rank exact
  IDs first, and stop offering unrelated editors after an unmatched search.
- Preserve multiline string values and the target's indentation during method
  insertion; compare the complete parsed structure before committing.
- Recognize standard-library exception-only acceptance checks in both cell and
  goal validation. Identify the rejected check and explain assertion syntax and
  fixture isolation; preserve independent review and executed verification.
- Explain that generated acceptance scripts do not automatically discover
  unittest/pytest test classes. Guide authors toward executed assertions and
  distinguish proposed answers from independent derivations in mismatch feedback.
- Read committed registry files back automatically, retaining exact bytes and
  hash-bound evidence before reporting success. Interrupted readback recovers
  the original write receipt without repeating the write or spending a model turn.
- Treat constructors, inheritance, decorators and dynamic attribute lookup as
  uncertain when checking method/property assertions; discard bindings after
  unknown side effects.
- Give independent result derivation bounded call-site context: imported names,
  literal receiver construction and preceding calls on that object. Keep proposed
  answers hidden and preserve the interpretation of older review receipts.
- Run ready caller-supplied goal checks at upstream cell boundaries, so a consumer
  is not started with a dependency that already fails its known contract. Freeze
  the routing policy and original authored checks, retain the complete final gate,
  and recover interrupted check preparation without changing saved evidence.


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
