from .atomic_interface import execute as create

def execute(args):
    mapped = dict(args)
    if mapped.pop("language", "python") != "python":
        return {"status": "ERROR", "message": "Only Python is implemented"}
    mapped.setdefault("commit", False)
    return create(mapped)
