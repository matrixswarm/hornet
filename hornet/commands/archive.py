"""Compatibility entry point; returns the new CLI's integer exit status."""
def archive_nodes(args=None):
    from hornet.cli import main
    return main(["archive", *(args or [])])
