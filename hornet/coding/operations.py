"""Controller identities, validation and audit copies for durable worker calls."""
from hornet.project import HornetError, write_text
from .operation_worker import TOOLS, digest, encoded, validate


def supported(worker, tool, arguments):
    return (tool in TOOLS and getattr(worker, 'operation_protocol', None) == 1
            and callable(getattr(worker, 'operation', None))
            and (not hasattr(worker, 'operation_allowed') or worker.operation_allowed(tool, arguments)))


def request(job, tool, arguments):
    return {'version': 1, 'id': 'op-' + digest([job['job_id'], job['pending'][0]['id']])[:32],
            'tool': tool, 'arguments': arguments, 'before': job['snapshot']}


def validate_checkpoint(job, tool, arguments):
    active = job.get('in_flight_operation')
    if job.get('operation_protocol') not in (None, 1):
        raise ValueError('Unknown operation receipt protocol')
    if active is None:
        return
    validate(active)
    if (job.get('operation_protocol') != 1 or job['phase'] != 'tool_in_flight'
            or active != request(job, tool, arguments)):
        raise ValueError('Saved operation does not match its pending call')


def validate_history(run, job):
    import json
    for item in job['history']:
        if ('dispatched_tool' in item or 'operation_result' in item) and item.get('tool') != 'call_proxy_tool':
            raise ValueError('Only a registry commit may wrap a worker operation result')
        if 'operation_id' not in item:
            continue
        ident = item['operation_id']
        if ident != 'op-' + digest([job['job_id'], item['call_id']])[:32]:
            raise ValueError('Operation history identity changed')
        path = run / 'operations' / (ident + '.json')
        if path.is_symlink() or path.stat().st_size > 1500 * 1024:
            raise ValueError('Invalid saved operation evidence')
        receipt = json.loads(path.read_text(encoding='utf-8'))
        active = receipt['request']
        validate(active)
        if (receipt['version'] != 1 or receipt['status'] != 'completed'
                or active['id'] != ident or active['tool'] != item.get('dispatched_tool', item['tool'])
                or active['arguments'] != item.get('dispatched_arguments', item['arguments'])
                or receipt['result'] != item.get('operation_result', item['result']) or receipt['request_sha256'] != digest(active)
                or receipt['result_sha256'] != digest(receipt['result'])
                or receipt['after_sha256'] != digest(receipt['after'])
                or receipt['after']['binding'] != active['before']['binding']):
            raise ValueError('Saved operation evidence changed')


def invoke(run, worker, active, take_snapshot):
    if not supported(worker, active['tool'], active['arguments']):
        raise HornetError('Worker cannot recover this operation within its current cell scope')
    validate(active)
    response = worker.operation(active)
    if not isinstance(response, dict) or response.get('ok') is not True:
        error = response.get('error', 'invalid worker receipt') if isinstance(response, dict) else 'invalid worker receipt'
        raise HornetError('Operation recovery paused: ' + str(error))
    receipt = response.get('receipt')
    try:
        if (not isinstance(receipt, dict) or receipt['version'] != 1 or receipt['status'] != 'completed'
                or receipt['request'] != active or receipt['request_sha256'] != digest(active)
                or not isinstance(receipt['result'], dict) or type(receipt['result'].get('ok')) is not bool
                or receipt['result_sha256'] != digest(receipt['result'])
                or receipt['after_sha256'] != digest(receipt['after'])
                or receipt['after']['binding'] != active['before']['binding']):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise HornetError('Worker operation receipt does not match its frozen request and result') from None
    # Check live state again: a receipt is historical evidence, not permission
    # to accept unrelated edits made since it was recorded.
    current = take_snapshot(worker)
    if current != receipt['after']:
        raise HornetError('Workspace changed after the recorded operation; receipt was not adopted')
    target = run / 'operations' / (active['id'] + '.json')
    text = encoded(receipt)
    if target.exists():
        if target.read_text(encoding='utf-8') != text:
            raise HornetError('Saved operation evidence changed')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text(target, text)
    return receipt['result'], current
