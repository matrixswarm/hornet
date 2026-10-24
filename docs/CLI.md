# MatrixSwarm Hornet CLI

Portable project state, ATS file contracts, completed-goal archival, and
model-assisted article recipes. Python 3.9 or later; no runtime dependencies.
Creator and project lead: Daniel F. MacDonald. See ../AUTHORS.md for Qwen, Gemma,
and Codex contributions.

## Patient questions (0.15.0)

A coding worker or an intake/recon agent can use `ask_user` when a required
decision is missing. Hornet saves the question with its goal, cell, tool call,
source checkpoint and contract binding, then returns `waiting_for_user`.
This is a successful pause, not completed work. The process can exit; no model
requests, worker connection, waiting deadline or execution-budget time are needed
while you are away. Repeated resume attempts without an answer keep waiting.

```console
hornet questions coding/recipes/RUN_ID
hornet answer coding/recipes/RUN_ID q-EXACT_QUESTION_ID --text "Use the example configuration."
hornet autoresume coding/recipes/RUN_ID
```

For a standalone `work` job, use its run directory and `hornet resume RUN`.
Answers can use `--text-file path/to/reply.txt`; free text is accepted even when
choices are offered. `hornet answer RUN QUESTION_ID --cancel "reason"` records
a cancellation and keeps that work stopped. Cancellation is final for that
question; start a revised task to replace it. An answer is saved before any
execution, and the separate resume command gives you control over when to proceed.

An exact repeated reply is idempotent while the question is pending. A different
reply cannot overwrite it. An old question, another goal's ID, a changed source
checkpoint or a changed answer receipt is rejected. `control --continue` and
ordinary inbox messages cannot answer a required question. Prior questions and
answers remain visible. Answers remain in the bounded working context and cannot
change frozen acceptance checks, permitted files or budgets. Eight questions per
cell/role, 2000 characters per question and 4000 per answer are the upper bounds;
the configured context budget still applies to retained clarifications.

This release provides CLI question handling. Conversational goal selection and
a chat interface remain future work. Journals share the controller account and
provide restart evidence, not a separate security boundary. Checkpoints recover
process interruption; this is not a universal power-loss or external-effect guarantee.

## Separate coding goals and follow-up revisions

Each new `autowork` goal gets its own source directory in the dedicated VM.
Completed and interrupted goals keep their files, evidence and attempt history.
Select a saved goal by its run path; there is no global active-goal pointer.

```console
hornet autowork --worker-config coding/worker.json --model google/gemma-4-12b --goal "Implement the requested function in module.py"
hornet goals
hornet goal-files coding/recipes/RUN_ID
hornet autoresume coding/recipes/UNFINISHED_RUN_ID
hornet revise coding/recipes/COMPLETED_RUN_ID --file module.py --goal "Add the requested compatible behavior; preserve existing behavior"
```

`revise` makes another copy and records its parent. It selects the target files
and declared dependent cell scopes, then runs new checks and retained regression
checks. Repeat `--file` for up to four targets. The index describes declared cell
dependencies; it cannot infer every consumer. Changing an existing contract in a
way that breaks inherited checks stops the revision for review.

The original baseline and parent goal are preserved. Source copies have their
own Git baseline and retain directory structure and executable permissions.
`file-index.json` maps file hashes to cell IDs, changes and return manifests;
`goal-files` rebuilds it from validated evidence. Completed work is still exported
for review rather than automatically merged into the original project.

Execution remains sequential on the dedicated worker. Goal directories share
the VM's account, so they provide source separation rather than a security
boundary for arbitrary Python. Old runs retain their original workspace binding.
Unknown in-flight mutations and incomplete workspace preparation require inspection.
Large workspace replies are fetched in chunks and checked against their SHA-256
before use, up to the existing 1.5 MiB worker reply limit. Individual request
payloads are bounded to 87,500 characters; prefer small edits to large replacements.

## Install

From this directory:

```console
python -m pip install .
hornet --version
```

The distribution is `matrixswarm-hornet`. Both `hornet` and
`matrixswarm-hornet` console commands are installed. `python -m hornet` works
too. Package builds contain the CLI and its bundled recipes; project state,
transfer conversations, and the legacy `bin/` experiments are excluded.

## Start a project

```console
hornet init --name my-project --path ./my-project
hornet --root ./my-project status --verbose
hornet --root ./my-project recipe list
hornet --root ./my-project recipe show article
hornet --root ./my-project doctor
```

Initialization preserves existing files, including README and local recipes.
Repeat it with the same name to fill missing scaffold files. It creates a
`.hornet.json` marker, goals/pointers folders, recipes, and output directories.
Existing Hornet projects with `continue/goals/` are recognized without migration.

`doctor` checks Python 3.9+ and the goals, pointers, and recipes directories.
It is read-only and local: it does not check model or network readiness.
Missing directories or files occupying those paths produce exit code 1.

