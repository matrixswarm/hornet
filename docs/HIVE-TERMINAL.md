# Hive terminal command center

Available in 0.16.0 and later. Windows and POSIX terminals are supported without
additional Python packages.

```console
hornet hive terminal
hornet hive terminal --run coding/recipes/EXACT_RUN_ID
hornet hive terminal --run-dir coding/recipes --interval 1
hornet hive terminal --once
hornet hive terminal --json
```

Use `--root PATH` when opening it from outside your Hornet project. `--run` is
an exact saved run directory inside that project. `--run-dir` searches a project
subdirectory; the default is `coding`. A redirected terminal needs `--once` or
`--json`. JSON always returns one snapshot. Close live mode with Q or Ctrl+C.
The live view remains read-only. It shows commands for a selected stopped cell
or a completed code cell; run those commands explicitly in a shell.

## Navigation

- **1 / C: All cells.** Includes an `@goal` orchestration row for each autonomous
  goal, its nested code/research cells, and standalone coding tickets.
- **2 / A: Active cells.** Shows cells whose recorded controller is running and
  whose goal currently selects them. Paused, waiting, finished and dead-controller
  runs are excluded. A controller can be waiting for a response during an active
  cell; the activity timer makes that wait visible.
- **3 / G: Goals.** Enter opens that goal's cell list.
- **Up/Down or J/K:** Move through a list. Enter selects a cell.
- **Selected cell:** Inspect the exact goal/cell IDs and run directory, assigned
  agent, last observed agent/model, phase, tool counts, verification evidence,
  files and dependencies. Scroll with Up/Down or J/K.
- **Stopped selected cell:** `hornet hive resume-cell RUN CELL` checks the exact
  current cell ID, its dependencies and whether the controller is already live.
  The existing resume path then validates locks, source and worker identity,
  remaining budgets and any in-flight operation. It never makes a verified cell
  active again or grants a new retry allowance.
- **Completed code cell:** `hornet revise RUN --cell CELL --goal "CHANGE"` copies
  the completed goal workspace, selects that cell's files, and freezes a link
  to its prior return, contract, value and changed-file list. Downstream cells
  are included in the revision scope; inherited checks rerun. The new goal's
  detail view shows the parent run and selected source cell. The parent goal and
  cell remain unchanged. `--file` remains available for file-based routing.
- **Inverted tree:** Children appear above parents, with the goal at the bottom.
  `>` marks the selected cell. This shows delegation relationships; declared
  dependencies are listed separately rather than implied by the drawing.
- **B / Escape:** Return to the main menu. **Q:** Quit the viewer.

Rows refresh from local files every second by default. The interval is configurable
from 0.25 to 60 seconds; reading many old runs can take longer. Narrow `--run-dir`
or select one `--run` for a busy project.

## What progress and activity mean

Progress shows recorded stages, verified-cell counts, model/tool budget counters
and checks passed against the saved check plan. No completion percentage is
estimated. A verified cell and a completed overarching goal are distinct states.
The terminal is an observer; execution and resume still validate the frozen
contracts, source snapshots and completion receipts.

New runs record `runner.json` and `activity.json` alongside existing checkpoints.
Activity records contain timestamps, event/tool names and agent/model labels.
They exclude prompts, tool arguments, tool output and API keys. A request is
recorded only after obtaining its inference claim. Tool dispatch and return are
separate events. The timer measures time since the last recorded action, not
CPU utilization or an estimate of the model's thinking time.

Refreshing the display never writes an activity timestamp, calls a model or
connects to a worker. A live assignment may therefore show **Assigned: qwen**
while **Last used: gemma** until the next request uses the new label. Reading
earlier runs remains supported; missing activity is shown as **unrecorded**,
and old runs without runner observations are not assumed active.

Malformed, inaccessible, linked or oversized records produce warnings. The
viewer continues showing other readable goals. Scanning is bounded to 256 runs,
4096 directories and eight directory levels and excludes child jobs from the
top-level list. Select a narrower path if a limit is reached. Terminal control
characters in model-authored text are removed before rendering.

## Limits

This version observes local saved records. Model inference may be remote (see
[Hive connections](HIVE.md#remote-models)), but the terminal does not aggregate
other Hornet controllers over the network. Execution remains sequential.
Observational metadata is not an execution lock or authority to retry work.
An interrupted attempt and its earlier evidence remain available for inspection
and explicit resume.
