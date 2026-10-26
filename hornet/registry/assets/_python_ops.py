import ast
import io
import keyword
import tokenize

def identifier(value):
    if not isinstance(value, str) or not value.isidentifier() or keyword.iskeyword(value):
        raise ValueError(f"Invalid Python identifier: {value!r}")
    return value

def interface(class_name, methods, version="1.0.0"):
    identifier(class_name)
    if "\n" in version or "\r" in version:
        raise ValueError("version must be one line")
    definitions = []
    names = set()
    for signature in methods:
        if isinstance(signature, dict):
            signature = identifier(signature["name"]) + "(" + signature.get("args", "") + ")"
        source = "def " + signature + ":\n    pass\n"
        tree = ast.parse(source)
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
            raise ValueError("methods must be signatures only")
        node = tree.body[0]
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Pass) or node.decorator_list:
            raise ValueError("methods must be signatures only")
        if node.name in names:
            raise ValueError("Duplicate method name")
        names.add(node.name)
        positional = node.args.posonlyargs + node.args.args
        if not positional or positional[0].arg != "self":
            if any(a.arg == "self" for a in ast.walk(node.args) if isinstance(a, ast.arg)):
                raise ValueError("self must be the first positional argument")
            signature = signature.replace("(", "(self, ", 1) if signature.split("(",1)[1].split(")",1)[0].strip() else signature.replace("(", "(self", 1)
        definitions.append("    @abstractmethod\n    def " + signature + ":\n        pass\n")
    return "# VERSION: " + version + "\nfrom abc import ABC, abstractmethod\n\nclass " + class_name + "(ABC):\n" + ("\n".join(definitions) if definitions else "    pass\n")

def _literal_continuations(text):
    """Physical lines inside a string token are data, not outer indentation."""
    protected, fstrings = set(), []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        kind = tokenize.tok_name[token.type]
        if kind == "STRING":
            protected.update(range(token.start[0] + 1, token.end[0] + 1))
        elif kind == "FSTRING_START":  # Python 3.12+; older versions use STRING.
            fstrings.append(token.start[0])
        elif kind == "FSTRING_END" and fstrings:
            protected.update(range(fstrings.pop() + 1, token.end[0] + 1))
    return protected


def _body_margin(body):
    try:
        protected = _literal_continuations(body)
    except (IndentationError, tokenize.TokenError) as exc:
        line = exc.lineno if isinstance(exc, IndentationError) else exc.args[1][0]
        message = exc.msg if isinstance(exc, IndentationError) else exc.args[0]
        raise ValueError(f"Invalid Python body at line {line}: {message}. "
                         "Correct the incomplete token or inconsistent indentation before retrying.") from exc
    lines, margin = body.splitlines(True), None
    for number, line in enumerate(lines, 1):
        if number in protected or not line.strip():
            continue
        prefix = line[:len(line) - len(line.lstrip(" \t"))]
        if margin is None:
            margin = prefix
        else:
            while margin and not prefix.startswith(margin):
                margin = margin[:-1]
    if not margin:
        return body
    return "".join(line[len(margin):] if number not in protected and line.startswith(margin) else line
                   for number, line in enumerate(lines, 1))


def prepare_body(body):
    """Remove a common margin and parse, without guessing internal nesting.

    A body can contain return/await: parsing here checks its layout, while the
    complete target module is compiled by the file transaction before writing.
    """
    logic = _body_margin(body).strip("\r\n")
    if not logic.strip():
        raise ValueError("Method body must not be empty")
    try:
        tree = ast.parse(logic, filename="<python-body>")
    except SyntaxError as exc:
        line = exc.lineno or 1
        source = logic.splitlines()
        excerpt = repr(source[line - 1][:160]) if line <= len(source) else "<end>"
        guidance = ("Top-level body statements must start at column 0; indent only statements "
                    "inside an if/for/while/try/with block or another Python suite. "
                    "The tool adds the enclosing method indentation. "
                    "For two sequential statements use 'check(value)\\nreturn value', "
                    "with no spaces before return. Removing a common margin cannot repair "
                    "mixed indentation; correct the indicated line.") if isinstance(exc, IndentationError) else (
                    "Supply Python body statements only, without the enclosing def/signature. "
                    "Correct this syntax before retrying.")
        raise ValueError(f"Invalid Python body at line {line}: {exc.msg}. Line: {excerpt}. {guidance}") from exc
    return logic, tree


