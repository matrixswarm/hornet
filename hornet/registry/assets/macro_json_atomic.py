from ._common import execute_guarded
from ._json_ops import mutate

@execute_guarded
def execute(args):
    def operation(data):
        if not isinstance(data, dict):
            raise ValueError("Ordered editing requires an object root")
        op, node = args.get("op", "INSERT_AFTER"), args["node"]
        if op == "DELETE":
            if node not in data:
                raise ValueError("Node does not exist")
            del data[node]
        elif op == "OVERWRITE":
            if "value" not in args:
                raise ValueError("value is required")
            data[node] = args["value"]
        elif op in ("INSERT_BEFORE", "INSERT_AFTER"):
            anchor = args["anchor"]
            if anchor == node or anchor not in data:
                raise ValueError("Anchor must exist and differ from node")
            if node in data and not args.get("force", False):
                raise ValueError("Node exists; force must be explicit")
            if "value" not in args:
                raise ValueError("value is required")
            data.pop(node, None)
            pairs = list(data.items())
            data.clear()
            for key, value in pairs:
                if key == anchor and op == "INSERT_BEFORE":
                    data[node] = args["value"]
                data[key] = value
                if key == anchor and op == "INSERT_AFTER":
                    data[node] = args["value"]
        else:
            raise ValueError("Unknown operation")
    return mutate(args, operation)
