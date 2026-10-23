"""Structured submissions avoid asking small models to JSON-encode JSON."""
import copy


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def strings(maximum):
    return {'type': 'array', 'items': {'type': 'string'}, 'maxItems': maximum}


RESULT = obj({
    'path': {'type': 'string', 'description': 'Scoped Python file containing the result function'},
    'function': {'type': 'string', 'description': 'Public Python function name'},
    'args_json': {'type': 'string', 'description': 'Complete JSON ARRAY text, e.g. [3], [] or [{"$cell":"earlier-id"}]. Encode this text once. A scalar such as 3 is invalid.'},
    'kwargs_json': {'type': 'string', 'description': 'JSON OBJECT text of keyword arguments, e.g. {}'},
    'schema_json': {'type': 'string', 'description': 'JSON Schema text, e.g. {"type":"boolean"}'},
})
CELL = obj({'id': {'type': 'string'}, 'task': {'type': 'string'}, 'files': strings(4),
            'depends_on': strings(3), 'result': RESULT})
CHECK = obj({'id': {'type': 'string'}, 'description': {'type': 'string'}, 'paths': strings(4),
             'timeout': {'type': 'integer', 'minimum': 1, 'maximum': 30}, 'python': {'type': 'string'}})
DOCUMENTS = {
    'submit_plan': obj({'schema_version': {'type': 'integer', 'const': 1},
                        'cells': {'type': 'array', 'items': CELL, 'minItems': 1, 'maxItems': 3}}),
    'submit_checks': obj({'schema_version': {'type': 'integer', 'const': 1},
                          'checks': {'type': 'array', 'items': CHECK, 'minItems': 1, 'maxItems': 4}}),
    'submit_advice': obj({'message': {'type': 'string'}}),
}
DOCUMENTS['submit_goal_plan'] = obj({'schema_version': {'type': 'integer', 'const': 2},
    'cells': {'type': 'array', 'items': CELL, 'minItems': 1, 'maxItems': 3},
    'coverage': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'items': obj({
        'requirement_id': {'type': 'string'}, 'cell_ids': strings(3)})}})
DOCUMENTS['submit_goal_checks'] = obj({'schema_version': {'type': 'integer', 'const': 1},
    'checks': {'type': 'array', 'items': obj(dict(CHECK['properties'], paths=strings(8))), 'minItems': 1, 'maxItems': 4}})

DOCUMENTS['submit_intake'] = obj({'schema_version': {'type': 'integer', 'const': 1},
    'mode': {'type': 'string', 'enum': ['execute', 'delegate', 'delegate-and-work']},
    'reason': {'type': 'string', 'minLength': 1, 'maxLength': 600},
    'self_cell_id': {'type': 'string', 'description': 'Your cell ID, or empty string for delegate'},
    'plan': DOCUMENTS['submit_goal_plan']})

DOCUMENTS['submit_route'] = obj({'schema_version': {'type': 'integer', 'const': 1},
    'route': {'type': 'string', 'enum': ['execute', 'needs_split']},
    'reason': {'type': 'string', 'minLength': 1, 'maxLength': 600},
    'plan': obj({'schema_version': {'type': 'integer', 'const': 2},
        'cells': {'type': 'array', 'items': CELL, 'maxItems': 3},
        'coverage': dict(DOCUMENTS['submit_goal_plan']['properties']['coverage'], minItems=0)})})


DOCUMENTS['submit_finding'] = obj({'value_json': {'type': 'string', 'maxLength': 4096},
    'evidence': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': obj({
        'path': {'type': 'string'}, 'start_line': {'type': 'integer', 'minimum': 1},
        'end_line': {'type': 'integer', 'minimum': 1}, 'quote': {'type': 'string', 'minLength': 1, 'maxLength': 2000}})}})

DOCUMENTS['submit_contract_review'] = obj({
    'decision': {'type': 'string', 'enum': ['approve', 'revise', 'uncertain']},
    'issues': {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 600}, 'maxItems': 6},
    'rationale': {'type': 'string', 'minLength': 1, 'maxLength': 1200}})

