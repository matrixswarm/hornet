# Hornet Coder and shared services

Hornet includes Mission Control and the existing command-line workflow:

- `hornet.coder` launches Mission Control for coding projects, revisions and
  reviewed model missions. Existing `hornet.desktop` imports and launchers
  remain supported.
- `hornet.core` exposes shared interfaces for existing workspace, model and
  registry services. Implementations remain in their existing modules.
- `hornet.registry` contains tool contracts, recipes and a standalone MCP
  gateway. A tool is available to a client only when connected and permitted.
- The CLI manages coding goals, scoped work and recovery evidence. Its worker
  workflow is separate from Mission Control's project revisions.

## Data and interaction boundaries

Mission Control projects default to `~/.hornet/projects`. Missions and project
initialization send selected source and prompts to the configured model server.
Proposals require review before acceptance. An empty optional VM check skips the
check; the optional runner requires a separately configured VM. See the
[desktop guide](DESKTOP.md) and [security boundaries](../SECURITY.md).

The standalone registry runs tools with the server process's permissions; scope
its allowlist to the client. See the [registry guide](REGISTRY.md).

## Development verification

From the repository root with Python 3.9 or later:

```sh
python -m unittest discover -s tests -v
```

The unit suite uses temporary projects and simulated endpoints. Live model trials
are separate and should not use personal projects without the owner's consent.
