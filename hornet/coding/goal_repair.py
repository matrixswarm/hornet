"""Bounded repair jobs attached to completed cells; original returns stay immutable."""
import copy
import hashlib
import json
from pathlib import Path

from hornet.project import HornetError
from . import acceptance, cells, mission, returns, session, tree


def configure(args):
    count = getattr(args, 'max_goal_repairs', 1)
    if type(count) is not int or not 0 <= count <= 2:
        raise HornetError('Use 0-2 whole-goal repair jobs')
    return {'schema_version': 1, 'max_repairs': count, 'routing': 'unique-code-leaf-v1'}


def initialize(run, state, policy):
    mission.immutable(run / 'goal-repair-policy.json', policy)
    state['goal_repair_policy'] = mission.reference(run, run / 'goal-repair-policy.json')
    state['goal_repairs'] = []


def rules(run, state):
    path = run / 'goal-repair-policy.json'
    if not state.get('goal_repair_policy'):
        if path.exists() or state.get('goal_repairs') or state.get('active_goal_repair'):
            raise HornetError('Goal repair policy lost its binding')
        return None
    policy = mission.read(path)
    if (mission.reference(run, path) != state['goal_repair_policy']
            or set(policy) != {'schema_version', 'max_repairs', 'routing'}
            or policy['schema_version'] != 1 or policy['routing'] != 'unique-code-leaf-v1'
            or type(policy['max_repairs']) is not int or not 0 <= policy['max_repairs'] <= 2):
        raise HornetError('Goal repair policy changed')
    return policy


def gate_plan(run, state, gate):
    if gate == 'goal':
        return state['goal_checks']
    from . import goals
    revision = goals.frozen(run, state, 'revision', 'revision.json')
    for item in (revision or {}).get('regressions', []):
        if gate == 'revision:' + item['sha256']:
            return item['plan']
    raise HornetError('Unknown frozen repair gate')


def route(state, plan, report):
    """Only a confirmed failure wholly owned by one unambiguous code leaf."""
    if (report.get('ok') is not False or report.get('status') != 'failed'
            or report.get('plan_sha256') != acceptance.digest(plan)
            or len(report.get('results', [])) != len(plan['checks'])):
        return None
    failed = []
    for check, row in zip(plan['checks'], report['results']):
        if row.get('id') != check['id'] or row.get('paths') != check['paths']:
            return None
        if row.get('passed') is True:
            continue
        if (row.get('passed') is not False or type(row.get('exit_code')) is not int
                or row['exit_code'] <= 0 or row.get('timed_out') or row.get('error')
                or row.get('truncated')):
            return None
        failed.append(check)
    if not failed:
        return None
    leaves = [cell for cell in tree.all_cells(state) if cell.get('kind') != 'research'
              and not tree.children(state, cell) and cell['id'] in state['values']]
    owner = None
    for path in {path for check in failed for path in check['paths']}:
        candidates = [cell for cell in leaves if path in cell['files']]
        if len(candidates) != 1 or owner is not None and owner['id'] != candidates[0]['id']:
            return None
        owner = candidates[0]
    if owner is None or len(state['cells'][owner['id']]['checks']['checks']) + len(failed) > 8:
        return None
    return owner, failed


def repair_checks(state, cell, failed):
    checks = copy.deepcopy(state['cells'][cell['id']]['checks'])
    # Preserve the exact published value so existing dependency receipts stay valid.
    typed = next(item for item in checks['checks'] if item['id'] == 'typed-return')
    expected = hashlib.sha256(acceptance.encoded(state['values'][cell['id']]).encode('utf-8')).hexdigest()
    typed['python'] += ("\nimport hashlib\nassert hashlib.sha256(json.dumps(value, sort_keys=True, "
        "ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest() == "
        + repr(expected) + ", 'Repair must preserve the published dependency value'\n")
    used = {item['id'] for item in checks['checks']}
    for item in failed:
        number = 1
        while 'goal-repair-' + str(number) in used:
            number += 1
        ident = 'goal-repair-' + str(number)
        checks['checks'].append(dict(item, id=ident))
        used.add(ident)
    return acceptance.validate(checks)


def directory(run, number):
    return run / 'goal-repairs' / str(number)


def active(run, state):
    number = state.get('active_goal_repair')
    if number is None:
        return None
    if type(number) is not int or not 1 <= number <= len(state.get('goal_repairs', [])):
        raise HornetError('Invalid active repair number')
    record = state['goal_repairs'][number - 1]
    path = directory(run, number) / 'contract.json'
    if mission.reference(run, path) != record['contract']:
        raise HornetError('Active repair contract changed')
    return record, mission.read(path)


