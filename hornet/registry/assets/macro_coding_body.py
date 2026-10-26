from .atomic_body import execute as fill

def execute(args):
    mapped = dict(args)
    mapped["logic"] = mapped.pop("new_body")
    mapped.setdefault("commit", False)
    return fill(mapped)
