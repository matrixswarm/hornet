"""Access the existing project, revision, blueprint and initialization services.

A workspace is rooted at ``~/.hornet`` by default; pass a root to ``Workspace``
for isolated projects. Existing accepted-revision safeguards still apply.
"""
from hornet.desktop.workspace import Workspace, read_blueprint
from hornet.desktop.automation import inventory, read_state, require_ready

__all__ = ["Workspace", "read_blueprint", "inventory", "read_state", "require_ready"]
