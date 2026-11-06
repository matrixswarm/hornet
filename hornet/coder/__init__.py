"""Mission Control: the desktop coding application.

The desktop implementation remains available at ``hornet.desktop`` for
compatibility with existing launchers. Importing this module does not start a GUI.
"""

def main():
    """Launch Mission Control using the existing desktop implementation."""
    from hornet.desktop.app import main as desktop_main
    return desktop_main()

__all__ = ["main"]
