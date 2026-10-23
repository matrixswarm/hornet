"""Durable root-plan approval, before the immutable execution plan is published."""
from pathlib import Path

from hornet.project import HornetError
from . import acceptance, mission
from .locking import WorkerLock

DEFAULT = {'mode': 'auto', 'approver': 'user', 'max_drafts': 3}


class WaitingForPlan(HornetError):
    status = 'waiting_for_plan_approval'


def policy(value, agents=()):
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise HornetError('Planning policy accepts mode, approver and max_drafts')
    result = dict(DEFAULT, **value)
    if (not isinstance(result['mode'], str) or result['mode'] not in {'auto', 'review'} or not isinstance(result['approver'], str)
            or result['approver'] != 'user' and result['approver'] not in agents
            or type(result['max_drafts']) is not int or not 1 <= result['max_drafts'] <= 5):
        raise HornetError('Use planning mode auto/review, approver user or a hive label, and max_drafts 1-5')
    return result


def configure(args, settings):
    registry = settings.get('hive', {}).get('registry', {})
    value = dict(registry.get('planning', {}))
    if getattr(args, 'plan_mode', None) is not None:
        value['mode'] = args.plan_mode
    if getattr(args, 'approver', None) is not None:
        value['approver'] = args.approver
        if getattr(args, 'plan_mode', None) is None:
            value['mode'] = 'review'
    return policy(value, registry.get('agents', {}))


def initialize(run, state, value):
    mission.immutable(run / 'plan-policy.json', value)
    state['plan_policy'] = mission.reference(run, run / 'plan-policy.json')
    state['planning'] = {'drafts': [], 'current': None, 'decisions': {}}


def read_policy(run, state):
    path = run / 'plan-policy.json'
    if mission.reference(run, path) != state['plan_policy']:
        raise HornetError('Planning policy changed')
    return policy(mission.read(path), state['settings'].get('hive', {}).get('registry', {}).get('agents', {}))


def draft(run, state):
    ref = state.get('planning', {}).get('current')
    if ref is None:
        return None
    path = run / ref['path']
    if mission.reference(run, path) != ref:
        raise HornetError('Plan draft changed')
    return mission.read(path)


def decision_path(run, proposal):
    return run / 'planning' / str(proposal['revision']) / 'decision.json'


def decision(run, state):
    proposal = draft(run, state)
    if proposal is None:
        return None
    path = decision_path(run, proposal)
    if not path.exists():
        if str(proposal['revision']) in state['planning']['decisions']:
            raise HornetError('Consumed plan decision is missing')
        return None
    bound = state['planning']['decisions'].get(str(proposal['revision']))
    if bound is not None and mission.reference(run, path) != bound:
        raise HornetError('Consumed plan decision changed')
    result = mission.read(path)
    if (not isinstance(result, dict) or set(result) != {'schema_version', 'draft_sha256', 'revision', 'actor', 'decision', 'feedback'}
            or result['schema_version'] != 1 or result['draft_sha256'] != acceptance.digest(proposal)
            or result['revision'] != proposal['revision'] or result['actor'] != proposal['approver']
            or result['decision'] not in {'approve', 'revise'} or not isinstance(result['feedback'], str)
            or len(result['feedback']) > 2000 or result['decision'] == 'revise' and not result['feedback'].strip()):
        raise HornetError('Plan decision does not match this revision and approver')
    return result


def consume(run, state):
    resolved = decision(run, state)
    if resolved is None:
        raise HornetError('Plan decision is still pending')
    proposal = draft(run, state)
    state['planning']['decisions'][str(proposal['revision'])] = mission.reference(run, decision_path(run, proposal))
    return resolved


def propose(run, state, choice):
    current = draft(run, state)
    if current is not None:
        if current['candidate'] != choice:
            raise HornetError('The pending plan cannot change before its review is resolved')
        return current
    rules = read_policy(run, state)
    number = len(state['planning']['drafts']) + 1
    if number > rules['max_drafts']:
        raise HornetError('Plan revision limit reached; all drafts and feedback remain saved')
    directory = run / 'planning' / str(number)
    role_path = directory / 'intake-role.json'
    mission.immutable(role_path, state['roles']['intake'])
    proposal = {'schema_version': 1, 'revision': number, 'goal_sha256': state['goal_sha256'],
                'source_sha256': acceptance.digest(state['snapshot']), 'candidate': choice,
                'approver': rules['approver'] if rules['mode'] == 'review' else 'controller',
                'role': mission.reference(run, role_path), 'policy': state['plan_policy']}
    path = directory / 'draft.json'
    mission.immutable(path, proposal)
    ref = mission.reference(run, path)
    state['planning']['drafts'].append(ref)
    state['planning']['current'] = ref
    return proposal


def record(run, state, choice, feedback='', actor='user'):
    proposal = draft(run, state)
    if proposal is None or proposal['approver'] != actor:
        raise HornetError('This plan requires its designated approver')
    value = {'schema_version': 1, 'draft_sha256': acceptance.digest(proposal),
             'revision': proposal['revision'], 'actor': actor, 'decision': choice, 'feedback': feedback}
    if (choice not in {'approve', 'revise'} or not isinstance(feedback, str) or len(feedback) > 2000
            or choice == 'revise' and not feedback.strip()):
        raise HornetError('A revision request requires feedback of 1-2000 characters')
    with WorkerLock(run / 'planning' / 'decision.lock', run):
        mission.immutable(decision_path(run, proposal), value)
    return decision(run, state)


