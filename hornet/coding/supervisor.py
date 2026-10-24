"""Deterministic, opt-in supervision of durable goals. Scans use no inference.

Every automatic activation is charged before dispatch. Unfinished receipts,
unknown worker operations and operator decisions require explicit resolution.
The existing controller retains all execution authority and frozen budgets.
"""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace

from hornet import hive, hive_status
from hornet.project import HornetError, write_text
from . import acceptance, autonomous, goal_repair, mission, planning, questions, session
from .locking import WorkerLock, process_alive


def read(project, path, limit=1024 * 1024):
    path = project.path(path, write=True)
    if not path.is_file() or path.stat().st_size > limit:
        raise HornetError('Missing or oversized supervisor evidence: ' + str(path))
    return json.loads(path.read_text(encoding='utf-8'))


def controls(project, run, state):
    """Preview control ordering without consuming messages or changing a pause."""
    seen = set(state.get('control_seen', []))
    queued = []
    paths = list((run / 'control/inbox').glob('*.json'))
    if len(paths) > 4096:
        raise HornetError('Control inbox exceeds supervisor scan limit')
    for path in sorted(paths, key=lambda p: (p.stat().st_mtime_ns, p.name)):
        if path.stem in seen:
            continue
        value = read(project, path, 12000)
        if (set(value) != {'id', 'action', 'message'} or value['id'] != path.stem
                or value['action'] not in {'pause', 'continue', 'stop', 'message'}
                or value['action'] == 'message' and (not isinstance(value['message'], str)
                    or not 1 <= len(value['message'].strip()) <= 2000)):
            raise HornetError('Invalid pending control envelope')
        queued.append(value)
    legacy = project.path(run / 'commander_input.txt', write=True)
    if legacy.exists():
        before = legacy.stat()
        if before.st_size > 12000:
            raise HornetError('Legacy control input exceeds size limit')
        text = legacy.read_text(encoding='utf-8-sig').strip()
        after = legacy.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise HornetError('Control input changed during inspection')
        ident = hashlib.sha256((str(before.st_mtime_ns) + ':' + text).encode('utf-8')).hexdigest()[:32]
        if text and ident not in seen:
            # The controller imports this at its next boundary. Do not guess
            # its ordering relative to already queued controls.
            return 'pending_legacy_control', queued + [{'id': ident, 'text': text}]
    paused, stopped = state.get('paused', False), state.get('stop_requested', False)
    for value in queued:
        if value['action'] == 'stop':
            stopped = True
        elif value['action'] in {'pause', 'continue'}:
            paused = value['action'] == 'pause'
    return 'stopped_by_operator' if stopped else 'paused' if paused else None, queued


def child_state(project, run, state):
    if state['phase'] == 'goal_repair':
        return goal_repair.checked_job(project, run, goal_repair.active(run, state)[1])
    if state['phase'] == 'cell_running':
        ident = state['active_cell']
        path = hive_status.child_run(project, run, ident, state['cells'][ident])
        return session.load(project, path) if path is not None else None
    return None


def worker_lease(project, run, state):
    binding = state['snapshot']['binding'].get('connection')
    if binding is None:
        paths = [project.path(state['settings']['worker_config'] + '.run.lock', write=True)]
    else:
        if not isinstance(binding, str) or not re.fullmatch('[0-9a-f]{64}', binding):
            raise HornetError('Invalid worker connection binding')
        paths = [project.path('coding/worker-locks/' + binding + '.lock', write=True)]
        if state['settings'].get('hive'):
            paths.append(hive.claim_path('worker:' + binding))
    for path in paths:
        if not path.exists():
            continue
        if path.stat().st_size > 16384:
            raise HornetError('Worker lease metadata exceeds size limit')
        owner = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(owner, dict) or process_alive(owner.get('pid')):
            return 'active_worker_lease'
        if owner.get('run_dir') != str(run):
            return 'stale_worker_lease_other_run'
    return None


