"""Native structured fields at the model boundary; stable frozen storage inside.

Models return lists/objects once. The controller performs exact JSON encoding;
it never extracts markup, repairs truncation or guesses a missing delimiter.
"""
import ast
import copy
import json
import re

from hornet.project import HornetError
from . import mission

NATIVE_POLICY = {'schema_version': 1, 'format': 'native-fields', 'submissions': ['submit_intake', 'submit_route']}
POLICY = dict(NATIVE_POLICY, schema_version=2, format='literal-call')
INTAKE_PROMPT = (
    'Choose execute for one small code cell, delegate to coordinate all children, or delegate-and-work '
    'to own one child and delegate the others. Supply the plan in the same submit_intake response. '
    'Intake fields: schema_version 1, mode, reason, self_cell_id, plan. self_cell_id is an exact cell ID '
    'you own; use an empty string only for delegate. Plan fields: schema_version 2, cells, coverage. '
    'Declare 1-{max_cells} ordered cells. Each cell has id, task, files (1-4 relative files), depends_on, '
    'and result. Each result has path, function, args, kwargs, schema, with native arrays/objects. '
    'Dependencies name earlier cells only. Independent cells must own disjoint files; shared files '
    'require explicit dependencies. Each task must preserve ALL its normal, edge, invalid-input and '
    'integration requirements. Preserve public interfaces. Do not invent work or implement in intake. '
    'coverage is a list of objects with requirement_id and cell_ids. Assign EVERY supplied requirement '
    'ID in order, and assign global constraints to every affected cell. Every cell needs coverage. '
    'Use one compact submission. Workers receive fresh contexts and execute sequentially. '
    'For dependency values use args like [{"$cell":"earlier-id"}]. '
    'The result is a REAL function call that the controller executes: supply concrete example inputs '
    'for every required parameter, never type names or empty args for a function requiring inputs. '
)
GUIDANCE = (' MODEL SUBMISSION FORMAT: result uses args as a JSON array, kwargs as a JSON object, '
    'and schema as a JSON Schema object. Write these as native fields, never strings containing JSON. '
    'Example result: {"path":"parity_cell.py","function":"is_even","args":[3],"kwargs":{},'
    '"schema":{"type":"boolean"}}. Preserve the required plan version and all other fields. '
    'Hornet encodes storage fields; do not generate args_json, kwargs_json or schema_json.')
CALL_GUIDANCE = (
    ' MODEL SUBMISSION FORMAT: a code result has exactly path, call, schema. '
    'call is one ordinary Python call with concrete literal inputs, such as add_tax(350, 8) or '
    'quote(items=[100, 250], rate_percent=8). schema is a native JSON Schema object. '
    'Example: {"path":"parity_cell.py","call":"is_even(3)","schema":{"type":"boolean"}}. '
    'schema describes the actual VALUE returned by the call, not a module, function definition, source file or API map. '
    'Supply every required parameter. is_even() supplies none. Use Python True/False/None in call text. '
    'Only literal strings, numbers, booleans, None, lists and string-keyed dictionaries are supported; '
    'no expressions, imports, nested calls or unpacking. A dependency is a literal dictionary such as '
    "consume({'$cell': 'earlier-id'}). "
    'Research results still contain schema only. Do not submit separate function, args, kwargs or encoded JSON fields. '
    'The controller parses the call without executing its text and handles storage encoding. '
    'An object return schema must specify properties, required and additionalProperties explicitly.')
CALL_INTAKE_PROMPT = INTAKE_PROMPT.replace(
    'path, function, args, kwargs, schema, with native arrays/objects.',
    'path, call, schema. call is a Python call with literal inputs; schema is a native object.').replace(
    'For dependency values use args like [{"$cell":"earlier-id"}].',
    "For dependency inputs use a literal dictionary like consume({'$cell': 'earlier-id'}).")


def initialize(run, state):
    mission.immutable(run / 'contract-format.json', POLICY)
    state['contract_format'] = mission.reference(run, run / 'contract-format.json')


def validate(run, state):
    if not state.get('contract_format'):
        if (run / 'contract-format.json').exists():
            raise HornetError('Contract format lost its checkpoint binding')
        return
    if (mission.reference(run, run / 'contract-format.json') != state['contract_format']
            or mission.read(run / 'contract-format.json') not in (POLICY, NATIVE_POLICY)):
        raise HornetError('Saved contract format changed')


def uses_calls(run, state):
    return bool(state.get('contract_format')) and mission.read(run / 'contract-format.json') == POLICY


def result_schema():
    common = {'enum': {'type': 'array', 'items': {}, 'minItems': 1, 'maxItems': 16}}
    def variant(kind, extras=None, required=()):
        props = dict(common, type=kind)
        props.update(extras or {})
        return {'type': 'object', 'properties': props,
                'required': ['type', *required], 'additionalProperties': False}
    nested = {'type': 'object', 'properties': {}, 'additionalProperties': True}
    return {'description': 'Schema of the concrete value returned by the call, not the source file or exported function names.', 'anyOf': [
        variant({'type': 'string', 'enum': ['boolean', 'integer', 'number', 'string', 'null']}),
        variant({'type': 'string', 'const': 'array'}, {'items': nested}, ('items',)),
        variant({'type': 'string', 'const': 'object'}, {
            'properties': nested,
            'required': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 16},
            'additionalProperties': {'type': 'boolean'}}, ('properties', 'required', 'additionalProperties'))]}


