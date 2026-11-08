"""Compatibility stubs: native drop-target detection is deferred.

Export uses an explicit folder chooser. This module performs no desktop
inspection, loads no native libraries, and starts no process.
"""


def cursor_position():
    """No native drop inspection is enabled."""
    return None


def resolve_drop_folder(x, y):
    """No location is inferred; the user must choose an export folder."""
    return None
