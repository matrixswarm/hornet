import json
from ._common import execute_guarded, read_text
from ._json_ops import get

@execute_guarded
def execute(args):
    data = json.loads(read_text(args["file_path"]))
    if args.get("target_path"):
        data = get(data, args["target_path"])
    types = {"str": str, "int": int, "list": list, "dict": dict, "bool": bool, "float": float, "null": type(None)}
    if not isinstance(data, dict):
        raise ValueError("Selected node must be an object")
    for key, name in args["schema"].items():
        if name not in types:
            raise ValueError(f"Unknown type {name!r}")
        if key not in data or type(data[key]) is not types[name]:
            return {"status": "ERROR", "valid": False, "message": f"{key}: expected {name}"}
    return {"status": "SUCCESS", "valid": True}
