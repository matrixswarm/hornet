"""Compatibility entry point; returns the new CLI's integer exit status."""
def generate_content(args=None):
    from hornet.cli import main
    return main(["generate", *(args or [])])