Use `--root` / `-C` before or after the command, or `HORNET_ROOT`. Otherwise
Hornet discovers the nearest project above the current directory. All command
file paths are relative to that root and must remain inside it. Status reports
direct child goals with state files; freeform legacy prose is reported as
unknown unless it has an explicit `Status:` or `[STATUS]` record. Active pointers have an
explicit active/locked status; idle pointers are counted separately.
Status normalization collapses whitespace, hyphen and underscore runs, so a
value such as `in--progress` is counted consistently as `IN_PROGRESS`. Falsey
and blank values are `UNKNOWN`. Status inspection remains read-only.

## Verify a contract

Save `contract.json` in the selected project:

```json
{
  "target_files": {
    "README.md": {"exists": true, "contains": ["my-project"], "min_size": 20},
    "obsolete.txt": {"exists": false}
  }
}
```

```console
hornet --root ./my-project verify --contract contract.json
hornet --root ./my-project verify --contract contract.json --json
```

Requirements: `exists`, `contains` / `not_contains` (Python regular expressions),
`min_size` / `max_size` (bytes), and `sha256` (64 hex digits). Target paths are
relative to the project, regardless of where the contract is stored. Contracts
require at least one target. Invalid contracts and unreadable files are errors.
Verification reads files; it never executes commands embedded in a contract.

Exit codes: **0** success, **1** valid contract failed its checks, **2** invalid
arguments, configuration, file, or operation error, **130** interrupted.
`--json` produces a JSON result, including operational/argument errors.
Help and version requests produce their normal text output.

## Archive completed goals

```console
hornet --root ./my-project archive --all --dry-run
hornet --root ./my-project archive --node my-goal
```

Only direct children of `continue/goals` with an explicit completed status are
eligible: COMPLETED, COMPLETE, SUCCESS, SUCCEEDED, DONE, or VERIFIED (case
insensitive). JSON `state.json`, `state`, or `state_stack.json` and plain-text
states are supported. A substring such as `unsuccessful` is never completion.
Goals with lock files, active pointers, or linked paths are refused. The full
plan is checked before moves, and destinations under `archive/v9/completed/`
get unique names. Failed batches attempt to restore earlier moves and report
any paths needing recovery. Session records are never given fabricated hashes.

Archive operations share an exclusive `.hornet-operation.lock`. Historical
tools do not participate in this lock; stop other writers before archival.
After a process crash, review the state before removing a stale operation lock.

## Generate using LM Studio or another compatible endpoint

Load a model and enable its local server, then supply its model ID:

```console
hornet --root ./my-project generate --topic "Restore drills" --model YOUR_MODEL_ID --base-url http://127.0.0.1:1234/v1
```

Environment alternatives: `HORNET_MODEL`, `HORNET_BASE_URL`, and optionally
`HORNET_API_KEY`. API keys are never saved to project files. Local loopback
requests bypass unrelated proxy settings; authenticated remote endpoints need
HTTPS. Requests do not follow redirects. There are no retries or recursive
refinement loops. Defaults: 60-second socket timeout, 2048 output tokens, and a
5 MiB response cap. `--timeout` and `--max-tokens` adjust request limits.
The timeout is a socket-operation timeout, not a guaranteed overall deadline.
A response that reports token truncation is an error and is not saved.

Without a model, explicitly request an **offline outline**:

```console
hornet --root ./my-project generate --provider template --topic "Restore drills"
```

Default output: `output/article_Restore_drills.md`. Use `--output path` to
choose another project file and `--force` to replace an existing output.
Generated model drafts still need editorial review. Offline mode identifies
itself in the output and command result; no detector accuracy is claimed.

## Recipes and text cleanup

Project recipes in `recipes/` override bundled names. Use `recipe show NAME`
or `recipe show recipes/custom.json` to validate and inspect one. Copy the
bundled article recipe as a starting point. Recipes require `name`, `prompt`,
and optional `template` for offline mode; `parameters` supports `style` and
`length_words`. Placeholders: `{topic}`, `{style}`, `{length_words}`. Steps
generate once, optionally followed by `smash_patterns` text cleanup. Unknown
actions fail before a model request; recipes cannot run arbitrary shell code.
Older detector/refinement recipes require conversion to this explicit format.

```console
hornet --root ./my-project smash --input draft.md --output output/clean.md
```

`smash` now performs deterministic Unicode/whitespace cleanup, including removal
of zero-width spaces and byte-order marks. It preserves sentence content and
does not promise to bypass detectors. It refuses to replace its input file.

## Supervised coding worker

`hornet work` runs a single coding ticket through a local model and a dedicated
Ubuntu VM. The Windows controller calls LM Studio; all model-requested file and
terminal operations run in the VM under a non-root account. There is no local
host-terminal fallback. The VM connection is bound to localhost, uses its own
SSH key, and requires its pinned host key. The guest's forced SSH command only
accepts worker JSON operations.

From the parent MatrixSwarm-Hornet directory, after worker provisioning:

```powershell
.\coding\Start-HornetCoding.ps1
.\coding\Run-GemmaTask.ps1 -TaskFile coding/tasks/your-ticket.txt
.\coding\Read-HornetProgress.ps1
.\coding\Stop-HornetCoding.ps1
```

