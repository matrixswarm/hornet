# Hornet desktop

The desktop interface is a fresh Tk application alongside the existing CLI.
Its storage, model client, and revision operations are independent of the CLI's
saved goals. CLI goal directories are not desktop project revisions.

The top bar creates a project or imports a folder. The left panel groups
project-map setup and revision actions below the project tree; **RevisionÃ¢â‚¬Â¦**
also exposes rename, export and trash actions without requiring a right-click.
The middle panel lists source files, and the tabs on the right show source,
blueprints, proposals, run output and activity. The bottom **Mission and model**
area keeps the mission, model connection, optional VM check and run/review
controls together. The status line identifies the next step or current activity.

## Coder interface

The Coder workspace has a project/revision sidebar, a file list, and a large
Code / Context / Map / Review / Tests / Activity area. The mission composer stays
below the editor, with **Run mission** as the primary action. After reviewing a
candidate, **Accept reviewed changes** appears only while that candidate is selected.

Use **+ New** for creation/import and **Project actions** for initialization,
map editing, creating working revisions, VM runs, and recoverable deletion. Initialization and map
acceptance also appear next to the project context when needed. **Model settings**
expands the server/model controls; **Mission options** expands the optional VM
check and diagnostic-output controls. Copy/paste/clear are available from the
mission text's context menu; Ctrl+Enter invokes the gated Run mission action.

In **Files**, select a file or folder (Ctrl-click or Shift-click for several), then
choose **Add to prompt** above the tree or from its right-click menu. Keyboard
users can open the menu with Shift+F10 or the Menu key, or Tab to the button
and press Space. Paths are appended without replacing existing prompt text;
Ctrl+Z undoes the insertion. References are quoted, project-relative, and use
forward slashes on Windows; folders end with a slash. Type how you want them
used, such as "Only edit these" or "Use these for context." Adding paths does
not send a request, create a mode, or enforce an edit boundary.

Selecting files still scopes diagnosis. Automated missions plan their own scope.
The endpoint notice remains visible even when connection settings are collapsed.
All existing review, initialization and VM isolation rules still apply.

## Layout

```text
~/.hornet/projects/project-name/
  revisions/UTC-TIMESTAMP-ID/
    .blueprint
    .hornet-meta/
      revision.json
      shadows.json
      shadows/main.py.shadow
      proposal.json          # when a proposal was applied
    main.py
  runs/UTC-TIMESTAMP-ID.json   # VM commands, output and source hashes
  trash/UTC-TIMESTAMP-ID/     # recoverable revisions removed from the tree
```

Revisions contain complete source copies. Publication uses an atomic directory
rename after writing files and rescanning metadata. Each revision records its
parent. Branching an older revision leaves newer revisions intact. The final
remaining revision cannot be trashed. To recover a trashed revision, close the
app and move its timestamped directory from `trash` back into `revisions`.
**Delete project** asks for confirmation, then moves the entire selected project
(including revisions, pending drafts and records) to `~/.hornet/project-trash/`.
It does not delete or modify the original imported folder. To restore a project,
close Hornet and move its uniquely named trash directory back to `projects/`,
renaming it to its original project name; make sure that name is not already in
use. Each revision has two colored dots: its own color followed by its parent's color.
A root revision has no parent dot. Colors and optional names are stored in the
project's `revision-display.json`, outside the revision contents, and remain stable
when revisions are renamed. Short IDs and parent IDs are also shown as text for
accessibility. Colors may look similar, so use the IDs as well. Right-click and
choose **Rename revision** to change or clear a name. The **RevisionÃ¢â‚¬Â¦** button
offers the same actions and can be reached without a mouse. The menu can move a
non-accepted revision to recoverable project trash. Hornet keeps the accepted default internally for opening a project. The tree
shows a small padlock for locked revisions and only **[WORKING]** for the editable
revision; selecting a locked row also explains its state in the workflow hint.

