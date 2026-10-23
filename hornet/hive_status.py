"""Bounded, read-only navigation over saved goal and cell observations."""
from collections import deque
from datetime import datetime, timezone
import os
from pathlib import Path

from . import hive
from .coding import activity, mission, questions, recovery
from .coding.locking import process_alive
from .project import HornetError, is_link

SKIP = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'site-packages',
        'attempts', 'verification', 'roles', 'cells', 'claims', 'dist', 'build'}
STOPPED = {'paused', 'waiting_for_user', 'question_cancelled', 'waiting_for_agent', 'waiting_for_plan_approval',
           'stopped', 'stopped_by_operator', 'cancelled', 'completed', 'failed'}


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (AttributeError, ValueError, TypeError, OverflowError, OSError):
        return None


def elapsed(value, now=None):
    then = timestamp(value)
    return None if then is None else max(0, int((now if now is not None else datetime.now(timezone.utc).timestamp()) - then))


def discover(project, directory, exact=None):
    if exact:
        return [project.path(exact, write=True)], []
    base = project.path(directory, write=True)
    queue, runs, warnings, visited = deque([(base, 0)]), [], [], 0
    while queue and visited < 4096 and len(runs) < 256:
        path, depth = queue.popleft()
        visited += 1
        if is_link(path) or not path.is_dir():
            continue
        if (path / 'recipe-checkpoint.json').exists() or (path / 'checkpoint.json').exists():
            runs.append(path)
            continue  # Never list an autonomous goal's child jobs as separate goals.
        if depth >= 8:
            warnings.append('Directory depth limit: ' + project.display(path))
            continue
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.name in SKIP or entry.name.endswith(('-env', '-install')):
                        continue
                    child = Path(entry.path)
                    if not is_link(child) and entry.is_dir(follow_symlinks=False):
                        queue.append((child, depth + 1))
                    if len(queue) >= 4096:
                        warnings.append('Directory scan limit reached; select --run or a narrower --run-dir')
                        break
        except OSError as exc:
            warnings.append('{}: {}'.format(project.display(path), exc))
    if queue:
        warnings.append('Run scan limit reached; select --run or a narrower --run-dir')
    return sorted(runs, reverse=True), warnings


def read(path, limit=32 * 1024 * 1024):
    return activity.read(path, limit)


def runner(run, state):
    owner = read(run / 'runner.json', 16384)
    progress = read(run / 'progress.json', 65536)
    live = (owner.get('status') == 'running' and owner.get('run_dir') == str(run)
            and type(owner.get('pid')) is int and owner['pid'] > 0 and process_alive(owner['pid']))
    # An outcome from an earlier attempt must not hide a currently resumed run.
    recent = (timestamp(progress.get('observed_at')) or 0) >= (timestamp(owner.get('started_at')) or 0)
    stage = progress.get('stage') if recent else None
    if state['phase'] == 'complete':
        if state.get('plan_policy') and not (run / 'closeout.json').exists():
            return False, 'closeout_pending', progress
        return False, 'completed', progress
    if live:
        return stage not in STOPPED, stage or state['phase'], progress
    if stage in STOPPED:
        return False, stage, progress
    outcome = state.get('last_outcome', {})
    return False, outcome.get('status') or ('interrupted' if owner else 'saved'), progress


def flattened(state):
    result, seen = [], set()
    def visit(cell, parent, depth):
        if (not isinstance(cell, dict) or depth > 8 or len(result) >= 128
                or not isinstance(cell.get('id'), str) or cell['id'] in seen):
            raise HornetError('Invalid or oversized cell tree')
        hive.label(cell['id'])
        if (not isinstance(cell.get('files'), list) or not all(isinstance(p, str) for p in cell['files'])
                or not isinstance(cell.get('depends_on'), list)
                or not all(isinstance(p, str) for p in cell['depends_on'])):
            raise HornetError('Invalid cell file/dependency inventory')
        seen.add(cell['id'])
        result.append((cell, parent, depth))
        for child in state.get('routes', {}).get(cell['id'], {}).get('plan', {}).get('cells', []):
            visit(child, cell['id'], depth + 1)
    for cell in (state.get('plan') or {}).get('cells', []):
        visit(cell, None, 1)
    return result


