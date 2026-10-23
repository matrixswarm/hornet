"""Explicit cell selection at safe autonomous resume boundaries."""
from types import SimpleNamespace

from hornet.project import HornetError
from . import autonomous, tree


def resume(project, run, ident, worker=None, request_model=None):
    path = project.path(run, write=True)
    state = autonomous.load(project, path)
    cell = next((item for item in tree.all_cells(state) if item['id'] == ident), None)
    if cell is None or ident in state['values']:
        raise HornetError('Select an exact unfinished cell ID; verified cells require a new revision')
    if state['active_cell'] != ident or state['phase'] not in {'checker', 'cell_running'}:
        raise HornetError('Selected cell is not the controller\'s current cell; dependencies and order stay frozen')
    if any(parent not in state['values'] for parent in cell['depends_on']):
        raise HornetError('Selected cell still has unverified dependencies')
    from hornet import hive_status
    live, _, _ = hive_status.runner(path, state)
    if live:
        raise HornetError('Selected cell is already running')
    # The controller rechecks locks, workspace identity, source hashes, budgets
    # and any in-flight worker operation. This selector grants no new attempts.
    return autonomous.resume(project, SimpleNamespace(run=str(path)), worker, request_model)
