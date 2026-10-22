# Hornet 0.15.1 — your first completed goal

Give a local model a small coding goal. Hornet keeps the work, checks and recovery
state in a separate goal directory, then exports the result for your review.

The downloadable starter kit includes the 0.15.1 wheel, its matching source, a small
Python exercise and a setup tool for a **new dedicated Ubuntu VM**. Start with the
installation check, then complete the coding exercise. The Windows walkthrough
uses Python 3.9+, OpenSSH and VirtualBox. The model runs on your Windows host.

## 1. Verify and install

Verify the ZIP before extracting it using `VERIFY-RELEASE.md`, supplied beside
the ZIP. Open PowerShell in the extracted starter folder. Commands use the
environment's executable directly; activating scripts is unnecessary.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --no-deps .\matrixswarm_hornet-0.15.1-py3-none-any.whl
.\.venv\Scripts\hornet.exe --version
.\.venv\Scripts\python.exe .\tools\first_run.py --project .\my-first-goal
```

Expected: `matrixswarm-hornet 0.15.1`, followed by `INSTALLATION CHECK PASSED`.
The check initializes a new project, runs local diagnostics, and verifies a
small file contract. It does not contact a model. It refuses to overwrite a
project, so choose another name for a second trial.

On Linux/macOS the equivalent environment executables are `.venv/bin/python`
and `.venv/bin/hornet`. VM provisioning below is currently Windows/VirtualBox.

## 2. Prepare the coding worker

Follow [WORKER-SETUP.md](WORKER-SETUP.md) to create a new worker. It needs one CPU,
2 GiB of RAM and disk space for the Ubuntu image and VM. The setup creates fresh
SSH keys and pins the VM's host key. It never adopts an existing VM.

## 3. Run a real coding goal

In LM Studio, load a tool-capable model and start the local server. Use the exact
model ID shown by this command:

```powershell
Invoke-RestMethod http://127.0.0.1:1234/v1/models | ConvertTo-Json -Depth 6
```

Set `$model` to that ID. Gemma 12B and Qwen 9B were used during development; model
reliability and speed vary. One request runs at a time. Hornet's context setting
below is a **character budget**, separate from the loaded model's token window.

```powershell
$model = 'google/gemma-4-12b'
.\.venv\Scripts\hornet.exe --root .\my-first-goal worker-status --worker-config worker.json --json
.\.venv\Scripts\hornet.exe --root .\my-first-goal autowork --worker-config worker.json --goal-file demo-goal.txt --model $model --base-url http://127.0.0.1:1234/v1 --protocol lmstudio-json --max-cells 1 --max-turns 30 --max-tools 40 --cell-turns 10 --cell-tools 16 --context-chars 16000 --max-tokens 2048 --timeout 120 --json
```

The worker status must show `ready: true`. The model should repair `calculator.py`
so `double(n)` returns twice an integer. The requirement includes negative, zero
and positive examples. Hornet creates a separate VM workspace for this goal.

**Success:** this autonomous goal reports `completed`; its coding cell reports
`ready_for_review`. `waiting_for_user`, `paused`
and a successful process exit alone do not mean the coding goal is complete.
The result includes the exact `run_dir`; save it as `$run` for these commands:

```powershell
$run = 'PASTE_THE_EXACT_RUN_DIR_FROM_THE_RESULT'
.\.venv\Scripts\hornet.exe --root .\my-first-goal goal-files $run
.\.venv\Scripts\hornet.exe --root .\my-first-goal questions $run
```

Open that run's exported changes and check receipts before integrating them into
your own code. The original VM baseline remains intact. A goal passing generated
checks establishes the exercised behavior; review remains necessary.

## 4. Leave, answer, resume

If the result says `waiting_for_user`, the process can close. There is no deadline
or repeated inference while the question waits. Use its exact question ID:

```powershell
.\.venv\Scripts\hornet.exe --root .\my-first-goal answer $run q-EXACT_ID --text 'Your answer'
.\.venv\Scripts\hornet.exe --root .\my-first-goal autoresume $run --json
```

Saving an answer does not start work. Resuming without an answer keeps waiting.
For an interrupted goal, use the same `autoresume` command. Keep the VM, worker
keys, run directory and source intact. Hornet stops for inspection when it cannot
establish a safe continuation. Cancellation is final for that question:

```powershell
.\.venv\Scripts\hornet.exe --root .\my-first-goal answer $run q-EXACT_ID --cancel 'Stop this task'
```

Standalone `work` jobs use `resume`; autonomous goals use `autoresume`.

## 5. Make a targeted follow-up

After a completed goal, request a revision using its run directory and file:

```powershell
.\.venv\Scripts\hornet.exe --root .\my-first-goal revise $run --file calculator.py --goal 'Add triple(n) returning 3*n for integers. Preserve double(n).'
```

Revision creates another workspace and retains the parent. Start with one goal
and one worker; simultaneous workers and a conversational UI are future work.

## If something stops

| Symptom | Next action |
| --- | --- |
| Python or OpenSSH missing | Install the prerequisite, then retry the named command. |
| `doctor` passes but model fails | `doctor` is local only; check `/v1/models`, model ID and server port. |
| Worker connection refused | Start the new VM; allow its first boot to finish; repeat `worker-status`. |
| Host-key mismatch | Inspect VM identity and the saved pin. Do not disable host verification. |
| `waiting_for_user` | List questions, save an answer, then explicitly resume. |
| Budget exhausted or uncertain operation | Inspect the saved attempt/checkpoint. Keep failed evidence. |
| No new source changes | A no-op does not establish completed coding work. Inspect the requirement and output. |

After the controller exits, shut down your demo VM using VirtualBox's ACPI
shutdown. Retain it to resume or revise saved goals. See `RELEASE-NOTES.md` for
validation scope and `source/hornet_cli/README.md` for the full command reference.
