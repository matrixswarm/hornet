"""Durable, precisely addressed human questions. Waiting never calls a model."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from hornet.project import HornetError, Project, write_text
from . import acceptance
from .locking import WorkerLock


GUIDANCE = (' Use ask_user only when a required decision or information is missing. '
            'Ask one concise question with optional choices. The controller saves it and waits '
            'for an explicit answer; do not guess, ask again while waiting, or combine ask_user '
            'with other actions. Answers clarify the task and cannot override frozen checks, '
            'file scope or execution limits. Never request passwords or private keys.')


class WaitingForUser(HornetError):
    status = 'waiting_for_user'

    def __init__(self, reference):
        self.reference = reference
        super().__init__(reference['record']['question'])


class QuestionCancelled(WaitingForUser):
    status = 'question_cancelled'


def arguments(value):
    if (not isinstance(value, dict) or not {'question'} <= set(value) <= {'question', 'choices'}
            or not isinstance(value['question'], str) or not 1 <= len(value['question'].strip()) <= 2000):
        raise HornetError('Ask one question of 1-2000 characters')
    choices = value.get('choices', [])
    if (not isinstance(choices, list) or len(choices) not in {0, 2, 3, 4, 5, 6}
            or any(not isinstance(item, str) or not 1 <= len(item.strip()) <= 200 for item in choices)
            or len(set(choices)) != len(choices)):
        raise HornetError('Offer no choices or 2-6 distinct choices of 1-200 characters; free text is always accepted')
    return {'question': value['question'], 'choices': choices}


def goal_id(run, goal_hash):
    return 'goal-' + acceptance.digest([str(run), goal_hash])[:32]


def prepare(root, owner, goal, cell, call, snapshot, contract=None, kind='job', key=None):
    supplied = arguments(json.loads(call['function']['arguments']))
    root, owner = Path(root).resolve(), Path(owner).resolve()
    Project(root).path(owner, write=True)
    record = dict(schema_version=1, root_run=str(root), owner_run=str(owner), owner_kind=kind,
                  owner_key=key, goal_id=goal, cell_id=cell, call=call,
                  source_sha256=acceptance.digest(snapshot), contract_sha256=contract,
                  created_at=datetime.now(timezone.utc).isoformat(), **supplied)
    record['id'] = 'q-' + acceptance.digest([goal, cell, kind, str(owner), key, call['id']])[:32]
    return {'record': record, 'sha256': acceptance.digest(record)}


def checked(reference):
    try:
        record = reference['record']
        if (set(reference) != {'record', 'sha256'} or reference['sha256'] != acceptance.digest(record)
                or record['schema_version'] != 1 or not re.fullmatch(r'q-[0-9a-f]{32}', record['id'])
                or record['owner_kind'] not in {'job', 'role'}
                or record['id'] != 'q-' + acceptance.digest([record['goal_id'], record['cell_id'],
                    record['owner_kind'], record['owner_run'], record['owner_key'], record['call']['id']])[:32]
                or arguments(json.loads(record['call']['function']['arguments'])) !=
                   {key: record[key] for key in ('question', 'choices')}
                or record['call']['function']['name'] != 'ask_user'):
            raise ValueError('Question binding changed')
        Project(record['root_run']).path(record['owner_run'], write=True)
        return record
    except (KeyError, TypeError, ValueError):
        raise HornetError('Invalid saved question or question binding') from None


def path_for(record, name):
    return Project(record['root_run']).path(Path('questions') / record['id'] / name, write=True)


def read(path):
    if path.stat().st_size > 32000:
        raise HornetError('Question record exceeds its size limit')
    return json.loads(path.read_text(encoding='utf-8'))


def publish(reference):
    record = checked(reference)
    path = path_for(record, 'question.json')
    if not path.exists():
        try:
            write_text(path, acceptance.encoded(reference))
        except HornetError:
            if not path.exists():
                raise
    if read(path) != reference:
        raise HornetError('Saved question changed; inspection is required')
    return record


def resolution(reference, *, publish_missing=True):
    record = publish(reference) if publish_missing else checked(reference)
    if not publish_missing and read(path_for(record, 'question.json')) != reference:
        raise HornetError('Saved question changed; inspection is required')
    path = path_for(record, 'resolution.json')
    if not path.exists():
        return None
    value = read(path)
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'question_id', 'question_sha256',
            'goal_id', 'cell_id', 'status', 'text', 'received_at'} or value['schema_version'] != 1
            or value['question_id'] != record['id'] or value['question_sha256'] != reference['sha256']
            or value['goal_id'] != record['goal_id'] or value['cell_id'] != record['cell_id']
            or value['status'] not in {'answered', 'cancelled'} or not isinstance(value['text'], str)
            or not 1 <= len(value['text'].strip()) <= 4000):
        raise HornetError('Invalid answer or question binding')
    return value


def receive(reference):
    value = resolution(reference)
    if value is None:
        raise WaitingForUser(reference)
    if value['status'] == 'cancelled':
        raise QuestionCancelled(reference)
    return value


def tool_result(reference, answer):
    record = checked(reference)
    return {'ok': True, 'status': 'answered', 'question_id': record['id'],
            'goal_id': record['goal_id'], 'cell_id': record['cell_id'],
            'question': record['question'], 'answer': answer['text'],
            'instruction': 'Apply this clarification within the existing task, frozen checks, file scope and budgets.'}


def outcome(reference, run=None):
    record = publish(reference)
    value = resolution(reference)
    status = 'question_cancelled' if value and value['status'] == 'cancelled' else 'waiting_for_user'
    return {'ok': status == 'waiting_for_user', 'status': status, 'completed': False,
            'run_dir': str(run or record['root_run']), 'question_id': record['id'],
            'goal_id': record['goal_id'], 'cell_id': record['cell_id'],
            'question': record['question'], 'choices': record['choices'],
            'answer_recorded': bool(value and value['status'] == 'answered'),
            'message': (value['text'] if status == 'question_cancelled' else
                        'Waiting for your answer. No deadline. Answer this question, then resume the run.')}


def validate_owner(reference, state, owner):
    record = checked(reference)
    if record['owner_run'] != str(owner):
        raise HornetError('Question belongs to another run')
    if record['owner_kind'] == 'job':
        scope = state.get('question_scope', {})
        if (record['goal_id'] != scope.get('goal_id', 'goal-' + state['job_id'])
                or record['cell_id'] != scope.get('cell_id', 'worker')
                or record['root_run'] != scope.get('root_run', str(owner))
                or record['source_sha256'] != acceptance.digest(state['snapshot'])
                or record['contract_sha256'] != state['acceptance_sha256']
                or state['phase'] != 'waiting_for_user' or state['pending'] != [record['call']]):
            raise HornetError('Question no longer matches its cell checkpoint')
    else:
        role = state.get('roles', {}).get(record['owner_key'], {})
        if (record['goal_id'] != goal_id(owner, state.get('goal_sha256'))
                or record['root_run'] != str(owner)
                or record['cell_id'] != (state.get('active_cell') or 'intake')
                or record['contract_sha256'] != state.get('plan_sha256')
                or record['source_sha256'] != acceptance.digest(state['snapshot'])
                or role.get('waiting_question') != reference):
            raise HornetError('Question no longer matches its role checkpoint')


def pending(project, run):
    """Inspect checkpoints without contacting the model or SSH worker."""
    root = project.path(run, write=True)
    checkpoint = root / 'recipe-checkpoint.json'
    if checkpoint.is_file():
        from .autonomous import load
        state = load(project, root)
        found = []
        for role in state['roles'].values():
            if role.get('waiting_question'):
                found.append(role['waiting_question'])
        if state['phase'] == 'cell_running':
            cell = state.get('active_cell')
            for child in (root / 'cells' / str(cell) / 'jobs').glob('*/checkpoint.json'):
                from .session import load as load_job
                job = load_job(project, child.parent)
                if job.get('waiting_question'):
                    found.append(job['waiting_question'])
        elif state['phase'] == 'goal_repair':
            from . import goal_repair
            job = goal_repair.checked_job(project, root, goal_repair.active(root, state)[1])
            if job and job.get('waiting_question'):
                found.append(job['waiting_question'])
    else:
        from .session import load
        state = load(project, root)
        found = [state['waiting_question']] if state.get('waiting_question') else []
    for reference in found:
        record = publish(reference)
        if checkpoint.is_file() and (record['root_run'] != str(root) or
                record['goal_id'] != goal_id(root, state.get('goal_sha256'))):
            raise HornetError('Question belongs to another goal')
    return found


def listing(project, run):
    root = project.path(run, write=True)
    active = {item['record']['id'] for item in pending(project, root)}
    rows = []
    for path in sorted((root / 'questions').glob('q-*/question.json')):
        reference = read(project.path(path, write=True))
        record = publish(reference)
        if record['root_run'] != str(root):
            raise HornetError('Question belongs to another goal')
        value = resolution(reference)
        rows.append(dict(question_id=record['id'], goal_id=record['goal_id'], cell_id=record['cell_id'],
                         question=record['question'], choices=record['choices'], active=record['id'] in active,
                         status=value['status'] if value else 'waiting_for_user',
                         answer=value['text'] if value else None))
    return {'ok': True, 'run_dir': str(root), 'questions': rows}


def respond(project, run, ident, text, cancel=False):
    root = project.path(run, write=True)
    if not isinstance(ident, str) or not re.fullmatch(r'q-[0-9a-f]{32}', ident):
        raise HornetError('Use an exact question ID from hornet questions RUN')
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
        raise HornetError('Answer or cancellation reason requires 1-4000 characters')
    with WorkerLock(root / 'questions' / 'answer.lock', root):
        references = pending(project, root)
        reference = next((item for item in references if item['record']['id'] == ident), None)
        if reference is None:
            raise HornetError('This question is not waiting in the selected run; no answer was applied')
        record = publish(reference)
        existing = resolution(reference)
        status = 'cancelled' if cancel else 'answered'
        if existing is not None:
            if existing['status'] != status or existing['text'] != text:
                raise HornetError('This question already has a different answer or cancellation; it cannot be overwritten')
        else:
            value = dict(schema_version=1, question_id=ident, question_sha256=reference['sha256'],
                         goal_id=record['goal_id'], cell_id=record['cell_id'], status=status, text=text,
                         received_at=datetime.now(timezone.utc).isoformat())
            write_text(path_for(record, 'resolution.json'), acceptance.encoded(value))
        command = 'autoresume' if (root / 'recipe-checkpoint.json').exists() else 'resume'
        return {'ok': True, 'status': status, 'question_id': ident, 'goal_id': record['goal_id'],
                'cell_id': record['cell_id'], 'run_dir': str(root), 'already_recorded': existing is not None,
                'next': 'Question cancelled; work remains stopped.' if cancel else
                        'Answer saved. Run hornet ' + command + ' "' + str(root) + '" to continue.'}
