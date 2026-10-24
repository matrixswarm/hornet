# Root plans, bounded recovery and closeout

Available in 0.16.0 and later. See the README for installation instructions.

For the agreed hierarchy of overall goals, branch responsibilities, cell
contracts and working attention, see [decision authority and working context](DECISION-CONTEXT.md).
That design note identifies planned additions separately from current behavior.

## Supplied whole-goal acceptance checks (unreleased)

`autowork` and `revise` now accept `--acceptance-file`, using the same JSON format
as standalone `work`. A user or supervising program can supply the final goal
criteria before the working model plans or edits anything:

```console
hornet autowork --worker-config worker.json --agent gemma --goal-file demo-goal.txt --scope-file calculator.py --acceptance-file checks.json
```

Save this example as `checks.json` in the project root:

```json
{
  "schema_version": 1,
  "checks": [{
    "id": "integer-double",
    "description": "Return exactly twice each integer, including negative and zero inputs",
    "paths": ["calculator.py"],
    "timeout": 10,
    "python": "from calculator import double\nfor value in (-100, -3, 0, 7, 101):\n    actual = double(value)\n    assert type(actual) is int\n    assert actual == 2 * value"
  }]
}
```

The file has 1-8 checks, each with a unique ID, description, declared production
paths, Python code and a 1-30 second timeout. The existing 32,000-byte input
limit applies. These are executable checks: use code you trust to run inside
the worker VM. Syntax and shape validation do not establish semantic coverage.

Hornet copies the checks to `operator-acceptance.json` and binds their digest
before the first model request. The planner must assign the named files to code
cells. When `--scope-file` is supplied, conflicting check paths fail before
worker contact or inference. Agents receive relevant descriptions and file
names; the complete check source remains in the controller's saved record.

These supplied checks replace generation of the overall goal checks. Cell
verifiers still generate and review their own checks and typed results. A green
cell cannot bypass a failed whole-goal check. Completion binds both the supplied
contract and the actual final verification report. Without this flag, existing
generated goal checks and earlier saved runs retain their behavior.

Resume uses the frozen copy even if the original input file changes or vanishes.
Changing the saved copy or losing its checkpoint binding stops resume. A revision
inherits its parent's final goal checks. Supplying a new acceptance file for the
revision adds a new goal gate; it does not remove inherited regressions.

Failed final checks remain under the attempt's `goal-checks.json`; the controller
does not mark the goal complete or silently relax the criteria. Final integration
failures currently require inspection: this change does not add an automatic
repair route from the final goal gate back into already completed cells. A
known failed check and an interrupted check with an unknown outcome remain
different states; uncertain operations retain their existing inspection stop.

This is a foundation for a quality cycle: define the outcome, work within the
contract, independently execute the criteria, preserve the evidence, and make a
bounded follow-up. It verifies the supplied cases. Missing requirements and
incorrect checks remain possible; continuous improvement must include improving
the criteria through an explicit new contract, with earlier evidence retained.

## Bind the goal to exact production files

When the requested files are known, list them before planning:

```console
hornet autowork --worker-config worker.json --agent gemma --goal "Implement the invoice calculator" --scope-file tax_math.py --scope-file invoice.py
```

Repeat `--scope-file` for each workspace-relative production file (1-12 distinct
paths). The saved `file-scope.json` binds the exact set to this goal. Every file
must belong to a code cell; the plan may not move a file into a new directory or
add another production file. The model's intake format offers these paths as
choices, and controller validation checks the submitted plan before review.
Changing the scope requires a new goal; a resume keeps its saved file set.

An autonomous goal may use `--max-turns` up to 60 across all roles and cells.
The default remains 30. Each coding cell keeps its own smaller turn limit, and
the goal retains its 30-minute execution limit. Standalone coding jobs still
have a maximum of 30 turns.

The goal verifier's check paths are limited to declared source files. Each cell
verifier's check paths are limited to that cell's files. These constraints stop
path drift in generated check metadata; checks still have to execute and pass.

## Simpler model submissions

New goals offer one familiar Python call and a native schema object for intake
and recon return contracts:

```json
{
  "path": "parity_cell.py",
  "call": "is_even(3)",
  "schema": {"type": "boolean"}
}
```

The model writes each structure once. Calls such as `add_tax(350, 8)` or
`quote(items=[100, 250], rate_percent=8)` supply concrete inputs. Hornet parses
the function name and literals without executing the text, then encodes the frozen storage fields
(`args_json`, `kwargs_json`, and, for version 2 plans, `schema_json`) itself.
Existing saved contracts, including the earlier native `args`/`kwargs` format,
remain readable. Mixed formats,
truncated responses, duplicate JSON keys and unsupported schema fields are
rejected; the controller does not guess how to repair them. Calls accept only
literal JSON-compatible values. Expressions, nested calls, unpacking, duplicate
dictionary keys and nonfinite numbers are rejected. Object return schemas must
state their properties, required keys and extra-key policy explicitly.

