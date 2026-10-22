"""Recipe APIs retained for import compatibility."""
from hornet.project import Project
from hornet.recipes import list_recipes as _list, load

def list_recipes(root=None):
    return _list(Project(root))

def show_recipe(name, root=None):
    return load(Project(root), name)[0]

def manage_recipes(action, name=None):
    from hornet.cli import main
    return main(["recipe", action, *([name] if name else [])])
