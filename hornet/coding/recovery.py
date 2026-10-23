"""Bounded cell handoffs. Failed attempts and immutable contracts are retained."""
from hornet.project import HornetError
from . import acceptance, mission

DEFAULT = {'max_handoffs': 1, 'agents': []}


class Stagnated(HornetError):
    status = 'stagnated'


def policy(value, agents=()):
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise HornetError('Recovery policy accepts max_handoffs and agents')
    result = dict(DEFAULT, **value)
    if (type(result['max_handoffs']) is not int or not 0 <= result['max_handoffs'] <= 2
            or not isinstance(result['agents'], list) or len(result['agents']) > 8
            or any(not isinstance(name, str) or name not in agents for name in result['agents'])
            or len(set(result['agents'])) != len(result['agents'])):
        raise HornetError('Use 0-2 recovery handoffs and unique configured hive agent labels')
    return result


def configure(args, settings):
    registry = settings.get('hive', {}).get('registry', {})
    value = dict(registry.get('recovery', {}))
    if getattr(args, 'max_handoffs', None) is not None:
        value['max_handoffs'] = args.max_handoffs
    if getattr(args, 'recovery_agents', None) is not None:
        value['agents'] = args.recovery_agents
    return policy(value, registry.get('agents', {}))


def initialize(run, state, value):
    mission.immutable(run / 'recovery-policy.json', value)
    state['recovery_policy'] = mission.reference(run, run / 'recovery-policy.json')


def rules(run, state):
    if mission.reference(run, run / 'recovery-policy.json') != state['recovery_policy']:
        raise HornetError('Recovery policy changed')
    return policy(mission.read(run / 'recovery-policy.json'),
                  state['settings'].get('hive', {}).get('registry', {}).get('agents', {}))


def observe(job, name, arguments, current):
    """Three identical operations in a six-operation window with unchanged source.

    This detects a narrow repetition pattern, not arbitrary lack of progress.
    New source or a new attempt resets the window; the full history survives.
    """
    if not job.get('detect_stagnation'):
        return False
    if current['files'] != job['snapshot']['files']:
        job.pop('stagnation_window', None)
        return False
    if name not in {'read_file', 'list_files', 'search', 'check_ticket', 'edit_file', 'write_file'}:
        return False
    # Read tokens are fresh handles, not a different operation.
    normalized = {key: value for key, value in arguments.items() if key != 'read_ref'}
    signature = acceptance.digest({'tool': name, 'arguments': normalized, 'files': current['files']})
    window = job.setdefault('stagnation_window', [])
    window.append(signature)
    del window[:-6]
    return window.count(signature) >= 3


def latest(run, record):
    refs = record.get('recoveries', [])
    return mission.read(run / refs[-1]['path']) if refs else None


def route(run, state, cell, profile):
    from hornet import hive
    event = latest(run, state['cells'].get(cell, {}))
    registry = state['settings'].get('hive', {}).get('registry', {})
    assigned = hive.assignments(run, registry) if registry else {}
    if (event and profile and profile['agent'] == event['routed_agent'] and event['to_agent']
            and event['assignment_sha256'] == acceptance.digest(assigned)):
        registry = state['settings']['hive']['registry']
        return dict(registry['agents'][event['to_agent']], agent=event['to_agent'])
    return profile


def prepare(run, state, cell, child_run, child, result, profile):
    from hornet import hive
    if not state.get('recovery_policy') or result.get('status') not in {
            'stagnated', 'verification_repeated_without_source_change'}:
        return None
    value = rules(run, state)
    record = state['cells'][cell['id']]
    refs = record.get('recoveries', [])
    previous = latest(run, record)
    # If interrupted between durable dispatch and child resume, consume the
    # same handoff once; never grant a new allowance for that interruption.
    if previous and previous['child_attempt'] == child['attempts']:
        return previous
    if (len(refs) >= value['max_handoffs'] or child['phase'] not in {'model', 'dispatch'}
            or child.get('in_flight_operation') or child.get('waiting_question')):
        return None
    remaining = state['settings']['max_turns'] - state['model_turns']
    tools_left = state['settings']['max_tools'] - state['role_tools'] - sum(state['cell_usage'].values())
    if remaining < 2 or tools_left < 1 or child['turns_used'] >= 30 or child['tools_used'] >= 80:
        return None
    selected = route(run, state, cell['id'], profile)
    old_agent = selected['agent'] if selected else None
    next_agent = next((name for name in value['agents'] if name != old_agent), old_agent)
    failure = child_run / 'attempts' / str(child['attempts']) / 'result.json'
    event = {'schema_version': 1, 'cell': cell['id'], 'number': len(refs) + 1,
        'child_attempt': child['attempts'], 'child_run': str(child_run),
        'from_agent': old_agent, 'to_agent': next_agent,
        'routed_agent': profile['agent'] if profile else None,
        'assignment_sha256': acceptance.digest(hive.assignments(run, state['settings']['hive']['registry'])
                                                if state['settings'].get('hive') else {}),
        'reason': result['status'], 'failure': mission.reference(run, failure),
        'policy': state['recovery_policy'], 'checks_sha256': record['checks_sha256'],
        'source_sha256': acceptance.digest(child['snapshot']),
        'turns_used': child['turns_used'], 'tools_used': child['tools_used'],
        'max_turns': min(30, child['turns_used'] + remaining, child['settings']['max_turns'] + state['limits']['cell_turns']),
        'max_tools': min(80, child['tools_used'] + tools_left, child['settings']['max_tools'] + state['limits']['cell_tools'])}
    path = run / 'cells' / cell['id'] / 'recoveries' / (str(len(refs) + 1) + '.json')
    mission.immutable(path, event)
    record.setdefault('recoveries', []).append(mission.reference(run, path))
    return event


def apply(job, event):
    """Called inside session.resume's lock after identity/source checks."""
    if (job['phase'] not in {'model', 'dispatch'} or job.get('in_flight_operation')
            or job.get('waiting_question') or event['child_attempt'] != job['attempts']
            or event['child_run'] != job['run_dir']
            or event['source_sha256'] != acceptance.digest(job['snapshot'])
            or event['checks_sha256'] != job['acceptance_sha256']):
        raise HornetError('Recovery no longer matches a safe confirmed cell boundary')
    job['history'].append({'handoff': event, 'undispatched_calls': job['pending']})
    job['pending'] = []
    job['phase'] = 'model'
    job.pop('stagnation_window', None)
    job.pop('repeated_edit', None)
    job['needs_resume_context'] = True


def validate(run, state):
    if not state.get('recovery_policy'):
        if (run / 'recovery-policy.json').exists():
            raise HornetError('Recovery policy lost its checkpoint binding')
        return
    value = rules(run, state)
    for cell, record in state['cells'].items():
        refs = record.get('recoveries', [])
        if not isinstance(refs, list) or len(refs) > value['max_handoffs']:
            raise HornetError('Cell recovery limit exceeded')
        for number, ref in enumerate(refs, 1):
            path = run / 'cells' / cell / 'recoveries' / (str(number) + '.json')
            if mission.reference(run, path) != ref:
                raise HornetError('Saved handoff changed')
            event = mission.read(path)
            if (event['cell'] != cell or event['number'] != number or event['policy'] != state['recovery_policy']
                    or event['checks_sha256'] != record['checks_sha256']
                    or mission.reference(run, run / event['failure']['path']) != event['failure']):
                raise HornetError('Cell handoff evidence changed')
