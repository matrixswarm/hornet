"""Compatibility entry point; returns the new CLI's integer exit status."""
def smash_patterns(args=None):
    from hornet.cli import main
    return main(["smash", *(args or [])])