Argument payloads retain the existing 500-character limit per field. Larger
outputs should use the existing artifact references. This format does not
introduce character-counted blocks or ask the model to calculate lengths.
The goal saves its format policy so a resume preserves the same interface.

Before new generated checks are frozen, a Python symbol-table preflight rejects
obvious undefined globals, including assignments accidentally swallowed by an
escaped newline in a comment. Checks must use explicit imports and assignments.
The original failed submission is retained and returned to its author; source
is never silently rewritten. This check does not prove control flow or runtime
correctness, which still require executed verification.

## Independent return-contract check (unreleased)

For new goals, each selected code call is checked before its contract is frozen.
A fresh model context receives the original requirements and call inputs, with
the proposed return schema, cell task and review verdict omitted. It derives:

- A concrete JSON value when the requirements establish it.
- Only the JSON return type when the exact value depends on runtime data or an
  unresolved cell dependency.
- Uncertainty when the return cannot be established, including invalid calls.

Hornet mechanically compares that derivation with the proposed schema. A known
value must satisfy its type, nested structure and enum. A type-only answer checks
the root type; it does not establish nested properties or an exact result.
For example, an integer-returning call cannot use a schema mapping function
names to results merely because the cell implements multiple functions.

Contradictions and uncertainty return feedback to the author within the existing
role and goal budgets. The general reviewer cannot override a failed comparison.
Rejected receipts remain on disk; no code is written while planning is rejected.
An unchanged call can reuse its independent derivation when the author corrects
the schema. Changed inputs, requirements or derivation instructions get a new
cache identity. Saved approvals are rechecked on resume.

This adds a bounded model request for a new planning proposal's calls and uses
the existing context limit. It does not run the proposed functions or infer an
unknown dependency value. Research-only returns and a recon decision to execute
an existing contract introduce no new code call. Previously frozen goals keep
their original policy; this does not retroactively certify their contracts.

The derivation still depends on model understanding. Executed acceptance checks
remain necessary; agreement between model contexts is not proof of correctness.

## Choose the planning policy

The default is `auto`: the planning agent chooses execute, delegate, or
delegate-and-work, the normal contract validation runs, and the controller
continues. Choose `review` when you want a decision before implementation:

```console
hornet autowork --worker-config worker.json --agent gemma --goal "YOUR GOAL" --plan-mode review
```

The result includes an exact run directory, draft path and revision number.
It is saved with status `waiting_for_plan_approval`. The process releases its
leases and exits; there is no approval timeout or background inference loop.
Calling `autoresume` while it is pending returns that same wait without
contacting the model or worker.

```console
hornet plan show coding/recipes/EXACT_RUN_ID
hornet plan approve coding/recipes/EXACT_RUN_ID --revision 1
hornet autoresume coding/recipes/EXACT_RUN_ID
```

To request changes instead of approving:

```console
hornet plan revise coding/recipes/EXACT_RUN_ID --revision 1 --feedback "Keep the public API unchanged and separate the parser from the renderer."
hornet autoresume coding/recipes/EXACT_RUN_ID
```

The next draft has a new revision and needs its own decision. Earlier drafts,
feedback, planning contexts and model usage remain on disk. A stale revision or
contradictory second decision is rejected. Approval is bound to the exact draft
and its original source snapshot. Execution still validates file scopes,
dependency order, typed returns and frozen checks.

### A model can supervise the plan

```console
hornet autowork --worker-config worker.json --agent gemma --approver qwen --goal "YOUR GOAL"
```

`--approver LABEL` implies review unless `--plan-mode` is explicit. The label
must exist in the saved Hive registry. That supervisor receives the original
goal and draft in a fresh review context. It can approve or request specific
changes. Uncertainty requests revision; it never counts as approval. The
default cap is three drafts, within the existing total model-turn budget.
The ordinary independent contract checks remain active too.

This is a root-plan decision. Child recon can split work inside its existing
contract and file scope without asking for another root approval. Changing the
overarching goal or widening file scope requires a new goal or revision.

## Freeze project defaults in hive.json

Add these optional top-level fields alongside `agents`, `roles` and `cells`:

```json
"planning": {
  "mode": "review",
  "approver": "user",
  "max_drafts": 3
},
"recovery": {
  "max_handoffs": 1,
  "agents": ["qwen"]
}
```

