"""Controller-selected copies of goal source inside the dedicated coding VM.

Directories isolate ordinary tool paths and preserve earlier revisions. They
are not security sandboxes: commands still share the VM's non-root account.
"""
import copy
import base64
import hashlib
import json
import re
import uuid

from hornet.project import HornetError

BASE = '/home/hornet/workspace/goals'


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'g-[0-9a-f]{32}', value):
        raise HornetError('Invalid goal workspace ID')
    return value


DISPATCH = """import hashlib, json, os, runpy, sys, uuid
from pathlib import Path
base = Path('/home/hornet/workspace/goals')
ident = sys.argv[1]
import re
if not re.fullmatch(r'g-[0-9a-f]{32}', ident): raise ValueError('Invalid workspace ID')
root = base / ident
if any(p.is_symlink() for p in (root, *root.parents)): raise ValueError('Linked workspace refused')
api = runpy.run_path('/opt/hornet-worker/worker.py')
try:
    request = json.loads(''.join(sys.argv[2:]))
    result = api['Worker'](root).call(request['tool'], request['arguments'])
except (api['WorkerError'], OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
    result = {'ok': False, 'error': str(exc)}
encoded = json.dumps(result, ensure_ascii=False).encode('utf-8')
if len(encoded) > 1500*1024: raise ValueError('Workspace reply exceeds worker limit')
if len(encoded) > 16000:
    replies = Path('/home/hornet/workspace/.hornet-replies')
    if any(p.is_symlink() for p in (replies, *replies.parents)): raise ValueError('Linked reply directory')
    replies.mkdir(mode=0o700, exist_ok=True)
    token = 'r-' + uuid.uuid4().hex
    fd = os.open(str(replies/token), os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream: stream.write(encoded)
    result = {'ok': True, 'hornet_reply': {'id': token, 'bytes': len(encoded),
              'sha256': hashlib.sha256(encoded).hexdigest()}}
print(json.dumps(result, ensure_ascii=False))
"""

FETCH = """import base64, hashlib, json, re, sys
from pathlib import Path
token, action = sys.argv[1:3]
if not re.fullmatch(r'r-[0-9a-f]{32}', token): raise ValueError('Invalid reply ID')
path = Path('/home/hornet/workspace/.hornet-replies')/token
if any(p.is_symlink() for p in (path, *path.parents)): raise ValueError('Linked reply refused')
if path.stat().st_size > 1500*1024: raise ValueError('Oversized reply')
if action == 'read':
    offset = int(sys.argv[3])
    if not 0 <= offset < 1500*1024: raise ValueError('Invalid reply offset')
    with path.open('rb') as stream:
        stream.seek(offset)
        value = base64.b64encode(stream.read(12000)).decode('ascii')
    result = {'ok': True, 'data': value}
elif action == 'remove':
    if hashlib.sha256(path.read_bytes()).hexdigest() != sys.argv[3]: raise ValueError('Reply changed')
    path.unlink()
    result = {'ok': True}
else: raise ValueError('Unknown reply operation')
print(json.dumps(result))
"""

CLONE = """import hashlib, json, os, re, runpy, subprocess, sys
from pathlib import Path
api = runpy.run_path('/opt/hornet-worker/worker.py')
ident = sys.argv[1]
if not re.fullmatch(r'g-[0-9a-f]{32}', ident): raise ValueError('Invalid workspace ID')
expected = json.loads(''.join(sys.argv[2:]))
source = api['Worker'](Path.cwd())
base = Path('/home/hornet/workspace/goals')
if any(p.is_symlink() for p in (base, *base.parents)): raise ValueError('Linked workspace parent')
base.mkdir(mode=0o700, exist_ok=True)
target, stage = base / ident, base / (ident + '.preparing')
if target.exists() or target.is_symlink() or stage.exists() or stage.is_symlink():
    raise ValueError('Workspace already exists; nothing was overwritten')
def inventory(worker):
    result = {}
    for directory, dirs, names in os.walk(worker.root, followlinks=False):
        dirs[:] = [n for n in dirs if n not in api['BLOCKED']]
        if any((Path(directory)/n).is_symlink() for n in dirs+names):
            raise ValueError('Linked source entry')
    for p in worker.files():
        if len(result) >= 200 or p.stat().st_size > 256*1024:
            raise ValueError('Source exceeds workspace limits')
        result[worker.rel(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result
if inventory(source) != expected: raise ValueError('Source changed before workspace copy')
stage.mkdir(mode=0o700)
for name, digest in expected.items():
    raw = source.path(name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest: raise ValueError('Source changed during copy')
    dest = stage / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    dest.chmod(source.path(name).stat().st_mode & 0o777)
if inventory(source) != expected or inventory(api['Worker'](stage)) != expected:
    raise ValueError('Workspace copy did not verify; staging copy retained')
def git(*args):
    subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=stage,
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
git('init')
git('add', '--force', '--all', '--', '.')
git('-c', 'user.name=Hornet goal worker', '-c', 'user.email=hornet-worker@localhost',
    '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', 'Frozen goal source')
if inventory(source) != expected or inventory(api['Worker'](stage)) != expected:
    raise ValueError('Source changed before publication; staging copy retained')
stage.rename(target)
print(json.dumps({'ok': True, 'workspace': ident, 'root': str(target), 'files': expected}))
"""


