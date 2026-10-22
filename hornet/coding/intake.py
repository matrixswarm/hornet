"""First-response ownership decisions, frozen before any coding begins."""
from hornet.project import HornetError
from . import acceptance, cells, mission


MODES = ('execute', 'delegate', 'delegate-and-work')
PROMPT = (
    'Your first action is submit_intake: choose execute, delegate, or delegate-and-work and supply the plan '
    'in the SAME structured submission. No source tools are available in this intake role. '
    'Choose execute for one small bounded cell you can implement yourself. '
    'Choose delegate when you should coordinate and assign every cell to a delegate. '
    'Choose delegate-and-work when you can own one bounded child and assign the remaining children to delegates. '
    'Use a short practical reason, not a reasoning transcript. self_cell_id is your cell ID for execute or '
    'delegate-and-work, and the empty string for delegate. Ownership is fixed after acceptance. '
    'Each worker receives a fresh scoped context; this runtime queues cells sequentially on one worker. '
    'Declare 1 to {max_cells} small ordered cells. Every cell must implement code in 1-4 declared relative files '
    'and expose a Python function returning a typed value. Prefer one cell for a single function. '
    'Do not invent extra work. Keep the original interfaces and every normal, edge and invalid-input requirement. '
    'Dependencies name earlier cells only. Independent cells must own disjoint files; shared files require '
    'an explicit dependency. Each result has path, function, args_json (JSON ARRAY text), kwargs_json '
    '(JSON OBJECT text), and schema_json (JSON Schema text). Dependency arguments use [{"$cell":"earlier-id"}]. '
    'Supported types: boolean, integer, number, string, null, array/items, object/properties/required/'
    'additionalProperties, and enum. Use plan schema_version 2. coverage must assign EVERY supplied requirement '
    'ID in its original order to one or more cell IDs; assign global constraints to every affected cell. '
    'Every cell needs an assignment. Use intake schema_version 1. For a single function, the shape is '
    '{"schema_version":1,"mode":"execute","reason":"One bounded function",'
    '"self_cell_id":"check-even","plan":{"schema_version":2,"cells":[{"id":"check-even",'
    '"task":"Implement the required parity behavior","files":["parity_cell.py"],"depends_on":[], '
    '"result":{"path":"parity_cell.py","function":"is_even","args_json":"[3]","kwargs_json":"{}",'
    r'"schema_json":"{\"type\":\"boolean\"}"}}],'
    '"coverage":[{"requirement_id":"r001","cell_ids":["check-even"]}]}}. '
    'Use actual goal files, functions, arguments and requirement IDs.'
)


def validate(value, goal, max_cells):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'mode', 'reason', 'self_cell_id', 'plan'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['mode'] not in MODES or not isinstance(value['reason'], str)
            or not 1 <= len(value['reason'].strip()) <= 600 or not isinstance(value['self_cell_id'], str)):
        raise HornetError('Intake requires schema_version 1, mode, a reason of 1-600 characters, self_cell_id and plan')
    plan = cells.validate(value['plan'], max_cells)
    mission.coverage(plan, goal)
    ids = [cell['id'] for cell in plan['cells']]
    mode, own = value['mode'], value['self_cell_id']
    if (mode == 'execute' and (len(ids) != 1 or own != ids[0])
            or mode == 'delegate' and own != ''
            or mode == 'delegate-and-work' and (len(ids) < 2 or own not in ids)):
        raise HornetError('Invalid ownership: mode=%r, self_cell_id=%r. Declared cell IDs: %s. '
            'For delegate-and-work, set self_cell_id to exactly one of those IDs (at least two cells). '
            'For delegate, set self_cell_id to the empty string. For execute, declare only one cell and '
            'set self_cell_id to that cell ID.' % (mode, own, ', '.join(ids)))
    addresses = ['intake'] + ids + ['verifier-' + key for key in ids]
    if len(set(addresses)) != len(addresses):
        raise HornetError('Cell IDs must not collide with intake or another cell verifier address')
    ancestors, files = {}, {}
    for cell in plan['cells']:
        ident = cell['id']
        ancestors[ident] = set(cell['depends_on'])
        for parent in cell['depends_on']:
            ancestors[ident].update(ancestors[parent])
        for earlier, scope in files.items():
            if scope.intersection(cell['files']) and earlier not in ancestors[ident]:
                raise HornetError('Cells sharing files require a dependency: %s -> %s' % (earlier, ident))
        files[ident] = set(cell['files'])
    return value


