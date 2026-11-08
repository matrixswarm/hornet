"""Safe, unambiguous project-relative references, not execution restrictions."""
import json
from .workspace import safe_path


def format_paths(root, names):
    paths = []
    for name in dict.fromkeys(names):
        if not isinstance(name, str) or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError('Invalid project path.')
        target = safe_path(root, name)
        if not target.exists():
            raise ValueError('Path no longer exists: ' + name)
        # Forward slashes match the inventory on Windows too. JSON quoting keeps
        # spaces, quotes and punctuation unambiguous in the natural-language prompt.
        paths.append(json.dumps(name + ('/' if target.is_dir() else ''), ensure_ascii=False))
    return ', '.join(paths)