def chunks(text):
    # The existing root-owned API permits up to 30 argv entries of 4000
    # characters. Chunk only data; never interpolate it into Python source.
    parts = [text[i:i + 3500] for i in range(0, len(text), 3500)]
    if len(parts) > 25:
        raise HornetError('Workspace request exceeds the bounded transport size')
    return parts


def unpack(reply):
    if not reply.get('ok'):
        raise HornetError('Workspace operation failed: ' + str(reply.get('error') or reply.get('output', 'unknown error'))[-1500:])
    try:
        result = json.loads(reply['output'])
        if not isinstance(result, dict) or type(result.get('ok')) is not bool:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise HornetError('Invalid workspace operation receipt') from None
    return result


def fork(worker, expected):
    ident = 'g-' + uuid.uuid4().hex
    reply = worker._rpc('run_command', {'argv': ['python3', '-c', CLONE, ident,
        *chunks(json.dumps(expected['files']))], 'timeout': 120})
    receipt = unpack(reply)
    if receipt != {'ok': True, 'workspace': ident, 'root': BASE + '/' + ident, 'files': expected['files']}:
        raise HornetError('Workspace copy receipt does not match its source')
    result = copy.copy(worker)
    result.workspace = ident
    result.creation = receipt
    return result


def receive(worker, reply):
    result = unpack(reply)
    if 'hornet_reply' not in result:
        return result
    ref = result['hornet_reply']
    if (not isinstance(ref, dict) or set(ref) != {'id', 'bytes', 'sha256'}
            or not isinstance(ref['id'], str) or not re.fullmatch(r'r-[0-9a-f]{32}', ref['id'])
            or type(ref['bytes']) is not int or not 16000 < ref['bytes'] <= 1500 * 1024
            or not isinstance(ref['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', ref['sha256'])):
        raise HornetError('Invalid large workspace reply reference')
    payload = bytearray()
    while len(payload) < ref['bytes']:
        part = unpack(worker._wire('run_command', {'argv': ['python3', '-c', FETCH, ref['id'], 'read', str(len(payload))]}))
        try:
            chunk = base64.b64decode(part['data'], validate=True)
        except (ValueError, TypeError, KeyError):
            raise HornetError('Invalid workspace reply chunk') from None
        if len(chunk) != min(12000, ref['bytes'] - len(payload)):
            raise HornetError('Incomplete workspace reply chunk')
        payload.extend(chunk)
    if hashlib.sha256(payload).hexdigest() != ref['sha256']:
        raise HornetError('Workspace reply hash mismatch')
    result = unpack({'ok': True, 'output': payload.decode('utf-8')})
    removed = unpack(worker._wire('run_command', {'argv': ['python3', '-c', FETCH, ref['id'], 'remove', ref['sha256']]}))
    if not removed.get('ok'):
        raise HornetError('Workspace reply cleanup did not complete')
    return result


def lock_path(project, config, worker):
    # All configs for this pinned worker share a lock, even when copied into
    # different goals. Child ScopedWorkers use their recipe-local inner lock.
    binding = getattr(worker, 'connection_binding', None)
    if binding is not None:
        if not isinstance(binding, str) or not re.fullmatch(r'[0-9a-f]{64}', binding):
            raise HornetError('Invalid worker lock identity')
        return project.path('coding/worker-locks/' + binding + '.lock', write=True)
    return project.path(config['worker_config'] + '.run.lock', write=True)
