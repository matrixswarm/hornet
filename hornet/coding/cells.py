"""Agent-created cell contracts, frozen checks and typed result extraction."""
import json
from pathlib import PurePosixPath
import re
import uuid

from hornet.project import HornetError
from . import acceptance, value_schema


def path(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 160 or value == '.'
            or '\\' in value or ':' in value or any(ord(char) < 32 for char in value)
            or PurePosixPath(value).is_absolute()
            or any(part in {'', '..', '.', '.git', '.venv', 'venv', '__pycache__'} for part in value.split('/'))):
        raise HornetError('Cell files must be safe relative workspace paths')
    return value


def result_spec(cell):
    result = cell['result']
    if ('schema' in result) == ('schema_json' in result):
        raise HornetError('Result needs exactly one schema or legacy schema_json')
    return value_schema.parse(json.dumps(result['schema'], ensure_ascii=False, allow_nan=False)
                              if 'schema' in result else result['schema_json'])


def validate(plan, max_cells=3):
    if (not isinstance(plan, dict) or type(plan.get('schema_version')) is not int
            or plan['schema_version'] not in {1, 2, 3}
            or set(plan) != ({'schema_version', 'cells'} if plan['schema_version'] == 1 else {'schema_version', 'cells', 'coverage'})
            or not isinstance(plan['cells'], list) or not 1 <= len(plan['cells']) <= max_cells):
        raise HornetError('Coding recipe requires schema_version 1 or 2 and 1-{} cells'.format(max_cells))
    previous = set()
    for cell in plan['cells']:
        fields = {'id', 'task', 'files', 'depends_on', 'result'} | ({'kind'} if plan['schema_version'] == 3 else set())
        if (not isinstance(cell, dict) or set(cell) != fields
                or plan['schema_version'] == 3 and cell['kind'] not in {'code', 'research'}
                or not isinstance(cell['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', cell['id'])
                or cell['id'] in previous or not isinstance(cell['task'], str) or not 1 <= len(cell['task'].strip()) <= 1600
                or not isinstance(cell['files'], list) or not 1 <= len(cell['files']) <= 4
                or not isinstance(cell['depends_on'], list)
                or any(not isinstance(value, str) or value not in previous for value in cell['depends_on'])
                or len(set(cell['depends_on'])) != len(cell['depends_on'])):
            raise HornetError('Invalid cell id, task, file scope or earlier-cell dependencies')
        for filename in cell['files']:
            path(filename)
        if len(set(cell['files'])) != len(cell['files']):
            raise HornetError('Cell file paths must be unique')
        result = cell['result']
        schema_field = 'schema' if plan['schema_version'] == 3 and isinstance(result, dict) and 'schema' in result else 'schema_json'
        if cell.get('kind') == 'research':
            if not isinstance(result, dict) or set(result) != {schema_field}:
                raise HornetError('Research result declares only schema (or legacy schema_json)')
            result_spec(cell)
            previous.add(cell['id'])
            continue
        if (not isinstance(result, dict) or set(result) != {'path', 'function', 'args_json', 'kwargs_json', schema_field}
                or result['path'] not in cell['files'] or not result['path'].endswith('.py')
                or not isinstance(result['function'], str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,63}', result['function'])):
            raise HornetError('Cell result requires a scoped Python file and named function')
        for key, kind in (('args_json', list), ('kwargs_json', dict)):
            try:
                if not isinstance(result[key], str) or len(result[key]) > 500:
                    raise ValueError()
                value = json.loads(result[key], object_pairs_hook=unique, parse_constant=invalid_constant)
                if type(value) is not kind:
                    raise ValueError()
                references(value, set(cell['depends_on']))
            except (ValueError, TypeError, RecursionError) as exc:
                shape = 'a JSON array of positional arguments, e.g. [3] (not the scalar 3)' if key == 'args_json' else 'a JSON object of keyword arguments, e.g. {}'
                detail = ('; parser error: ' + str(exc)) if isinstance(exc, json.JSONDecodeError) else ''
                raise HornetError(cell['id'] + '.' + key + ' must encode ' + shape +
                    '; a dependency argument uses [{"$cell":"earlier-id"}] with a declared earlier-cell ID; '
                    'use finite JSON values' + detail) from None
        result_spec(cell)
        previous.add(cell['id'])
    if len(json.dumps(plan, ensure_ascii=False)) > 12000:
        raise HornetError('Recipe exceeds 12000 characters')
    if plan['schema_version'] == 3 and not any(c['kind'] == 'code' for c in plan['cells']):
        raise HornetError('This coding recipe requires at least one code cell; research can supply its findings')
    return plan


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate field')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('Non-finite JSON value')


def references(value, allowed, depth=0):
    if depth > 6:
        raise ValueError('Argument nesting too deep')
    if isinstance(value, dict):
        if '$cell' in value:
            if set(value) != {'$cell'} or not isinstance(value['$cell'], str) or value['$cell'] not in allowed:
                raise ValueError('Invalid cell reference')
        else:
            for item in value.values():
                references(item, allowed, depth + 1)
    elif isinstance(value, list):
        for item in value:
            references(item, allowed, depth + 1)


def resolve(value, results):
    if isinstance(value, dict):
        if '$cell' in value:
            return results[value['$cell']]
        return {key: resolve(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, results) for item in value]
    return value


def freeze(cell, checks, results):
    acceptance.validate(checks)
    if len(checks['checks']) > 4 or any(not set(check['paths']) <= set(cell['files']) for check in checks['checks']):
        raise HornetError('Verifier checks must name scoped files and contain at most four checks')
    if any(check['id'] == 'typed-return' for check in checks['checks']):
        raise HornetError('typed-return is reserved for the controller')
    spec = result_spec(cell)
    config = dict(path=cell['result']['path'], function=cell['result']['function'], schema=spec,
                  args=resolve(json.loads(cell['result']['args_json']), results),
                  kwargs=resolve(json.loads(cell['result']['kwargs_json']), results))
    payload = json.dumps(config, ensure_ascii=False, allow_nan=False)
    marker = 'HORNET_CELL_VALUE_' + uuid.uuid4().hex + ':'
    code = ('import importlib, json\n' + value_schema.MATCHER +
        '\nconfig = json.loads(' + repr(payload) + ')\n' +
        "module = importlib.import_module(config['path'][:-3].replace('/', '.'))\n"
        "value = getattr(module, config['function'])(*config['args'], **config['kwargs'])\n"
        "encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)\n"
        "assert len(encoded.encode('utf-8')) <= 4096, 'Cell return exceeds 4096 bytes'\n"
        "assert matches(value, config['schema']), 'Cell return type/schema mismatch'\n"
        'print(' + repr(marker) + ' + encoded)\n')
    generated = {'id': 'typed-return', 'description': 'Return actual JSON matching the declared schema: ' + json.dumps(result_spec(cell))[:120],
                 'paths': [cell['result']['path']], 'timeout': 15, 'python': code}
    plan = {'schema_version': 1, 'checks': checks['checks'] + [generated]}
    acceptance.validate(plan)
    return plan, marker


def extract(report, marker, cell):
    item = next(value for value in report['results'] if value['id'] == 'typed-return')
    lines = [line[len(marker):] for line in item.get('output', '').splitlines() if line.startswith(marker)]
    if not item['passed'] or len(lines) != 1:
        raise HornetError('No unique verified typed cell result')
    value = json.loads(lines[0], object_pairs_hook=unique, parse_constant=invalid_constant)
    return value_schema.validate_value(value, result_spec(cell))
