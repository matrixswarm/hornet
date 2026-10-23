# Builders with durable contracts

Status: planned prototype, recorded 2026-10-23. No tools described here are
implemented or released yet. Origin: Daniel F. MacDonald's proposal for an
orchestrator to provision an interface, assign its implementation through a UUID
contract, and assemble agent returns into a complete product.

## Intent

Let an agent decide what to build, then give it a small, explicit implementation
contract. A versioned builder owns the structural rules: interface, signatures,
package layout, implementation slots and assembly. Agents supply the behavior
inside those boundaries. Verification covers both individual implementations
and the assembled application's use of them.

Use the Strategy pattern in Python as the first example. Keep the orchestration
lifecycle general enough for other patterns, languages and structured artifacts.
The orchestrator should choose this builder only when interchangeable behavior
fits the requested design; simpler work can use ordinary coding cells.

## Catalog and MCP boundary

The proposed logical address is:

```text
languages.coding.<language>.design_pattern.strategy.server.py@create_strategy
```

Treat this as a catalog address for an explicitly registered, versioned handler.
The eventual MCP adapter maps it to exposed tools. It must not execute arbitrary
Python paths or import names supplied by a model. Physical module layout and wire
tool names remain adapter decisions.

Expose the selected builder and its return operations to the working agent.
Keep the rest of the catalog outside its prompt; discovery returns small
capability summaries. One process may host multiple builders.

Proposed operations, with names illustrative until the prototype is implemented:

| Operation | Responsibility | Small return |
| --- | --- | --- |
| `create_strategy` | Validate a specification and provision its contract and scaffold. | Contract ID, revision, digest, slot IDs, artifact references. |
| `read_contract` | Fetch the assigned slot, shared interface and relevant dependencies. | Bounded specification and source excerpts. |
| `submit_implementation` | Record a body for a slot against the exact contract revision. | Submission ID, body digest, validation findings. |
| `assemble_contract` | Combine accepted submissions into a candidate package and run checks. | Status, output manifest, findings and evidence references. |

The return tool accepts implementations as data. Submission is distinct from
successful verification and from promotion into the user's destination.

## Provisioning the contract

The orchestrator supplies:

- Goal ID, owning cell ID and the builder's pinned version.
- Package location relative to the authorized goal workspace; exact output files.
- Interface and implementation class names; the context that consumes the interface.
- Stable method IDs, parameters, return types and sync/async behavior.
- Relevant behavioral requirements, examples, invariants and acceptance checks.
- Declared state, constructor dependencies, imports and external dependencies.
- Assignment boundaries, attempt budgets and dependency order.

The builder validates identifiers, paths, collisions and supported features before
creating anything. For Python's first adapter, use an abstract base class with
explicit method signatures; validate the generated structure and executable
behavior. Type annotations alone cannot establish correct behavior.

The interface becomes a shared dependency. If an implementation needs a different
signature or new shared state, its agent requests a contract revision instead of
silently modifying the interface. Freeze a revised contract and revalidate its
dependents before assembly.

The orchestrator decides cell granularity. One cell can implement a cohesive
class or several related methods; every method need not become a separate cell.

## Contract storage and access

Proposed logical layout inside a goal's managed storage:

```text
contracts/<contract-uuid>/
  revisions/0001/
    contract.json
    scaffold/
    checks/
    submissions/<cell-id>/<attempt-id>/
      methods/<method-id>.py
      submission.json
    assemblies/<assembly-id>/
      product/
      verification.json
      return-manifest.json
  events/
```

Bind the UUID to the goal, cells, workspace, contract revision and contract digest.
The UUID identifies a contract; it does not grant access or prevent conflicting
writes. The controller enforces ownership and scope through tools and leases.

Both the orchestrator and assigned agents can access the same logical contract
through artifact handles. They need not share an operating-system drive path;
remote model connections still use the controller's authorized worker tools.

Keep immutable specifications and submissions apart from generated candidates.
Only the assembler writes the canonical candidate package. Each agent writes to
its own assigned submission area. Preserve failed submissions, diagnostics and
previous outputs for later investigation or changed conditions.

## Returning method bodies

