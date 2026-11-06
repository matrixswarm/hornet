# Hornet desktop

The desktop interface is a fresh Tk application alongside the existing CLI.
Its storage, model client, and revision operations are independent of the CLI's
saved goals. CLI goal directories are not desktop project revisions.

The top bar creates a project or imports a folder. The left panel groups
project-map setup and revision actions below the project tree; **Revision…**
also exposes rename, export and trash actions without requiring a right-click.
The middle panel lists source files, and the tabs on the right show source,
blueprints, proposals, run output and activity. The bottom **Mission and model**
area keeps the mission, model connection, optional VM check and run/review
controls together. The status line identifies the next step or current activity.

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
choose **Rename revision** to change or clear a name. The **Revision…** button
offers the same actions and can be reached without a mouse. The menu can move a
non-accepted revision to recoverable project trash. Hornet alone controls the
[ACCEPTED] marker; it is not part of the editable name.

Right-click a revision and choose **Save revision to disk** to create a complete
copy, including its blueprint and Hornet metadata, in a new folder outside the
workspace. Existing folders are never overwritten. This version assumes one
desktop instance per workspace.

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
before mission, revision-copy, blueprint-edit or VM-run controls are enabled.
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
Existing revisions remain intact. Copy revision, missions, diagnosis, running,
and blueprint edits require an initialized revision: choose the accepted map
revision or a later revision copied from it. Revisions from before initialization
remain viewable but cannot be used as a starting point for those actions. If
Hornet closes after drafting, select the project and choose **Review pending**
to recover the draft; the map is not initialized until it is accepted.

Enter a mission and select **Run mission**. The bottom status and Activity tab show
controller stages and tool receipts. The moving indicator means work is in
progress, not a percentage; model requests may take several minutes and do not
stream their reasoning. Selecting a project defaults to its
accepted revision, labeled [ACCEPTED] at the start of its row; selecting a
specific revision branches from that revision.
Hornet copies a working revision and sends the mission, blueprint, shadow index,
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
if you enter a command, Hornet runs it in the configured test VM and permits at most
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
Use **Run in Test VM** to execute a Python command in a registered test VM.
This runner needs a compatible host VM helper and VM configured separately. It
defaults to the helper `~/.local/bin/project-vm` and VM `MatrixSwarm-Test`; set
`HORNET_VM_TOOL` and `HORNET_TEST_VM` for your helper and VM. It copies up to 55 KB of project
files into a unique VM test directory, executes without a shell for at most 60
seconds, and saves stdout, stderr, exit status and source hashes under the
project’s `runs/` directory. VM logs retain full output; the desktop record
keeps up to 16 KB per stream. It does not provision a VM or install dependencies.
The VM command runs in a headless session: interactive desktop windows are not
shown. Hornet identifies the common Tkinter missing-display error and includes
an environment hint with the diagnostic. Automated GUI tests may use a virtual
display; interactive use requires a graphical VM session.

To ask the model to diagnose a result, select the same revision and tick
**Include latest VM run output with this mission**. The command, captured output
and exit status are sent to the configured model endpoint. If source files have
changed since that run, Hornet requires a fresh run before attaching the output.

## Development validation

```sh
python3 -m unittest discover -s tests -p test_desktop_workspace.py -v
python3 -m hornet.desktop --workspace /tmp/hornet-smoke --smoke-test
```

The model integration tests use a temporary local HTTP server; they do not
certify a particular LM Studio model. The smoke test opens and closes the actual
Tk window and requires a graphical display. Linux launch has been checked;
Windows and macOS launchers require platform validation.

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
downgrade the request. File-description requests also use a schema when the
server supports it, with local validation and one correction attempt. Other
model requests still use the existing JSON prompt.
The model ID is saved in the workspace desktop settings, and the Proposal tab
begins with a compact review summary followed by full receipts and diffs.
