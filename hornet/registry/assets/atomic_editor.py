from pathlib import Path
import re

from ._common import execute_guarded, update_file, edit_args

@execute_guarded
def execute(args):
    def transform(text):
        lines = text.splitlines(True)
        if args["action"] == "create":
            if Path(args["path"]).exists():
                raise ValueError("create refuses to overwrite an existing file")
            return args["new_text"]
        elif args["action"] == "replace":
            target = args.get("target_text")
            if not target:
                raise ValueError("replace requires nonempty target_text")
            if "\n" in target or "\r" in target:
                # Models commonly send LF even when the source uses CRLF. Match
                # line breaks independently of their encoding, retaining every
                # other character and requiring a unique block.
                parts = re.split(r"\r\n|\r|\n", target)
                pattern = r"(?:\r\n|\r|\n)".join(map(re.escape, parts))
                matches = list(re.finditer(pattern, text))
                if len(matches) != 1:
                    raise ValueError(f"Expected one exact matching block; found {len(matches)}")
                match = matches[0]
                newline = re.search(r"\r\n|\r|\n", match.group()).group()
                replacement = re.sub(r"\r\n|\r|\n", lambda _: newline, args["new_text"])
                return text[:match.start()] + replacement + text[match.end():]
            matches = [i for i, line in enumerate(lines) if target in line]
            if len(matches) != 1:
                raise ValueError(f"Expected one matching line; found {len(matches)}")
            line = lines[matches[0]]
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            lines[matches[0]] = re.sub(r"\r\n|\r|\n", lambda _: newline, args["new_text"].rstrip("\r\n")) + newline
        elif args["action"] == "delete":
            target = args.get("target_text")
            if not target:
                raise ValueError("delete requires nonempty target_text")
            matches = [i for i, line in enumerate(lines) if target in line]
            if len(matches) != 1:
                raise ValueError(f"Expected one matching line; found {len(matches)}")
            del lines[matches[0]]
        elif args["action"] == "insert":
            index = args["line_number"] - 1
            if not 0 <= index <= len(lines):
                raise ValueError(f"line_number out of range: choose 1 through {len(lines) + 1} (1-based; last value appends)")
            if index > 0 and not lines[index-1].endswith("\n"):
                lines[index-1] += "\n"
            lines.insert(index, args["new_text"].rstrip("\r\n") + "\n")
        else:
            raise ValueError("Unknown action")
        return "".join(lines)
    return update_file(args["path"], transform, create=args["action"] == "create", **edit_args(args))
