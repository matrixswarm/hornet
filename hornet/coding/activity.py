"""Small observational records; never replay or authorize work from telemetry."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from hornet.project import HornetError, is_link, write_text


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path, limit=128 * 1024):
    """Reject linked, oversized and malformed telemetry, including partial writes."""
    path = Path(path)
    if any(is_link(part) for part in (path, *path.parents)):
        raise HornetError('Linked activity record refused')
    if not path.exists():
        return {}
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise HornetError('Activity record exceeds its size limit')
    value = json.loads(data.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise HornetError('Activity record must be an object')
    return value


def record(run, event, cell=None, agent=None, model=None, tool=None):
    """One writer per run. Keep latest actions per cell, without prompts or output."""
    path = Path(run) / 'activity.json'
    data = read(path)
    latest = data.setdefault('cells', {})
    if not isinstance(latest, dict) or len(latest) > 128:
        raise HornetError('Invalid activity cell inventory')
    item = {'observed_at': now(), 'event': event, 'cell': cell,
            'agent': agent, 'model': model, 'tool': tool}
    latest[cell or '@goal'] = item
    data.update(schema_version=1, latest=item)
    write_text(path, json.dumps(data, ensure_ascii=True, indent=2), force=True)


@contextmanager
def running(run):
    """A dead PID is interrupted work, even if its old checkpoint says running."""
    path = Path(run) / 'runner.json'
    read(path)
    owner = {'schema_version': 1, 'run_dir': str(run), 'pid': os.getpid(),
             'token': uuid.uuid4().hex, 'started_at': now(), 'status': 'running'}
    write_text(path, json.dumps(owner, indent=2), force=True)
    try:
        yield
    finally:
        owner.update(status='idle', ended_at=now())
        write_text(path, json.dumps(owner, indent=2), force=True)