def revise(run, state):
    resolved = consume(run, state)
    if resolved is None or resolved['decision'] != 'revise' or state['intake'] is not None:
        raise HornetError('Only an unapproved draft can be revised')
    state['planning']['current'] = None
    state['roles'].pop('intake', None)


def feedback(run, state):
    rows = []
    for ref in state['planning']['drafts']:
        proposal = mission.read(run / ref['path'])
        path = decision_path(run, proposal)
        if path.exists():
            value = mission.read(path)
            if value['decision'] == 'revise':
                rows.append({'revision': proposal['revision'], 'feedback': value['feedback']})
    return rows[-2:]


def clarifications(run, state):
    """Carry explicit user answers across draft revisions, without old prompts."""
    from . import questions
    found = {}
    for ref in state.get('planning', {}).get('drafts', []):
        proposal = mission.read(run / ref['path'])
        archived = mission.read(run / proposal['role']['path'])
        for item in archived.get('answered_questions', []):
            if questions.resolution(item['reference']) != item['answer']:
                raise HornetError('Saved planning clarification changed')
            found[item['reference']['record']['id']] = item
    return list(found.values())


def validate(run, state):
    if 'plan_policy' not in state:
        if (run / 'plan-policy.json').exists():
            raise HornetError('Saved planning policy lost its checkpoint binding')
        return
    rules = read_policy(run, state)
    value = state['planning']
    if (not isinstance(value, dict) or set(value) != {'drafts', 'current', 'decisions'} or not isinstance(value['drafts'], list)
            or not isinstance(value['decisions'], dict)
            or not set(value['decisions']) <= {str(n) for n in range(1, len(value['drafts']) + 1)}
            or len(value['drafts']) > rules['max_drafts']
            or value['current'] is not None and (not value['drafts'] or value['current'] != value['drafts'][-1])):
        raise HornetError('Invalid planning journal')
    for number, ref in enumerate(value['drafts'], 1):
        expected = run / 'planning' / str(number) / 'draft.json'
        if mission.reference(run, expected) != ref:
            raise HornetError('Historical plan draft changed')
        proposal = mission.read(expected)
        actor = rules['approver'] if rules['mode'] == 'review' else 'controller'
        if (proposal['revision'] != number or proposal['goal_sha256'] != state['goal_sha256']
                or proposal['source_sha256'] != acceptance.digest(state['initial_snapshot'])
                or proposal['policy'] != state['plan_policy'] or proposal['approver'] != actor
                or mission.reference(run, expected.parent / 'intake-role.json') != proposal['role']):
            raise HornetError('Plan draft bindings changed')
        archived = mission.read(expected.parent / 'intake-role.json')
        if archived.get('value') != proposal['candidate']:
            raise HornetError('Plan differs from the saved planning agent submission')
        temporary = dict(state, planning=dict(value, current=ref))
        resolved = decision(run, temporary)
        if ref != value['current'] and (resolved is None or resolved['decision'] != 'revise'
                                       or str(number) not in value['decisions']):
            raise HornetError('A superseded plan needs its retained revision decision')
    if state.get('intake') is not None:
        proposal, resolved = draft(run, state), decision(run, state)
        if (proposal is None or resolved is None or resolved['decision'] != 'approve'
                or str(proposal['revision']) not in value['decisions'] or proposal['candidate'] != state['intake']):
            raise HornetError('Execution requires approval of this exact plan revision')
    clarifications(run, state)


def outcome(run, state):
    proposal = draft(run, state)
    return {'ok': True, 'status': 'waiting_for_plan_approval', 'completed': False, 'run_dir': str(run),
            'revision': proposal['revision'], 'approver': proposal['approver'],
            'draft': str(run / state['planning']['current']['path']),
            'plan_sha256': acceptance.digest(proposal['candidate']['plan']),
            'message': 'Plan saved. Approve this revision or request changes; waiting has no timeout.'}


def pending(run, state):
    proposal = draft(run, state)
    return proposal is not None and proposal['approver'] == 'user' and decision(run, state) is None


def command(project, args):
    from . import autonomous
    run = project.path(args.run, write=True)
    state = autonomous.load(project, run)
    proposal = draft(run, state)
    if proposal is None:
        raise HornetError('This goal has no current plan draft')
    if args.plan_action == 'show':
        return {'ok': True, 'run_dir': str(run), 'draft': proposal, 'decision': decision(run, state)}
    if state['phase'] != 'intake' or state['intake'] is not None or args.revision != proposal['revision']:
        raise HornetError('Select the current unapproved revision; this decision is stale or execution has begun')
    resolved = record(run, state, 'approve' if args.plan_action == 'approve' else 'revise', getattr(args, 'feedback', '') or '')
    return {'ok': True, 'run_dir': str(run), 'decision': resolved, 'message': 'Decision saved. Use autoresume to continue.'}
