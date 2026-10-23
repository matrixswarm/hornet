# Hornet roadmap

Updated 2026-10-23. This records planned work; it is not a list of released features.

## 1. Finish the setup and contract-review maintenance release

Complete the current 0.16.1 preview before expanding the execution model:

- Make worker provisioning and the first-goal instructions dependable on Windows
  and Linux hosts; preserve existing workers and failed runs during recovery.
- Check the actual Git baseline, protected worker code and connection before
  spending model turns.
- Distinguish a cell's single typed result from the acceptance checks that cover
  the full requirement. Keep review evidence bound to its instruction version.
- Exercise the packaged first-user path, finish regression checks and document
  remaining limitations before preparing a release.

## 2. Prototype builders with durable contracts

Daniel's October 23 proposal is the reference design for this next capability:
an orchestrator selects a named builder, supplies an interface and destination,
and receives a UUID-bound contract. An assigned agent supplies implementations
through a return tool. The builder reconstructs and verifies the finished product.

Start with the Python Strategy pattern. See
[the builder design](CONTRACT-BUILDERS.md) for the lifecycle, proposed tools,
ownership rules and acceptance criteria.

The reusable unit is provision -> implement -> return -> assemble -> verify.
Language and artifact adapters supply the structure and checks. Preserve Hornet's
existing goal/cell identities, bounded contexts, failed attempts, revisions,
workspace boundaries and return manifests throughout.

### First deliverable

A local model creates two interchangeable Python strategies and a context that
uses their declared interface. Hornet assembles the returned bodies into an
importable package, verifies behavior, and supports a targeted method revision
without changing the original result.

Use one worker and sequential execution for this prototype. Design independent
submission areas now; concurrent execution needs its own implementation and
validation before it becomes a supported feature.

## 3. Expand after the prototype proves useful

- Add further patterns and language adapters based on completed user tasks.
- Add builder discovery by capability with only the selected schema in context.
- Extend the same contract/return lifecycle to other structured artifacts where
  assembly and acceptance can be defined explicitly.

Measure completion, retries, prompt size, assembly failures and revision
preservation. Do not treat generated structure or passing syntax checks as proof
of complete application behavior.