Choose **Save revision to disk** from the revision menu, select an export
folder, then review the exact destination in the confirmation dialog. Cancel
leaves everything unchanged. Native drag-and-drop destination detection is
currently deferred; dragging a revision does not export it.

The export is a complete copy, including hidden files, blueprint and Hornet
metadata, outside the workspace. The source and revision stay unchanged. Existing
names receive a numbered suffix such as `-2`; nothing is overwritten. If a name
is taken while confirming or copying, Coder asks again with the new destination.
Links, junctions and special files are rejected rather than followed. An export
that changes during copying is not published. This version assumes one desktop
instance per workspace.

Before importing, choose which files to copy. Hidden files, Git metadata,
environments, node_modules, caches and symbolic links are not importable. Chat,
memory and attachment folders, backups, local databases and common credential
files start unselected, but check the complete list: this is a heuristic, not
secret detection. You can explicitly select a file when needed. Import leaves
the source folder unchanged. Hidden configuration required by an application
must be handled separately. A revision under `project-trash/<name>/revisions/`
can also be imported as a new project; other folders inside the active Hornet
workspace are not valid import sources. The hidden blueprint and metadata are not
imported, so review the new map after initialization.

Blueprints contain human-authored architecture and purpose. Each source file has
an associated JSON `.shadow`; `shadows.json` is the UI/context index. Python AST
scans extract docstrings, imports and top-level definitions. These are declared
imports, not a resolved dependency graph. Changed hashes flag architectural
review; other formats initially have a purpose placeholder. Every revision scans
all included files. This does not constitute an agent review of the entire system.

## Initialize and run an automated mission

New projects and older projects without an automation state must be initialized
before mission, working-revision creation, blueprint-edit or VM-run controls are enabled.
Choose a revision and a model, then select **Initialize** under Project setup. Review the
file selection and displayed server before continuing. Only selected source files
and the project blueprint are sent to the server; excluded file names and contents
are not included in the generated file inventory. The blueprint can itself
contain sensitive material, so inspect it as well. Common private-data and backup
paths start unselected, but review the whole selection before sending.

Hornet sends each included text file in bounded parts, one model request at a
time, and saves a checkpoint after every completed part. Initialization can take
considerable time for large projects or slow models; the Activity area shows the
current file and part. UTF-8 files up to 1 MB can be initialized; binary,
undecodable and larger files remain incomplete until excluded. Model failures
and missing summaries leave a resumable draft. Choose **Initialize**
again to retry incomplete parts; **Start over** makes a fresh draft and archives
the previous one. If project files change, Hornet archives the old draft and
starts over for all included files, because older maps do not record which file
contributed each blueprint change. No source code is executed during initialization.

Review the blueprint, summaries, excluded files and any issues in the Proposal
tab. **Accept map** is enabled only when every included file has all
parts summarized. At acceptance Hornet checks the source hashes, part coverage
and original blueprint again, creates an initialization revision and records it
as the accepted default. Descriptions are model claims, not proof of semantic
correctness. You can reinitialize an initialized revision; its accepted revision
remains unchanged until the new draft is accepted.
Existing revisions remain intact. Creating working revisions, missions, diagnosis, running,
and blueprint edits require an initialized revision: choose the accepted map
revision or a later revision copied from it. Revisions from before initialization
remain viewable but cannot be used as a starting point for those actions. If
Hornet closes after drafting, select the project and choose **Review pending**
to recover the draft; the map is not initialized until it is accepted.

Choose **Project actions > Create working revision** from an initialized revision.
Then enter a mission and select **Run mission**. The bottom status and Activity tab show
controller stages and tool receipts. The moving indicator means work is in
progress, not a percentage; model requests may take several minutes and do not
stream their reasoning. Selecting a project defaults to its
accepted revision; selecting a
specific locked revision opens it for inspection without allowing missions.
Hornet maintains one logical working revision across missions, with immutable internal checkpoints, and sends the mission, blueprint, shadow index,
file inventory and optional VM diagnostic to the configured endpoint. It asks
the model to select a bounded file scope, then sends those files' contents and
registry contracts. The file-tree selection does not limit **Run mission**;
it selects source files for **Diagnose only**. Review the whole revision and
the endpoint before running a mission. The supported
automated registry tools are `files.text.read` and `files.text.atomic_edit`. All agent source changes, including
creating a file or deleting a line, use the atomic editor; operations run against a disposable copy with schema,
path-scope, source-hash and readback checks. This is a subset of the CLI's tool
registry, not a general tool-calling implementation. The mission record retains
tool inspection and execution receipts.

