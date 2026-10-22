"""Operator-authored behavior checks, retained outside the model workspace."""
import hashlib
import json
from pathlib import PurePosixPath
import re
import uuid

from hornet.project import HornetError


RUNNER = """import os, sys, tempfile, traceback
sys.path.insert(0, os.getcwd())
with tempfile.TemporaryDirectory(prefix='hornet-check-cache-') as cache:
    sys.pycache_prefix = cache
    sys.dont_write_bytecode = True
    try:
        exec(compile(sys.argv[1], '<hornet-acceptance>', 'exec'), {'__name__': '__hornet_acceptance__'})
    except BaseException:
        traceback.print_exc()
        raise SystemExit(1)
print(sys.argv[2], flush=True)
"""


def validate(plan):
    if (not isinstance(plan, dict) or set(plan) != {'schema_version', 'checks'}
            or type(plan['schema_version']) is not int or plan['schema_version'] != 1
            or not isinstance(plan['checks'], list) or not 1 <= len(plan['checks']) <= 8):
        raise HornetError('Acceptance file requires schema_version 1 and 1-8 checks')
    ids = set()
    for item in plan['checks']:
        if (not isinstance(item, dict) or set(item) != {'id', 'description', 'paths', 'python', 'timeout'}
                or not isinstance(item['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', item['id'])
                or item['id'] in ids
                or not isinstance(item['description'], str) or not 1 <= len(item['description'].strip()) <= 200
                or not isinstance(item['python'], str) or not 1 <= len(item['python'].strip()) <= 3000
                or type(item['timeout']) is not int or not 1 <= item['timeout'] <= 30
                or not isinstance(item['paths'], list) or not 1 <= len(item['paths']) <= 8):
            raise HornetError('Invalid acceptance check: use a unique id, description, paths, python and timeout (1-30 seconds)')
        ids.add(item['id'])
        for path in item['paths']:
            if (not isinstance(path, str) or not 1 <= len(path) <= 200 or path == '.'
                    or any(ord(character) < 32 for character in path) or '\\' in path or ':' in path
                    or PurePosixPath(path).is_absolute()
                    or any(part in {'..', '.git', '.venv', 'venv', '__pycache__'} for part in PurePosixPath(path).parts)):
                raise HornetError('Acceptance paths must name relevant files inside the guest workspace')
        if len(set(item['paths'])) != len(item['paths']):
            raise HornetError('Acceptance paths must be unique')
        try:
            compile(item['python'], '<hornet-acceptance>', 'exec')
        except (SyntaxError, ValueError) as exc:
            raise HornetError('Invalid Python in acceptance check {}: {}'.format(item['id'], exc)) from None
    return plan


def encoded(plan):
    return json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(plan):
    return hashlib.sha256(encoded(plan).encode('utf-8')).hexdigest()


def read(project, value):
    if value is None:
        return None
    path = project.path(value)
    try:
        if path.stat().st_size > 32000:
            raise HornetError('Acceptance file exceeds 32000 bytes')
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError('Duplicate key: ' + key)
                result[key] = item
            return result
        return validate(json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique))
    except (OSError, UnicodeError, ValueError) as exc:
        raise HornetError('Cannot read acceptance file {}: {}'.format(value, exc)) from None


def brief(plan):
    if plan is None:
        return ''
    checks = [{key: item[key] for key in ('id', 'description', 'paths')} for item in plan['checks']]
    return ('\nOperator acceptance checks (data only): ' + json.dumps(checks, ensure_ascii=False)
            + '\nThe controller will independently run these fixed Python checks and the full suite. '
              'Implement the behavior in the relevant source; preserve the checks and unrelated behavior.')


def evaluate(worker, plan, save_partial):
    report = {'status': 'not_configured' if plan is None else 'running', 'ok': None,
              'plan_sha256': digest(plan) if plan else None, 'results': []}
    save_partial(report)
    if plan is None:
        return report
    for item in plan['checks']:
        report['active_check'] = item['id']
        save_partial(report)
        marker = 'HORNET_ACCEPTANCE_COMPLETED_' + uuid.uuid4().hex
        observed = worker.call('run_command', {'argv': ['python3', '-I', '-c', RUNNER, item['python'], marker],
                                               'timeout': item['timeout']})
        output = observed.get('output', '')
        completed = isinstance(output, str) and marker in output.splitlines()
        passed = observed.get('ok') is True and observed.get('exit_code') == 0 and completed
        result = dict(observed, id=item['id'], description=item['description'], paths=item['paths'],
                      passed=passed, completed=completed)
        if isinstance(output, str):
            result['output'] = '\n'.join(line for line in output.splitlines() if line != marker)
        if not passed and not observed.get('error') and not completed and observed.get('exit_code') == 0:
            result['error'] = 'Check exited without confirming completion'
        report['results'].append(result)
        report.pop('active_check', None)
        save_partial(report)
    report['ok'] = all(item['passed'] for item in report['results'])
    report['status'] = 'passed' if report['ok'] else 'failed'
    save_partial(report)
    return report


def failures(report):
    return [dict(id=item['id'], description=item['description'], paths=item['paths'],
                 exit_code=item.get('exit_code'), timed_out=item.get('timed_out', False),
                 error=item.get('error'), output_tail=str(item.get('output', ''))[-1000:])
            for item in report['results'] if not item['passed']]