def boundary(project, run, *, lease_held=False):
    """Read and validate the exact boundary; eligibility never comes from prose."""
    run = project.path(run, write=True)
    state = autonomous.load(project, run)
    child = child_state(project, run, state)
    control, queued = controls(project, run, state)
    proposal = planning.draft(run, state)
    decision = planning.decision(run, state) if proposal is not None else None
    refs = [role['waiting_question'] for role in state['roles'].values() if role.get('waiting_question')]
    if child and child.get('waiting_question'):
        refs.append(child['waiting_question'])
    answers = [questions.resolution(ref, publish_missing=False) for ref in refs]
    runner_path = project.path(run / 'runner.json', write=True)
    runner = read(project, runner_path, 16384) if runner_path.exists() else {}
    assignment_path = project.path(run / 'hive-assignments.json', write=True)
    assignment = read(project, assignment_path) if assignment_path.exists() else None
    fingerprint = acceptance.digest({'state': state, 'child': child, 'controls': queued,
                                     'decision': decision, 'answers': answers, 'assignment': assignment})
    lease = None if lease_held else worker_lease(project, run, state)
    reason = None
    if runner and (runner.get('run_dir') != str(run) or runner.get('status') not in {'running', 'idle'}):
        reason = 'invalid_runner'
    elif runner.get('status') == 'running' and process_alive(runner.get('pid')):
        reason = 'active_process'
    elif lease:
        reason = lease
    elif control:
        reason = control
    elif state.get('last_outcome', {}).get('status') in {'cancelled', 'stopped_by_operator', 'question_cancelled'}:
        reason = 'operator_resolution_required'
    elif (state.get('final_checks_in_flight') or any(c.get('parent_in_flight') for c in state['cells'].values())
          or child and (child.get('in_flight_operation') or child['phase'] in {'tool_in_flight', 'verification_in_flight'})):
        reason = 'unknown_operation_outcome'
    elif any(answer is None for answer in answers):
        reason = 'waiting_for_user'
    elif any(answer['status'] == 'cancelled' for answer in answers):
        reason = 'question_cancelled'
    elif proposal is not None and proposal['approver'] == 'user' and decision is None:
        reason = 'waiting_for_plan_approval'
    elif state['phase'] == 'complete':
        reason = 'completed' if (run / 'closeout.json').exists() or not state.get('plan_policy') else 'closeout_pending'
    elif (state['model_turns'] >= state['settings']['max_turns']
          or state['role_tools'] + sum(state['cell_usage'].values()) >= state['settings']['max_tools']
          or state['elapsed_seconds'] >= 1800
          or child and (child['turns_used'] >= child['settings']['max_turns']
                        or child['tools_used'] >= child['settings']['max_tools'])):
        reason = 'budget_exhausted'
    elif answers:
        reason = 'answer_ready'
    elif state['phase'] == 'intake' and decision is not None:
        reason = 'plan_decision_ready'
    elif any(item['action'] == 'continue' for item in queued):
        reason = 'continue_ready'
    elif state.get('last_outcome', {}).get('status') == 'waiting_for_agent':
        reason = 'agent_wait'
    else:
        records = state.get('request_records', [])
        last = records[-1] if records else {}
        if (last.get('attempt') == state['attempts'] and last.get('status') in {'in_flight', 'response_unavailable'}
                and (child is None or child['phase'] == 'model_request')):
            reason = 'model_response_lost'
    eligible = reason in {'answer_ready', 'plan_decision_ready', 'continue_ready', 'agent_wait',
                          'model_response_lost', 'closeout_pending'}
    return {'run_dir': str(run), 'goal_id': questions.goal_id(run, state['goal_sha256']),
            'phase': state['phase'], 'cell': state.get('active_cell'), 'condition': reason or 'inspection_required',
            'eligible': eligible, 'fingerprint': fingerprint}


def policy(project, run, *, recover_enrollment=False):
    path = project.path(run / 'supervisor/policy.json', write=True)
    if not path.exists():
        return None, False
    value = read(project, path, 16384)
    if (set(value) != {'schema_version', 'run_dir', 'goal_id', 'max_activations', 'cooldown'}
            or value['schema_version'] != 1 or value['run_dir'] != str(run)
            or type(value['max_activations']) is not int or not 1 <= value['max_activations'] <= 10
            or type(value['cooldown']) is not int or not 30 <= value['cooldown'] <= 3600):
        raise HornetError('Invalid supervisor policy')
    if recover_enrollment and not (run / 'supervisor/enabled.json').exists():
        return value, False
    enabled = read(project, run / 'supervisor/enabled.json', 16384)
    if (set(enabled) != {'enabled', 'policy_sha256'} or type(enabled['enabled']) is not bool
            or enabled['policy_sha256'] != acceptance.digest(value)):
        raise HornetError('Supervisor policy changed or lost its enrollment binding')
    return value, enabled['enabled']


