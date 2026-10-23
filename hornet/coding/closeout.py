"""Completion evidence inside the worker lease; release receipt after exit."""
from pathlib import Path

from hornet.project import HornetError
from . import acceptance, activity, mission, questions, session


def prepare(project, run, state, worker):
    if state['phase'] != 'complete' or not state.get('plan_policy'):
        return
    if (state.get('final_checks_in_flight') or any(row['status'] == 'in_flight' for row in state['request_records'])
            or any(row.get('parent_in_flight') for row in state['cells'].values())
            or questions.pending(project, run)):
        raise HornetError('Closeout refused: unresolved work remains')
    if session.snapshot(worker) != state['snapshot']:
        raise HornetError('Closeout refused: source changed after final verification')
    for record in state['cells'].values():
        if record.get('run_dir'):
            child = session.load(project, Path(record['run_dir']))
            if child['phase'] != 'complete' or child['pending'] or child.get('in_flight_operation'):
                raise HornetError('Closeout refused: a child has unfinished operations')
    mission.validate(run, state)
    mission.immutable(run / 'closeout-ready.json', {
        'schema_version': 1, 'goal_completion': state['goal_completion'],
        'checkpoint': mission.reference(run, run / 'recipe-checkpoint.json'),
        'source_sha256': acceptance.digest(state['snapshot']),
        'all_children_complete': True, 'no_pending_operations': True})


def finish(run, state, result):
    """Caller must have exited worker/inference/activity contexts first."""
    if state['phase'] != 'complete' or not state.get('plan_policy'):
        return result
    runner = activity.read(run / 'runner.json')
    if runner.get('status') == 'running':
        raise HornetError('Closeout refused: goal runner is still active')
    ready = mission.read(run / 'closeout-ready.json')
    if ready['checkpoint'] != mission.reference(run, run / 'recipe-checkpoint.json'):
        raise HornetError('Closeout checkpoint changed')
    mission.immutable(run / 'closeout.json', {
        'schema_version': 1, 'ready': mission.reference(run, run / 'closeout-ready.json'),
        'controller_leases_released': True, 'runner_idle': True,
        'scope': 'This goal controller released its leases; shared models and the VM remain available.'})
    return dict(result, closeout=mission.reference(run, run / 'closeout.json'))


def validate(run, state):
    if (run / 'closeout.json').exists():
        if state['phase'] != 'complete':
            raise HornetError('Closeout belongs to a completed goal only')
        value = mission.read(run / 'closeout.json')
        ready = mission.read(run / 'closeout-ready.json')
        if (value.get('ready') != mission.reference(run, run / 'closeout-ready.json')
                or value.get('controller_leases_released') is not True or value.get('runner_idle') is not True
                or ready['checkpoint'] != mission.reference(run, run / 'recipe-checkpoint.json')
                or ready['goal_completion'] != state['goal_completion']):
            raise HornetError('Closeout evidence changed')