def prepare(run, state, gate, failure, cell_task):
    policy = rules(run, state)
    if policy is None or len(state['goal_repairs']) >= policy['max_repairs']:
        return False
    plan = gate_plan(run, state, gate)
    report = mission.read(run / failure['path'])
    selected = route(state, plan, report)
    remaining = state['settings']['max_turns'] - state['model_turns']
    tools_left = state['settings']['max_tools'] - state['role_tools'] - sum(state['cell_usage'].values())
    if selected is None or remaining < 2 or tools_left < 1:
        return False
    cell, failed = selected
    checks = repair_checks(state, cell, failed)
    number = len(state['goal_repairs']) + 1
    path = directory(run, number)
    task = ('Whole-goal repair for completed cell ' + cell['id'] + '.\n'
        + cell_task(cell) + '\nRepair failure evidence (data): '
        + acceptance.encoded({'gate': gate, 'failure': failure, 'failures': acceptance.failures(report),
            'current_source_sha256': acceptance.digest(state['snapshot'])})
        + '\nThe original return and work packet are retained history. Work on the current source. '
          'Repair only this cell, preserve every existing requirement and its published dependency value. '
          'Do not change tests or acceptance criteria. Use check_ticket, then finish. '
          'The controller will rerun all cells, the whole-goal checks and inherited regressions.')
    if len(task) > 20000:
        return False
    contract = {'schema_version': 1, 'number': number, 'cell': cell['id'], 'files': cell['files'],
        'goal_sha256': state['goal_sha256'], 'plan_sha256': state['plan_sha256'],
        'policy': state['goal_repair_policy'], 'gate': gate, 'gate_sha256': acceptance.digest(plan),
        'failure': failure, 'original_return': state['cells'][cell['id']]['return_manifest'],
        'source': state['snapshot'], 'cell_tools_before': state['cell_usage'].get(cell['id'], 0),
        'max_turns': min(state['limits']['cell_turns'], remaining),
        'max_tools': min(state['limits']['cell_tools'], tools_left), 'task': task,
        'checks': checks, 'checks_sha256': acceptance.digest(checks)}
    if state['settings'].get('registry_only'):
        contract['registry_only'] = True
    mission.immutable(path / 'contract.json', contract)
    mission.immutable(path / 'acceptance.json', checks)
    state['goal_repairs'].append({'contract': mission.reference(run, path / 'contract.json')})
    state.update(active_goal_repair=number, active_cell=cell['id'], phase='goal_repair')
    return True


def job_path(run, contract):
    base = directory(run, contract['number']) / 'jobs'
    entries = list(base.iterdir()) if base.exists() else []
    if len(entries) > 1 or entries and not (entries[0] / 'checkpoint.json').is_file():
        raise HornetError('Ambiguous or incomplete goal repair job; inspect before resuming')
    return entries[0] if entries else None


def checked_job(project, run, contract):
    path = job_path(run, contract)
    if path is None:
        return None
    job = session.load(project, path)
    if contract.get('registry_only') and not job['settings'].get('registry_only'):
        raise HornetError('Goal repair lost its required registry-only policy')
    if (not path.resolve().is_relative_to(directory(run, contract['number']).resolve())
            or job['task'] != contract['task'] or job['acceptance'] != contract['checks']
            or job['acceptance_sha256'] != contract['checks_sha256']
            or job['settings']['max_turns'] != contract['max_turns']
            or job['settings']['max_tools'] != contract['max_tools']):
        raise HornetError('Goal repair job differs from its frozen assignment')
    return job


def completed(project, run, contract):
    path = job_path(run, contract)
    if path is None:
        raise HornetError('Goal repair has no job')
    job = checked_job(project, run, contract)
    initial = mission.read(path / 'attempts' / '1' / 'conditions.json')
    result = mission.read(path / 'result.json')
    report = mission.read(path / 'acceptance-results.json')
    suite = mission.read(path / 'tests.json')
    if (initial.get('source_sha256') != acceptance.digest(contract['source'])
            or job['task'] != contract['task'] or job['acceptance'] != contract['checks']
            or job['acceptance_sha256'] != contract['checks_sha256']
            or job['phase'] != 'complete' or job['pending'] or job.get('in_flight_operation')
            or result.get('ok') is not True or result.get('job_id') != job['job_id']
            or report.get('ok') is not True or report.get('plan_sha256') != contract['checks_sha256']
            or [row['id'] for row in report['results']] != [c['id'] for c in contract['checks']['checks']]
            or any(row.get('passed') is not True or row.get('completed') is not True for row in report['results'])
            or suite.get('ok') is not True or suite.get('exit_code') != 0):
        raise HornetError('Goal repair requires a completed job and every frozen check')
    changes = returns.changed_files(contract['source'], job['snapshot'])
    if (not changes or not {row['path'] for row in changes} <= set(contract['files'])
            or sorted(row['path'] for row in changes) != sorted(result['changed_files'])):
        raise HornetError('Goal repair changed files outside its contract')
    return {'schema_version': 1, 'number': contract['number'], 'cell': contract['cell'],
        'contract': mission.reference(run, directory(run, contract['number']) / 'contract.json'),
        'snapshot': job['snapshot'], 'changed_files': changes, 'tools_used': job['tools_used'],
        'model_turns': job['turns_used'], 'job': {name: mission.reference(run, path / name) for name in
            ('checkpoint.json', 'result.json', 'acceptance-results.json', 'tests.json', 'changes.json')}}