Hornet creates a candidate revision. The optional VM check field starts empty;
if you enter a command, Hornet runs it in MatrixSwarm-Test and permits at most
one scoped model repair after a failing check. An empty field skips automated
checks, so run appropriate tests separately before accepting. Hornet then asks
the model to update the blueprint and changed file shadows. This final request
includes source, inventory and shadows only for changed files, together with the
project blueprint. Description responses use an exact-filename schema when the
server supports it and are also checked locally. Hornet requests one correction
for invalid descriptions; a persistent failure preserves the candidate but does
not make it ready for acceptance.
A check returning success after running zero unittest tests is not treated as a
pass. The Proposal tab shows actual changes, tool receipts, checks and candidate
revision; **Accept mission** makes it the accepted default. A failed check cannot
be accepted through this button. Clearing the check command explicitly skips
verification, which is shown in the report. Pending review records can be
reopened with **Review pending**. Failed or interrupted records remain for
inspection and are not replayed automatically.

Model-generated descriptions and architectural compliance still need review;
source hashes prove which bytes were described, not whether a description is
correct. Project contents and any check output sent to the configured endpoint
are visible to that model. The project map is a user-reviewed draft, not a
security boundary or guarantee of full system understanding.

## Mission protocol

Before a mission, Hornet reads the selected revision's `.blueprint` and blocks
the request if it is missing, invalid, empty or too large. The model receives
the mission and project metadata first, then the contents of the files it
chooses from the inventory. The tree selection does not constrain this plan.
Hornet validates the planned paths and limits the plan to 20 existing and new
paths; new paths must not already exist. Individual source files are limited to
128 KB and serialized context to 240 KB. These are byte caps, not a tokenizer
guarantee. Choose a server you intend to receive this information.

The model is instructed to follow the blueprint, but Hornet does not automatically
verify arbitrary architectural rules. Review the candidate and its recorded
checks before accepting it. Failed or malformed responses do not change the
accepted revision. Requests run on a background thread; closing the app abandons
an unfinished request. Candidate and mission records stay tied to their base
revision for review. Generated code is not executed on the host by the desktop.
Use **Run in Test VM** to execute a Python command in the registered
`MatrixSwarm-Test` VM. This first runner is specific to the current Linux host
and defaults to `~/.local/bin/project-vm`; set `HORNET_VM_TOOL` and
`HORNET_TEST_VM` for a different tool or VM. It copies up to 55 KB of project
files into a unique VM test directory, executes without a shell for at most 60
seconds, and saves stdout, stderr, exit status and source hashes under the
projectÃ¢â‚¬â„¢s `runs/` directory. VM logs retain full output; the desktop record
keeps up to 16 KB per stream. It does not provision a VM or install dependencies.
The VM command runs in a headless session: interactive desktop windows are not
shown. Hornet identifies the common Tkinter missing-display error and includes
an environment hint with the diagnostic. Automated GUI tests may use a virtual
display; interactive use requires a graphical VM session.

To ask the model to diagnose a result, select the same revision and tick
**Include latest VM run output with this mission**. The command, captured output
and exit status are sent to the configured model endpoint. If source files have
changed since that run, Hornet requires a fresh run before attaching the output.

### Existing Windows VirtualBox worker

The desktop can now reuse the pinned SSH worker created by the starter kit.
Configure an existing worker without generating credentials or changing networking:

```powershell
python -m hornet.desktop.prepare_test_vm --worker-config C:\path\worker.json
```

