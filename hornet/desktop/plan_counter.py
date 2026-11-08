"""Counter a pending plan without approval or source effects."""

from .mission_checkpoint import read_json, write_json
from .mission_lease import mission_lease


def feedback_path(ws, record):
    from .mission_resume import _mission_id
    return ws.project(record['project']) / 'missions' / _mission_id(record['id']) / 'counter-feedback.json'


def save_feedback(ws, record, feedback):
    from .mission_resume import _mission_id
    if not isinstance(feedback, str) or not 1 <= len(feedback.strip()) <= 16000:
        raise ValueError('Enter counter feedback (at most 16,000 characters).')
    directory = ws.project(record['project']) / 'missions' / _mission_id(record['id'])
    with mission_lease(directory):
        from .working_revision import is_abandoned
        if is_abandoned(ws, record['project'], record['id']):
            raise ValueError('This mission was explicitly closed; start a fresh mission instead.')
        saved = read_json(directory.with_suffix('.json'))
        if saved != record or saved.get('state') != 'plan_review' or saved.get('plan_approval'):
            raise ValueError('The saved plan changed or was approved. Review it again before countering.')
        write_json(feedback_path(ws, record), {'feedback': feedback.strip()})
    return feedback.strip()


def counter_mission_plan(ws, record, feedback, progress=None):
    """Keep the old review and feedback durable; a new plan always needs approval."""
    from .mission_resume import prepare_mission
    from .mission_task_context import reject_legacy_counter
    reject_legacy_counter(record)
    feedback = save_feedback(ws, record, feedback)
    previous = record.get('counter_context', {})
    context = {'feedback': list(previous.get('feedback', [])) + [feedback],
               'previous_plan': record['plan']}
    identity = record['identity']
    return prepare_mission(ws, record['project'], record['base_revision'], record['mission'],
        identity['endpoint'], identity['model'], record.get('check_command'), progress,
        record.get('diagnostic'), require_plan_confirmation=True, require_goal_decomposition=True,
        counter_plan_of=record['id'], counter_context=context)
