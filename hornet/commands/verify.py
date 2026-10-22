"""Compatibility entry point; returns the new CLI's integer exit status."""
def verify_contract(args=None):
    from hornet.cli import main
    return main(["verify", *(args or [])])
