# Security and execution boundaries

Hornet has several interfaces with different execution boundaries:

- The original CLI coding workflow runs agent-supplied Python and commands in
  its configured worker. The starter provisions a dedicated VM with a non-root
  guest account without sudo, a fixed SSH entry point, pinned host keys and
  loopback-only SSH forwarding. The VM has outbound NAT networking. Goal folders
  share the guest account and do not isolate arbitrary code from other files
  owned by that account. Keep unrelated secrets and production workloads out
  of this VM. Do not disable host-key verification to resolve an error.
- Mission Control creates and edits project revisions on the host. Its optional
  test runner copies a revision to a separately configured VM and runs a Python
  command there; it does not provision that VM. An empty check command skips that
  test. Review generated changes and run appropriate checks before accepting or
  exporting them. See [the desktop guide](docs/DESKTOP.md).
- The standalone registry MCP server executes tools on its host with the
  server process's permissions, without the CLI worker's VM isolation or cell
  scope. By default it exposes the catalog except opt-in assets, including shell
  and Python execution helpers. Use explicit `--allow-uri` restrictions appropriate to
  the client; see [the registry guide](docs/REGISTRY.md#standalone-mcp-server).

Project files, prompts and test output sent to a model endpoint
are visible to that endpoint. Review the selection and endpoint before sending
sensitive information. Local project files and run records may
also contain private data. The controller's journals provide restart evidence
within its account; they are not tamper-proof attestations. Preserve failed
attempts and uncertain-operation records for inspection.

## Report a vulnerability

Use GitHub's private vulnerability reporting for this repository when available.
If unavailable, open an issue requesting a private reporting channel without
including exploit details, keys, passwords or private logs. This repository
contains version 0.20.0; see the project releases for published versions and
their validation records. A release signature identifies publisher-approved bytes;
it does not establish that every behavior is safe or that every dependency was
audited.