Or call the CLI directly:

```console
hornet --root .. work --worker-config coding/worker.json --task-file coding/tasks/your-ticket.txt --model google/gemma-4-12b --protocol lmstudio-json --max-tokens 2048 --timeout 120
```

The tools list/search/read files, replace one small exact block with `edit_file`,
write files using expected SHA-256 values, run Python or permitted command
executables, run the full unittest suite, and inspect
Git changes. Arbitrary Python runs inside the VM; the VM is the isolation
boundary. Source data is copied into the guest rather than shared from the host.

Only one controller run may hold a worker configuration's run lock. The loop
defaults to 12 model turns and 40 tool operations, with elapsed time checked
between model turns. Model responses that hit their output limit are rejected
before tool calls execute. Command deadlines stop their process group on Linux.
Socket timeouts are not guaranteed overall request deadlines.

Every run writes its ticket, progress, and completed tool transcript under the
selected `--run-dir`. Completed runs also write final tests, a model summary,
and changed files; a failed run preserves its error and partial transcript.
Ctrl+C cancels the controller and records cancellation. A successful run means tests passed and
new changes from this run were exported **for review**. Pre-existing guest
edits alone do not count as completed work. It never automatically merges, commits,
publishes, or deploys those files. Review and test the exported changes before
integrating them into the main CLI.

The model server's sampling settings are respected. The default `native`
protocol uses ordinary function calls and carries opaque reasoning state across
tool turns for models that require it. The opt-in `lmstudio-json` protocol uses
LM Studio's constrained JSON output and `reasoning_effort: none`; it rejects
malformed actions, unoffered operations, missing usage, or reported reasoning.
No special model token strings are parsed or evaluated. This mode was tested
with LM Studio 0.4.26 and google/gemma-4-12b. Other servers/models must support
these request options or the run stops.

The Gemma helper uses that JSON protocol with 12 turns, 20 tool operations,
2048 output tokens per response, and a 120-second socket timeout. Prefer one
function, one bug, or a small test per ticket. `-MaxTokens` and `-Timeout` can
adjust request limits. `--tools` on the CLI narrows the model's tool palette.
The output cap is separate from the model's loaded context window.
Each ticket starts a fresh conversation containing its own instructions and
ticket, without this operator chat or the whole repository. Source is read on
demand, in 80-line ranges by default. `--context-chars` defaults to 24000
characters for active history plus tool definitions. This is a character
budget, not a tokenizer count; backend framing adds overhead and the loaded
model capacity stays unchanged. Old complete exchanges are removed when needed,
with a compact record of recent operations. Oversized source results are
explicitly omitted with a smaller-read hint; large command output keeps its
tail. Full results remain in the disk transcript. The ticket and latest exchange
are retained; an exchange that cannot fit stops with a clear error.
Progress reports expose tool names and status,
not model reasoning. The CLI does not refresh or reset an existing guest
workspace automatically; review its pending changes before assigning another ticket.

The local private VM keys and cloud-init seeds live in `../coding/private/`
with restricted Windows permissions. They are provisioning material, not part
of the Python distribution or source-release archive.

## Resume an interrupted coding job

Jobs created with 0.4.0 save an atomic `checkpoint.json` before model requests
and tool dispatch, and after each confirmed tool result and complete workspace
snapshot. Resume uses the same ticket and original change baseline:

```console
hornet --root .. resume coding/runs/YOUR_RUN_ID --json
```

The Windows helper is `coding/Resume-HornetTask.ps1 -RunDirectory
coding/runs/YOUR_RUN_ID`. `Read-HornetProgress.ps1 -RunDirectory ...` can inspect
a selected run. Each attempt retains its own result under `attempts/`.

Resume checks the pinned connection, guest machine identity, Git HEAD, and all
workspace file-content hashes against the last confirmed step. The snapshot
excludes Git metadata and Python/environment caches, rejects linked source
entries, and supports up to 200 files, 256 KiB per file, and a 20000-byte
manifest. Filesystem permissions and external service state are not snapshots.
A changed workspace or worker requires inspection before continuing.

A lost model reply can be regenerated. Confirmed tool calls are not replayed.
Lost read-only results are marked interrupted and may be requested again by the
model. New 0.14 jobs use durable worker receipts for individual edits and Python
commands, as described below. Commands without a completed receipt and interrupted
test sequences pause for inspection: their effects cannot be inferred from a lost response.
Completed jobs and jobs created before 0.4.0 cannot be resumed.

### Durable worker operation receipts (0.14.0)

New SSH coding jobs save an operation ID and exact arguments before sending
`write_file`, `edit_file`, `run_python`, or `run_command`. The worker takes an OS
lock, checks the frozen source and identity, flushes a started record to disk,
executes once, and flushes the result and final source manifest. Receipts live
outside the editable goal source; accepted copies are kept in the job's `operations/`.

After a lost reply, `resume` and coding-cell `autoresume` reuse that operation ID.
A completed receipt is adopted only when its request, result, worker identity
and current source match. A request that never reached the worker can execute
under the same lock if its original source is unchanged. Concurrent duplicate
requests cannot execute together, and an ID cannot be rebound to different data.
Usage counters and earlier failed attempts remain intact.

