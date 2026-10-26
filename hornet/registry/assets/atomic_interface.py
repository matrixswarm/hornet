from pathlib import Path
from ._common import execute_guarded, update_file, edit_args
from ._python_ops import interface

@execute_guarded
def execute(args):
    def transform(text):
        if Path(args["path"]).exists() and not args.get("overwrite", False):
            raise ValueError("Interface exists; overwrite must be explicit")
        return interface(args["class_name"], args.get("methods", []), args.get("version", "1.0.0"))
    return update_file(args["path"], transform, create=True, **edit_args(args))
