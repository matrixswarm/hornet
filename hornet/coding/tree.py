"""Bounded recon decisions and durable, sequential parent/child contracts."""
import json

from hornet.project import HornetError, write_text
from . import acceptance, cells, mission


LIMITS = {'max_depth': 2, 'max_nodes': 9, 'recent_exchanges': 2, 'recon_reads': 2}
EMPTY = {'schema_version': 2, 'cells': [], 'coverage': []}
INTAKE_PROMPT = (
    'You are the initial read-only recon cell. Inspect at most two relevant source sections, then submit_intake. '
    'This stage declares TOP-LEVEL delivery contracts only. If the goal requests a parent with nested children, '
    'declare ONLY that parent here. Its later recon cell will declare the children. Do not flatten a requested tree. '
    'Choose execute for one top-level contract, delegate to assign all top-level contracts, or delegate-and-work '
    'to own one of at least two top-level contracts. execute means ownership of the contract; that contract may '
    'later split. self_cell_id must name your declared root, or be empty for delegate. '
    'Submit schema_version 1, mode, short reason, self_cell_id and plan. Plan schema_version is 2. '
    'Each of 1-{max_cells} cells has id, task, files, depends_on, result. Use actual goal interfaces. '
    'Each result has path, function, args_json (JSON ARRAY text), kwargs_json (JSON OBJECT text), '
    'schema_json (valid JSON Schema text). Cells own 1-4 relative files. Shared files need dependencies '
    'on earlier cells. Preserve every supplied requirement; coverage assigns EACH requirement ID in order '
    'to one or more declared top-level IDs. Every declared cell must have coverage. '
    'A Boolean schema is {"type":"boolean"}. An object schema example is '
    '{"type":"object","properties":{"even":{"type":"boolean"},"label":{"type":"string"}},'
    '"required":["even","label"],"additionalProperties":false}. Use required, never required_all. '
    'Use each cell task to preserve its normal, edge and invalid-input requirements. '
    'Use one compact submission with submit_intake; no implementation tools are available.'
)
PROMPT = (
    'You are the read-only recon cell for an EXISTING frozen parent contract. Initial intake is already '
    'complete. Do not declare the existing parent again: a split contains only NEW child contracts. '
    'Requirements describing the initial intake stage have already been satisfied; preserve its result. '
    'Inspect relevant source if needed '
    '(at most two reads), then submit_route. Choose execute for a small implementation. Choose needs_split '
    'only when two or three smaller children make the work easier. Use a short reason. '
    'Do not write code in this role. The parent contract and acceptance checks remain fixed. '
    'Use schema_version 1, route, reason and plan. For execute, plan is '
    '{"schema_version":2,"cells":[],"coverage":[]}. For needs_split, plan uses schema_version 2, '
    'cells and coverage, exactly like the initial plan. Each child has id, task, files, depends_on, result '
    '(path, function, args_json, kwargs_json, schema_json). args_json is JSON ARRAY text, kwargs_json '
    'is JSON OBJECT text, schema_json is JSON Schema text. '
    'Use globally unique IDs and only the parent files. Dependencies refer to earlier siblings. '
    'Shared files require a dependency. Assign every supplied requirement ID in order. '
    'Children must make scoped changes and expose typed Python results; the final child should integrate '
    'their work if necessary. Every child inherits the parent constraints. '
    'For object schemas use properties, required (an array of property names), and additionalProperties:false; '
    'required_all is invalid. Return only the decision using submit_route; do not reproduce the parent checks or source.'
)


def enabled(state):
    return state.get('schema_version', 1) >= 5


def children(state, cell):
    return state.get('routes', {}).get(cell['id'], {}).get('plan', {}).get('cells', [])


def all_cells(state):
    result = []
    def walk(cell):
        result.append(cell)
        for child in children(state, cell):
            walk(child)
    for cell in (state.get('plan') or {}).get('cells', []):
        walk(cell)
    return result


