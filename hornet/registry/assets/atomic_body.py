from ._common import execute_guarded, update_file, edit_args
from ._python_ops import fill_body

@execute_guarded
def execute(args):
    return update_file(args["path"], lambda text: fill_body(text, args["method_name"], args["logic"], args.get("class_name")), **edit_args(args))