def history(project, run, rules):
    base = project.path(run / 'supervisor/attempts', write=True)
    folders = sorted(base.iterdir(), key=lambda p: int(p.name) if p.name.isdecimal() else -1) if base.exists() else []
    if len(folders) > rules['max_activations']:
        raise HornetError('Supervisor activation history exceeds its frozen limit')
    result = []
    for number, folder in enumerate(folders, 1):
        if folder.name != str(number):
            raise HornetError('Supervisor activation history has a gap or unexpected entry')
        item = read(project, folder / 'decision.json', 16384)
        if (set(item) != {'number', 'run_dir', 'goal_id', 'fingerprint', 'condition', 'policy_sha256', 'started_at'}
                or item['number'] != number or item['run_dir'] != str(run) or item['goal_id'] != rules['goal_id']
                or item['policy_sha256'] != acceptance.digest(rules)
                or type(item['started_at']) not in {float, int} or not 0 <= item['started_at'] < 1e12):
            raise HornetError('Supervisor activation receipt changed')
        path = project.path(folder / 'outcome.json', write=True)
        outcome = read(project, path, 16384) if path.exists() else None
        if outcome is not None and (outcome.get('decision_sha256') != acceptance.digest(item)
                                   or not isinstance(outcome.get('status'), str)):
            raise HornetError('Supervisor outcome lost its decision binding')
        result.append((item, outcome))
    return result


def inspect(project, run, now):
    row = boundary(project, run)
    rules, enabled = policy(project, run)
    row.update(enrolled=rules is not None, enabled=enabled, activations=0)
    if rules is None:
        row.update(eligible=False, reason='not_enrolled')
        return row
    if rules['goal_id'] != row['goal_id']:
        raise HornetError('Supervisor policy belongs to another goal')
    attempts = history(project, run, rules)
    row.update(activations=len(attempts), max_activations=rules['max_activations'])
    reason = row['condition']
    if not enabled:
        reason = 'disabled'
    elif any(outcome is None for _, outcome in attempts):
        reason = 'supervisor_outcome_unknown'
    elif len(attempts) >= rules['max_activations']:
        reason = 'activation_limit'
    elif attempts and attempts[-1][0]['fingerprint'] == row['fingerprint']:
        reason = 'unchanged_since_activation'
    elif attempts and now < attempts[-1][0]['started_at'] + rules['cooldown']:
        reason = 'cooldown'
        row['retry_after'] = attempts[-1][0]['started_at'] + rules['cooldown']
    row['reason'] = reason
    row['eligible'] = row['eligible'] and reason == row['condition']
    return row


def configure(project, args):
    run = project.path(args.run, write=True)
    row = boundary(project, run) if not args.disable else None
    rules, enabled = policy(project, run, recover_enrollment=args.enable)
    if row and rules and rules['goal_id'] != row['goal_id']:
        raise HornetError('Supervisor policy belongs to another goal')
    directory = project.path(run / 'supervisor', write=True)
    if args.enable:
        if rules is None:
            rules = {'schema_version': 1, 'run_dir': str(run), 'goal_id': row['goal_id'],
                     'max_activations': args.max_activations, 'cooldown': args.cooldown}
            mission.immutable(directory / 'policy.json', rules)
        elif rules['max_activations'] != args.max_activations or rules['cooldown'] != args.cooldown:
            raise HornetError('Enrollment limits are frozen; re-enabling cannot reset or increase them')
    elif rules is None:
        raise HornetError('This goal is not enrolled')
    if args.acknowledge is not None:
        if row['condition'] in {'active_process', 'active_worker_lease', 'invalid_runner'}:
            raise HornetError('Cannot acknowledge while a goal process is active')
        attempts = history(project, run, rules)
        if not 1 <= args.acknowledge <= len(attempts):
            raise HornetError('Select an existing supervisor attempt')
        decision, outcome = attempts[args.acknowledge - 1]
        if outcome is not None:
            raise HornetError('This supervisor attempt already has an outcome')
        mission.immutable(directory / 'attempts' / str(args.acknowledge) / 'outcome.json',
                          {'status': 'acknowledged_by_operator', 'decision_sha256': acceptance.digest(decision)})
    else:
        write_text(directory / 'enabled.json', acceptance.encoded(
            {'enabled': bool(args.enable), 'policy_sha256': acceptance.digest(rules)}), force=True)


