# Registry tools for coding cells

Available in 0.18.0. Existing goals keep their saved tool selection. New
`autowork` code cells and revision repair jobs can use the registry alongside
ordinary file tools.

The registry gives an agent small, discoverable contracts for common changes.
An agent chooses the operation and supplies its arguments; Hornet validates the
contract, prepares a candidate and verifies the worker's result. A Python method
body, for example, can be replaced using its class and method names without
asking the model to reproduce the surrounding file.

## Agent procedure

1. `find_asset(query)` returns a bounded list of canonical capability names.
   Search for an operation, such as `method` or `read`, rather than a project
   class such as `Calculator`. Underscores, hyphens and dots split search words;
   `method_body` finds method/body operations. Exact IDs/aliases rank first.
   A search with no matches returns guidance, without unrelated editor suggestions.
2. `get_asset_details(uri)` supplies the exact input schema, examples and limits.
   Hornet requires this inspection before that tool can be called. The execution
   menu considers the two most recently inspected tool contracts. Edits of
   existing files appear only after a current read. Inspect another operation
   to switch; inspect an older one again to bring its arguments back.
3. Read the assigned file with `read_file` when offered and retain its short
   `read_ref`. Alternatively, `files.text.read` and `data.json.query` provide
   `source_sha256`. Paths are relative to the cell's isolated Linux workspace.
4. `call_proxy_tool(uri, arguments)` performs the chosen operation. Use an object
   for `arguments`; the cell menu presents that operation's exact object schema.
   The underlying gateway also accepts one JSON encoding of an object for
   compatible clients. Arrays, double encoding, unknown fields and guessed
   argument names are rejected.
5. For an existing file, supply `read_ref` from that job's confirmed `read_file`,
   or its current `expected_sha256`, never both. The controller resolves a short
   reference to the observed hash; stale or cross-file references are rejected.
   The execution menu offers exact observed versions for each file. A completed
   write replaces its old version with the independently confirmed readback hash.
   Menu reconstruction never upgrades an old reference to a newer file version.
   New files use `expected_sha256=""`. Set `commit` explicitly.
   `commit=false` returns a `STAGED` preview retained in the job's
   receipt. Repeating with `commit=true` recomputes from the same source version.
6. Inspect the controller's committed-file readback, call `check_ticket`, and finish when the checks
   pass. The controller still runs its final checks and the full test suite.

`read_recipe(uri)` provides a short procedure. `registry_status()` shows this
cell's allowed files, assets and implementation digest. The complete catalog
never needs to accumulate in the model's active context.

Distinguish adding a new definition from replacing an existing body before
selecting an operation. Body replacement preserves the existing name and
signature. To add a new function, discover an insertion operation, inspect its
schema and use the exact requested name. A similarly named existing function
is not a substitute; preserve its behavior unless the goal requests a change.

### A method edit

After inspecting both contracts, read:

```json
{"uri":"files.text.read","arguments":{"path":"calculator.py"}}
```

Then call the following with the actual 64-character source hash from the read:

```json
{
  "uri": "languages.coding.python.method.fill",
  "arguments": {
    "path": "calculator.py",
    "class_name": "Calculator",
    "method_name": "double",
    "logic": "return value * 2",
    "expected_sha256": "<source_sha256 from the current read>",
    "commit": true
  }
}
```

The placeholder is explanatory, not a usable hash. The body uses ordinary Python
indentation relative to the method. The tool preserves nested indentation and
decorators, but replaces the complete body, including its docstring. It refuses
ambiguous targets and inline one-line methods. Syntax validity is only one check;
the cell's behavioral checks establish whether the implementation meets its task.
Supplying the target's whole `def` as its body is rejected before writing; genuine
nested helper functions followed by the enclosing method's logic remain supported.

Method insertion performs body preparation automatically: remove a common margin,
parse the body, add the target's enclosing indentation, and compile the complete candidate
before writing. The agent need not call a formatter first. Sequential statements
start at column 0 in `logic`; only nested suites are indented. Syntax errors report
the body line and explain the expected layout, without flattening nested logic.