This saves a connection reference in the desktop workspace's `test-vm.json`.
`HORNET_VM_WORKER_CONFIG` may override that reference for a process. On Windows,
when the configuration names an existing VM UUID and VirtualBox is installed at
its standard location, a powered-off VM starts headlessly when a run is requested.
Saved/paused VMs need manual attention. Authentication and host-key failures stop
without weakening SSH verification. Restart an already-open Coder after upgrading
its code. No VM runs or unit tests start just because a mission finishes.

Each explicit run transfers a compressed, hash-verified project copy into a fresh
`/home/hornet/.hornet-desktop-runs/run-*/project` directory. It does not overwrite
existing guest workspaces. Transfer uses SSH stdin, avoiding Windows command-line
size limits. The worker backend accepts up to 25 MiB of source and 2,000 files;
virtual environments, caches, hidden files and Git metadata are not copied.
The requested Python command runs with a 60-second timeout. Full logs remain in
the guest and the desktop captures the last 16,000 bytes of each stream.

For a project needing a clean template around its edited code, add explicit paths:

```powershell
python -m hornet.desktop.prepare_test_vm --worker-config C:\path\worker.json --project example --template C:\clean-template --template-path codex --template-path unified_registry.db --revision-path codex
```

The configuration is separate from immutable revisions. This example copies the
template's `codex` and registry DB, then overlays only selected-revision `codex`
files at the same relative paths. Unmatched template files remain; missing files
are never interpreted as deletion requests. The revision's DB does not replace
the template DB. Hosts and saved revisions remain unchanged. Source hashes and a
template/settings fingerprint bind the result; changed inputs require a new run.
The setup command reuses the same global worker when configuring another project,
and refuses conflicting connections or existing project settings.

This prepares files, not dependencies: no repository installer, pip command or
unit suite is run implicitly. Install needed dependencies in a dedicated guest
environment separately. The existing VM is the security boundary, not the staging
directory: executed project code has the guest account's permissions and existing
NAT network access. Do not put unrelated secrets in that VM.

## Development validation

```sh
python3 -m unittest discover -s tests -p test_desktop_workspace.py -v
python3 -m hornet.desktop --workspace /tmp/hornet-smoke --smoke-test
```

The model integration tests use a temporary local HTTP server; they do not
certify a particular LM Studio model. The smoke test opens and closes the actual
Tk window and requires a graphical display. Linux and Windows 10 VM launch
checks have been performed; macOS has not been validated.

## Diagnosis without edits

Select a revision, enter a question and choose **Diagnose only**. Optionally select
source files and include the latest matching VM run output. Hornet sends the
blueprint, shadows, inventory, selected files and optional diagnostic to the
model, and saves a report with the mission records. No revision is created.
Use **Review pending** to reopen the most recent diagnosis or pending work.
Model findings are suggestions and should be checked against the evidence.

The mission box has **Copy**, **Paste**, and **Clear** buttons. Copy uses the
selection, or the entire mission if nothing is selected. Paste replaces the
selection or inserts at the cursor. Clear empties the mission box.

For `python3 -m unittest` checks, the Test VM runner uses `xvfb-run` when
available and no display is configured. This supports automated Tk tests; it
does not show an interactive window. The run record reports `virtual_display`.

Before applying model-proposed operations, Hornet checks the plan structure,
tool schemas and file scope. It supplies a compact atomic-edit recipe and, if
preflight fails, asks once for a corrected complete plan. This is a controller
check, not a model call to `get_help` or `read_recipe`. Hornet rehearses target matching and syntax on a disposable copy, then checks
hashes and repeats tool validation during execution. A preflight pass does not
prove the code works; VM tests follow. The mission record includes preflight receipts.

For edit plans, Hornet requests LM Studio JSON-schema structured output and
validates the returned plan locally before preflight. A server or model must
support structured output; Hornet reports a rejection and does not silently
downgrade the request. Other model requests still use the existing JSON prompt.
The model ID is saved in the workspace desktop settings, and the Proposal tab
begins with a compact review summary followed by full receipts and diffs.

### Import selection and status light