def assignment(state, cell):
    if state['schema_version'] >= 5:
        from . import tree
        above = tree.parent(state, cell)
        if above:
            return {'mode': 'delegate', 'owner': 'delegate/' + cell['id'], 'role': 'delegate',
                    'inbox': cell['id'], 'intake_record': state['intake_record'],
                    'parent_id': above['id'], 'recon_return': state['cells'][above['id']]['recon_return']}
    choice = state['intake']
    own = cell['id'] == choice['self_cell_id']
    return {'mode': choice['mode'], 'owner': 'intake' if own else 'delegate/' + cell['id'],
            'role': 'self' if own else 'delegate', 'inbox': cell['id'],
            'intake_record': state['intake_record']}


def summary(state):
    choice = state.get('intake')
    if choice is None:
        return None
    return {'mode': choice['mode'], 'reason': choice['reason'], 'self_cell_id': choice['self_cell_id'],
            'record': state['intake_record'], 'execution': 'sequential',
            'owners': {cell['id']: assignment(state, cell)['owner'] for cell in choice['plan']['cells']}}


def document(state, choice):
    validate(choice, mission.document(state['goal']), state['limits']['max_cells'])
    if choice['plan']['schema_version'] != (3 if state['schema_version'] >= 6 else 2):
        raise HornetError('Plan schema must match the frozen recipe mode')
    result = {'schema_version': 1, 'kind': 'intake_decision', 'goal_sha256': state['goal_sha256'],
            'initial_source_sha256': acceptance.digest(state['initial_snapshot']),
            'limits': state['limits'], 'decision': choice}
    if state['schema_version'] >= 5:
        result.update(kind='recon_intake_return', tree_limits=state['tree_limits'])
    return result


def saved(run, state):
    record = mission.read(run / 'intake.json')
    choice = record['decision']
    if acceptance.encoded(record) != acceptance.encoded(document(state, choice)):
        raise HornetError('Frozen intake bindings changed')
    cached = state['roles'].get('intake', {}).get('value')
    if cached is None or acceptance.encoded(cached) != acceptance.encoded(choice):
        raise HornetError('Frozen intake differs from its accepted submission')
    return choice


def publish(run, state, choice):
    # Publication precedes the checkpoint. A crash can adopt this exact document;
    # it cannot ask the model to choose another strategy for the same recipe.
    mission.immutable(run / 'intake.json', document(state, choice))
    mission.immutable(run / 'recipe-plan.json', choice['plan'])
    state.update(intake=choice, intake_record=mission.reference(run, run / 'intake.json'),
                 plan=choice['plan'], plan_sha256=acceptance.digest(choice['plan']), phase='goal_checks')


def validate_state(run, state):
    if state['schema_version'] < 4:
        return
    if state['intake'] is None:
        if (state['phase'] != 'intake' or state['plan'] is not None or state['intake_record'] is not None
                or state['cells'] or state['values'] or state['goal_checks'] is not None):
            raise HornetError('Coding cannot start before a frozen intake decision')
        if (run / 'intake.json').exists():
            saved(run, state)
        return
    choice = saved(run, state)
    if (state['phase'] in {'intake', 'plan'} or state['intake_record'] != mission.reference(run, run / 'intake.json')
            or acceptance.encoded(state['intake']) != acceptance.encoded(choice)
            or acceptance.encoded(state['plan']) != acceptance.encoded(choice['plan'])):
        raise HornetError('Frozen intake decision, ownership or plan changed')
