from ._common import execute_guarded, update_file, edit_args
from ._python_ops import strategy

@execute_guarded
def execute(args):
    if args.get("pattern", "strategy") != "strategy":
        raise ValueError("Only strategy is implemented")
    return update_file(args["path"], lambda text: strategy(text, args["class_name"]), **edit_args(args))
