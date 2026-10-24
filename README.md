# Hornet

**Give a local model a coding goal. Keep the work when the conversation ends.**

Hornet turns a goal into small cells with explicit file scopes, typed results,
acceptance checks and attempt budgets. Each model request gets bounded working
context. Files, failed attempts and recovery evidence stay on disk.

It is a command-line tool for developers using local models, including an
OpenAI-compatible LM Studio server. Python 3.9+; no runtime Python dependencies.

## Install from PyPI

[Hornet on PyPI](https://pypi.org/project/matrixswarm-hornet/) is available as
`matrixswarm-hornet`. Install it in a Python 3.9+ virtual environment:

```console
python -m pip install matrixswarm-hornet
python -m hornet --version
python -m hornet --help
```

The package also installs the `hornet` command. Follow the walkthrough below to
configure a worker VM and connect your local model for coding goals.

## Try one completed goal

1. Open the [v0.17.0 release](https://github.com/matrixswarm/hornet/releases/tag/v0.17.0).
2. Download the **starter ZIP**, `SHA256SUMS`, `SHA256SUMS.asc`, the public signing
   key and `VERIFY-RELEASE.md`.
3. Verify the download, extract it, and follow **START-HERE.md** inside.

The walkthrough installs Hornet, checks a local file contract, creates a new
Ubuntu worker VM and asks your local model to fix a small Python function.
The starter VM setup supports **Windows or Linux hosts with VirtualBox**. The model runs
on the host; agent code runs inside the dedicated VM.

Read the [first-goal walkthrough](https://github.com/matrixswarm/hornet/blob/main/starter/START-HERE.md)
and [worker setup](https://github.com/matrixswarm/hornet/blob/main/starter/WORKER-SETUP.md)
before running the demo. Use the downloadable starter for the ready-to-install
wheel; GitHub's automatic source archive does not contain that wheel.

## What Hornet keeps track of

| Need | Hornet's behavior |
| --- | --- |
| A task grows too large | Cells can execute, delegate, or delegate and work within configured limits. |
| Context keeps growing | Requests carry bounded context and references to saved evidence. |
| An attempt fails | The failed attempt stays available for inspection and later decisions. |
| The process stops | Saved checkpoints and operation receipts support resume; uncertain operations stop for inspection. |
| A decision is missing | A durable question waits without repeated inference or a response deadline. |
| A finished file needs a change | Target a saved goal and file with `revise`; retain the parent workspace. |

Workers currently execute **sequentially**. Separate goal directories share the
VM account; the dedicated VM supplies the execution boundary. Passing generated
checks is evidence of the checked behavior, and exported code still needs review.

## Named agents

Hornet supports a project-root `hive.json` mapping labels to
model connections. Run `hornet hive init`, select `--agent gemma`, or assign an
unfinished goal cell with `hornet hive assign RUN CELL --agent qwen`.
See [the hive guide](docs/HIVE.md) for role routing, shared resource claims and
resume. Named agents are available in 0.16.0 and later.

## Quiet supervision

In 0.17.0, explicitly enroll saved goals for bounded automatic resume. The
periodic scan uses no inference and respects pauses, questions, active workers
and remaining budgets. Each activation leaves a receipt; uncertain outcomes
stay stopped for inspection. See [the supervisor guide](docs/SUPERVISOR.md).

## Questions wait for you

```console
hornet questions coding/recipes/RUN_ID
hornet answer coding/recipes/RUN_ID q-EXACT_QUESTION_ID --text "Use the existing configuration."
hornet autoresume coding/recipes/RUN_ID
```

An answer is saved before execution resumes. `waiting_for_user` is a pause;
`completed` is a separate result. There is no background model polling while a
required question waits. Use `resume` for standalone `work` runs.

## A measured first run

In the 0.15.0 first-user trial, Gemma 12B completed the small coding demo using
**16 model requests and 5 tool calls**, with a peak input of **3,084 tokens**.
Only `calculator.py` changed; independent checks returned `[-6, 0, 14]`.
The original worker baseline was preserved.

That was one focused goal on a fresh Python environment and Ubuntu VM on an
existing Windows host. It is not a general model-reliability benchmark.
See [the validation record](https://github.com/matrixswarm/hornet/blob/main/docs/VALIDATION.md).

## Install from source

```console
git clone https://github.com/matrixswarm/hornet.git
cd hornet
python -m venv .venv
```

On Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\hornet.exe --version
```

On Linux/macOS:

```sh
.venv/bin/python -m pip install .
.venv/bin/hornet --version
```

Our publishing workflow verifies the signed GitHub package files before uploading
those same bytes to PyPI. See the [CLI reference](https://github.com/matrixswarm/hornet/blob/main/docs/CLI.md)
for goals, revisions, contracts and recovery commands.

### Upgrading from 0.16.0

Version 0.16.1 fixes ambiguous contract-review instructions and adds Linux host
setup and stronger worker preflight. Install the new version in a separate
environment and use its matching starter for a new worker. Preserve the old
environment, VM, keys and saved goals together: the new controller refuses
mismatched worker code, and this release does not migrate existing deployments.

## Project

Created and led by **Daniel F. MacDonald**, with AI engineering collaboration
credited in [AUTHORS.md](https://github.com/matrixswarm/hornet/blob/main/AUTHORS.md).
See [CONTRIBUTING.md](https://github.com/matrixswarm/hornet/blob/main/CONTRIBUTING.md)
for development and [SECURITY.md](https://github.com/matrixswarm/hornet/blob/main/SECURITY.md)
for the execution boundary and reporting.

The [roadmap](docs/ROADMAP.md) records planned work, including a Strategy-pattern
builder that assembles agent implementations through durable contracts.

## License

Copyright (c) 2026 Daniel F. MacDonald. Licensed under **AGPL-3.0-only**;
see [LICENSE](https://github.com/matrixswarm/hornet/blob/main/LICENSE).

**Victory Always.**


### Hive terminal

Run `hornet hive terminal` from the project root to browse saved goals, all
cells and active cells. Select a cell to see its inverted tree, agent, observed
checks and time since the last recorded action. Named connections may use local
or compatible remote model servers. See [Hive connections](docs/HIVE.md) and the
[terminal guide](docs/HIVE-TERMINAL.md). These features are available in 0.16.0.

The [goal controller](docs/GOAL-CONTROLLER.md) adds optional root-plan
approval by a user or named supervisor, bounded recovery for repeated cell
operations, and a closeout receipt after the controller releases its leases.
Drafts, rejected attempts and frozen checks stay available across resumes.
