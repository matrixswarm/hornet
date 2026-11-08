"""Action-specific atomic edit schemas, bound to the current approved leaf."""


def edit_plan_schema(context=None):
    context = context or {}
    variants = []
    for action in ('create', 'replace', 'insert', 'delete'):
        properties = {'path': {'type': 'string'}, 'action': {'type': 'string', 'enum': [action]},
                      'new_text': {'type': 'string'}}
        required = ['path', 'action', 'new_text']
        if action in ('replace', 'delete'):
            properties['target_text'] = {'type': 'string', 'minLength': 1}
            required.append('target_text')
        if action == 'insert':
            properties['line_number'] = {'type': 'integer', 'minimum': 1}
            required.append('line_number')
        if action == 'delete':
            properties['new_text']['enum'] = ['']
        variants.append({'type': 'object', 'additionalProperties': False,
                         'properties': properties, 'required': required})
    for position in ('start', 'end', 'before', 'after'):
        fields = {'path': {'type': 'string'}, 'action': {'type': 'string', 'enum': ['insert']},
                  'new_text': {'type': 'string'}, 'position': {'type': 'string', 'enum': [position]}}
        required_fields = ['path', 'action', 'new_text', 'position']
        if position in ('before', 'after'):
            fields['anchor_text'] = {'type': 'string', 'minLength': 1}
            required_fields.append('anchor_text')
        variants.append({'type': 'object', 'additionalProperties': False,
                         'properties': fields, 'required': required_fields})
    properties = {'goal_id': {'type': 'string'},
                  'uri': {'type': 'string', 'enum': ['files.text.atomic_edit']},
                  'arguments': {'anyOf': variants}}
    required = ['uri', 'arguments']
    graph = context.get('goal_decomposition') or []
    if graph:
        parents = {node['parent'] for node in graph}
        leaves = [node['id'] for node in graph if node['id'] not in parents]
        active = context.get('active_goal_id')
        if active and active not in leaves:
            raise ValueError('Active goal must be an approved leaf.')
        properties['goal_id']['enum'] = [active] if active else leaves
        # Single-leaf execution already owns this identity. Only a multi-leaf
        # proposal needs model attribution; conflicting explicit IDs still fail.
        if not active:
            required.append('goal_id')
    return {'type': 'object', 'additionalProperties': False, 'properties': {
        'summary': {'type': 'string'},
        'edits': {'type': 'array', 'maxItems': 20, 'items': {'type': 'object',
            'additionalProperties': False, 'properties': properties, 'required': required}}},
        'required': ['summary', 'edits']}
