from ._common import execute_guarded
from ._json_ops import mutate, get, delete, set_value, tokens

@execute_guarded
def execute(args):
    def operation(data):
        src, dst = args["source_path"], args["destination_path"]
        a, b = tokens(src), tokens(dst)
        if a == b or a[:len(b)] == b or b[:len(a)] == a:
            raise ValueError("Source and destination must be distinct, non-overlapping paths")
        if args["migration_type"] == "rename" and (a[:-1] != b[:-1] or not isinstance(a[-1], str) or not isinstance(b[-1], str)):
            raise ValueError("rename requires object keys in the same parent")
        value = get(data, src)
        delete(data, src)
        try:
            get(data, dst)
        except ValueError:
            pass
        else:
            if not args.get("overwrite", False):
                raise ValueError("Destination exists; overwrite must be explicit")
        set_value(data, dst, value)  # Destination indexes evaluated AFTER deletion.
    return mutate(args, operation)
