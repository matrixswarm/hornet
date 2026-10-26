"""Controller-owned, evidence-backed receipts for completed coding cells."""
import hashlib
import json
from pathlib import Path

from hornet.project import HornetError, write_text
from . import acceptance, cells, session, value_schema, mission


def read(path):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise HornetError('Return evidence exceeds 32 MiB')
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=cells.unique,
                      parse_constant=cells.invalid_constant)


def artifact(run, path):
    relative = path.relative_to(run).as_posix()
    if not path.resolve().is_relative_to(run.resolve()):
        raise HornetError('Return evidence escapes its recipe')
    return {'path': relative, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def child_path(run, cell, record):
    path = Path(record['run_dir'])
    if (path.parent != run / 'cells' / cell['id'] / 'jobs'
            or not path.resolve().is_relative_to(run.resolve())):
        raise HornetError('Cell job does not belong to this recipe')
    return path


def changed_files(before, after):
    if before['binding'] != after['binding']:
        raise HornetError('Cell worker identity changed')
    changed = []
    for path in sorted(before['files'].keys() | after['files'].keys()):
        old, new = before['files'].get(path), after['files'].get(path)
        if old != new:
            changed.append({'path': path, 'change': 'added' if old is None else 'deleted' if new is None else 'modified',
                            'before_sha256': old, 'after_sha256': new})
    return changed


def attempts(run, directory, through, conditions_required=False):
    history = []
    for folder in sorted(directory.glob('*'), key=lambda p: int(p.name) if p.name.isdecimal() else -1):
        if not folder.is_dir() or not folder.name.isdecimal() or not 1 <= int(folder.name) <= through:
            continue
        result_path, conditions_path = folder / 'result.json', folder / 'conditions.json'
        result = read(result_path) if result_path.is_file() else None
        if conditions_required and not conditions_path.is_file():
            raise HornetError('Recipe attempt conditions are missing')
        history.append({'attempt': int(folder.name),
            'outcome': 'unrecorded' if result is None else 'succeeded' if result.get('ok') is True else 'failed',
            'status': result.get('status') if result else None,
            'result': artifact(run, result_path) if result is not None else None,
            'conditions': artifact(run, conditions_path) if conditions_path.is_file() else None})
    return history


def compose(project, run, state, cell, recorded_attempt):
    if cell.get('kind') == 'research':
        from . import research
        return research.receipt(run, state, cell, recorded_attempt)
    from . import tree
    if tree.enabled(state) and tree.children(state, cell):
        return tree.parent_receipt(project, run, state, cell, recorded_attempt)
    record = state['cells'][cell['id']]
    job_run = child_path(run, cell, record)
    job = session.load(project, job_run)
    if state['settings'].get('registry_only') and not job['settings'].get('registry_only'):
        raise HornetError('Cell lost its required registry-only policy')
    if tree.enabled(state) and job.get('context_window') != tree.LIMITS['recent_exchanges']:
        raise HornetError('Cell working context policy changed')
    result = read(job_run / 'result.json')
    report = read(job_run / 'acceptance-results.json')
    suite = read(job_run / 'tests.json')
    if (job['phase'] != 'complete' or result.get('ok') is not True
            or result.get('job_id') != job['job_id'] or result != record['result']
            or job['acceptance'] != record['checks'] or job['acceptance_sha256'] != record['checks_sha256']
            or report.get('ok') is not True or report.get('plan_sha256') != record['checks_sha256']
            or [item['id'] for item in report['results']] != [item['id'] for item in record['checks']['checks']]
            or any(item.get('passed') is not True or item.get('completed') is not True for item in report['results'])
            or suite.get('ok') is not True or type(suite.get('exit_code')) is not int or suite['exit_code'] != 0):
        raise HornetError('Cell return requires a completed job, full suite and every frozen check')
    before, after = record['initial_snapshot'], job['snapshot']
    changes = changed_files(before, after)
    paths = [item['path'] for item in changes]
    if not paths or set(paths) != set(result['changed_files']) or not set(paths) <= set(cell['files']):
        raise HornetError('Cell return changed-file evidence does not match its scope')
    value = cells.extract(report, record['marker'], cell)
    spec = cells.result_spec(cell)
    if type(recorded_attempt) is not int or not 1 <= recorded_attempt <= state['attempts']:
        raise HornetError('Invalid recipe attempt for cell return')
    rounds = []
    for folder in sorted((job_run / 'verification').glob('*'), key=lambda p: int(p.name) if p.name.isdecimal() else -1):
        if not folder.name.isdecimal() or not folder.is_dir():
            continue
        files = {name: artifact(run, folder / name) for name in ('tests.json', 'acceptance-results.json', 'changes.json')
                 if (folder / name).is_file()}
        tests = read(folder / 'tests.json') if 'tests.json' in files else {}
        checks = read(folder / 'acceptance-results.json') if 'acceptance-results.json' in files else {}
        rounds.append({'round': int(folder.name), 'suite_passed': tests.get('ok') is True and tests.get('exit_code') == 0,
                       'checks_passed': checks.get('ok') is True, 'artifacts': files})
    dependencies = {key: state['cells'][key]['return_manifest'] for key in cell['depends_on']}
    receipt = {'schema_version': 1, 'kind': 'cell_return', 'ok': True, 'status': 'verified',
        'recipe_run': str(run), 'cell_id': cell['id'], 'plan_sha256': state['plan_sha256'],
        'contract': cell, 'contract_sha256': acceptance.digest(cell),
        'result': {'schema': spec, 'value': value,
                   'args': cells.resolve(json.loads(cell['result']['args_json']), state['values']),
                   'kwargs': cells.resolve(json.loads(cell['result']['kwargs_json']), state['values'])},
        'dependencies': dependencies, 'changed_files': changes,
        'source': {'before_sha256': acceptance.digest(before), 'after_sha256': acceptance.digest(after),
                   'worker_binding': after['binding']},
        'verification': {'checks_sha256': record['checks_sha256'], 'passed': len(report['results']),
            'total': len(report['results']), 'suite_exit_code': 0,
            'checks': [{'id': item['id'], 'passed': True, 'exit_code': item.get('exit_code'), 'paths': item['paths']}
                       for item in report['results']],
            'acceptance': artifact(run, job_run / 'acceptance-results.json'),
            'suite': artifact(run, job_run / 'tests.json')},
        'provenance': {'job_id': job['job_id'], 'completed_at': job['updated_at'],
            'recorded_in_recipe_attempt': recorded_attempt,
            'reconstructed_from_legacy': record.get('legacy_return', False),
            'recipe_conditions': artifact(run, run / 'attempts' / str(recorded_attempt) / 'conditions.json'),
            'prior_recipe_attempts': attempts(run, run / 'attempts', recorded_attempt - 1, True),
            'job_attempts': attempts(run, job_run / 'attempts', job['attempts']),
            'verification_rounds': rounds,
            'checkpoint': artifact(run, job_run / 'checkpoint.json'),
            'changes': artifact(run, job_run / 'changes.json'),
            'result': artifact(run, job_run / 'result.json'),
            'frozen_checks': artifact(run, run / 'cells' / cell['id'] / 'acceptance.json')}}
    if state['schema_version'] >= 3:
        receipt['goal'] = mission.binding(state, cell)
    if job.get('registry_binding'):
        receipt['provenance']['registry'] = {
            'binding': job['registry_binding'],
            'calls': [{'call_id': e['call_id'], 'tool': e['tool'],
                       'uri': e['result'].get('uri') or e['result'].get('asset', {}).get('uri'),
                       'ok': e['result']['ok'], 'status': e['result'].get('status'),
                       'receipt': artifact(run, job_run / e['result']['registry_receipt']['path']),
                       'readback': artifact(run, job_run / e['result']['readback_receipt']['path'])
                                   if e['result'].get('readback_receipt') else None,
                       'operation': artifact(run, job_run / 'operations' / (e['operation_id'] + '.json'))
                                    if e.get('operation_id') else None}
                      for e in job['history'] if 'registry_receipt' in e.get('result', {})]}
        if job['settings'].get('registry_only'):
            receipt['provenance']['registry']['required'] = True
    return receipt


def path_for(run, cell):
    return run / 'cells' / cell['id'] / 'return-manifest.json'


def checked(project, run, state, cell, indexed=True):
    """Rebuild the receipt from retained evidence before returning its value."""
    path = path_for(run, cell)
    index = artifact(run, path)
    actual = read(path)
    if indexed and index != state['cells'][cell['id']]['return_manifest']:
        raise HornetError('Cell return manifest changed: ' + cell['id'])
    expected = compose(project, run, state, cell, actual['provenance']['recorded_in_recipe_attempt'])
    if acceptance.encoded(actual) != acceptance.encoded(expected):
        raise HornetError('Cell return manifest does not match its verified evidence: ' + cell['id'])
    if indexed and (acceptance.encoded(actual['result']['value']) != acceptance.encoded(state['values'][cell['id']])
                    or acceptance.encoded(actual['result']['value']) != acceptance.encoded(state['cells'][cell['id']]['value'])):
        raise HornetError('Cell return value changed: ' + cell['id'])
    return actual


def publish(project, run, state, cell):
    path = path_for(run, cell)
    if path.exists():
        # A crash after atomic publication but before the recipe checkpoint
        # can adopt the exact existing receipt. Never overwrite its history.
        receipt = checked(project, run, state, cell, indexed=False)
    else:
        receipt = compose(project, run, state, cell, state['attempts'])
        write_text(path, acceptance.encoded(receipt))
    return artifact(run, path), receipt['result']['value']


def validate_completed(project, run, state):
    from . import tree
    if tree.enabled(state):
        return tree.validate_completed(project, run, state)
    previous, pending = state['initial_snapshot'], False
    for cell in (state['plan'] or {}).get('cells', []):
        ident = cell['id']
        if ident not in state['values']:
            pending = True
            continue
        if pending or state['cells'][ident]['initial_snapshot'] != previous:
            raise HornetError('Cell return source chain is inconsistent')
        checked(project, run, state, cell)
        previous = session.load(project, child_path(run, cell, state['cells'][ident]))['snapshot']


def migrate(project, run, state):
    """Reconstruct legacy receipts only from original completed-job evidence."""
    previous, pending = state['initial_snapshot'], False
    for cell in (state['plan'] or {}).get('cells', []):
        record = state['cells'].get(cell['id'])
        if cell['id'] not in state['values']:
            pending = True
            if record is not None:
                record.setdefault('initial_snapshot', previous)
            continue
        if pending:
            raise HornetError('Legacy completed cells are not an ordered prefix')
        record.update(initial_snapshot=previous, legacy_return=True)
        index, value = publish(project, run, state, cell)
        if acceptance.encoded(value) != acceptance.encoded(state['values'][cell['id']]):
            raise HornetError('Legacy cell value does not match its original verification')
        record['return_manifest'] = index
        previous = session.load(project, child_path(run, cell, record))['snapshot']
    state['schema_version'] = 2
    validate_completed(project, run, state)
