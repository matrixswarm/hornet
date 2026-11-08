# Goals, pointers, and safe continuation

Hornet now separates three things: the user's goal, the agent's proposed subgoals, and the controller's execution checkpoints. None of these is proof that a change is correct merely because the model says it is.

## Normal flow

1. Describe the mission and run it.
2. The agent proposes a bounded goal tree, breaking work into small actionable leaves with file scopes, dependencies, and observable success criteria.
3. By default, review that plan and choose **Approve plan & continue**. The **Review agent plan before execution** checkbox in Mission options makes that review optional. Turning it off does not turn off goal decomposition or scope checks.
4. Hornet works through actionable leaves in dependency order. Each leaf gets its own scoped model request, durable proposal checkpoint, and tagged revision publication. On resume, verified completed leaves are reused rather than re-requested. Each atomic edit must name an approved leaf goal. Hornet checks its file scope and prerequisite ordering, then records actual edit receipts and file hashes.
5. Review the resulting candidate and explicitly accept it. Plan approval is not final acceptance. Skipped or failed tests are never represented as successful verification. Leaf evidence remains pending human review or not demonstrated.

The planner is bounded to 40 total nodes, 20 actionable leaves, and five levels. It is instructed to decompose recursively within those limits. Oversized or invalid plans stop instead of silently increasing scope. This is not an unlimited autonomous recursive-agent system.

## Revision snapshots

Every newly published revision has a fresh `.hornet-meta/goals.json`, its own goal identity, parent-revision link, and goal/pointer snapshot. The snapshot binds source, project-understanding, and relevant metadata hashes. Tagged mission revisions also contain the operation graph at publication, including the agent's proposed subgoals.

Snapshots are written while staging a new revision and are not updated afterward. Acceptance is recorded separately. Older revisions are not rewritten or falsely backfilled. Editing a revision outside Hornet invalidates its saved evidence and blocks reuse.

## Active working memory

Understanding work lives under the project's `working_memory/understanding/<operation>/`. Mission checkpoints live under `missions/<mission-id>/`. A single atomic `state.json` is authoritative. Each transition has an immutable sequence snapshot; the mirrored `goals/<goal-path>/_goal.json`, `pointers/<goal-path>/_pointer.json`, and `current.json` are rebuildable navigation views.

An explicit Start over gets a new understanding operation identity. Resume reuses the existing identity and verified saved file parts. Invalid summaries remain incomplete and can be retried; they are not marked successful merely to advance the pointer.

## Resume after interruption

Use **Project actions > Resume interrupted mission** from the original base revision. Hornet verifies the mission, model settings, source and understanding hashes, cached result hashes, and checkpoint ownership before continuing. Completed safe stages are reused. A saved plan awaiting approval still requires approval after restart.

Publication intent is recorded before a new revision is published. If interruption occurs after publication but before its result was recorded, a unique tagged revision with matching evidence can be reconciled without creating another copy. Ambiguous or mismatched receipts stop recovery.

An interrupted VM command with no trustworthy completion receipt is an uncertain effect. Hornet refuses to rerun it automatically. Inspect the VM and saved receipts before intentionally starting new work or performing evidence-backed recovery. A pointer alone never authorizes retry.

A failed or cancelled owned checkpoint retains its path, attempt identity, input fingerprint, and stop reason with an explicit `failed` or `cancelled` status. It is not successful work and no longer claims to be running. Read-only work can be reclaimed on explicit resume; stopped effects still require verified recovery evidence. Older interrupted records may retain `running`, which is not proof that a worker is live.

Cancellation retains previously saved progress and prompts. Late model responses are ignored. The model server may continue processing its abandoned request; Hornet does not send a server-wide stop. An active local/VM step returns at its safe boundary.

## Agent briefing

Every model request receives a fresh bounded operating contract plus the main goal, current subgoal/checkpoint, and explicit available-tool capabilities. Exact editing schemas accompany editing requests; planning requests do not execute tools. The agent is told to work methodically while choosing architecture appropriate to the task, rather than following a rigid design-pattern template. It requires structured atomic edits, evidence-based claims, scope preservation, and explicit final review. Source files, comments, project descriptions and logs remain untrusted data. The coding default is one class per file and separate responsibilities, with justified tightly coupled exceptions; no unnecessary SQLite is introduced.

## Limits

This is durable checkpointing, not a guarantee of exactly-once external effects or semantic correctness. Human review remains essential. Initial work has some additional durable-write overhead; the main benefit is safer recovery and avoiding repeated verified stages. The live model and VM remain responsible for their actual execution times.


## Revision journal

New missions freeze a bounded revision journal in their saved identity. Planning,
leaf edits, repairs and changed-file descriptions receive it as contextual data,
not instructions or permission. Entries name prior goals, actually published file
changes, recorded failures, check coverage and acceptance state. Model summaries
are explicitly unverified. Selecting a path is not evidence it was read or changed.

The journal follows the selected revision ancestry and working session, not unrelated
branches. Recent completed leaf publications are added deterministically as the
mission progresses. At most six entries and 12 KiB enter a request; omitted history
is explicit. Full evidence remains in existing mission and revision records.

Restarting the app does not call the model or execute work. The next authorized
request uses saved context. Resume reuses the original frozen journal; older
missions without one keep their exact checkpoint inputs. No live mission is
migrated or restarted. Published, syntax-checked, functionally tested and accepted
remain distinct states. A journal never turns an attempted action into success.

## Read-only plan context

New plans may list existing reference sources in `context_files`, separate from
`existing_files` and `new_files`, which authorize edits. All three lists share
the 20-path bound. Every editable path still needs an actionable leaf; references
do not. The reference text is provided separately to each leaf and repair, and
registry edit guards reject reference paths. Review displays the reference list.
Saved plans without this field retain their original scope and checkpoint inputs;
no saved plan is rewritten or automatically assigned to a guessed goal.
