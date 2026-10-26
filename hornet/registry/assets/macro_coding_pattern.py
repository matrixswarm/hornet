from .atomic_pattern import execute as apply

def execute(args):
    mapped = dict(args)
    mapped.setdefault("class_name", "Strategy")
    mapped.setdefault("commit", False)
    return apply(mapped)