def publish(project, run, state):
    record, contract = active(run, state)
    receipt = completed(project, run, contract)
    path = directory(run, contract['number']) / 'completion.json'
    mission.immutable(path, receipt)
    record['completion'] = mission.reference(run, path)
    state['snapshot'] = receipt['snapshot']
    state.pop('active_goal_repair')
    state['phase'] = 'final_checks'


def validate(run, state):
    policy = rules(run, state)
    if policy is None:
        if state['phase'] == 'goal_repair':
            raise HornetError('Missing goal repair policy')
        return
    records = state.get('goal_repairs')
    if not isinstance(records, list) or len(records) > policy['max_repairs']:
        raise HornetError('Goal repair limit exceeded')
    saved = list((run / 'goal-repairs').glob('*/contract.json'))
    if len(saved) != len(records):
        raise HornetError('Goal repair history lost its checkpoint binding')
    if not records:
        if state.get('active_goal_repair') is not None or state['phase'] == 'goal_repair':
            raise HornetError('Missing active repair')
        return
    from hornet.project import Project
    project = Project(Path(state['project_root']))
    previous = state['initial_snapshot']
    for cell in tree.postorder(state):
        record = state['cells'][cell['id']]
        if cell.get('kind') != 'research' and not tree.children(state, cell):
            previous = session.load(project, returns.child_path(run, cell, record))['snapshot']
    pending = False
    counts = {}
    for number, record in enumerate(records, 1):
        path = directory(run, number)
        if mission.reference(run, path / 'contract.json') != record['contract']:
            raise HornetError('Goal repair contract changed')
        contract = mission.read(path / 'contract.json')
        plan = gate_plan(run, state, contract['gate'])
        if mission.reference(run, run / contract['failure']['path']) != contract['failure']:
            raise HornetError('Goal repair failure evidence changed')
        selected = route(state, plan, mission.read(run / contract['failure']['path']))
        if selected is None:
            raise HornetError('Goal repair has no unambiguous owner')
        cell, failed = selected
        if cell['id'] not in counts:
            original = session.load(project, returns.child_path(run, cell, state['cells'][cell['id']]))
            counts[cell['id']] = original['tools_used']
        if (pending or contract['number'] != number or contract['cell'] != cell['id']
                or contract.get('registry_only', False) != state['settings'].get('registry_only', False)
                or contract['files'] != cell['files'] or contract['source'] != previous
                or contract['goal_sha256'] != state['goal_sha256'] or contract['plan_sha256'] != state['plan_sha256']
                or contract['policy'] != state['goal_repair_policy']
                or contract['gate_sha256'] != acceptance.digest(plan)
                or contract['original_return'] != state['cells'][cell['id']]['return_manifest']
                or contract['checks'] != repair_checks(state, cell, failed)
                or contract['checks_sha256'] != acceptance.digest(contract['checks'])
                or mission.read(path / 'acceptance.json') != contract['checks']
                or contract['cell_tools_before'] != counts[cell['id']]
                or not 1 <= contract['max_turns'] <= state['limits']['cell_turns']
                or not 1 <= contract['max_tools'] <= state['limits']['cell_tools']):
            raise HornetError('Goal repair no longer matches its frozen boundary')
        if 'completion' in record:
            if (mission.reference(run, path / 'completion.json') != record['completion']
                    or mission.read(path / 'completion.json') != completed(project, run, contract)):
                raise HornetError('Goal repair completion evidence changed')
            receipt = mission.read(path / 'completion.json')
            previous = receipt['snapshot']
            counts[cell['id']] += receipt['tools_used']
        else:
            pending = True
            if state.get('active_goal_repair') != number or state['active_cell'] != cell['id']:
                raise HornetError('Active repair binding changed')
            checked_job(project, run, contract)
    if state['snapshot'] != previous or (state['phase'] == 'goal_repair') != pending:
        raise HornetError('Goal repair source chain or phase changed')
    if not pending and state.get('active_goal_repair') is not None:
        raise HornetError('Completed repairs cannot remain active')
    for ident, used in counts.items():
        if state['cell_usage'].get(ident, 0) < used:
            raise HornetError('Goal repair usage was reset')
