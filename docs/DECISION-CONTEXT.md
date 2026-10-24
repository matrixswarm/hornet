# Decision authority and working context

Recorded 2026-10-24 from Daniel F. MacDonald's design discussion.

Status: agreed design direction. Hornet already has scoped work packets, frozen
contracts, dependency receipts, bounded contexts and retained attempts. This
note specifies how those pieces should guide decisions. It does not claim that
every proposed packet field or conflict-routing behavior is implemented.

## Intent

Give each agent enough context to understand its contribution, focus on one
assignment, recognize completion and recover its place. The controller keeps
the durable project state; a model context should not have to remember the
whole conversation or reconstruct the project by searching directories.

Separate decision authority from working attention. An overall requirement can
govern every cell while occupying very little space in each working prompt.

## Decision authority

| Priority | Authority | Meaning |
| --- | --- | --- |
| 1 | Hard boundaries | Allowed actions, file ownership, permissions, resource budgets and protected behavior. A task cannot expand these implicitly. |
| 2 | Overall goal and its acceptance criteria | The intended outcome and the evidence required for completion. Preserve the original requirements and their identity. |
| 3 | Branch goal | The branch's contribution, dependencies and responsibilities within the overall goal. Here, branch means a branch of the cell tree. |
| 4 | Current cell contract | The assigned behavior, inputs, outputs, owned files, preservation requirements and checks. |
| 5 | Implementation choices | The agent's choice of method within that contract and its boundaries. |

This order guides contract formation and conflict detection. A higher-level
purpose does not authorize a worker to bypass its frozen cell contract, rewrite
checks or edit another cell's files. Requirements and checks can themselves
conflict; passing an incorrect check does not establish the intended outcome.

On a contradiction, preserve the evidence and report the conflicting requirement
and contract references to the supervisor. The design target is to hold affected
work while the supervisor resolves it within its existing authority and budget.
Independent work may continue when its prerequisites remain valid. Missing
information must remain explicit. Human input is needed when the resolution
requires information or authority the system does not have.

Any authorized contract change needs a recorded revision with prior evidence
retained. Current Hornet revisions preserve inherited checks; general automatic
conflict routing and selective supersession of checks remain planned.

## Working attention

The agent's immediate working context should emphasize, in this order:

1. The current cell objective and the next unresolved decision.
2. Its success criteria, preservation requirements and return contract.
3. Exact target files, relevant source excerpts and verified dependency inputs.
4. The latest attempt outcome, failed check or missing fact.
5. A short branch-purpose and overall-goal reminder.

Include applicable hard boundaries in every assignment. Keep relevant assigned
requirements verbatim; a short purpose summary must not replace or weaken them.
Store broader history, large artifacts and other branches outside the prompt,
retrievable through bounded references when needed.

Current multi-cell workers receive assigned requirements and dependency
evidence. Consistent branch-purpose and overall-purpose reminders are a planned
addition. They should be derived from the saved goal, bound to its revision,
and identify the relevant requirement IDs. Avoid copying the complete goal and
all ancestor histories into every descendant.

## Pointer and artifact identity

A pointer identifies the assignment and its data. Resolve it through:

```text
goal identity -> cell lineage -> contract revision -> workspace -> exact files
```

Track attempt identity separately: retrying an attempt is different from changing
the contract. Bind input artifacts and dependency returns to their producer,
revision and evidence digest. The controller validates the resolved workspace,
ownership and source state before permitting operations.

A filename, model label, recent directory or first matching pointer is not
sufficient identity. A follow-up must resolve to the intended saved goal and
cell. Stale or ambiguous references require resolution before editing.

## The complete cell contract

A return schema is one part of the contract. The complete assignment needs:

- Required behavior and the requirement IDs it satisfies.
- Inputs, dependencies and explicit unresolved facts.
- Owned files or artifacts and the exact workspace binding.
- Existing behavior and files that must remain intact.
- Acceptance checks and evidence needed to declare completion.
- The selected call's return type/value constraints or artifact return manifest.
- Action limits, attempt budget and recovery conditions.

A correctly typed return alone cannot establish success. Check both the cell
and the assembled goal against their applicable criteria. Unknown outcomes
remain unknown until evidence resolves them.

Review contexts need the same discipline. When deriving a selected call's
expected output, provide that call's actual inputs and applicable requirements.
Omit the proposed answer. Clearly distinguish other examples from the call
being judged; the live trial showed that confusing them can cause false
rejections. Treat model derivations as evidence subject to error and retain
executed acceptance checks.

## Recovery and implementation checkpoints

Rebuild a fresh working context from the unchanged contract, confirmed progress,
current source identity, latest failure and relevant inbox information. Keep
earlier attempts available by reference. Record the conditions of a failure;
changed inputs or a new hypothesis may justify a bounded retry without erasing
that failure. An interrupted action with an unknown outcome needs reconciliation
before retrying an action that could be duplicated.

Before implementing the remaining context additions, demonstrate that:

- A child can identify its contribution without receiving the full conversation.
- Splitting deeper or retrying does not accumulate ancestor transcripts.
- A local assignment cannot silently override an overall requirement or boundary.
- A stale pointer cannot redirect work to another goal, revision or workspace.
- Recovery retains completed work, failed evidence and the existing budgets.
- A successful cell cannot conceal a failing parent or whole-goal check.

See [goal controller behavior](GOAL-CONTROLLER.md),
[durable goal packets](CLI.md#durable-goals-083) and the
[quality-cycle roadmap](ROADMAP.md#1-establish-a-repeatable-quality-cycle).
