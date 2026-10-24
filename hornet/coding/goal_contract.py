"""Caller-supplied goal checks frozen before the first model request."""
from hornet.project import HornetError
from . import acceptance, cells, mission


FILENAME = 'operator-acceptance.json'


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
