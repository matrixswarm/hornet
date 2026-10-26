"""Bounded syntax facts for check authors; never import or execute project code."""
import ast
import base64
import hashlib
import json

from hornet.project import HornetError
from . import cells


GUIDANCE = ('Existing API declarations below are source-bound syntax facts, not behavior. '
            'A module function is called on the module; an instance method normally needs '
            'an instance. An ordinary method must be CALLED: instance.name() returns its value; '
            'instance.name is a method object, not a string property. Do not compare a method '
            'object directly with a scalar expected result. Dynamic attribute lookup, constructors, inheritance '
            'and decorators can change access rules; declarations marked dynamic do not establish the runtime attribute type. '
            'Use the declared public result function for behavioral checks where '
            'possible. Do not invent a class attribute with the name of a module function. '
            'Missing/omitted declarations are unknown, not proof that an API cannot be added. '
            'After earlier method calls, a bare method/scalar comparison may be ambiguous; '
            'call the declared method to check its return. '
            'Derive expected outputs from the requirements, never from current bodies.')


def ordinary_class(node):
    """Only simple declarations justify a method-object preflight rejection."""
    if node.bases or node.keywords or node.decorator_list:
        return False
    names = set()
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if item.name in names or item.name in {'__init__', '__new__', '__getattr__', '__getattribute__'}:
                return False
            names.add(item.name)
            if any(not isinstance(d, ast.Name) or d.id not in {'staticmethod', 'classmethod'}
                   for d in item.decorator_list):
                return False
        elif not (isinstance(item, ast.Pass) or isinstance(item, ast.Expr)
                  and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str)):
            return False
    return True


def outline(text):
    tree = ast.parse(text)
    declarations = []
    truncated = False

    def add(node, owner=None, ordinary=False):
        nonlocal truncated
        if len(declarations) >= 32:
            truncated = True
            return
        kind = 'function'
        if owner:
            decorators = [d.id if isinstance(d, ast.Name) else '?' for d in node.decorator_list]
            kind = ('static_method' if decorators == ['staticmethod'] else
                    'class_method' if decorators == ['classmethod'] else
                    'instance_method' if not decorators else 'decorated_method')
        args = node.args
        positional = args.posonlyargs + args.args
        required = len(positional) - len(args.defaults)
        parameters = [{'name': p.arg, 'kind': 'positional_only' if i < len(args.posonlyargs) else 'positional_or_keyword',
                       'required': i < required} for i, p in enumerate(positional)]
        if args.vararg:
            parameters.append({'name': args.vararg.arg, 'kind': 'var_positional', 'required': False})
        parameters.extend({'name': p.arg, 'kind': 'keyword_only', 'required': default is None}
                          for p, default in zip(args.kwonlyargs, args.kw_defaults))
        if args.kwarg:
            parameters.append({'name': args.kwarg.arg, 'kind': 'var_keyword', 'required': False})
        declaration = {'name': (owner + '.' if owner else '') + node.name, 'kind': kind,
                       'async': isinstance(node, ast.AsyncFunctionDef), 'parameters': parameters}
        if kind == 'instance_method':
            declaration['attribute_lookup'] = 'ordinary' if ordinary else 'dynamic'
            declaration['access'] = ('Call this method on an instance of ' + owner +
                '; attribute access alone does not return its result.' if ordinary else
                'Declared instance method; this class may alter attribute lookup. Runtime attribute type is unknown.')
        declarations.append(declaration)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node)
        elif isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(method, node.name, ordinary_class(node))
    return {'declarations': declarations, 'truncated': truncated}


def capture(worker, paths, snapshot):
    result = {'version': 1, 'files': [], 'omitted_files': max(0, len(paths) - 8)}
    for path in sorted(paths)[:8]:
        path = cells.path(path)
        expected = snapshot['files'].get(path)
        item = {'path': path, 'source_sha256': expected}
        if expected is None:
            item['unavailable'] = 'File is not present yet; use declared future interfaces.'
        elif not path.endswith('.py'):
            item['unavailable'] = 'Only Python declarations are described.'
        else:
            reply = worker.call('read_source', {'path': path})
            if not reply.get('ok'):
                raise HornetError('Cannot inspect API declarations: ' + str(reply.get('error')))
            raw = base64.b64decode(reply['content_base64'], validate=True)
            if len(raw) > 256 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
                raise HornetError('Source changed while inspecting API declarations')
            try:
                item.update(outline(raw.decode('utf-8-sig')))
            except (SyntaxError, UnicodeError):
                item['unavailable'] = 'Current source cannot be parsed; use declared future interfaces.'
        trial = dict(result, files=result['files'] + [item])
        if len(json.dumps(trial).encode()) > 12000:
            result['omitted_files'] = len(paths) - len(result['files'])
            break
        result['files'].append(item)
    return result


