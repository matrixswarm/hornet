"""One path implementation for every JSON operator; missing differs from null."""
import json
import re
from ._common import update_file, edit_args


def tokens(path):
    if not isinstance(path, str) or not path:
        raise ValueError("A nonempty dot path is required")
    output = []
    for part in path.split("."):
        match = re.fullmatch(r"([^\[\]]+)((?:\[\d+\])*)", part)
        if not match:
            raise ValueError(f"Invalid path {path!r}; use names and nonnegative [index]")
        output.append(match[1])
        output.extend(int(x) for x in re.findall(r"\[(\d+)\]", match[2]))
    return output


def child(data, key):
    if isinstance(key, int):
        if not isinstance(data, list) or key >= len(data):
            raise ValueError(f"Missing array index [{key}]")
    elif not isinstance(data, dict) or key not in data:
        raise ValueError(f"Missing object key {key!r}")
    return data[key]


def get(data, path):
    for key in tokens(path):
        data = child(data, key)
    return data


def set_value(data, path, value):
    parts = tokens(path)
    for i, key in enumerate(parts[:-1]):
        if isinstance(key, str) and isinstance(data, dict) and key not in data:
            data[key] = [] if isinstance(parts[i + 1], int) else {}
        data = child(data, key)
    key = parts[-1]
    if isinstance(key, int):
        if not isinstance(data, list) or key > len(data):
            raise ValueError("Array writes may replace an index or append at length; no sparse arrays")
        if key == len(data):
            data.append(value)
        else:
            data[key] = value
    elif isinstance(data, dict):
        data[key] = value
    else:
        raise ValueError("Refusing to replace an intermediate scalar/container with an object")


def delete(data, path):
    parts = tokens(path)
    for key in parts[:-1]:
        data = child(data, key)
    child(data, parts[-1])
    del data[parts[-1]]


def mutate(args, operation):
    def transform(text):
        data = json.loads(text)
        operation(data)
        return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    return update_file(args.get("file_path", args.get("path")), transform, **edit_args(args))
