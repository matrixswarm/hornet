from ._common import execute_guarded
from ._json_ops import mutate, set_value, delete

@execute_guarded
def execute(args):
    def operation(data):
        for index, op in enumerate(args["operations"]):
            if op["action"] == "set":
                if "value" not in op:
                    raise ValueError(f"operations[{index}]: set requires value")
                set_value(data, op["target_path"], op["value"])
            elif op["action"] == "delete":
                delete(data, op["target_path"])
            else:
                raise ValueError(f"Unsupported action: {op['action']}")
    return mutate(args, operation)