A started record without completion is **uncertain**, even if files appear
unchanged. It pauses for inspection. This includes the gap after an effect but
before its completion record. Compound `check_ticket`, test suites, final and
parent checks, workspace creation and peer delivery retain their existing
recovery rules. Older jobs are not retroactively assigned receipts.

This is lost-reply recovery, not a guarantee of exactly-once external effects.
The journal shares the worker account; it is not a security sandbox. Source
hashes do not cover permissions or external systems. Execution remains sequential.

The new attempt starts with the original ticket and a compact verified summary,
then reads current source. Previous exchanges remain in the audit transcript.
Model turns and tool operations count across attempts. `--max-turns` and
`--max-tools` explicitly raise total limits within the existing 30/80 ceilings.
`--model`, `--base-url`, `--protocol`, `--max-tokens`, `--timeout`, and
`--context-chars` can adjust the next attempt. The 30-minute elapsed check applies
to each active attempt; downtime does not consume that time budget.

An OS lock prevents two controllers using the same worker configuration.
Resume can reclaim that job's stale metadata only after confirming its owner
process has exited. A live or unknown owner is never cleared automatically.

## Ticket acceptance checks

Version 0.5.0 adds operator-authored Python checks to coding jobs:

```console
hornet --root .. work --worker-config coding/worker.json --task-file coding/tasks/fix.txt --acceptance-file coding/tasks/fix.acceptance.json --model google/gemma-4-12b --protocol lmstudio-json --json
```

Example `fix.acceptance.json` for a small addition function:

```json
{
  "schema_version": 1,
  "checks": [{
    "id": "addition",
    "description": "add returns the sum, including cancelling inputs",
    "paths": ["calculator.py"],
    "timeout": 15,
    "python": "from calculator import add\nassert add(2, 3) == 5\nassert add(-2, 2) == 0"
  }]
}
```

The plan supports 1-8 uniquely named checks, 1-30 seconds per check, and up to
3000 Python characters per check. Each check names 1-8 relevant relative source
paths and a short behavior description. Files are limited to 32000 bytes;
unknown fields, duplicate JSON keys, invalid Python, and unsafe paths fail
before contacting the worker or model.

The controller copies the plan into the job and records its SHA-256 before
the model starts. Later edits to the original plan do not change an existing
job. Resume verifies its saved copy and digest; it has no check-override option.
Only descriptions and relevant paths enter the model's instructions. Check
bodies remain controller inputs, executed as separate isolated-mode Python
processes inside the Ubuntu workspace. They have the same guest permissions
as other Python tools; write checks that observe behavior without changing
source. A source-content change during verification pauses for inspection.
Both final suite and acceptance processes use a fresh bytecode-cache location
with bytecode writes disabled, so same-size rapid edits are read from source.

Every model finish runs the full suite and every acceptance check. Success
requires all checks, passing suite results, and new source changes. A zero
exit without the controller's completion marker fails. Exceptions, timeouts,
and `SystemExit(0)` fail. Each result is saved immediately, including partial
results if a later check is interrupted. Full observations and source exports
remain under `verification/ROUND/`; root-level files show the latest round.

Failed checks can trigger two automatic repairs by default. The next model
context retains the original ticket, confirmed progress, failed-check
diagnostics, and relevant source paths; it asks the model to re-read current
code. `--max-repairs 0-3` sets the initial limit. Repairs, model turns, and tool
usage carry across resume. Final suite/check commands have their own bounded
verification rounds and do not consume the model-tool counter. Two identical
failed verifications without source changes stop the loop. Three identical
unsuccessful edit requests also stop; resume refuses another identical request
until the approach changes. A lost in-flight check result requires inspection.

The PowerShell task helper requires `-AcceptanceFile` and optionally accepts
`-MaxRepairs`. Start from `coding/tasks/TEMPLATE.acceptance.json`; its placeholder
deliberately fails until replaced with real assertions. Direct CLI jobs may
omit the plan for compatibility and report `acceptance_status: not_configured`;
that status establishes no ticket-specific behavior coverage. Version 0.4.0
checkpoints remain resumable with their original suite-only completion gate.

Acceptance results establish only the behaviors written into the checks.
Review the candidate source before integrating it.

## Worker-requested ticket checks (0.6.0)

`check_ticket` takes exactly `{}` and executes the job's saved operator plan.
The normal Gemma helper offers it. For a custom tool palette, include
`--tools read_file edit_file check_ticket`. After editing, request the verdict,
repair failures, then finish when it passes. Each call consumes one tool
operation; model turns and existing job budgets continue to apply.

The JSON return contract is `schema_version: 1`, `kind: ticket_check`, boolean
`ok`, string `status`, integer `passed` and `total`, and a `results` array.
Each item includes its check ID, boolean `passed`, paths, nullable integer
exit code, boolean timeout, nullable error and a bounded diagnostic tail.
`plan_sha256` identifies the fixed plan. `source_sha256` hashes the complete
source manifest and worker identity, rather than Git HEAD alone.
`source_unchanged` confirms matching before/after manifests. A missing plan
returns `not_configured`, `ok: false`, zero counts and a null plan digest.