def fill_body(text, name, body, class_name=None):
    identifier(name)
    tree = ast.parse(text)
    candidates = []
    if class_name is not None:
        identifier(class_name)
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name]
        if len(classes) != 1:
            raise ValueError("class_name must identify exactly one top-level class")
        scope = classes[0].body
    else:
        scope = list(tree.body)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                scope.extend(node.body)
    candidates = [n for n in scope if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one method named {name}; found {len(candidates)}. Supply class_name to disambiguate.")
    method = candidates[0]
    first, last = method.body[0], method.body[-1]
    if first.lineno == method.lineno:
        raise ValueError("Inline one-line methods are not supported; expand the method first")
    logic, body_tree = prepare_body(body)
    body_nodes = body_tree.body
    if (len(body_nodes) == 1 and isinstance(body_nodes[0], (ast.FunctionDef, ast.AsyncFunctionDef))
            and body_nodes[0].name == name):
        raise ValueError("Supply only the method body in logic, without its def/signature; the existing signature is preserved")
    lines = text.splitlines(True)
    start = min([first.lineno] + [d.lineno for d in getattr(first, "decorator_list", [])])
    original_line = lines[start - 1]
    prefix = original_line[:len(original_line) - len(original_line.lstrip(" \t"))]
    protected = _literal_continuations(logic)
    new_body = "".join(prefix + line if number not in protected and line.strip() else line
                       for number, line in enumerate(logic.splitlines(True), 1)) + "\n"
    candidate = "".join(lines[:start - 1]) + new_body + "".join(lines[last.end_lineno:])
    method.body = body_nodes
    if ast.dump(ast.parse(candidate), include_attributes=False) != ast.dump(tree, include_attributes=False):
        raise ValueError("Body insertion changed Python structure or literal values; nothing was written")
    return candidate

def strategy(text, class_name):
    identifier(class_name)
    tree = ast.parse(text)
    context = class_name + "Context"
    if any(isinstance(n, ast.ClassDef) and n.name == context for n in tree.body):
        raise ValueError(f"{context} already exists")
    return text.rstrip() + f"""
\nclass {context}:
    def __init__(self, strategy):
        self._strategy = strategy

    def set_strategy(self, strategy):
        self._strategy = strategy

    def execute_strategy(self, *args, **kwargs):
        return self._strategy.execute(*args, **kwargs)
"""

def add_imports(text, imports):
    tree = ast.parse(text)
    existing = {ast.dump(n, include_attributes=False) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))}
    added = []
    for statement in imports:
        nodes = ast.parse(statement).body
        if len(nodes) != 1 or not isinstance(nodes[0], (ast.Import, ast.ImportFrom)):
            raise ValueError("Each imports entry must be one import statement")
        node = nodes[0]
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            raise ValueError("Future imports must be authored separately at the module header")
        key = ast.dump(node, include_attributes=False)
        if key not in existing:
            existing.add(key)
            added.append(statement.strip() + "\n")
    if not added:
        return text
    # Keep shebang, encoding comments, module docstring, and __future__ imports first.
    index = 0
    lines = text.splitlines(True)
    while index < len(lines) and (lines[index].lstrip().startswith("#") or not lines[index].strip()):
        index += 1
    for i, node in enumerate(tree.body):
        if (i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)) or (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
            index = node.end_lineno
        else:
            break
    if index and not lines[index-1].endswith("\n"):
        lines[index-1] += "\n"
    return "".join(lines[:index] + added + lines[index:])