For a separate diagnostic, `files.text.indent` accepts `op="python_body"` and
returns prepared `text`. The same preparation is used by `method.fill` and
`method.stage`. A successful body parse alone does not prove that `break`, `await`
or other context-sensitive statements are valid in the target function, nor that
the application is correct. Whole-file compilation and acceptance checks remain
required. `recipe.precision_formatting` describes this route; it is procedure
data, not an executable macro. Generic `reset` only removes a common margin.
Python body preparation preserves multiline string contents while adjusting code
indentation. Insertion also compares the resulting parsed structure with the
intended replacement, so formatting cannot silently change a literal or sibling.

## Namespaces and available assets

Logical names are capability IDs. They are never imported as Python paths or
executed as filenames. Older `mcp.*` names resolve through explicit aliases;
discovery and receipts use the canonical names.

| Namespace | Cell operations |
| --- | --- |
| `files.text` | `read`, `indent`, `atomic_edit` |
| `data.json` | `wrap`, `query`, `edit`, `batch`, `validate_shape`, `move`, `ordered_edit` |
| `languages.coding.python` | `interface.create`, `interface.stage`, `method.fill`, `method.stage`, `imports.add` |
| `languages.coding.python.design_pattern` | `strategy`, `stage` |
| `recipe` | `atomic_commit`, `precision_formatting` |

There are 17 reviewed cell tools and two procedural recipes. JSON edits require
an existing JSON file. Interface creation supports a new Python file. The
Strategy helper appends a context class to an existing module; it does not yet
implement the planned multi-agent contract assembler described in
[CONTRACT-BUILDERS.md](CONTRACT-BUILDERS.md).

### Restrict the tools for a goal

From a configured Hornet project:

```console
hornet autowork --worker-config coding/worker.json --agent gemma --scope-file calculator.py --registry-asset files.text.read --registry-asset languages.coding.python.method.fill --goal "Fix Calculator.double using the registry, read back, and check the ticket."
```

Replace `gemma` with your `hive.json` label. Without a hive, use `--model` and
`--base-url` as described in [CLI.md](CLI.md). Repeat `--registry-asset` for each
canonical capability. Omission makes all reviewed cell assets available; this
flag restricts registry capabilities, not the ordinary cell file tools.

To require registry edits while letting the agent discover the operation itself:

```console
hornet autowork --worker-config coding/worker.json --agent gemma --scope-file calculator.py --registry-only --goal "Fix integer doubling in calculator.py and preserve the public interface."
```

`--registry-only` removes `edit_file`, `write_file`, `run_command` and `run_python`
from the model's tools. A model call to one of them is refused before dispatch.
The controller still uses its internal journaled write to commit an accepted
registry candidate. The requirement carries into cell jobs, bounded goal repairs
and subsequent revisions; return evidence records it. No individual asset needs
to be named. Combine it with `--registry-asset` to narrow the catalog further.

This is a workflow constraint, not a new sandbox. Existing tests and acceptance
checks still execute in the worker VM. If no allowed asset can perform the task,
the agent must report that limitation; there is no automatic direct-write fallback.

The selection is frozen for resume and carried into revisions. The file scope
still governs every registry target. Shell execution, generated-code execution,
arbitrary plugin imports and mixed read/write legacy line helpers are excluded
from this cell adapter.

## API evidence for generated checks

For a new `--registry-only` goal, check authors receive a small declaration map
of their assigned Python files. It distinguishes module functions, instance
methods, static methods and class methods, with parameter names and required
arguments. Hornet parses syntax without importing the source. Method bodies,
default values and implementation behavior are omitted; expected results must
still come from the goal.

The same evidence reaches contract review and independent expected-result
derivation. It is bound to source hashes and retained under the role's
`interfaces.json`; resume rejects a changed artifact or missing binding. Existing
frozen roles keep their original evidence and checks.

For directly imported classes and directly constructed instances, a syntax
preflight rejects scalar equality assertions on known ordinary methods, such as
`instance.name == "v1"`; it asks the author to call the declared method. The
rejected check remains in the role history. This is a narrow check of the current
API, not complete type inference: unknown factories, decorated methods and
dynamic bindings remain subject to review. Constructors, inheritance, class
decorators and custom attribute lookup prevent a static method-object rejection;
unknown side effects also discard inferred instance bindings. Changes from methods to properties
need updated API declarations before these checks can be frozen.