def tick(project, args, *, resume=None, clock=time.time):
    """At most one synchronous activation; concurrent ticks cannot dispatch twice."""
    dispatch = args.dispatch
    changing = args.enable or args.disable or args.acknowledge is not None
    directory = project.path('coding/supervisor', write=True)
    with ExitStack() as stack:
        if dispatch or changing:
            try:
                stack.enter_context(WorkerLock(directory / 'dispatch.lock', directory))
            except HornetError as exc:
                return {'ok': True, 'status': 'supervisor_busy', 'message': str(exc), 'goals': []}
        if changing:
            configure(project, args)
        runs, warnings = hive_status.discover(project, args.run_dir, args.run)
        rows = []
        for run in runs:
            if not (run / 'recipe-checkpoint.json').is_file():
                continue
            try:
                rows.append(inspect(project, run, clock()))
            except (HornetError, OSError, ValueError, KeyError, TypeError) as exc:
                rows.append({'run_dir': str(run), 'eligible': False, 'reason': 'invalid_evidence', 'error': str(exc)})
        result = {'ok': True, 'status': 'inspected', 'goals': rows, 'warnings': warnings}
        ready = [row for row in rows if row['eligible']]
        if not dispatch or not ready:
            return result
        # Prefer goals with fewer activations, then stable path order.
        selected = min(ready, key=lambda r: (r['activations'], r['run_dir']))
        run = Path(selected['run_dir'])
        rules, _ = policy(project, run)
        number = selected['activations'] + 1
        decision = {key: selected[key] for key in ('run_dir', 'goal_id', 'fingerprint', 'condition')}
        decision.update(number=number, policy_sha256=acceptance.digest(rules), started_at=clock())
        folder = project.path(run / 'supervisor/attempts' / str(number), write=True)
        mission.immutable(folder / 'decision.json', decision)

        def guard():
            current = boundary(project, run, lease_held=True)
            current_rules, enabled = policy(project, run)
            if (not enabled or current_rules != rules or not current['eligible']
                    or current['fingerprint'] != selected['fingerprint']):
                raise HornetError('Supervisor boundary changed before dispatch; inspect the new state')

        try:
            outcome = (resume or autonomous.resume)(project, SimpleNamespace(run=str(run)), before_resume=guard)
            saved = {'status': outcome['status'], 'ok': outcome['ok']}
        except KeyboardInterrupt:
            saved = {'status': 'interrupted_by_operator', 'ok': False}
            mission.immutable(folder / 'outcome.json', dict(saved, decision_sha256=acceptance.digest(decision)))
            raise
        except (HornetError, OSError, ValueError, KeyError, TypeError) as exc:
            saved = {'status': 'activation_refused', 'ok': False, 'error': str(exc)[:2000]}
        mission.immutable(folder / 'outcome.json', dict(saved, decision_sha256=acceptance.digest(decision)))
        result.update(status='activation_finished', activation=dict(saved, run_dir=str(run), number=number,
                      receipt=str(folder / 'outcome.json')))
        return result


def command(project, args):
    if (not 30 <= args.interval <= 3600 or not 30 <= args.cooldown <= 3600
            or not 1 <= args.max_activations <= 10):
        raise HornetError('Use interval/cooldown 30-3600 seconds and max-activations 1-10')
    if (args.enable or args.disable or args.acknowledge is not None) and not args.run:
        raise HornetError('Select one exact --run for enable, disable or acknowledge')
    if not args.watch:
        return tick(project, args)
    previous = None
    try:
        while True:
            value = tick(project, args)
            encoded = acceptance.encoded(value)
            if encoded != previous:
                print(encoded, flush=True)
                previous = encoded
            args.enable = args.disable = False
            args.acknowledge = None
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return {'ok': True, 'status': 'supervisor_stopped', 'message': 'Saved goals and receipts remain available.'}
