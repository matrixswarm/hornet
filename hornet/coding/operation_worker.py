"""Portable operation journal; sent as fixed code to the non-root SSH worker.

Receipts recover lost replies, not arbitrary side effects of interrupted code.
The journal shares the worker account and is not a security boundary.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

TOOLS = {'write_file', 'edit_file', 'run_python', 'run_command'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def validate(request):
    if (not isinstance(request, dict) or set(request) != {'version', 'id', 'tool', 'arguments', 'before'}
            or type(request['version']) is not int or request['version'] != 1
            or not isinstance(request['id'], str) or not re.fullmatch(r'op-[0-9a-f]{32}', request['id'])
            or request['tool'] not in TOOLS or not isinstance(request['arguments'], dict)
            or not isinstance(request['before'], dict) or set(request['before']) != {'binding', 'files'}):
        raise ValueError('Invalid durable operation request')
    before = request['before']
    if (not isinstance(before['binding'], dict) or not before['binding']
            or not isinstance(before['files'], dict)
            or any(not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v)
                   for k, v in before['files'].items())):
        raise ValueError('Invalid operation source manifest')
    digest(request)


def no_links(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Linked operation journal refused')


def sync_directory(path):
    if os.name == 'posix':
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def store(path, value):
    no_links(path)
    fd, temporary = tempfile.mkstemp(prefix='.receipt-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def make_directory(directory):
    missing = []
    parent = directory
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for item in reversed(missing):
        no_links(item)
        item.mkdir(mode=0o700, exist_ok=True)
        # Flush every new ancestor entry, including the top journal directory.
        # Flushing only the leaf could lose its parent name after power loss.
        sync_directory(item.parent)


def lock(stream):
    if os.name == 'posix':
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)


def unlock(stream):
    if os.name == 'posix':
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    else:
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def execute(request, directory, snapshot, dispatch, fault=None):
    """Dispatch at most once after a durable started marker; never infer success."""
    validate(request)
    directory = Path(directory)
    no_links(directory)
    make_directory(directory)
    sync_directory(directory.parent)
    path = directory / (request['id'] + '.json')
    guard = directory / (request['id'] + '.lock')
    no_links(path)
    no_links(guard)
    # The persistent lock inode is never removed. A late original request and
    # its reconnect therefore contend on the same OS lock, even after a crash.
    fd = os.open(str(guard), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        try:
            lock(stream)
        except OSError:
            return {'ok': False, 'status': 'busy', 'error': 'Operation is still running; retry resume later'}
        try:
            if path.exists():
                if path.stat().st_size > 1500 * 1024:
                    raise ValueError('Oversized operation receipt')
                receipt = json.loads(path.read_text(encoding='utf-8'))
                if (receipt.get('request') != request or receipt.get('request_sha256') != digest(request)
                        or receipt.get('version') != 1):
                    raise ValueError('Operation ID is already bound to a different request')
                if receipt.get('status') == 'started':
                    return {'ok': False, 'status': 'uncertain',
                            'error': 'Operation started without a completion receipt; inspection is required, not replay'}
                if (receipt.get('status') != 'completed' or not isinstance(receipt.get('result'), dict)
                        or type(receipt['result'].get('ok')) is not bool
                        or receipt.get('result_sha256') != digest(receipt['result'])
                        or receipt.get('after_sha256') != digest(receipt.get('after'))):
                    raise ValueError('Invalid operation completion receipt')
                if snapshot() != receipt['after']:
                    raise ValueError('Workspace changed after the recorded operation')
                return {'ok': True, 'receipt': receipt, 'recovered': True}
            if snapshot() != request['before']:
                raise ValueError('Workspace changed before the operation; nothing was dispatched')
            receipt = {'version': 1, 'request': request, 'request_sha256': digest(request), 'status': 'started'}
            store(path, receipt)
            if fault:
                fault('started')
            result = dispatch(request['tool'], request['arguments'])
            if fault:
                fault('executed')
            if not isinstance(result, dict) or type(result.get('ok')) is not bool:
                raise ValueError('Invalid operation result; started receipt retained')
            after = snapshot()
            receipt.update(status='completed', result=result, result_sha256=digest(result),
                           after=after, after_sha256=digest(after))
            if len(encoded(receipt).encode('utf-8')) > 1500 * 1024:
                raise ValueError('Oversized completion; started receipt retained')
            store(path, receipt)
            if fault:
                fault('completed')
            return {'ok': True, 'receipt': receipt, 'recovered': False}
        finally:
            unlock(stream)


def dispatch(worker, worker_error, tool, arguments):
    try:
        if tool == 'edit_file':
            fields = {'path', 'old_text', 'new_text', 'expected_sha256'}
            if (set(arguments) != fields or any(not isinstance(arguments[k], str) for k in fields)
                    or not arguments['old_text'] or not re.fullmatch(r'[0-9a-f]{64}', arguments['expected_sha256'])):
                raise ValueError('edit_file requires a path, nonempty old_text, new_text and current 64-character SHA-256')
            if len(json.dumps(arguments, ensure_ascii=False).encode('utf-8')) > 3500:
                raise ValueError('Use a smaller source block; edit_file arguments exceed 3500 bytes')
            if arguments['old_text'] == arguments['new_text']:
                return {'ok': False, 'unchanged': True, 'error': 'Old and new blocks are identical; no edit ran'}
            text = worker.path(arguments['path']).read_text(encoding='utf-8')
            count = text.count(arguments['old_text'])
            if count != 1:
                raise ValueError('old_text matched %s places; read the current file and include its enclosing function to make the block unique' % count)
            result = worker.write_file(arguments['path'], text.replace(arguments['old_text'], arguments['new_text'], 1), arguments['expected_sha256'])
        elif tool == 'run_python':
            if set(arguments) != {'code'} or not isinstance(arguments['code'], str) or not 1 <= len(arguments['code']) <= 4000:
                raise ValueError('run_python requires 1-4000 characters of Python code')
            result = worker.run_command(['python3', '-c', arguments['code']])
        else:
            result = worker.call(tool, arguments)
        if tool in {'write_file', 'edit_file'} and result.get('ok'):
            target = worker.path(arguments['path'])
            with target.open('rb' if os.name == 'posix' else 'r+b') as stream:
                os.fsync(stream.fileno())
            # Flush newly created ancestor entries as well as the file rename.
            for parent in (target.parent, *target.parent.parents):
                sync_directory(parent)
                if parent == worker.root:
                    break
        return result
    except (worker_error, OSError, ValueError, TypeError, UnicodeError) as exc:
        return {'ok': False, 'error': str(exc)}


def main(request):
    import runpy
    import subprocess
    import uuid
    workspace = request.pop('workspace')
    if workspace is not None and not re.fullmatch(r'g-[0-9a-f]{32}', workspace):
        raise ValueError('Invalid workspace ID')
    base = Path('/home/hornet/workspace')
    root = base / 'goals' / workspace if workspace else base / 'hornet_cli'
    no_links(root)
    api = runpy.run_path('/opt/hornet-worker/worker.py')
    worker = api['Worker'](root)
    def snapshot():
        files = {}
        for directory, dirs, names in os.walk(worker.root, followlinks=False):
            dirs[:] = [n for n in dirs if n not in api['BLOCKED']]
            if any((Path(directory) / n).is_symlink() for n in dirs + names):
                raise ValueError('Linked workspace entries refused')
        for item in worker.files():
            if len(files) >= 200 or item.stat().st_size > 256 * 1024:
                raise ValueError('Source exceeds snapshot limits')
            files[worker.rel(item)] = hashlib.sha256(item.read_bytes()).hexdigest()
        return {'binding': {'root': str(worker.root), 'uid': os.getuid(),
            'machine_id': Path('/etc/machine-id').read_text().strip(),
            'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
            'connection': request['before']['binding'].get('connection')}, 'files': files}
    directory = base / '.hornet-operations' / digest(str(root))
    try:
        result = execute(request, directory, snapshot, lambda name, args: dispatch(worker, api['WorkerError'], name, args))
    except (api['WorkerError'], OSError, ValueError, TypeError, KeyError) as exc:
        result = {'ok': False, 'status': 'refused', 'error': str(exc)}
    # Reuse the bounded, hash-checked reply channel for receipts with source
    # manifests larger than the root API's stdout tail. Journal stays intact.
    raw = encoded(result).encode('utf-8')
    if len(raw) > 1500 * 1024:
        raise ValueError('Operation response exceeds transport limit')
    if len(raw) > 16000:
        replies = base / '.hornet-replies'
        no_links(replies)
        replies.mkdir(mode=0o700, exist_ok=True)
        token = 'r-' + uuid.uuid4().hex
        fd = os.open(str(replies / token), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        result = {'ok': True, 'hornet_reply': {'id': token, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}
    print(encoded(result))