The job retains `ticket-checks/OPERATION/source.json`, `result.json`, and full
`acceptance-results.json`, including partial observations if interrupted.
The returned `artifact` is relative to the run directory. Compact model context
retains the verdict, digest and failed-check IDs; full observations stay on disk.
These outputs can be consumed as a typed JSON result by a later node scheduler.

Every call executes the checks afresh. Worker/source drift before execution
refuses the call; changes during checks pause for inspection. Lost in-flight
results are never replayed automatically, even if source hashes match.
A passing tool result does not complete a job or establish untested behavior.
The final gate reruns the entire suite and fixed checks against the final source
and still requires new changes. This release does not implement autonomous
changes to model weights or a distributed node scheduler.
An edit error starts a fresh focused context on the next model request, keeping
the ticket, confirmed changes and latest verdict. The worker is asked to read
current source before another edit. This does not reset budgets or the guard
against three identical unsuccessful edits. After a stale-file error, the
worker must read the file again before another edit.

## Short read references (0.7.3)

In coding jobs, a confirmed `read_file` returns a short `read_ref` alongside
the full SHA-256. Pass that reference to `edit_file` or `write_file` for an
existing file. The controller retrieves the original digest from its durable
tool journal and supplies it to the worker's existing file-version check.
The model does not need to reproduce 64 hexadecimal characters.

References identify a particular read, job, worker and file. They do not grant
write permission: cell file restrictions and worker path checks still apply.
A reference never updates itself to a newer digest. Read again after editing;
using an older reference fails if the file has changed. Worker identity is
checked before a referenced write, and the journal records both the requested
reference and the full arguments dispatched to the worker.

Use **either** `read_ref` **or** `expected_sha256`, never both. Existing callers
can still supply a full digest. To create a new file, use `expected_sha256: ""`
and omit `read_ref`; this cannot overwrite an existing file.

Confirmed references survive checkpoint loading and safe resume. References
from another job or worker and inconsistent read records are rejected. An
uncertain in-flight write still requires inspection and is never replayed.
Old checkpoints without read references retain their existing hash workflow.
References are identifiers, not secrets or cryptographic authorization tokens.

## Autonomous coding recipes (0.7.2)

Start from a goal; agents create the cell plan and checks:

```console
hornet --root .. autowork --worker-config coding/worker.json --goal-file coding/tasks/goal.txt --model google/gemma-4-12b --json
hornet --root .. control coding/recipes/RUN --message "New information for the current iteration"
hornet --root .. control coding/recipes/RUN --pause
hornet --root .. control coding/recipes/RUN --continue
hornet --root .. autoresume coding/recipes/RUN --json
```

The intake agent selects ownership and defines 1-3 ordered coding cells. Each declares a task, up to four
editable files, earlier-cell dependencies and a named Python function to call.
A verifier in a separate model context writes 1-4 assertion checks. The
planner makes each task self-contained. Verifiers, advice roles and workers
receive their current cell contract instead of the whole multi-cell goal.
For a single-cell plan, the original goal is retained verbatim in that context.
Checks call production APIs directly; importing test modules is rejected before
freezing so checks do not depend on tests the worker has yet to create.
The controller freezes the plan and checks outside the guest workspace and adds
a fixed typed-return check. The worker implements each cell, uses check_ticket,
repairs failures, and finishes. Every coding cell requires new changes and the
full suite. Final recipe checks rerun every cell's frozen checks and confirm
that later work preserved earlier results. Successful execution exports
candidate changes; host integration remains a separate operation.

Return schemas support boolean, integer, number, string, null, array/items,
object/properties/required/additionalProperties, and enum. Objects are closed
by default. Booleans are distinct from integers; false is a completed value.
Results must be finite JSON and fit 4096 bytes. A later cell can reference
an earlier value with `{"$cell":"earlier-id"}` inside its JSON arguments.
Schemas and call arguments are declared as bounded JSON strings in the plan.

The model roles execute sequentially against one server. Default total budgets
are 30 model turns and 80 model tool operations; each cell allows 10 turns,
20 tools and two verification repairs. Planning/check roles get at most six
turns per attempt, within the shared budget. No human acceptance file or routine
approval is needed between cells. Agent-written tests establish only the
coverage they actually exercise; the controller validates execution and types.

Operator controls enter `control/inbox/*.json` through the control command.
The loop checks this inbox before model and tool actions. A new message cancels
any proposed, unexecuted tool batch and refreshes the focused context. A pause
holds the confirmed boundary until continue arrives. In-flight commands finish
before controls apply. Messages clarify work within the frozen contract; a new
goal uses a new recipe run. Optional legacy `commander_input.txt` accepts pause,
continue, stop, or ordinary prompt text. `commander_response.txt` holds the
latest acknowledgment, and received messages are archived. Publish files
atomically; the CLI does this automatically.

