"""Small, closed JSON Schema subset for actual cell return values."""
import json

from hornet.project import HornetError

TYPES = {'boolean', 'integer', 'number', 'string', 'null', 'array', 'object'}


def parse(text):
    if not isinstance(text, str) or len(text) > 700:
        raise HornetError('Return schema must be JSON text of at most 700 characters')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON field')
            value[key] = item
        return value
    try:
        spec = json.loads(text, object_pairs_hook=unique, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, TypeError):
        raise HornetError('Invalid return schema JSON') from None
    validate_schema(spec)
    return spec


def validate_schema(spec, depth=0):
    if depth > 4 or not isinstance(spec, dict) or spec.get('type') not in TYPES:
        raise HornetError('Return schema requires a supported type and nesting depth at most four')
    kind = spec['type']
    allowed = {'type', 'enum'}
    if kind == 'object':
        allowed |= {'properties', 'required', 'additionalProperties'}
        if (not isinstance(spec.get('properties', {}), dict) or len(spec.get('properties', {})) > 16
                or not isinstance(spec.get('required', []), list)
                or any(not isinstance(key, str) or key not in spec.get('properties', {}) for key in spec.get('required', []))
                or len(set(spec.get('required', []))) != len(spec.get('required', []))
                or type(spec.get('additionalProperties', False)) is not bool):
            raise HornetError('Invalid object return schema')
        for value in spec.get('properties', {}).values():
            validate_schema(value, depth + 1)
    if kind == 'array':
        allowed |= {'items'}
        validate_schema(spec.get('items'), depth + 1)
    if set(spec) - allowed:
        raise HornetError('Unsupported return schema fields: ' + ', '.join(sorted(set(spec) - allowed)))
    if 'enum' in spec and (not isinstance(spec['enum'], list) or not 1 <= len(spec['enum']) <= 16):
        raise HornetError('Return schema enum requires 1-16 JSON values')
    return spec


# Kept as a fixed guest program too; no candidate-provided validator is imported.
MATCHER = '''def matches(value, spec):
    kind = spec['type']
    valid = {'boolean': type(value) is bool, 'integer': type(value) is int,
        'number': type(value) in (int, float), 'string': type(value) is str,
        'null': value is None, 'array': type(value) is list, 'object': type(value) is dict}[kind]
    if not valid:
        return False
    if 'enum' in spec and not any(type(value) is type(item) and value == item for item in spec['enum']):
        return False
    if kind == 'array':
        return all(matches(item, spec['items']) for item in value)
    if kind == 'object':
        props = spec.get('properties', {})
        return (all(key in value for key in spec.get('required', []))
            and (spec.get('additionalProperties', False) or not set(value) - set(props))
            and all(matches(item, props[key]) for key, item in value.items() if key in props))
    return True
'''
exec(MATCHER)  # This constant is controller source, never model-generated text.


def validate_value(value, spec):
    validate_schema(spec)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise HornetError('Cell result must be finite JSON data') from None
    if len(encoded.encode('utf-8')) > 4096 or not matches(value, spec):
        raise HornetError('Cell result does not satisfy its return schema or exceeds 4096 bytes')
    return value
