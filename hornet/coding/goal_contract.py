"""Caller-supplied goal checks frozen before the first model request."""
import copy
from hornet.project import HornetError
from . import acceptance, cells, mission


FILENAME = 'operator-acceptance.json'
CELL_POLICY_FILE = 'cell-gate-policy.json'
CELL_POLICY = {'schema_version': 1, 'routing': 'flat-dependency-ready-v1',
               'max_combined_checks': 8, 'final_gate': 'all-original-checks'}


def validate_checks(checks):
    acceptance.validate(checks)
    for check in checks['checks']:
        for path in check['paths']:
            cells.path(path)
    return checks


def configure(project, filename, scope=None):
    checks = acceptance.read(project, filename)
    if checks is not None:
        validate_checks(checks)
        if scope is not None:
            validate_paths(checks, scope['files'])
    return checks


def validate_paths(checks, allowed):
    required = {path for check in checks['checks'] for path in check['paths']}
    missing = required - set(allowed)
    if missing:
        raise HornetError('Supplied goal checks require declared production files: ' + ', '.join(sorted(missing)))


def initialize(run, state, checks):
    if checks is not None:
        mission.immutable(run / FILENAME, checks)
        state['operator_acceptance'] = mission.reference(run, run / FILENAME)
        mission.immutable(run / CELL_POLICY_FILE, CELL_POLICY)
        state['cell_gate_policy'] = mission.reference(run, run / CELL_POLICY_FILE)


def read(run, state):
    reference = state.get('operator_acceptance')
    path = run / FILENAME
    if reference is None:
        if path.exists():
            raise HornetError('Supplied goal checks lost their checkpoint binding')
        return None
    if mission.reference(run, path) != reference:
        raise HornetError('Supplied goal checks changed')
    return validate_checks(mission.read(path))


def validate_plan(run, state, plan):
    checks = read(run, state)
    if checks is not None:
        validate_paths(checks, [path for cell in plan['cells'] if cell.get('kind') != 'research'
                                for path in cell['files']])


def validate(run, state):
    from . import tree
    cell_policy(run, state)
    checks = read(run, state)
    if checks is None:
        return
    if state.get('plan') is not None:
        validate_plan(run, state, state['plan'])
    if state.get('goal_checks') is not None and state['goal_checks'] != checks:
        raise HornetError('Overall goal checks differ from the supplied contract')
    current = state.get('planning', {}).get('current')
    if current is not None:
        draft = mission.read(run / current['path'])
        validate_plan(run, state, draft['candidate']['plan'])
    for cell in tree.all_cells(state):
        record = state.get('cells', {}).get(cell['id'], {})
        if 'checks' not in record:
            continue
        if not state.get('cell_gate_policy'):
            if 'generated_acceptance' in record or 'early_goal_checks' in record:
                raise HornetError('Early cell checks lost their policy binding')
            continue
        path = run / 'cells' / cell['id'] / 'generated-acceptance.json'
        if mission.reference(run, path) != record.get('generated_acceptance'):
            raise HornetError('Generated cell checks lost their original evidence')
        generated = acceptance.validate(mission.read(path))
        # The typed-return check already has its frozen marker and ordinary cell
        # evidence. Keep it exactly as saved; only the author's deterministic
        # checks belong in the additional immutable preparation artifact.
        typed = next(c for c in record['checks']['checks'] if c['id'] == 'typed-return')
        original = {'schema_version': 1, 'checks': generated['checks'] + [typed]}
        expected, routing = extend_cell_checks(run, state, cell, original)
        if expected != record['checks'] or routing != record.get('early_goal_checks'):
            raise HornetError('Early cell checks differ from their frozen goal contract')


def cell_policy(run, state):
    path = run / CELL_POLICY_FILE
    ref = state.get('cell_gate_policy')
    if ref is None:
        if path.exists():
            raise HornetError('Early cell checks lost their policy binding')
        return None
    if (not state.get('operator_acceptance') or mission.reference(run, path) != ref
            or mission.read(path) != CELL_POLICY):
        raise HornetError('Early cell check policy changed')
    return CELL_POLICY


def extend_cell_checks(run, state, cell, original):
    """Protect consumers with ready upstream checks, without guessing imports.

    A check runs early only for a flat-plan cell with downstream consumers when
    all its declared file owners are this cell or its transitive dependencies.
    Later writers defer the check. Nested plans and terminal cells keep the final
    gate. Original verifier checks and every final goal check remain intact.
    """
    policy = cell_policy(run, state)
    if policy is None:
        return original, None
    supplied = read(run, state)
    plan = (state.get('plan') or {}).get('cells', [])
    by_id = {c['id']: c for c in plan if c.get('kind') != 'research'}
    ready = {cell['id']}
    pending = list(cell.get('depends_on', []))
    while pending:
        ident = pending.pop()
        if ident not in ready:
            ready.add(ident)
            pending.extend(by_id.get(ident, {}).get('depends_on', []))
    consumers = any(cell['id'] in c['depends_on'] for c in by_id.values())
    owners = {}
    for item in by_id.values():
        for path in item['files']:
            owners.setdefault(path, set()).add(item['id'])
    eligible = []
    if state.get('schema_version', 1) < 5 and consumers:
        for check in supplied['checks']:
            paths = set(check['paths'])
            if (paths.intersection(cell['files']) and
                    all(path in owners and owners[path] <= ready for path in paths)):
                eligible.append(check)
    merged = copy.deepcopy(original)
    ids = {c['id'] for c in merged['checks']}
    selected = []
    for check in eligible[:max(0, policy['max_combined_checks'] - len(merged['checks']))]:
        name = 'goal-' + acceptance.digest(check)[:16]
        while name in ids:
            name += 'x'
        ids.add(name)
        merged['checks'].append(dict(check, id=name))
        selected.append({'goal_check_id': check['id'], 'cell_check_id': name})
    acceptance.validate(merged)
    routing = {'schema_version': 1, 'policy': state['cell_gate_policy'],
               'goal_acceptance': state['operator_acceptance'], 'selected': selected,
               'deferred_eligible': [c['id'] for c in eligible[len(selected):]]}
    return merged, routing


def freeze_cell_checks(run, state, cell, generated, checks, record):
    merged, routing = extend_cell_checks(run, state, cell, checks)
    if routing is not None:
        path = run / 'cells' / cell['id'] / 'generated-acceptance.json'
        # No random typed-return marker here: a crash before the checkpoint can
        # repeat this preparation using the same retained verifier submission.
        mission.immutable(path, generated)
        record['generated_acceptance'] = mission.reference(run, path)
        record['early_goal_checks'] = routing
    return merged


def brief(run, state, paths=None):
    checks = read(run, state)
    if checks is None:
        return ''
    relevant = [dict(id=item['id'], description=item['description'], paths=item['paths'])
                for item in checks['checks'] if paths is None or set(item['paths']).intersection(paths)]
    if not relevant:
        return ''
    return ('\nSupplied goal acceptance criteria (data): ' + acceptance.encoded(relevant)
            + '\nThe controller froze these checks before planning and will run them against the final product. '
              'Preserve their requirements. Cell success alone does not complete the goal.')