def reject_method_value_checks(checks, facts):
    """Reject direct scalar assertions on known ordinary method objects.

    This is a narrow syntax check, not Python type inference. Unknown factories,
    decorated methods, reassigned instances and nested scopes remain unknown.
    No project code is imported. Existing declarations must match a proposed
    check; an API migration needs new declarations before freezing its checks.
    """
    methods = {}
    for item in facts.get('files', []):
        if not item['path'].endswith('.py'):
            continue
        module = item['path'][:-3].replace('/', '.')
        for declaration in item.get('declarations', []):
            if declaration['kind'] == 'instance_method' and declaration.get('attribute_lookup') == 'ordinary':
                owner, name = declaration['name'].rsplit('.', 1)
                methods.setdefault(module + '.' + owner, set()).add(name)
    for check in checks.get('checks', []):
        aliases, instances, uncertain_instances = {}, {}, {}

        def qualified(node):
            if isinstance(node, ast.Name):
                return aliases.get(node.id)
            if isinstance(node, ast.Attribute):
                parent = qualified(node.value)
                return parent + '.' + node.attr if parent else None

        def owner(node):
            if isinstance(node, ast.Name):
                return instances.get(node.id)
            if isinstance(node, ast.Call):
                try:
                    for arg in node.args + [k.value for k in node.keywords]:
                        ast.literal_eval(arg)
                except (ValueError, TypeError):
                    return None
                return qualified(node.func)

        def invalidate(statement=None):
            # A declared method call can change attributes. Retain only a type
            # hint so a later bare-method assertion is reported as ambiguous,
            # never as a proven bound method. Unknown calls/control flow erase
            # even that hint. No method body is inspected or executed.
            calls = [n for n in ast.walk(statement) if isinstance(n, ast.Call)] if statement else []
            known = dict(uncertain_instances, **instances)
            def declared_call(call):
                target = call.func
                if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                        and target.attr in methods.get(known.get(target.value.id), set())):
                    return False
                try:
                    for arg in call.args + [k.value for k in call.keywords]:
                        ast.literal_eval(arg)
                except (ValueError, TypeError):
                    return False
                return True
            uncertain_instances.clear()
            if calls and all(declared_call(call) for call in calls):
                uncertain_instances.update(known)
            aliases.clear()
            instances.clear()

        for statement in ast.parse(check['python']).body:
            if isinstance(statement, ast.ImportFrom):
                for alias in statement.names:
                    aliases[alias.asname or alias.name] = (statement.module or '') + '.' + alias.name
            elif isinstance(statement, ast.Import):
                for alias in statement.names:
                    aliases[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
            elif isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                selected = owner(statement.value) if not isinstance(statement, ast.AugAssign) else None
                if selected not in methods and any(isinstance(n, ast.Call) for n in ast.walk(statement)):
                    invalidate(statement)
                for target in targets:
                    if isinstance(target, ast.Name):
                        aliases.pop(target.id, None)
                        instances.pop(target.id, None)
                        uncertain_instances.pop(target.id, None)
                        if selected in methods:
                            instances[target.id] = selected
                    else:
                        # Attribute/subscript writes can affect aliases of the
                        # same instance, or mutate the class itself.
                        invalidate()
            elif isinstance(statement, ast.Assert):
                expression = statement.test
                if not (isinstance(expression, ast.Compare) and len(expression.ops) == 1
                        and isinstance(expression.ops[0], (ast.Eq, ast.Is))):
                    if any(isinstance(n, ast.Call) for n in ast.walk(statement)):
                        invalidate(statement)
                    continue
                left, right = expression.left, expression.comparators[0]
                for target, expected in ((left, right), (right, left)):
                    if not (isinstance(target, ast.Attribute) and isinstance(expected, ast.Constant)
                            and type(expected.value) in (str, int, float, bool)):
                        continue
                    class_name = owner(target.value)
                    if target.attr in methods.get(class_name, set()):
                        raise HornetError('Check {} line {} compares {}.{} as a scalar property. '
                            'The source declares an ordinary method; call {}() to check its return. '
                            'Preserve the declared API.'.format(check['id'], statement.lineno,
                                class_name, target.attr, ast.unparse(target)))
                    possible = (uncertain_instances.get(target.value.id)
                                if isinstance(target.value, ast.Name) else None)
                    if target.attr in methods.get(possible, set()):
                        raise HornetError('Check {} line {} compares declared method {}.{} with a scalar '
                            'after earlier method calls. Attribute lookup is now ambiguous; those calls '
                            'do not establish a scalar property. Call {}() to check the declared return, '
                            'or make intentional dynamic attribute behavior explicit and justify it '
                            'from the original requirements. Preserve the declared API.'.format(
                                check['id'], statement.lineno, possible, target.attr, ast.unparse(target)))
                if any(isinstance(n, ast.Call) for n in ast.walk(statement)):
                    invalidate(statement)
            elif isinstance(statement, ast.Expr) and any(isinstance(n, ast.Call) for n in ast.walk(statement)):
                invalidate(statement)
            elif not isinstance(statement, (ast.Expr, ast.Pass)):
                # Branches, definitions and deletes may rebind names. Avoid
                # claiming a concrete instance type across unknown control flow.
                invalidate()
