"""Shared Hornet services, backed by the existing workspace and registry.

These are compatibility exports: the implementation still lives in its original
modules, so existing installations and project revisions remain usable.
"""
from .workspace import Workspace, inventory, read_blueprint, read_state, require_ready
from .models import chat, discover_models
from .registry import ProxyResolver, RegistryManager

__all__ = ["Workspace", "inventory", "read_blueprint", "read_state", "require_ready",
           "chat", "discover_models", "ProxyResolver", "RegistryManager"]
