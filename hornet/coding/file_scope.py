"""Operator-selected production files bound to one autonomous goal."""
from hornet.project import HornetError
from . import cells, mission


def configure(paths):
    if paths is None:
        return None
    if (not isinstance(paths, list) or not 1 <= len(paths) <= 12
            or any(not isinstance(path, str) for path in paths)
            or len(paths) != len(set(paths))):
        raise HornetError('Select 1-12 distinct --scope-file paths')
    for path in paths:
        cells.path(path)
    return {'schema_version': 1, 'files': sorted(paths)}


def initialize(run, state, policy):
    if policy is not None:
        mission.immutable(run / 'file-scope.json', policy)
        state['file_scope'] = mission.reference(run, run / 'file-scope.json')


def files(run, state):
    ref = state.get('file_scope')
    if ref is None:
        if (run / 'file-scope.json').exists():
            raise HornetError('Goal file scope lost its checkpoint binding')
        return None
    path = run / 'file-scope.json'
    if mission.reference(run, path) != ref:
        raise HornetError('Goal file scope changed')
    value = mission.read(path)
    if configure(value.get('files')) != value:
        raise HornetError('Invalid goal file scope')
    return value['files']


def validate_plan(run, state, plan):
    allowed = files(run, state)
    if allowed is None:
        return
    owned = {path for cell in plan['cells'] if cell.get('kind') != 'research' for path in cell['files']}
    declared = {path for cell in plan['cells'] for path in cell['files']}
    if declared != set(allowed) or owned != set(allowed):
        raise HornetError('Plan must own exactly the scoped production files: ' + ', '.join(allowed))


def validate(run, state):
    files(run, state)
    if state.get('plan') is not None:
        validate_plan(run, state, state['plan'])
    current = state.get('planning', {}).get('current')
    if current is not None:
        draft = mission.read(run / current['path'])
        validate_plan(run, state, draft['candidate']['plan'])
