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

1. Open the [v0.15.1 release](https://github.com/matrixswarm/hornet/releases/tag/v0.15.1).
2. Download the **starter ZIP**, `SHA256SUMS`, `SHA256SUMS.asc`, the public signing
   key and `VERIFY-RELEASE.md`.
3. Verify the download, extract it, and follow **START-HERE.md** inside.

The walkthrough installs Hornet, checks a local file contract, creates a new
Ubuntu worker VM and asks your local model to fix a small Python function.
The starter VM setup currently targets **Windows + VirtualBox**. The model runs
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

The PyPI 0.15.1 wheel and source distribution match the files in the signed
GitHub release. See the [CLI reference](https://github.com/matrixswarm/hornet/blob/main/docs/CLI.md)
for goals, revisions, contracts and recovery commands.

## Project

Created and led by **Daniel F. MacDonald**, with AI engineering collaboration
credited in [AUTHORS.md](https://github.com/matrixswarm/hornet/blob/main/AUTHORS.md).
See [CONTRIBUTING.md](https://github.com/matrixswarm/hornet/blob/main/CONTRIBUTING.md)
for development and [SECURITY.md](https://github.com/matrixswarm/hornet/blob/main/SECURITY.md)
for the execution boundary and reporting.

## License

Copyright (c) 2026 Daniel F. MacDonald. Licensed under **AGPL-3.0-only**;
see [LICENSE](https://github.com/matrixswarm/hornet/blob/main/LICENSE).

**Victory Always.**