Before freezing new generated checks, the reviewer sees Python rendered from its
parsed syntax without comments, making exception-handler scope visible. The
original submitted text remains in the immutable review evidence and is the text
used for execution. Check-writing guidance recommends the standard library's
`TestCase.assertRaises` callable form to keep exception cases independent.
Each acceptance check executes as a Python script. unittest/pytest discovery
does not run automatically; defining a test class alone does not execute its
assertions. Use module-level assertions or explicitly invoke checking helpers.

Independent expected-result derivation also receives bounded call-site syntax.
For example, `instance = FlatRate(); instance.quote(500)` includes the qualified
`FlatRate` constructor, giving the reviewer evidence to distinguish it from
`WeightRate.quote`. The model can still misinterpret that evidence.
Simple aliases and earlier literal calls on the same object are recorded in
order. Each check starts with separate bindings. Unknown control flow, mutations
or oversized context are marked unresolved; factory return types are not inferred.
The packet omits proposed answers and check descriptions. Existing saved oracle
packets retain their original interpretation.

The map is limited to eight files, 32 declarations per file and 12,000 JSON bytes.
Missing, unparseable and omitted files are reported explicitly. Dynamic APIs,
inherited methods and custom decorator behavior are not inferred. This improves
the information available to the model; it does not prove that generated checks
are correct. See [VALIDATION.md](VALIDATION.md) for the live trial and its limits.

## Writes, evidence and recovery

- A reviewed asset runs on a disposable controller-side copy of the exact worker
  source. It does not receive access to the live worker filesystem. Generated
  Python is parsed/compiled there, never executed there.
- The prepared content goes through Hornet's existing scoped `write_file`
  operation journal. Existing files require a matching source hash; new files
  must still be absent when committed. Worker code needs no registry installation.
- Every call, including a rejected call, has an immutable preparation receipt
  under its job's `registry/` directory. Write receipts remain under `operations/`.
  The return manifest links both sets, committed readback evidence, the canonical
  URI, status and cell binding.
- A completed receipt can recover a lost write reply without repeating the write.
  A started operation without completion evidence stops for inspection.
- Three identical failures against unchanged source stop the current attempt.
  A changed source gives a new retry identity. Failed evidence remains intact.
  New read handles for the same observed bytes share the same failure identity;
  rereading does not reset that budget. A corrected body or different observed
  version is a different attempt. Old references never advance to current bytes.
- Repeated discovery/read calls also participate in the existing stagnation
  detector: three identical operations within six operations on unchanged source
  can trigger a bounded fresh-context handoff. Changing query wording is not
  detected as the same search; overall turn and tool budgets still apply.
- The saved binding covers the file scope, allowed assets and registry source
  digest. A changed implementation or changed permissions cannot silently resume
  an old cell. Keep the matching Hornet package and environment with the saved
  run; loading its completed cell evidence and creating a linked revision also
  validate that binding. Upgrading the registry does not migrate old bindings.

After a confirmed registry write, the controller independently reads the committed
bytes and checks the current source snapshot and worker binding. It retains the
bytes under `registry-readbacks/` and gives the model the current hash and a preview
of at most 80 lines/6,000 characters, marked when truncated. An interrupted read
can be repeated with the original durable write receipt; the write is not repeated.
Failure to read or verify the bytes prevents completion. The readback gate requires
an observation of the current committed version. It
does not prove that a model understood every byte; final behavioral checks remain
necessary. This implementation has a 256 KiB candidate limit and a 64 KiB registry
result limit, with smaller practical limits for worker transport and active
context. Large observations are explicitly omitted from active context while
retaining their receipt and status; request a smaller range when needed.

## Dependency behavior in task packets

New goals include the declared behavior of completed direct dependencies and
inherited parent inputs in each consumer's task packet. Providers are identified
by their task, file paths, result-call inputs, contract hash and saved work-packet
reference. Their assigned requirements are copied verbatim and deduplicated
against the consumer's own requirements. The dependency value and return-manifest
reference remain in the existing input record.

