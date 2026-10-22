# Security and execution boundaries

Agent-supplied Python and commands execute in a dedicated worker VM. The starter
uses a non-root guest account without sudo, a fixed SSH entry point, pinned host
keys and loopback-only SSH forwarding. The VM has outbound NAT networking.

Goal folders share the guest account. They separate source work but do not isolate
arbitrary code from other files owned by that account. Keep unrelated secrets and
production workloads out of this VM. The controller's journals are restart
evidence within its account, not tamper-proof attestations.

Do not disable host-key verification to resolve a connection error. Preserve
failed attempts and uncertain-operation records for inspection. Review exported
changes before applying them to another project.

## Report a vulnerability

Use GitHub's private vulnerability reporting for this repository when available.
If unavailable, open an issue requesting a private reporting channel without
including exploit details, keys, passwords or private logs. Supported release:
0.15.x. A release signature identifies publisher-approved bytes; it does not
establish that every behavior is safe or that every dependency was audited.
