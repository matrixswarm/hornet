# Periodic goal supervisor

Available in 0.17.0. The supervisor inspects saved autonomous goals and can resume
explicitly enrolled goals at known safe boundaries. It uses **zero model tokens
for scanning**. Execution uses the goal's existing agents, contracts, workspace,
questions and remaining budgets. There is no separate planning model in this
supervisor version.

## Inspect, enroll, then run

Use the project root containing your saved goal. Replace `EXACT_RUN_ID` with its
actual directory; `--root` can select a project when your terminal is elsewhere.

```console
hornet hive supervise --run-dir coding
hornet hive supervise --run coding/recipes/EXACT_RUN_ID --enable
hornet hive supervise --run coding/recipes/EXACT_RUN_ID --dispatch
hornet hive supervise --run-dir coding --dispatch --watch --interval 180
```

The first command is read-only. Enrollment alone does not start work. `--dispatch`
allows at most one activation per scan; `--watch` repeats scans and prints only
changed observations. A single invocation without `--watch` is suitable for cron
or Task Scheduler. Hornet does not install an OS scheduler or start a background
service. Keep the terminal running for watch mode, or configure your own scheduler
using absolute executable/project paths. Do not combine an external scheduler
with an unbounded shell retry loop.

Each activation runs synchronously to its next controller stop or completion.
The project supervisor lease stays held during that activation. A concurrent
supervisor returns `supervisor_busy`; manual coding commands retain their normal
worker leases. Cells still execute sequentially. Claims coordinate cooperating
Hornet processes on one controller computer, not separate controllers.

Enrollment freezes a lifetime limit (default **3 activations**, maximum 10) and
a cooldown (default **180 seconds**, range 30–3600). Specify different limits on
the first `--enable` using `--max-activations` and `--cooldown`. Disable/re-enable
does not reset counts or increase these limits. Goal/cell turn, tool, repair and
elapsed-time limits remain in force.

```console
hornet hive supervise --run coding/recipes/EXACT_RUN_ID --disable
```

Disabling affects future activations. To pause or stop an active goal, use its
existing `hornet control RUN --pause` or `--stop` command. A supervisor currently
holding the project lease must finish before another supervisor can change
enrollment. The file control channel remains available during execution.

## Which goals can wake up?

| Saved condition | Supervisor behavior |
| --- | --- |
| Lost model reply, with no worker operation in flight | Eligible with remaining budget. The lost request still counts. |
| Waiting for a claimed model endpoint | Eligible; normal resource claims still apply. |
| Answer recorded for the exact pending question | Eligible after validating the answer binding. |
| Approved root plan or recorded request to revise it | Eligible using that exact decision. |
| Paused checkpoint with a queued explicit `--continue` | Eligible if all other conditions permit. |
| Verified goal with unfinished closeout | Resume closeout only. |
| Live process or active worker lease | Observe; never kill or steal its work based on age. |
| Pause, operator stop/cancel, unanswered/cancelled question | Wait for the operator. |
| Unknown write, command or verification outcome | Stop for inspection. |
| Failed checks, malformed contracts or other unexplained errors | Stop for inspection; no guessed retry based on error text. |
| Budget or activation limit reached | Stop; preserve history. |

The supervisor rechecks the observed checkpoint, child job, controls, answers,
plan decision and assignment **inside the worker lease**, before contacting the
worker or model. Normal resume then checks the actual worker identity and source.
Changed source is refused. A race to the worker lease can still refuse an
activation; the refusal is retained and counts toward the limit.

Whole-goal repairs are handled by the existing bounded repair controller.
Supervision can resume an interrupted repair; it cannot invent another repair,
broaden file permissions, change acceptance criteria or restart a completed goal.
Standalone `work` jobs are not enrolled by this version.

## Retained evidence and interruptions

Each enrolled goal contains:

```text
supervisor/policy.json
supervisor/enabled.json
supervisor/attempts/1/decision.json
supervisor/attempts/1/outcome.json
```

The decision is saved and charged before activation. It identifies the exact
goal, observed state, eligibility reason and frozen policy. The outcome is linked
to that decision. Original controller attempts, failed checks and cell receipts
remain intact. The hive terminal shows enrollment, counts and unresolved receipts.

If the supervisor dies before recording an outcome, automatic dispatch stops.
Inspect the worker and saved goal, and use normal `autoresume` only when its
checks allow it. After inspecting the interrupted supervisor attempt, acknowledge
that specific attempt:

```console
hornet hive supervise --run coding/recipes/EXACT_RUN_ID --acknowledge 1
```

Acknowledgment records the operator action; it does not claim the work succeeded,
erase the attempt, grant more budget or resolve an unknown worker operation.
An unchanged boundary is never automatically reactivated after an attempt.
Manual resume or an explicit new control/answer can produce a new boundary.
Legacy `commander_input.txt` input requires normal manual resume so the controller
can import it in order; the supervisor does not guess its ordering.

Watch mode exits on Ctrl+C. If an activation is running, its normal interruption
handling applies and its evidence remains saved. Repeated unchanged scans do not
call a model or accumulate conversation history.