DOCUMENTS['submit_contract_oracle'] = obj({'expectations': {'type': 'array', 'maxItems': 32,
    'items': obj({'id': {'type': 'string'}, 'status': {'type': 'string', 'enum': ['determined', 'uncertain']},
                  'basis': {'type': 'string', 'minLength': 1, 'maxLength': 600},
                  'value_json': {'type': 'string', 'minLength': 1, 'maxLength': 4096}})}})


def oracle_schema(packet):
    schema = copy.deepcopy(DOCUMENTS['submit_contract_oracle'])
    rows = schema['properties']['expectations']
    rows.update(minItems=len(packet['calls']), maxItems=len(packet['calls']))
    rows['items']['properties']['id']['enum'] = [c['id'] for c in packet['calls']]
    return schema


def check_schema(name, paths):
    """Constrain generated check path choices to their frozen source scope."""
    schema = copy.deepcopy(DOCUMENTS[name])
    schema['properties']['checks']['items']['properties']['paths']['items']['enum'] = sorted(set(paths))
    return schema


def recon_schema(name, requirements, max_cells, research=False, file_paths=None):
    """Expose stage-specific closed choices to constrained generation as well as validation."""
    schema = copy.deepcopy(DOCUMENTS[name])
    plan = schema['properties']['plan']
    ids = [item['id'] for item in requirements]
    coverage = plan['properties']['coverage']
    coverage.update(minItems=len(ids), maxItems=len(ids))
    coverage['items']['properties']['requirement_id']['enum'] = ids
    coverage['items']['properties']['cell_ids']['minItems'] = 1
    plan['properties']['cells']['maxItems'] = max_cells
    if research:
        plan['properties']['schema_version']['const'] = 3
        code = obj(dict(copy.deepcopy(CELL['properties']), kind={'type': 'string', 'const': 'code'}))
        for field in code['properties']['result']['properties'].values():
            field.pop('description', None)
        typed_schema = {'type': 'object', 'properties': {'type': {'type': 'string',
            'enum': ['string', 'boolean', 'integer', 'number', 'null', 'array', 'object']}},
            'required': ['type'], 'additionalProperties': True}
        code_result = code['properties']['result']['properties']
        code_result.pop('schema_json')
        code_result['schema'] = typed_schema
        code['properties']['result'] = obj(code_result)
        read = obj(dict(copy.deepcopy(CELL['properties']), kind={'type': 'string', 'const': 'research'},
                        result=obj({'schema': typed_schema})))
        plan['properties']['cells']['items'] = {'anyOf': [code, read]}
    if file_paths is not None:
        for variant in plan['properties']['cells']['items'].get('anyOf', [plan['properties']['cells']['items']]):
            variant['properties']['files']['items']['enum'] = sorted(set(file_paths))
            result = variant['properties']['result']['properties']
            if 'path' in result:
                result['path']['enum'] = sorted(set(file_paths))
    if name == 'submit_intake':
        if research:
            # Research/code cell variants already make this schema large.
            # Ownership is checked by intake.validate after submission; avoid
            # tripling the plan schema inside the model's fixed context.
            return schema
        variants = []
        for mode in ('delegate', 'execute', 'delegate-and-work'):
            if mode == 'delegate-and-work' and max_cells == 1:
                continue
            variant = copy.deepcopy(schema)
            props = variant['properties']
            props['mode'] = {'type': 'string', 'const': mode}
            props['self_cell_id'] = ({'type': 'string', 'const': ''} if mode == 'delegate'
                                    else {'type': 'string', 'minLength': 1})
            if mode == 'execute':
                props['plan']['properties']['cells']['maxItems'] = 1
            if mode == 'delegate-and-work':
                props['plan']['properties']['cells']['minItems'] = 2
            variants.append(variant)
        return {'anyOf': variants}
    schema['properties']['route'] = {'type': 'string', 'const': 'needs_split'}
    plan['properties']['cells']['minItems'] = 2
    execute = copy.deepcopy(schema)
    execute['properties']['route']['const'] = 'execute'
    empty = execute['properties']['plan']['properties']
    empty['schema_version']['const'] = 2
    empty['cells'].update(minItems=0, maxItems=0)
    empty['coverage'].update(minItems=0, maxItems=0)
    return {'anyOf': [execute, schema]}
