from ._common import execute_guarded
from ._json_ops import mutate, set_value, delete

@execute_guarded
def execute(args):
    action = args.get("action", "set")
    def operation(data):
        if action == "set":
            if "value" not in args:
                raise ValueError("set requires value (null is allowed)")
            set_value(data, args["target_path"], args["value"])
        elif action == "delete":
            delete(data, args["target_path"])
        else:
            raise ValueError("action must be set or delete")
    return mutate(args, operation)
