"""Compatibility entry point; returns the new CLI's integer exit status."""
def show_status(args=None):
    from hornet.cli import main
    return main(["status", *(args or [])])
