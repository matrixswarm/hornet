"""Opt-in, persistent staged edits on the standalone registry host.

Sessions are local evidence, not authorization tokens or a filesystem sandbox.
All operations use an explicit root, relative path, session and revision digest.
The lock serializes cooperating Hornet writers; unrelated writers are not locked.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from ._common import check_content, execute_guarded

MAX_SOURCE = 128_000
MAX_STATE = 1_000_000


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _target(root, name):
    root = Path(root).expanduser()
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError('root must be an absolute, existing directory, not a symbolic link')
    root = root.resolve()
    relative = Path(name)
    if relative.is_absolute() or relative.drive or not relative.parts or any(x in ('.', '..') for x in relative.parts):
        raise ValueError('path must be relative to root without parent traversal')
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('symbolic links are not supported')
    if path.suffix.lower() not in ('.py', '.json') or not path.is_file():
        raise ValueError('path must name an existing Python or JSON file')
    return path


def _replace(path, raw, mode_source=None):
    fd, name = tempfile.mkstemp(prefix='.hornet-session-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        if mode_source is not None:
            shutil.copymode(mode_source, name)
        os.replace(name, str(path))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _result(state, raw, **extra):
    return {'status': 'SUCCESS', 'session': state['session'], 'revision': _hash(raw),
            'source_sha256': state['source_sha256'], **extra}


@execute_guarded
def execute(args):
    target = _target(args['root'], args['path'])
    action = args['action']
    lock = target.with_name(target.name + '.hornet.lock')
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('File is locked; inspect the lock before recovery')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'pid': os.getpid(), 'target': str(target)}, stream)
        source = target.read_bytes()
        if len(source) > MAX_SOURCE:
            raise ValueError('Source exceeds 128 KB')
        if action == 'begin':
            if args.get('source_sha256') != _hash(source):
                raise ValueError('Source hash does not match; read it before beginning')
            bom = source.startswith(b'\xef\xbb\xbf')
            text = source.decode('utf-8-sig')
            session = uuid.uuid4().hex
            state = {'version': 1, 'session': session, 'target': str(target),
                     'source_sha256': _hash(source), 'bom': bom, 'text': text}
            raw = json.dumps(state, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            sidecar = target.with_name(target.name + '.hornet-session-' + session + '.json')
            fd = os.open(str(sidecar), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            return _result(state, raw, sidecar=str(sidecar))

        session = args.get('session', '')
        if not re.fullmatch(r'[0-9a-f]{32}', session):
            raise ValueError('Provide the exact session ID returned by begin')
        sidecar = target.with_name(target.name + '.hornet-session-' + session + '.json')
        if sidecar.is_symlink() or not sidecar.is_file():
            raise ValueError('Session not found or sidecar is a symbolic link')
        if sidecar.stat().st_size > MAX_STATE:
            raise ValueError('Session sidecar exceeds its size limit')
        raw = sidecar.read_bytes()
        if args.get('revision') != _hash(raw):
            raise ValueError('Session revision changed; inspect the preserved sidecar')
        state = json.loads(raw.decode('utf-8'))
        if (not isinstance(state, dict) or set(state) != {'version', 'session', 'target', 'source_sha256', 'bom', 'text'}
                or state['version'] != 1 or state['session'] != session or state['target'] != str(target)
                or not re.fullmatch(r'[0-9a-f]{64}', state['source_sha256'])
                or type(state['bom']) is not bool or not isinstance(state['text'], str)
                or len(state['text'].encode('utf-8')) > MAX_SOURCE):
            raise ValueError('Invalid session sidecar; it was preserved')
        unchanged = _hash(source) == state['source_sha256']
        if action == 'show':
            offset = args.get('offset', 0)
            if offset > len(state['text']):
                raise ValueError('offset is outside staged text')
            return _result(state, raw, text=state['text'][offset:offset + 4000],
                           next_offset=min(len(state['text']), offset + 4000),
                           complete=offset + 4000 >= len(state['text']), source_unchanged=unchanged)
        if not unchanged:
            raise ValueError('Source changed since begin; session and source were preserved')
        if action == 'replace':
            old, new = args.get('target_text'), args.get('new_text')
            if not isinstance(old, str) or not old or not isinstance(new, str):
                raise ValueError('replace needs nonempty target_text and new_text')
            if state['text'].count(old) != 1:
                raise ValueError('target_text must occur exactly once')
            state['text'] = state['text'].replace(old, new, 1)
            if len(state['text'].encode('utf-8')) > MAX_SOURCE:
                raise ValueError('Staged text exceeds 128 KB')
            updated = json.dumps(state, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            if target.read_bytes() != source or sidecar.read_bytes() != raw:
                raise ValueError('Source or session changed during edit; both were preserved')
            _replace(sidecar, updated, sidecar)
            return _result(state, updated)
        if action in ('validate', 'commit'):
            check_content(target, state['text'])
            if action == 'validate':
                return _result(state, raw, valid=True)
            encoded = (b'\xef\xbb\xbf' if state['bom'] else b'') + state['text'].encode('utf-8')
            if target.read_bytes() != source or sidecar.read_bytes() != raw:
                raise ValueError('Source or session changed during commit; both were preserved')
            _replace(target, encoded, target)
            # Retain the sidecar as evidence. Its old baseline prevents replay.
            return _result(state, raw, committed_sha256=_hash(encoded), sidecar=str(sidecar))
        raise ValueError('Unknown session action')
    finally:
        lock.unlink(missing_ok=True)