The import picker groups files into expandable folders. Click the Included column or press Space to toggle a file or an entire folder, including its nested files. Use Include selected / Exclude selected for mixed multi-selection. Partial marks a folder containing both included and excluded files. Default exclusions remain unselected; only the final selected files are copied. The same picker is used when choosing initialization context.

The bottom status light stays softly lit and occasionally pulses across once. While work is active, a wider, brighter amber comet makes frequent one-way passes over a soft glow. It returns to the quiet ambient light when processing finishes or fails; status text remains the source of progress information. Windows animation settings disable the motion.

### Understand project

Use Understand project to analyze selected files. Review the plain-language description, then explicitly choose Looks right, continue to accept it. Correct understanding provides purpose, organization, and file-description fields. Details retains advanced JSON access. Corrections save a pending draft; they never accept automatically. Incomplete analysis and stale drafts retain the existing acceptance checks. Project actions provides access to understanding afterward.

### Stop query and progress

Understand project, mission, and diagnosis operations show their current phase and elapsed time. Stop query discards an in-flight model response and prevents subsequent draft commits or revision publication by that worker. It does not send a server-wide stop command: LM Studio may finish computing the abandoned response. Previously saved understanding progress, existing revisions, and the prompt are kept. A running local step or VM check stops at its next safe boundary; the interface remains busy until the worker exits. Nothing is accepted automatically.

Interrupted understanding is restored as incomplete on restart, with completed-file counts and a Resume understanding action. A complete saved draft is ready for review but not accepted. If a refresh was interrupted, the previously accepted understanding remains available and is explicitly distinguished from the unfinished refresh.

### Set or open the projects folder

Project actions > Open Projects Folder opens the current workspace root in the system file manager. Project actions > Set Projects Folder chooses an existing workspace root, containing a projects subfolder, for the next launch. Confirming saves only the preference; it does not migrate, delete, or copy projects, change the active session, or overwrite per-workspace model settings. You can choose the old workspace again to access its projects. Switching is disabled during active work. The launcher honors the saved choice unless --workspace explicitly overrides it. An unavailable saved folder produces an error rather than silently opening a different workspace.

### Goal plans and pointers

New missions propose scoped agent-authored subgoals. Review plan is enabled by default in Mission options; approve the plan to continue, then separately review and accept the final candidate. Project actions offers Goals and pointers and Resume interrupted mission. See [Goals, pointers, and safe continuation](GOALS-AND-POINTERS.md) for durable state, revision snapshots, evidence, and recovery limits.

## Reply chime

The header's **Reply chime** checkbox enables a quiet, short fairy-dust sound
when a new agent plan, diagnosis, completed project understanding, or mission
result is ready for review. It starts on and remembers your choice in the
workspace's desktop settings. Turn it off at any time, including while waiting.
Progress messages, loading saved reviews, incomplete understanding, errors, and
cancelled queries stay silent. Mission results signal that a response is ready,
not that its proposed changes or checks have passed. Sound plays in the background
on Windows, without downloaded audio or extra packages. Other platforms remain
silent. Windows output volume and mute still apply.


### Working revisions and checkpoint cleanup

A project has at most one **[WORKING]** revision. Choose **Create working revision**
from an initialized source revision to explicitly create it. Creating another
locks the previous working revision. The logical working revision keeps its
identity across missions; per-goal, repair and memory snapshots remain immutable
internal checkpoints rather than filling the normal tree with revisions.

Choose **Lock revision** to preserve the working revision. A small padlock icon means no
missions can run on it, including plan approval, counter plans and continuation.
To make further changes, explicitly create a working revision from it. Locking
is preservation, not a quality judgment. A hidden `.LOCK` marker and controller
checks enforce the lock; renaming a row cannot unlock it. There are no good/bad marks or DEFAULT/LOCKED text badges. The accepted default
is independent: it controls which revision opens when selecting the project,
not permission to edit. Existing revisions without working-session state appear
locked until you explicitly create a working revision. Reading them does not
migrate or modify their state.

