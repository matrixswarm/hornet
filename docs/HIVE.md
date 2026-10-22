# Named agents with hive.json

This feature is available in the development source after 0.15.1. Use the
[source installation instructions](../README.md#install-from-source) to try it.

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