An agent receives the exact signature, declared state/imports, requirements and
relevant interface excerpts. It returns the body for a stable implementation slot,
such as `multiply.apply`, with:

- Contract ID, revision and expected contract digest.
- Cell/assignment identity, slot ID and a unique operation ID.
- Body artifact reference and content digest; optional bounded inline source.
- References to declared dependencies and any evidence it produced.

Keep substantial source in artifacts rather than repeatedly escaping whole
classes inside JSON. The tool computes byte counts and digests; the model does
not calculate lengths or checksums. Opaque references are resolved by the
controller inside the authorized workspace.

The first adapter accepts method bodies only. Imports and attributes must already
be declared, or proposed through a revision. Do not let a body submission replace
the interface, add unrequested top-level code or edit another slot.

For Python, parse the body inside its exact method context, enforce the permitted
shape, and assemble using syntax-aware generation. Avoid blind string insertion
or replacement based on method names. Parsing checks syntax and structure; it
does not establish that code is safe or that its behavior is correct. Execute
candidate code only through the worker's existing execution boundary.

## Assembly, verification and recovery

1. Freeze the contract and baseline. Scaffold creation is repeatable for the
   pinned builder version and validated input.
2. Assign slots. Agents return implementations into separate attempt areas.
3. Validate each submission against its assignment and contract revision. Keep
   rejected source and give its author specific, bounded feedback.
4. Once all required slots have accepted submissions, assemble a fresh candidate
   with an explicit mapping from slot to submission digest.
5. Check syntax, imports, interface compatibility, exact file scope, behavior,
   existing regressions and interactions through the context. A structurally
   valid class is not yet a verified product.
6. Write a return manifest recording exact output paths and hashes, selected
   submissions, builder/contract versions, dependency manifests and check results.
7. Mark the candidate verified only after the required checks pass. Promote or
   export through Hornet's controlled integration path, checking the destination
   baseline again. Mere reconstruction must not overwrite unrelated files.

One assembler holds the write lease. Versioned candidate directories and a
recorded promotion operation make interrupted assembly recoverable; completion
must never expose a half-written multi-file product as verified. Unknown effects
stop for reconciliation under the existing operation-receipt rules.

Retrying the same operation ID with identical input returns its saved receipt.
Reusing it with different input is refused. Revisions retain their parent and
require explicit selections of which earlier bodies remain compatible. They do
not clear failed attempts or reset the goal's shared budget.

## First prototype and acceptance criteria

Build a small Python package containing:

- `IntegerStrategy.apply(value: int) -> int` as an abstract interface.
- `DoubleStrategy` and `NegateStrategy` as concrete implementations.
- A context that accepts an `IntegerStrategy` and delegates through `apply`.

Let a local model supply the implementation bodies. The builder generates the
declared structure. Verify positive, negative and zero inputs, exact integer
results, and substitution of both strategies through the same context.

Before expanding the catalog, demonstrate:

- A complete model-authored result passes independent integration checks.
- Missing, duplicate, unknown or malformed slots cannot produce a verified output.
- Stale contract digests, conflicting submissions and out-of-scope paths are refused.
- Repeated return calls are idempotent; interrupted assembly resumes from evidence.
- A targeted method revision preserves the old product and unaffected source.
- Failed submissions remain discoverable and useful to the next assigned agent.
- Requests carry only the needed contract slice; source and history stay in artifacts.
- Two separately authored submissions combine without agents writing each other's
  files. Execution can remain sequential for this first milestone.

Record model, request count, peak prompt tokens, retries, generated-file changes
and independent checks for each trial. Broader OOP construction and other artifact
types follow only after this lifecycle works reliably in the focused prototype.

## Relationship to existing Hornet

Reuse goal/cell identity, dependency manifests, isolated goal workspaces, source
snapshots, bounded recovery and closeout. Current code in `hornet/coding/returns.py`,
`workspaces.py`, `cells.py` and `contract_wire.py` supplies relevant building blocks.
The builder protocol, slot assignment, syntax-aware assembler and MCP exposure
are new work; they must not bypass those existing boundaries.

The planned order is recorded in [the roadmap](ROADMAP.md).