These actions are available in **Project actions** and the revision context
menu. Creating or locking is unavailable during active work, and unresolved
missions must be resolved before changing the working session. One mission or
understanding operation per project can process at a time. If exactly one unfinished legacy mission exists, select its original base and
choose **Create working revision** to explicitly adopt its recovery. Resume or
review its plan from the new working copy. Its original base stays locked, and
continuation retains the saved checkpoint identity. Ambiguous unfinished work
requires reconciliation rather than automatic adoption. Older locked revisions
remain available for review and as sources for new working revisions.

Choose **Show internal checkpoints** to inspect the complete physical history.
The original, default, named revisions and referenced branch bases remain visible.
Damaged grouping metadata falls back to showing every revision, not hiding work.

For a locked session, **Clean up checkpoints...** previews the exact internal
revisions to move to project Trash and asks for confirmation. The original and
final result, default, named snapshots, other mission inputs/results and manual
branch bases are protected. Any unfinished mission blocks cleanup. Dependencies
and source evidence are rechecked under locks after confirmation. A cleanup
manifest preserves the removed snapshots' IDs, parent links and source hashes;
immutable history is not rewritten. A partially interrupted move remains
recoverable in Trash and is recorded as incomplete. The final revision remains
self-contained.

Cleanup is recoverable storage cleanup, not permanent deletion: it does **not**
free disk space. Nothing is deleted or cleaned up automatically by locking.

### Switching projects and revisions

Review restores the newest saved mission record belonging to the selected revision,
including a plan awaiting approval, completed review, diagnosis, or accepted history.
Accepted history is read-only and does not restore an acceptance action. Reports are
re-read from disk rather than cached, so a previously displayed plan cannot replace
a newer saved mission state. Unrelated projects and revisions do not share reports.

Within a Coder session, each revision retains its prompt, Activity text, selected
files, and active tab. Source and file context are re-read when returning to the
revision. Tests shows the selected revision's saved run, labeled as historical;
rerun checks after source changes. Mission records and runs survive restart, while
unsent prompts, Activity display, selected files, and tabs are session-only.

### Partial work after a stopped mission

Each verified source publication advances the same working revision, including
when a later model response fails or a mission is stopped. Its displayed working
ID stays stable; the source browser shows the current partial files. Reopening
Coder reconciles saved progress without repeating model calls or edits.

**Resume interrupted mission** continues the original saved mission from its fixed
base and verified checkpoints. **Run mission** on an unfinished working session
asks whether to keep partial work and start a new mission. Choosing Yes explicitly
ends automatic resume of the original mission; its record and checkpoints remain
in recovery history. Choosing No leaves it available to resume.

**Project actions > Discard working revision...** asks for confirmation, then locks
and removes that session from the active working view. Recovery history and all
source files remain; this action does not delete files. No discard, replacement,
or resume is allowed while processing is active. The accepted default is unchanged
until reviewed changes are explicitly accepted.

### Default mission verification

Missions syntax-check created or changed Python (`.py`, `.pyw`) and JSON files without executing project code. Other file types are explicitly reported as unsupported/skipped. Existing atomic Python syntax guards remain enabled. A syntax failure prevents the mission reaching acceptance review. Syntax success does not establish functional correctness: the user reviews and runs the result. Unit tests and test-suite goals are not expected unless requested. The extra VM check command remains empty by default and runs only when the user supplies it; its existing failure and repair rules still apply. Older missions without a syntax report are labeled as such, not retroactively marked passed.

Stopped missions show **Needs attention** with the stopped step and saved reason.
The current working revision offers **Resume**, **Keep partial / new mission...**,
and **Discard working revision...**. Resume is enabled only for a saved resumable
read-only failed/cancelled step; uncertain effects must be inspected and reconciled.
The new-mission choice focuses the prompt without closing or running anything.
Run mission then asks to retain the partial work and close the old mission.
Discard uses its existing confirmation and retains recovery history and files.
These choices disappear when switching projects/revisions or while work is running.
