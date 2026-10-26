from ._common import execute_guarded, update_file, edit_args
from ._python_ops import add_imports

@execute_guarded
def execute(args):
    return update_file(args["path"], lambda text: add_imports(text, args["imports"]), **edit_args(args))