def last_action(run, cell):
    data = read(run / 'activity.json', 128 * 1024)
    value = data.get('cells', {}).get(cell or '@goal')
    return value if isinstance(value, dict) and timestamp(value.get('observed_at')) is not None else None


def child_run(project, run, cell, record):
    if record.get('run_dir'):
        child = project.path(record['run_dir'], write=True)
        try:
            child.relative_to(run / 'cells' / cell / 'jobs')
        except ValueError:
            raise HornetError('Child job points outside its cell') from None
        return child
    base = project.path(run / 'cells' / cell / 'jobs', write=True)
    if not base.exists():
        return None
    found = []
    with os.scandir(base) as entries:
        for entry in entries:
            if len(found) >= 2:
                break
            if not is_link(entry.path) and entry.is_dir(follow_symlinks=False) and (Path(entry.path) / 'checkpoint.json').exists():
                found.append(Path(entry.path))
    if len(found) > 1:
        raise HornetError('Ambiguous child jobs; no automatic selection')
    return found[0] if found else None


def checks(run, ident, record, child):
    total = len(record.get('checks', {}).get('checks', []))
    report = read(run / 'final-checks' / (ident + '.json'))
    if not report and child:
        report = read(child / 'acceptance-results.json')
    if not report or report.get('plan_sha256') != record.get('checks_sha256'):
        return {'passed': None, 'total': total, 'observed': False}
    results = report.get('results', [])
    return {'passed': sum(item.get('passed') is True and item.get('completed') is True for item in results),
            'total': total, 'observed': True}


def ticket_checks(run, state):
    plan = state.get('acceptance') or {}
    report = read(run / 'acceptance-results.json')
    observed = bool(report) and report.get('plan_sha256') == state.get('acceptance_sha256')
    return {'passed': sum(item.get('passed') is True and item.get('completed') is True for item in report.get('results', [])) if observed else None,
            'total': len(plan.get('checks', [])), 'observed': observed}


