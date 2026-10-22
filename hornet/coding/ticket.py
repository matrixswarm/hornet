"""Controller-owned acceptance tool with a source-bound JSON return contract."""
import hashlib
import json

from hornet.project import HornetError, write_text
from . import acceptance


def source_digest(value):
    """Include worker identity and every source file, rather than Git HEAD alone."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def check(worker, plan, expected, directory, artifact, snapshot, progress):
    before = snapshot(worker)
    if before != expected:
        raise HornetError('Ticket check refused: worker or source changed since the last confirmed step')
    directory.mkdir(parents=True, exist_ok=True)
    revision = source_digest(before)
    write_text(directory / 'source.json', json.dumps(before, indent=2), force=True)
    envelope = {'schema_version': 1, 'kind': 'ticket_check', 'ok': False, 'status': 'running',
                'source_sha256': revision, 'source_unchanged': False,
                'plan_sha256': acceptance.digest(plan) if plan else None,
                'passed': 0, 'total': len(plan['checks']) if plan else 0,
                'results': [], 'artifact': artifact}

    def save_partial(report):
        write_text(directory / 'acceptance-results.json', json.dumps(report, ensure_ascii=False, indent=2), force=True)
        progress('ticket_checks', active_check=report.get('active_check'),
                 checks_completed=len(report['results']), checks_passed=sum(item['passed'] for item in report['results']))

    # Persist uncertainty before running any operator code. A lost result is never replayed on resume.
    write_text(directory / 'result.json', json.dumps(envelope, indent=2), force=True)
    report = acceptance.evaluate(worker, plan, save_partial)
    after = snapshot(worker)
    unchanged = before == after
    envelope.update(ok=report['ok'] is True and unchanged,
                    status=report['status'] if unchanged else 'source_changed', source_unchanged=unchanged,
                    passed=sum(item['passed'] for item in report['results']))
    for item in report['results']:
        output = str(item.get('output', ''))
        error = item.get('error')
        envelope['results'].append({'id': item['id'], 'passed': item['passed'], 'paths': item['paths'],
            'exit_code': item.get('exit_code'), 'timed_out': bool(item.get('timed_out', False)),
            'error': str(error)[:600] if error is not None else None,
            'output_tail': output[-600:], 'output_truncated': len(output) > 600})
    write_text(directory / 'result.json', json.dumps(envelope, ensure_ascii=False, indent=2), force=True)
    if not unchanged:
        raise HornetError('Ticket checks changed source or worker identity; inspect the worker and saved results before continuing')
    return envelope, after
