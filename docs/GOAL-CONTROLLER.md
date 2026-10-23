# Root plans, bounded recovery and closeout

Available in 0.16.0 and later. See the README for installation instructions.

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
