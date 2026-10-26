import textwrap
from ._common import execute_guarded
from ._python_ops import prepare_body

@execute_guarded
def execute(args):
    text, op = args["text"], args.get("op", "stay")
    width = {"javascript": 2, "json": 2, "yaml": 2}.get(args.get("lang", "python"), 4)
    if op == "python_body":
        if args.get("lang", "python") != "python":
            raise ValueError("python_body requires lang=python")
        text, _ = prepare_body(text)
        return {"status": "SUCCESS", "text": text, "validation": "body_parse_only",
                "next": "Pass text as logic to method.fill, or new_body to method.stage. "
                        "Do not add outer indentation or a def wrapper. "
                        "Insertion compiles the complete target before writing; run acceptance checks afterward."}
    if op == "indent":
        text = textwrap.indent(text, " " * width)
    elif op == "outdent":
        text = "".join(line[width:] if line.startswith(" " * width) else line for line in text.splitlines(True))
    elif op == "reset":
        text = textwrap.dedent(text)
    elif op != "stay":
        raise ValueError("Unknown indentation operation")
    return {"status": "SUCCESS", "text": text}
