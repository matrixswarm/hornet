# Named agents with hive.json

This feature is available in 0.16.0 and later. Install from
[PyPI](../README.md#install-from-pypi) or [source](../README.md#install-from-source).

Create `hive.json` in your Hornet project root:

```console
hornet hive init
hornet hive list
```

The template defines `gemma` and `qwen`. Edit each `model` to match the exact ID
your server accepts. `base_url` and `protocol` identify its connection. You can
use any label beginning with a letter, up to 64 letters, digits, underscores or
hyphens. `hive list` validates the file without contacting a model server.

```json
{
  "schema_version": 1,
  "default_agent": "gemma",
  "agents": {
    "gemma": {
      "model": "google/gemma-4-12b",
      "base_url": "http://127.0.0.1:1234/v1",
      "protocol": "lmstudio-json",
      "resource": "local-gpu"
    },
    "qwen": {
      "model": "qwen/qwen3.5-9b",
      "base_url": "http://127.0.0.1:1234/v1",
      "protocol": "lmstudio-json",
      "resource": "local-gpu"
    }
  },
  "roles": {
    "planner": "gemma",
    "worker": "gemma",
    "verifier": "qwen",
    "reviewer": "qwen",
    "researcher": "gemma"
  },
  "cells": {
    "check-even": "qwen"
  }
}
```

## Start and assign

Select a label for a ticket or goal:

```console
hornet autowork --worker-config worker.json --agent gemma --goal "Fix the parity function."
hornet work --worker-config worker.json --agent qwen --task "Fix the parity function."
```

Without `--model` or `HORNET_MODEL`, a project containing `hive.json` selects its
`default_agent` automatically. An explicit `--agent` selects a named connection;
use it without `--model` or `--base-url`. Existing direct model commands continue
to work. Model loading and availability are managed by your model server.

Role mappings override the default label. Exact cell mappings override the
implementation label for that cell; its verifier uses the verifier role. Once
a goal has planned its cells, assign an unfinished cell on demand:

```console
hornet hive assign coding/recipes/EXACT_RUN_ID check-even --agent qwen
```

Use the exact run directory and cell ID from the goal checkpoint. This saves an
assignment for the next implementation/research request, including when the goal
is already running. An in-flight response finishes before the new assignment
applies. The frozen contract, checks and earlier attempt records are retained.
Completed cells require a revision for further work.

Each goal saves its own registry in its checkpoint. Editing the root `hive.json`
affects new goals. A saved goal's assignments must use labels from its saved
registry. Request records include the actual agent label and model used.

## Claims and resume

Optional `planning` and `recovery` settings control root-plan signoff and bounded
fallbacks for stuck cells. Supervisors and fallback models use the same named
connections. See [the goal controller guide](GOAL-CONTROLLER.md) for the saved
review loop, CLI examples and final closeout receipts.

Cooperating Hornet processes reserve an endpoint before sending a model request.
`localhost`, `127.0.0.1` and `::1` on the same port share the same claim. If two
servers use the same GPU, give their profiles the same optional `resource` label.
Claims are shared across projects under the current OS user's `.hornet/claims`.

A busy endpoint or resource stops the attempt with `waiting_for_agent`. It does
not spend a model turn or poll the model. Resume after the resource becomes free:

```console
hornet autoresume coding/recipes/EXACT_RUN_ID
hornet resume coding/runs/EXACT_RUN_ID
```

`--agent LABEL` can change the saved default on resume; explicit role and cell
assignments still apply. Live claims are protected by OS locks. Process death
releases the OS lock, but retained ownership metadata requires resuming the same
run before recovery. Unreadable or unknown ownership blocks execution.

Worker execution remains sequential. The existing worker lock reserves the
worker across the goal, and scoped cell tools restrict edits to declared files.
Hive runs also hold a shared reservation for the pinned worker connection, so
different projects using this controller cannot run that same worker at once.
This version provides named routing and reservations; parallel cell execution
requires additional scheduling and workspace integration. Claims coordinate
Hornet processes on one controller machine and do not control external clients
using your model server.

Use `HORNET_API_KEY` for existing authenticated endpoint support. Keep passwords
and API keys out of `hive.json`.


## Remote models

A label can point to a model server on another computer or a compatible hosted
API. It does not have to be local:

```json
"remote_coder": {
  "model": "your-server-model-id",
  "base_url": "https://inference.example.com/v1",
  "protocol": "native"
}
```

Add that profile under `agents` and select `--agent remote_coder`, assign it to
an unfinished cell, or use it in a role mapping. `native` requires compatible
chat-completions tool calls. Use `lmstudio-json` for an LM Studio server with the
constrained JSON action protocol. A private network LM Studio address works too.
These are model connections, not arbitrary remote Hornet/Codex agent sessions.
Hornet on this controller still enforces cell scopes, checks and worker access.

The existing `HORNET_API_KEY` environment variable supplies a single shared API
key to model requests; there are no per-label credentials yet. Authenticated
remote requests require HTTPS. Keep keys out of the configuration file.
Only task context and relevant tool observations selected by the existing
context policy are sent to the chosen endpoint. Selecting a remote model does
not relocate the coding worker or change its SSH configuration.

Inference claims coordinate cooperating processes under one OS user on this
controller. They do not coordinate separate controller computers or unrelated
clients of the remote server. Use separate resource labels for independent
machines and a shared label for endpoints that share the same hardware.

## Live terminal

```console
hornet hive terminal
hornet hive terminal --run coding/recipes/EXACT_RUN_ID
hornet hive terminal --once
```

Browse all cells, active cells and goals. Select a cell for its inverted tree,
agent assignment, observed progress and time since the last recorded agent
action. The viewer makes no model or worker requests. See the
[terminal guide](HIVE-TERMINAL.md) for keys, JSON snapshots and limits.