def postorder(state):
    result = []
    def walk(cell):
        for child in children(state, cell):
            walk(child)
        result.append(cell)
    for cell in (state.get('plan') or {}).get('cells', []):
        walk(cell)
    return result


def parent(state, cell):
    return next((node for node in all_cells(state)
                 if any(child['id'] == cell['id'] for child in children(state, node))), None)


def depth(state, cell):
    above = parent(state, cell)
    return 1 if above is None else depth(state, above) + 1


def requirements(state, cell):
    above = parent(state, cell)
    plan = state['routes'][above['id']]['plan'] if above else state['plan']
    supplied = requirements(state, above) if above else mission.document(state['goal'])['requirements']
    assigned = {entry['requirement_id'] for entry in plan['coverage'] if cell['id'] in entry['cell_ids']}
    return [item for item in supplied if item['id'] in assigned]


def next_cell(state):
    def visit(cell):
        if cell['id'] in state['values']:
            return None
        for child in children(state, cell):
            pending = visit(child)
            if pending:
                return pending
        return cell
    return next((pending for cell in (state['plan'] or {}).get('cells', [])
                 for pending in [visit(cell)] if pending is not None), None)


def validate_choice(state, cell, value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'route', 'reason', 'plan'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['route'] not in {'execute', 'needs_split'} or not isinstance(value['reason'], str)
            or not 1 <= len(value['reason'].strip()) <= 600):
        raise HornetError('Recon requires schema_version 1, route, reason and plan')
    if value['route'] == 'execute':
        if value['plan'] != EMPTY:
            raise HornetError('execute requires an empty schema 2 plan')
        return value
    if cell.get('kind') == 'research':
        raise HornetError('Research cells are bounded read-only leaves')
    if depth(state, cell) >= LIMITS['max_depth']:
        raise HornetError('Maximum tree depth reached; execute this bounded leaf')
    from . import intake
    proposal = {'schema_version': 1, 'mode': 'delegate', 'reason': value['reason'],
                'self_cell_id': '', 'plan': value['plan']}
    # --max-cells bounds the initial plan. Each nested plan has its own fixed
    # fanout cap of three, within the original total-node/depth budgets.
    intake.validate(proposal, {'requirements': requirements(state, cell)}, 3)
    if value['plan']['schema_version'] != (3 if state['schema_version'] >= 6 else 2):
        raise HornetError('Nested plan schema must match the recipe mode')
    proposed = value['plan']['cells']
    if len(proposed) < 2:
        raise HornetError('A split requires at least two smaller children')
    previous = children(state, cell)
    existing = [node for node in all_cells(state) if node['id'] not in {node['id'] for node in previous}]
    ids = [node['id'] for node in existing + proposed]
    addresses = ['intake'] + ids + ['verifier-' + key for key in ids] + ['recon-' + key for key in ids]
    if len(ids) > LIMITS['max_nodes'] or len(set(addresses)) != len(addresses):
        raise HornetError('Split exceeds the total node limit or reuses a cell/inbox ID. '
                          'Declare only NEW children; existing IDs: ' + ', '.join(node['id'] for node in existing))
    if any(not set(child['files']) <= set(cell['files']) for child in proposed):
        raise HornetError('Child file permissions must stay inside the parent scope')
    return value


def contract(state, cell):
    record = state['cells'][cell['id']]
    return {'schema_version': 1, 'kind': 'recon_contract', 'goal_sha256': state['goal_sha256'],
            'cell': cell, 'requirements': requirements(state, cell),
            'checks_sha256': record['checks_sha256'],
            'source_sha256': acceptance.digest(record['initial_snapshot']),
            'permissions': {'read_files': cell['files'], 'write_files': []},
            'limits': LIMITS, 'depth': depth(state, cell)}