Agents use `post_message` to address `intake` (`planner` for older recipes),
a cell ID, or verifier-CELL_ID.
The controller assigns the sender. Peer messages are observations, separate
from trusted operator steering. They are saved in `peers/inbox/RECIPIENT` with
delivery receipts. An active worker's question to its verifier starts a bounded
read-only advice turn and delivers the reply before the worker's next request.
Messages to other cells wait for those cells to activate. There is one active
model request at a time, rather than simultaneous GPU requests.

`recipe-checkpoint.json` holds the current phase, confirmed source state,
frozen plan, cell results and shared counters. `attempts/NUMBER/` retains each
attempt's conditions and outcome. Failed attempts remain available as evidence
and do not blacklist future attempts. `autoresume` verifies contracts and
continues safe checkpoints; uncertain in-flight edits or checks require
inspection and are never automatically replayed. The execution budget is
30 minutes across attempts, excluding live explicit pauses. Optional messages
are capped at 32 operator and 64 peer messages per recipe.

## Cell return manifests (0.7.4)

Each completed recipe cell publishes `cells/CELL_ID/return-manifest.json`.
The controller assembles this file from the completed job and its retained
evidence before making the value available to dependent cells. It contains:

- The frozen cell contract, declared return schema, actual value and resolved
  call arguments. A successful `false` value remains distinct from failure.
- Changed file paths, added/modified/deleted status, and before/after SHA-256s.
- Every acceptance verdict, the full-suite result, and references to the
  original evidence files with their SHA-256s.
- Job attempts and verification rounds, including failed ones, plus the
  recipe attempt's conditions and earlier recipe attempts.
- References to the manifests of dependencies used by this cell.

The recipe checkpoint and successful JSON result index each manifest by path
and SHA-256. Before downstream work, final verification, or resume, the
controller checks the index, rebuilds the manifest from its original evidence,
and checks that the value and source-state chain agree. Missing or altered
evidence stops that operation. The model cannot publish its own success receipt.

These are historical records of a cell's completion. Final recipe checks still
rerun every cell against the final workspace. Agent-written checks establish
only the behavior they exercise; a manifest is not a signature or proof that
the implementation is correct for every possible input.

Failed attempts remain on disk for future inspection and retries. A failed cell
does not publish a consumable success manifest. Interruption after manifest
publication can safely adopt the unchanged receipt without repeating edits.
Legacy recipes reconstruct receipts from their original completed-job evidence
on resume; missing evidence is never replaced by an invented success. Legacy
job attempts without conditions files explicitly record that absence.

## Recon cells and bounded nested work (0.10.1)

Use `autowork --recon-tree` to enable checkpoint schema 5. Initial recon can
make two read-only source observations before choosing execute, delegate, or
delegate-and-work. The original goal and overall acceptance checks stay frozen.

Each root work cell gets frozen checks, then a read-only recon contract using
the same bounded role loop, durable attempts and prompt inbox mechanism. Recon
returns `execute` or `needs_split`. A split declares 2-3 children inside the
parent's file permissions. Children receive their own task, assigned original
requirements, a compact parent-contract reference and verified inputs. Full
parent tasks and transcripts stay in the durable record.
Shared files require ordered dependencies. A Boolean `false` remains application
data and never means “split” or “failed.”

Limits are two levels below the goal (goal -> root cell -> child), nine total
work cells, and the existing shared time/model-turn/tool budgets. `--max-cells`
bounds the initial root plan; each split allows up to three children. Leaves at the
depth limit execute directly. Scheduling is sequential on one worker; splitting
is selected before implementation, not midway through an edited leaf. Parent
completion runs the full suite and its original checks after all children finish.
Passing children cannot override a failed parent contract.

`cells/<id>/recon/contract.json` and `return.json` retain the split decision.
Parent return manifests link to child receipts and actual verification evidence.
Resume preserves completed children and does not ask recon to choose again.
Uncertain in-flight verification stops for inspection rather than rerunning it.

All roles and workers in this mode send at most two recent complete exchanges,
a compact checkpoint, and their frozen task/rules. The full audit remains on disk.
The character budget includes the task and offered tools; the backend adds its
protocol wrapper. This is a bounded working-history policy, not an exact tokenizer
limit or a promise about LM Studio's resident cache. Cumulative lifetime token
counts still increase. Usage reports distinguish peak request size from totals.

The default flat mode and schemas 1-4 retain their existing behavior. The launcher
accepts `-ReconTree` for this opt-in mode. Concurrent workspaces and per-owner
models remain future work.

Full SSH-worker suites run in sequential batches of whole test modules, targeting
50 cases per batch. Discovery is pinned by the ordered test-ID digest; every case
must complete and every batch must return a matching receipt. Module fixtures
stay together, including modules larger than 50 cases. Each command retains its
120-second deadline; the complete suite is limited to 300 seconds and 64 batches.
Batch outcomes remain in the verification record. A timeout or missing receipt
cannot become a pass. Tests that require global state across separate modules
need to be reorganized or run through an explicit dedicated integration check.

## First-turn selection (0.9.0)