def schema(value, calls=False):
    value = copy.deepcopy(value)
    def visit(node):
        if not isinstance(node, dict):
            return
        props = node.get('properties', {})
        if 'args_json' in props:
            props['args'] = {'type': 'array', 'items': {}, 'maxItems': 64,
                'description': 'Actual positional inputs for one real call, e.g. [350,8]. [] means zero arguments, not unspecified inputs.'}
            props['kwargs'] = {'type': 'object', 'properties': {}, 'additionalProperties': True}
            props.pop('args_json')
            props.pop('kwargs_json')
            old = 'schema_json' if 'schema_json' in props else 'schema'
            props.pop(old)
            props['schema'] = result_schema()
            node['required'] = [dict(args_json='args', kwargs_json='kwargs', schema_json='schema').get(k, k)
                                for k in node['required']]
            if calls:
                for name in ('function', 'args', 'kwargs'):
                    props.pop(name)
                props['call'] = {'type': 'string', 'minLength': 3, 'maxLength': 1100,
                    'description': 'One real Python call with concrete literal inputs, e.g. add_tax(350, 8). No imports, expressions or nested calls.'}
                node['required'] = ['path', 'call', 'schema']
        for item in props.values():
            visit(item)
        if isinstance(node.get('items'), dict):
            visit(node['items'])
        for item in node.get('anyOf', []):
            visit(item)
    visit(value)
    return value


def encode(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError, RecursionError):
        raise HornetError('Contract fields must contain finite JSON data') from None


def parse_call(text):
    """Parse data from a call expression. Never evaluate or execute the call."""
    try:
        if not isinstance(text, str) or not 3 <= len(text) <= 1100:
            raise ValueError()
        call = ast.parse(text, mode='eval').body
        if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name)
                or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,63}', call.func.id)
                or any(k.arg is None for k in call.keywords)
                or len({k.arg for k in call.keywords}) != len(call.keywords)):
            raise ValueError()
        def literal(node):
            # literal_eval alone silently collapses duplicate dictionary keys.
            for item in ast.walk(node):
                if isinstance(item, ast.Dict):
                    keys = [ast.literal_eval(key) for key in item.keys]
                    if any(type(key) is not str for key in keys) or len(set(keys)) != len(keys):
                        raise ValueError()
            value = ast.literal_eval(node)
            def check(item, depth=0):
                if depth > 6 or type(item) not in (str, int, float, bool, type(None), list, dict):
                    raise ValueError()
                if isinstance(item, (list, dict)):
                    for child in (item.values() if isinstance(item, dict) else item):
                        check(child, depth + 1)
            check(value)
            encode(value)
            return value
        return call.func.id, [literal(arg) for arg in call.args], {k.arg: literal(k.value) for k in call.keywords}
    except (SyntaxError, ValueError, TypeError, RecursionError, HornetError):
        raise HornetError('Use one Python call with literal JSON-compatible inputs, e.g. add_tax(350, 8); '
                          'no expressions, nested calls, unpacking, duplicate keys or nonfinite values') from None


def decode(document):
    """Normalize a complete submission. Legacy checkpoints/fixtures stay readable."""
    value = copy.deepcopy(document)
    plan = value.get('plan', {}) if isinstance(value, dict) else {}
    if not isinstance(plan, dict) or not isinstance(plan.get('cells', []), list):
        raise HornetError('Submit plan as an object with a cells array')
    for cell in plan.get('cells', []):
        result = cell.get('result', {}) if isinstance(cell, dict) else {}
        if not isinstance(result, dict):
            continue
        if 'call' in result:
            if set(result) != {'path', 'call', 'schema'}:
                raise HornetError('A call result has exactly path, call and schema; do not mix argument formats')
            name, args, kwargs = parse_call(result.pop('call'))
            result.update(function=name, args=args, kwargs=kwargs)
        if 'args' in result or 'kwargs' in result:
            if ('args_json' in result or 'kwargs_json' in result or 'schema_json' in result
                    or not isinstance(result.get('args'), list) or not isinstance(result.get('kwargs'), dict)
                    or not isinstance(result.get('schema'), dict)):
                raise HornetError('Use native args (array), kwargs (object), schema (object); do not mix encoded fields')
            args, kwargs = encode(result.pop('args')), encode(result.pop('kwargs'))
            if len(args) > 500 or len(kwargs) > 500:
                raise HornetError('Each result argument payload is limited to 500 characters; pass large outputs by reference')
            result.update(args_json=args, kwargs_json=kwargs)
        if 'schema' in result and plan.get('schema_version') != 3:
            if 'schema_json' in result or not isinstance(result['schema'], dict):
                raise HornetError('Use exactly one native schema object')
            result['schema_json'] = encode(result.pop('schema'))
    return value


def presentation(document, calls=False):
    """Show reviewers native fields while retaining the original hashed contract."""
    value = copy.deepcopy(document)
    plan = value.get('plan', {})
    for cell in plan.get('cells', []):
        result = cell['result']
        for old, new in [('args_json', 'args'), ('kwargs_json', 'kwargs'), ('schema_json', 'schema')]:
            if old in result:
                result[new] = json.loads(result.pop(old))
        if calls and 'function' in result:
            args, kwargs = result.pop('args'), result.pop('kwargs')
            parts = [repr(value) for value in args] + [key + '=' + repr(value) for key, value in kwargs.items()]
            result['call'] = result.pop('function') + '(' + ', '.join(parts) + ')'
    return value