def publish(run, state, cell, choice, automatic=False):
    validate_choice(state, cell, choice)
    directory = run / 'cells' / cell['id'] / 'recon'
    frozen = contract(state, cell)
    mission.immutable(directory / 'contract.json', frozen)
    receipt = {'schema_version': 1, 'kind': 'recon_return', 'ok': True,
               'contract_sha256': acceptance.digest(frozen), 'result': choice,
               'producer': 'controller_depth_limit' if automatic else 'agent',
               'role': 'recon-' + cell['id']}
    mission.immutable(directory / 'return.json', receipt)
    state['routes'][cell['id']] = choice
    state['cells'][cell['id']]['recon_return'] = mission.reference(run, directory / 'return.json')


def validate_state(run, state):
    if not enabled(state):
        return
    if state.get('tree_limits') != LIMITS or not isinstance(state.get('routes'), dict):
        raise HornetError('Frozen tree limits changed')
    # Discover children only from validated decisions, so malformed/cyclic state
    # cannot drive recursion before validation.
    view = dict(state, routes={})
    queue = list((state.get('plan') or {}).get('cells', []))
    ids = [cell['id'] for cell in queue]
    addresses = ['intake'] + ids + ['verifier-' + key for key in ids] + ['recon-' + key for key in ids]
    if len(set(addresses)) != len(addresses):
        raise HornetError('Root cell IDs collide with recon inboxes')
    seen = set()
    while queue:
        cell = queue.pop(0)
        ident = cell['id']
        if ident in seen or len(seen) >= LIMITS['max_nodes']:
            raise HornetError('Invalid cell tree')
        seen.add(ident)
        if cell.get('kind') == 'research':
            if state['schema_version'] < 6 or ident in state['routes'] or 'run_dir' in state['cells'].get(ident, {}):
                raise HornetError('Research cannot own an implementation job or a split')
            continue
        if ident not in state['routes']:
            if ident in state['values'] or 'run_dir' in state['cells'].get(ident, {}):
                raise HornetError('Implementation requires a frozen recon decision')
            continue
        choice = state['routes'][ident]
        validate_choice(view, cell, choice)
        view['routes'][ident] = choice
        directory = run / 'cells' / ident / 'recon'
        frozen = contract(view, cell)
        receipt = mission.read(directory / 'return.json')
        if (mission.read(directory / 'contract.json') != frozen
                or receipt.get('contract_sha256') != acceptance.digest(frozen)
                or receipt.get('result') != choice or receipt.get('ok') is not True
                or state['cells'][ident].get('recon_return') != mission.reference(run, directory / 'return.json')):
            raise HornetError('Frozen recon evidence changed')
        if receipt.get('producer') == 'agent':
            if state['roles'].get('recon-' + ident, {}).get('value') != choice:
                raise HornetError('Recon result differs from the accepted submission')
        elif (receipt.get('producer') != 'controller_depth_limit' or depth(view, cell) != LIMITS['max_depth']
              or choice['route'] != 'execute'):
            raise HornetError('Invalid automatic leaf route')
        queue.extend(children(view, cell))
    if not set(state['routes']) <= seen:
        raise HornetError('Orphaned recon decision')


def verify_parent(run, state, cell, worker, save):
    """Run original parent checks after all descendants; never trust child flags."""
    from . import session
    record = state['cells'][cell['id']]
    directory = run / 'cells' / cell['id'] / 'parent-verification'
    if record.get('parent_in_flight'):
        raise HornetError('Parent verification was in flight; outcome requires inspection')
    snapshot = session.snapshot(worker)
    if snapshot != state['snapshot']:
        raise HornetError('Source changed before parent verification')
    if 'parent_verification' in record:
        if record['parent_verification']['snapshot'] != snapshot:
            raise HornetError('Parent verification belongs to another source snapshot')
        return
    record['parent_in_flight'] = True
    save()
    # Each retry gets its own immutable evidence directory.
    directory = directory / str(state['attempts'])
    suite = worker.call('run_tests')
    mission.immutable(directory / 'tests.json', suite)
    report = acceptance.evaluate(worker, record['checks'],
        lambda value: write_text(directory / 'partial.json', acceptance.encoded(value), force=True))
    mission.immutable(directory / 'acceptance.json', report)
    if session.snapshot(worker) != snapshot:
        raise HornetError('Parent verification changed the worker source')
    record['parent_in_flight'] = False
    save()
    if suite.get('ok') is not True or suite.get('exit_code') != 0 or report.get('ok') is not True:
        raise HornetError('Parent contract verification failed: ' + cell['id'])
    record['parent_verification'] = {
        'suite': mission.reference(run, directory / 'tests.json'),
        'checks': mission.reference(run, directory / 'acceptance.json'), 'snapshot': snapshot}
    save()


