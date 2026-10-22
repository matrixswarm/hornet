"""Compatibility entry point; returns the new CLI's integer exit status."""
def init_project(args=None):
    from hornet.cli import main
    return main(["init", *(args or [])])
