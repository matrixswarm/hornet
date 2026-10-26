from .radd_file_editor import execute as read


def execute(args):
    return read(dict(args, action="read_lines"))