The packet does not expand transitive histories, source bodies or unrelated cells.
Declarations explain how to use a dependency; they grant no extra file access and
do not establish that every declared behavior was tested. Existing context budgets
still apply. The policy and resulting packets are frozen and checked on resume;
older saved goals keep their original packets.

For code consumers, the current policy also captures a compact Python API outline
from direct code providers' files outside the consumer's own file scope, at the
consumer's starting snapshot. Research findings retain their cited return data;
research cells and files already owned by the consumer add no API outline or
extra API guidance. This avoids duplicating source context. The outline distinguishes module
functions, instance methods, static methods and class methods and lists parameters.
The controller parses source without executing it or forwarding implementation
bodies. The capture is bounded to eight files, 32 declarations per file and a
12,000-byte outline; omissions are explicit. Inheritance, decorators and dynamic
lookup remain uncertain. The saved outline is bound to the provider contracts,
source snapshot and work packet; a restart reuses that exact evidence. The earlier
behavior-only policy remains readable and never silently adds new declarations.

Check preflight also flags bare method/scalar comparisons after earlier calls to
declared methods. Such calls may alter attributes, so the diagnostic reports
ambiguity and asks the author to call the method or make intentional dynamic
lookup explicit. It does not claim that stateful attribute changes are impossible.
Unknown calls, rebinding and control flow discard the type hint. Requirement review
and executed checks still decide whether explicit dynamic behavior is correct.

## Dependency checks before handoff

New flat goals with a caller-supplied acceptance file also run eligible goal checks
before closing an upstream cell. Eligibility uses the frozen plan: the check must
touch this cell's files, all declared file owners must be this cell or its transitive
dependencies, and this cell must have a downstream consumer. This catches known
dependency defects while their owning cell can still repair them. File permissions
do not expand; a check may read a dependency without granting write access to it.

The controller retains the authored checks and typed return, then appends eligible
caller checks up to the existing eight-check limit. Overflow is recorded as deferred.
Checks involving later writers, terminal cells and nested plans stay at the final
gate. All original goal checks still run at the end. The routing policy, original
authored checks and selected check IDs are retained and verified on resume. Older
saved goals without this policy retain their original behavior. Eligibility relies
on accurately declared check paths; it does not infer hidden imports.

## Standalone MCP server

The same reviewed catalog is packaged for direct MCP clients:

```console
python -m hornet.registry --allow-uri files.text.read --allow-uri data.json.query
```

Configure your MCP client to launch that executable and arguments over stdio.
The server exposes five gateway tools: `find_asset`, `get_asset_details`,
`call_proxy_tool`, `read_recipe`, and `registry_status`. Native assets are loaded
from the installed package; the catalog is authoritative and SQLite is only an
optional rebuildable index. No D: drive, prototype checkout or database is needed.

Direct MCP calls operate on the MCP server host with that process's permissions.
They do not receive Hornet cell scope, VM isolation, operation recovery or return
manifests. Use the integrated coding-cell path when those guarantees are needed.
Without `--allow-uri`, the standalone server exposes the full native catalog,
including runtime shell/Python helpers; choose an explicit allowlist for the client.

## Extending the registry

1. Choose one canonical capability name and add its closed argument schema,
   description, examples, effect classification and explicit aliases to
   `hornet/registry/asset_catalog.json`.
2. Add a package module under `hornet/registry/assets/` with
   `execute(arguments) -> dict`. Return `SUCCESS`, `STAGED` or `ERROR` explicitly.
   Preserve failure details; do not convert a native error into success.
3. Add behavioral tests for valid inputs, malformed arguments, stale hashes,
   failed previews and exact target selection. Register recipes as data.
4. To expose it to coding cells, review its effects and add an adapter in
   `hornet/coding/registry.py`. The current adapter supports pure values or one
   declared file argument. A new catalog entry alone grants no cell access.
5. For multi-file effects or external services, implement an explicit scoped,
   recoverable adapter and its failure tests first. Keep the generic gateway
   unchanged and avoid putting the whole catalog in the prompt.

This establishes discovery, small contracts and durable file commits. Distributed
method submissions, contract UUID revisions and package assembly remain separate
planned work.