def parent_receipt(project, run, state, cell, attempt):
    from . import returns
    record = state['cells'][cell['id']]
    evidence = record['parent_verification']
    for key in ('suite', 'checks'):
        ref = evidence[key]
        if mission.reference(run, run / ref['path']) != ref:
            raise HornetError('Parent verification evidence changed')
    suite, report = (mission.read(run / evidence[key]['path']) for key in ('suite', 'checks'))
    if (suite.get('ok') is not True or type(suite.get('exit_code')) is not int or suite['exit_code'] != 0
            or report.get('ok') is not True or report.get('plan_sha256') != record['checks_sha256']
            or [item['id'] for item in report['results']] != [item['id'] for item in record['checks']['checks']]
            or any(item.get('passed') is not True or item.get('completed') is not True for item in report['results'])):
        raise HornetError('Parent return requires its original checks and full suite')
    changes = returns.changed_files(record['initial_snapshot'], evidence['snapshot'])
    if not set(item['path'] for item in changes) <= set(cell['files']):
        raise HornetError('Descendants changed files outside the parent scope')
    offspring = children(state, cell)
    if any(child['id'] not in state['values'] for child in offspring):
        raise HornetError('Parent return is missing verified children')
    value = cells.extract(report, record['marker'], cell)
    if type(attempt) is not int or not 1 <= attempt <= state['attempts']:
        raise HornetError('Invalid parent return attempt')
    return {'schema_version': 1, 'kind': 'parent_return', 'ok': True, 'status': 'verified',
            'cell_id': cell['id'], 'contract': cell, 'contract_sha256': acceptance.digest(cell),
            'goal': mission.binding(state, cell), 'plan_sha256': state['plan_sha256'],
            'result': {'value': value, 'schema': cells.result_spec(cell)},
            'changed_files': changes, 'verification': evidence,
            'children': {child['id']: state['cells'][child['id']]['return_manifest'] for child in offspring},
            'recon_return': record['recon_return'],
            'provenance': {'recorded_in_recipe_attempt': attempt,
                'recipe_conditions': mission.reference(run, run / 'attempts' / str(attempt) / 'conditions.json')}}


def validate_completed(project, run, state):
    from . import returns, session
    previous = state['initial_snapshot']
    def walk(cell):
        nonlocal previous
        record = state['cells'].get(cell['id'], {})
        if record and record['initial_snapshot'] != previous:
            raise HornetError('Nested source chain is inconsistent: ' + cell['id'])
        offspring = children(state, cell)
        if offspring:
            pending = False
            for child in offspring:
                if pending and (child['id'] in state['cells'] or child['id'] in state['values']):
                    raise HornetError('Children must complete in dependency order')
                walk(child)
                pending = child['id'] not in state['values']
            if cell['id'] in state['values']:
                if any(child['id'] not in state['values'] for child in offspring):
                    raise HornetError('Completed parent has unfinished children')
                returns.checked(project, run, state, cell)
                if record['parent_verification']['snapshot'] != previous:
                    raise HornetError('Parent verification source chain changed')
        elif cell['id'] in state['values']:
            returns.checked(project, run, state, cell)
            if cell.get('kind') != 'research':
                previous = session.load(project, returns.child_path(run, cell, record))['snapshot']
    pending = False
    for cell in (state.get('plan') or {}).get('cells', []):
        if pending and (cell['id'] in state['cells'] or cell['id'] in state['values']):
            raise HornetError('Root cells must complete in dependency order')
        walk(cell)
        pending = cell['id'] not in state['values']
