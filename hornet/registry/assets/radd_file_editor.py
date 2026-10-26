from pathlib import Path
from ._common import execute_guarded, update_file, edit_args, digest

@execute_guarded
def execute(args):
    action, path = args["action"], args["path"]
    if action == "read_lines":
        raw = Path(path).read_bytes()
        lines = raw.decode("utf-8-sig").splitlines(True)
        start, end = args.get("start", 1), args.get("end", len(lines))
        if start < 1 or (end < start and not (not lines and start == 1 and "end" not in args)):
            raise ValueError("Line range is 1-based inclusive; end must be >= start")
        return {"status": "SUCCESS", "text": "".join(lines[start-1:end]), "line_count": len(lines),
                "source_sha256": digest(raw)}
    def transform(text):
        lines = text.splitlines(True)
        if action == "create_file":
            if Path(path).exists():
                raise ValueError("create_file refuses to overwrite an existing file")
            return args["text"]
        index = args["line"] - 1
        if index < 0 or index >= len(lines) + (action == "insert_line"):
            raise ValueError("line out of range (1-based)")
        if action == "delete_line":
            del lines[index]
        else:
            replacement = args["text"].rstrip("\r\n") + "\n"
            if action == "replace_line":
                lines[index] = replacement
            elif action == "insert_line":
                if index > 0 and not lines[index-1].endswith("\n"):
                    lines[index-1] += "\n"
                lines.insert(index, replacement)
            else:
                raise ValueError("Unknown action")
        return "".join(lines)
    return update_file(path, transform, create=action == "create_file", **edit_args(args))