Use `"approver": "qwen"` for an agent supervisor or `"mode": "auto"` to
proceed without a separate root decision. CLI options override these defaults
for new goals. Each goal freezes its policy and registry; editing the project
file does not silently change a running goal. `max_drafts` accepts 1–5.

## Recover a stuck coding cell

New autonomous code cells detect a narrow repetition pattern: three identical
read, search, check or edit requests within six observed operations, without a
source change. Existing repeated verification failure detection also remains.
These signals identify repeated behavior; they do not prove that a model has
exhausted every useful approach.

After a confirmed stop at a safe boundary, the controller may give the same
cell a fresh context and an eligible fallback model:

```console
hornet autowork --worker-config worker.json --agent gemma --recovery-agent qwen --max-handoffs 1 --goal "YOUR GOAL"
```

- Default: one handoff per cell; use 0 to disable automatic handoffs, or 2 for
  two attempts. Repeat `--recovery-agent` for an ordered fallback list.
- The first configured label different from the current agent is selected.
  With no alternative, the same model gets one fresh context.
- The original goal, file scope, checks, source hashes and typed return stay
  fixed. Resume uses the same child job so earlier edits and attempts remain
  part of its provenance.
- Child allowances may grow within the goal's remaining shared budgets and
  the existing hard limits. A handoff never resets usage or the repair count.
- The receipt points to the original failed attempt. Undispatched calls from
  that response are archived and discarded before the new model responds.
- Unknown in-flight mutations or verification, external source changes,
  cancelled work, pending questions and busy agents are not automatic retries.

This policy covers coding cells. Planning/contract roles retain their own
bounded validation attempts. It does not load models, guarantee success,
create new workspaces for failed attempts, or enable parallel execution.

## Repair a failed final goal gate (unreleased)

New goals allow one bounded repair job after a confirmed failure in whole-goal
acceptance or inherited revision checks. Set `--max-goal-repairs 0` to stop for
inspection, or `2` to allow a second job. This setting is frozen for the goal;
older goals without it keep their original behavior.

The controller routes the failed checks only when all their declared paths
belong to one completed code leaf with no competing owner. It appends a repair
job under `goal-repairs/NUMBER/`, attached to that cell. The original cell job,
work packet, return manifest, failed report and acceptance criteria remain intact.
The repair receives the current source and compact failure evidence, and may
edit only the owner's existing files.

Repair checks retain every original cell check and add the failed goal checks.
They also require the exact value already published to dependent cells. A repair
cannot silently change that value or weaken a contract. After it succeeds, the
controller reruns all cell checks, whole-goal checks and inherited regressions.
Completion evidence and the file index include the appended repair receipts.

Repair requests and tools count against the goal's remaining shared budgets;
they do not reset usage. The usual per-cell limits still apply. Ambiguous or
multiple owners, missing owners, more than eight combined checks, exhausted
budgets, transport errors, timeouts and unknown verification outcomes stop for
inspection. A failed final cell recheck also stops; this first version routes
only failed whole-goal and inherited gates.

Hive terminal marks the affected cell as repairing while retaining its earlier
verified history. Questions stay pending without a deadline. `autoresume RUN`
or `hive resume-cell RUN CELL_ID` resumes the same repair job at a safe boundary.
A completed repair interrupted before receipt publication can be adopted without
another model request. Failed repair attempts remain available for inspection.

## The controller closes the goal

The controller rechecks the final cells and whole-goal acceptance, validates
return manifests, confirms all children are finished and checks for unresolved
operations. It writes `closeout-ready.json` while it still owns the worker lease.
After its lease and activity contexts exit, it writes `closeout.json` and returns
the release receipt. Hive terminal shows `closeout_pending` if verification
finished but this final receipt is missing.

If interrupted between verification and closeout, `autoresume` validates the
saved evidence and source, then finishes closeout without rerunning the models
or successful cells. The workspace, failed attempts and shared model server
remain available. Closing this goal releases its leases; it does not shut down
the VM or unload models used by other work.

Passing frozen checks establishes those tested behaviors. It does not establish
that every possible requirement or edge case has been verified. Independent
acceptance checks and human review remain useful for release decisions.

## Periodic supervision

The opt-in [supervisor](SUPERVISOR.md) observes saved goals without inference and
can resume known safe boundaries within frozen limits. A retained activation
decision precedes dispatch; the controller rechecks the boundary inside its
worker lease. Pending human decisions, active workers, unknown operations and
operator stops remain authoritative. This layer schedules existing controllers;
it does not rewrite contracts or replace their final completion gates.
