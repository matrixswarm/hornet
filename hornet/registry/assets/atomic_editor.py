from ._common import execute_guarded, update_file, edit_args

@execute_guarded
def execute(args):
    def transform(text):
        lines = text.splitlines(True)
        if args["action"] == "replace":
            target = args.get("target_text")
            if not target:
                raise ValueError("replace requires nonempty target_text")
            matches = [i for i, line in enumerate(lines) if target in line]
            if len(matches) != 1:
                raise ValueError(f"Expected one matching line; found {len(matches)}")
            lines[matches[0]] = args["new_text"].rstrip("\r\n") + "\n"
        elif args["action"] == "insert":
            index = args["line_number"] - 1
            if not 0 <= index <= len(lines):
                raise ValueError("line_number out of range (1-based)")
            if index > 0 and not lines[index-1].endswith("\n"):
                lines[index-1] += "\n"
            lines.insert(index, args["new_text"].rstrip("\r\n") + "\n")
        else:
            raise ValueError("Unknown action")
        return "".join(lines)
    return update_file(args["path"], transform, **edit_args(args))