`autowork` starts by asking the agent to select a mode and submit its plan in
one response. The controller validates and freezes this choice before coding:

| Mode | Implementation ownership |
| --- | --- |
| `execute` | The intake agent owns one small cell. |
| `delegate` | The intake agent coordinates; delegates own every cell. |
| `delegate-and-work` | The intake agent owns one child; delegates own the others. |

Each child receives a fresh focused context, declared files, typed return
contract and independent checks. Current execution queues these logical roles
sequentially on the configured model and worker. This flat mode has no recursive
splitting. Concurrent workspaces and different models per owner remain future work.

The first response offers only `submit_intake`; implementation tools become
available after the choice and checks are frozen. Rejected choices consume the
existing budgets and remain in attempt history. The plan remains bounded to
1-3 cells. Shared files require a dependency between their owners.

`intake.json` records the mode, short reason, own cell ID, plan, goal, limits and
initial source digest. Work packets and return manifests bind to that decision.
`goal-status.json` and `Read-HornetRecipe.ps1` show the mode and owners. Resume
retains the choice, including after interrupted publication. Older recipes
continue their original planning behavior without acquiring new requirements.

## Durable goals (0.8.3)

New `autowork` recipes freeze the original request in `goal.json`. Stable
requirement IDs identify its nonblank source lines; long requests group adjacent
lines into at most 64 units. The original objective remains verbatim. These
units preserve the request and make assignments explicit; they are not an
automatic semantic interpretation of everything the user intended.

The planner must assign every requirement to one or more cells and give every
cell an assignment. Global constraints should be assigned to every affected
cell. A separate goal verifier then writes 1-4 overall acceptance checks before
coding starts. These checks are frozen in `goal-acceptance.json`, outside the
worker's editable files, and exercise the combined public interfaces. The goal
verifier gets at most two exploratory source reads per attempt, then only the
check-submission tool. This bounds repeated-read loops without accepting weaker
results. Resumed roles archive their previous context and start from the frozen
requirements, previous outcome and current inbox; full history stays on disk.
After two rejected submissions within a role attempt, the controller archives
the active context and gives the role one fresh context containing the unchanged
requirements and validation error. This prevents malformed output from dominating
each repair. The six-turn role limit and total budget remain unchanged; every
rejected document and its error remain in history.
Before freezing checks, the controller rejects unconditional loop-else failures
where the loop has no break. This catches a misplaced exception-checking else;
it does not prove that arbitrary generated tests are correct.

Each cell receives one `work-packet.json`: its contract, assigned verbatim
requirements, verified dependency values and receipt references, and starting
source digest. Workers request relevant source through tools. They do not get
the entire goal, future cell history, or duplicate copies of their contract.
Existing context limits and prompt inboxes still apply. Cell return manifests
bind their evidence to the goal digest and work packet.

`goal-status.json` is a derived progress view of assignments, remaining cells,
active errors, earlier outcomes and usage. The atomic recipe checkpoint remains
authoritative. Retrying creates a new attempt and clears the active error;
previous failed results and their conditions remain intact. A changed hypothesis
must stay within the frozen goal and cell scope. New requirements use a new goal.

Completion requires the final cell rechecks and the frozen overall goal checks.
The controller publishes `goal-completion.json` linking the goal, plan, source
state, cell receipts and final evidence. Passing cells alone cannot declare the
goal complete. Publication interrupted before checkpointing can adopt the same
receipt without repeating completed operations. All evidence is checked on
resume; uncertain interrupted mutations or checks still stop for inspection.

Request accounting retains serialized input characters and model-reported
prompt/output tokens for each role and attempt. Missing token usage is marked
unknown and counted separately. Character budgets are not tokenizer limits.
Use these observations to assess total tokens per verified goal, including retries.

New recipes use checkpoint schema 3 and plan schema 2. Older schema-1/2 recipes
retain their original contracts and resume behavior; they do not silently gain
new goal checks. This release still schedules 1-3 cells sequentially. Concurrent
agents and automatic reconciliation of uncertain external actions remain later
work. Frozen, model-authored checks prove only the behavior actually exercised.

## Research branches (0.11.0)

`autowork --research-cells` lets the agent choose read-only research branches
alongside coding branches. It includes `--recon-tree` behavior and uses checkpoint
schema 6 / plan schema 3. The existing default and older saved recipes retain
their modes. In the Windows launcher, use `coding/Run-AutonomousGoal.ps1
-GoalFile <file> -ResearchCells`.

Each cell declares `kind: research` or `kind: code`. New model submissions use
`result.schema` as an ordinary JSON Schema object. Saved `schema_json` contracts
remain supported, with the same type and size limits. Research returns a typed
value with 1-4 exact source citations; it has only scoped `read_file` and
`submit_finding` tools, with four reads and six model turns per role attempt.
It cannot run commands or write files. Its contract, observed source lines,
finding and receipt stay under that goal's cell directory. Research is a leaf;
code parents may split into research and coding children within their scope.
The recipe still requires at least one coding cell with executable acceptance
checks. This iteration researches local workspace files, not the web.