def goal(project, run):
    auto = (run / 'recipe-checkpoint.json').exists()
    state = read(run / ('recipe-checkpoint.json' if auto else 'checkpoint.json'))
    if state.get('run_dir') != str(run) or not isinstance(state.get('settings'), dict) or not isinstance(state.get('phase'), str):
        raise HornetError('Checkpoint has no matching run identity/settings/phase')
    live, status, progress = runner(run, state)
    settings = state['settings']
    revision = None
    if auto and state.get('revision'):
        path = run / 'revision.json'
        if mission.reference(run, path) != state['revision']:
            raise HornetError('Saved goal revision binding changed')
        record = read(path, 1024 * 1024)
        origin = record.get('source_cell')
        revision = {'parent_run': record['parent_run'],
                    'source_cell_id': origin.get('id') if isinstance(origin, dict) else None}
    binding = settings.get('hive')
    if binding:
        hive.validate_binding(binding)
    ident = questions.goal_id(run, state.get('goal_sha256')) if auto else state.get('question_scope', {}).get('goal_id', 'goal-' + state['job_id'])
    goal_row = {'id': ident, 'run_dir': str(run), 'run': project.display(run),
        'goal': state.get('goal', state.get('task', '')), 'phase': state['phase'], 'status': status,
        'live': live, 'model_requests': state.get('model_turns', state.get('turns_used', 0)),
        'model_limit': settings.get('max_turns'), 'tools_used': state.get('role_tools', 0) + sum(state.get('cell_usage', {}).values()) if auto else state.get('tools_used', 0),
        'tool_limit': settings.get('max_tools'), 'attempts': state.get('attempts', 0),
        'stage': progress.get('stage'), 'error': state.get('active_error'),
        'revision': revision,
        'cells_verified': len(state.get('values', {})) if auto else int(state['phase'] == 'complete')}
    nodes = flattened(state) if auto else []
    goal_row['cells_total'] = len(nodes) if auto else 1
    rows = []
    # The orchestration cell makes intake and final verification visible too.
    controller = {'id': '@goal' if auto else 'worker', 'task': goal_row['goal'],
        'files': [] if auto else sorted(state.get('snapshot', {}).get('files', {})),
        'depends_on': [], 'kind': 'orchestrator' if auto else 'code'}
    for cell, parent, depth in [(controller, None, 0)] + nodes:
        key = cell['id']
        root_cell = depth == 0
        record = state.get('cells', {}).get(key, {}) if auto else state
        child = child_run(project, run, key, record) if auto and not root_cell else None
        job = read(child / 'checkpoint.json') if child else (state if not auto else {})
        action = last_action(run, None if root_cell else key)
        if child:
            other = last_action(child, key) or last_action(child, None)
            if other and (not action or timestamp(other['observed_at']) > timestamp(action['observed_at'])):
                action = other
        active = live and (state.get('active_cell') == key if not root_cell else not auto or state['phase'] in {'intake', 'plan', 'goal_checks', 'final_checks'})
        verified = key in state.get('values', {}) if not root_cell else state['phase'] == 'complete'
        active = active and not verified and (root_cell or state['phase'] in {'checker', 'cell_running'})
        dependencies = cell.get('depends_on', [])
        cell_status = ('verified' if verified else status if active or root_cell else
                       'failed' if record.get('active_error') else 'blocked' if any(d not in state.get('values', {}) for d in dependencies) else 'pending')
        if not active and auto and state.get('active_cell') == key and not verified:
            cell_status = status
        profile = hive.select(settings, 'planner' if root_cell and auto else 'researcher' if cell.get('kind') == 'research' else 'worker', None if root_cell else key, run)
        if auto and not root_cell and cell.get('kind', 'code') == 'code':
            profile = recovery.route(run, state, key, profile)
        row = {'id': key, 'goal_id': ident, 'run_dir': str(run), 'run': goal_row['run'],
            'task': cell.get('task', ''), 'kind': cell.get('kind', 'code'), 'parent': parent,
            'depth': depth, 'depends_on': dependencies, 'files': cell['files'],
            'status': cell_status, 'active': active, 'verified': verified,
            'current': auto and not root_cell and state.get('active_cell') == key,
            'assigned_agent': profile['agent'] if profile else None,
            'assigned_model': profile['model'] if profile else settings.get('model'),
            'actual_agent': action.get('agent') if action else None,
            'actual_model': action.get('model') if action else None,
            'last_action': action, 'phase': job.get('phase') or state['phase'],
            'tools_used': job.get('tools_used', state.get('cell_usage', {}).get(key, 0)),
            'model_requests': job.get('turns_used'), 'attempts': job.get('attempts', 0),
            'error': record.get('active_error'),
            'checks': (checks(run, key, record, child) if not root_cell else {'passed': None, 'total': 0, 'observed': False}) if auto else ticket_checks(run, state)}
        rows.append(row)
    return goal_row, rows


def snapshot(project, directory='coding', run=None):
    paths, warnings = discover(project, directory, run)
    goals, cells = [], []
    for path in paths:
        try:
            item, rows = goal(project, path)
            goals.append(item)
            cells.extend(rows)
        except (HornetError, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
            warnings.append('{}: {}'.format(project.display(path), exc))
    return {'ok': not warnings, 'root': str(project.root), 'goals': goals, 'cells': cells,
            'active_cells': sum(item['active'] for item in cells), 'warnings': warnings,
            'scope': 'Read-only checkpoint observations; completion evidence is validated by the execution/resume commands.'}