Dependent cells receive the typed finding and its receipt reference. The
controller validates the declared type, exact quotes, source digest, goal
binding and unchanged source during research. This establishes provenance;
it does not prove a model's interpretation is correct. Findings describe their
recorded snapshot, even when later code changes those files. Coding cells must
still pass their own frozen checks, the full suite and overall goal checks.

Completed research is reused after interruption without another model call.
Rejected findings and failed coding checks remain in history. Every role shares
the original goal's turn, tool and time budgets. Active history keeps at most
two recent exchanges; full history remains on disk. Lifetime token consumption
continues to accumulate even though each request's context is bounded.

Goals have separate records, contracts and inboxes. Since 0.12.0, new goals also
have separate source directories. The worker lock still serializes execution;
simultaneous workers and security isolation between goals remain future work.

## Contract generation review (0.13.0)

New autonomous goals and revisions review generated plans and checks **before
they freeze**. The author receives rejection reasons and can submit a corrected
proposal within its existing six-turn role budget. Coding starts only after
the required contracts have been approved. Earlier saved runs retain their
original contracts and protocol.

The first gate recognizes explicit literal examples such as `offset(3)=7`.
It rejects a matching call with a quoted numeric argument (`["3"]`), and requires
the matching return schema to enforce the exact value (`"enum": [7]`). Boolean,
integer and string values remain distinct. Direct literal assertions that
contradict a recognized example are also rejected. This parser deliberately
handles a narrow syntax; arbitrary prose, algebra and indirect assertions need
the next gate.

A reviewer in a fresh context checks the proposal against the requirements,
types, interfaces, arithmetic and available source excerpts. It has only a
structured review submission tool: no shell, writes or edits to the candidate.
It returns approve, revise or uncertain. Uncertainty stops that proposal from
freezing. Reviews use at most three turns and share the goal's existing total
turn and time limits. The reviewer receives the current proposal and at most
two bounded source excerpts, rather than the author's accumulated conversation.
Nested split proposals receive the same review; choosing to execute an already
reviewed contract requires no extra model call.

Before that review, direct literal equality assertions also get a blind expected
value check. A fresh role sees the requirements, relevant source excerpts and
function calls, with the proposed test answers omitted. It derives each output
and records its calculation. The controller compares those typed values with
the proposed assertions; disagreement or uncertainty rejects the proposal.
There are at most 32 such assertions per proposal. This counters agreement with
an incorrect proposed answer, but the derivation still depends on the model.
New operator clarification changes the review and expected-value cache keys;
earlier rejected proposals and their judgments remain available on disk.

`contract-policy.json` records the protocol. `contract-reviews/` retains the
candidate, review packet and verdict for every reviewed proposal, including
rejections. Accepted role records and goal completion receipts reference their
reviews by hash. Resume validates those bindings and cannot substitute new
checks for an already frozen contract. A rejected proposal may be regenerated;
a frozen contract still requires a new goal or revision to change its meaning.

The reviewer and expected-value role use bounded calls to the configured model, not a guarantee
of semantic correctness or an independent model ensemble. It can make mistakes.
Literal anchors, executable checks and independent release oracles provide
different evidence; none establishes correctness for every possible input.

## Supplied goal checks (unreleased)

`autowork --max-goal-repairs 0|1|2` and `revise --max-goal-repairs 0|1|2`
freeze the number of automatic repair jobs for failed whole-goal or inherited
regression checks. Default: 1; 0 disables dispatch. This differs from
`--max-repairs`, which limits verification retries inside an individual job.
Repairs require one unambiguous code-cell owner, retain all original criteria
and published dependency values, and use the remaining shared goal budget.
See [final goal repair](GOAL-CONTROLLER.md#repair-a-failed-final-goal-gate-unreleased).

`autowork --acceptance-file checks.json` freezes caller-supplied final goal checks
before planning. `revise --acceptance-file checks.json` adds criteria for a new
revision while retaining the parent's regression checks. The file uses the
standalone `work --acceptance-file` schema. See
[the goal controller guide](GOAL-CONTROLLER.md#supplied-whole-goal-acceptance-checks-unreleased)
for an example, evidence paths and recovery limits.

## Development checks

```console
python -m unittest discover -s tests -v
python -m pip wheel --no-deps .
```

The CLI is independent of the manifesto/system prompt and does not execute
historical boot recipes. Original `../hornet/` state and experiments in `bin/`
are retained separately. This release implements the CLI workflows above;
distributed swarm scheduling and automatic self-modification are separate work.
The earlier stamped source archive remains the immutable pre-rewrite checkpoint.

## Periodic supervision (0.17.0)

`hive supervise` inspects autonomous goals without inference. Use `--run RUN --enable` to enroll one goal, `--dispatch` to allow one eligible activation,
and `--watch --interval 180` to repeat. Enrollment freezes `--max-activations`
(default 3) and `--cooldown` (default 180 seconds). `--disable` prevents future
activations without clearing history. `--acknowledge N` records manual inspection
of an interrupted supervisor attempt. See [SUPERVISOR.md](SUPERVISOR.md).
