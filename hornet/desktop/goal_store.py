"""Durable revision-scoped goal checkpoints with fail-closed effect recovery.

state.json is the only authority. Mirrored goals/pointers and immutable sequence
snapshots are derived views, never recovery inputs. Begin before work and complete
with evidence afterwards. Restarted effects require explicit reconciliation.
This store is not an executor or an exactly-once effects system.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid

from .cancellation import commit, terminal_metadata


class GoalStoreError(ValueError):
    """Invalid, unsafe, stale, or inconsistent goal state."""


class GoalIdentityMismatch(GoalStoreError):
    """A checkpoint belongs to a different operation or input revision."""


class GoalResumeBlocked(GoalStoreError):
    """An effect may already have happened and must not be retried blindly."""


_UNFINISHED = {'running', 'failed', 'cancelled'}
_VERSION = 1
_MAX_BYTES = 16 * 1024 * 1024
_COMPONENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')


def _json_copy(value):
    def check(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise GoalStoreError('JSON object keys must be strings.')
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise GoalStoreError('Non-finite numbers are not valid checkpoint data.')
        elif item is not None and not isinstance(item, (str, int, bool)):
            raise GoalStoreError('Checkpoint data must contain JSON values only.')
    check(value)
    return deepcopy(value)


def _goal_path(path):
    if isinstance(path, (list, tuple)):
        if not path or any(not isinstance(part, str) or '/' in part for part in path):
            raise GoalStoreError('Use nonempty goal path components.')
        path = '/'.join(path)
    if (not isinstance(path, str) or len(path) > 2048 or
            not 1 <= len(path.split('/')) <= 32 or
            any(not _COMPONENT.fullmatch(part) or part in {'.', '..'} for part in path.split('/'))):
        raise GoalStoreError('Use a relative slash-separated goal path without parent segments.')
    for component in path.split('/'):
        if component.endswith('.') or re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', component):
            raise GoalStoreError('Goal path contains a reserved filesystem name.')
    return path


def _native_path(path):
    """Use Windows extended paths without changing logical goal identifiers."""
    path = Path(path)
    if os.name == 'nt':
        raw = str(path.absolute())
        if not raw.startswith('\\\\?\\') and len(raw) >= 200:
            raw = ('\\\\?\\UNC\\' + raw[2:] if raw.startswith('\\\\') else '\\\\?\\' + raw)
            return Path(raw)
    return path


def _safe_path(path):
    """Reject symlinks, junctions and other reparse points before file access."""
    path = _native_path(path)
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise GoalStoreError('Goal storage cannot use symbolic links or reparse points: ' + str(item))
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise GoalStoreError('Goal storage has a non-directory parent.')
    return path


def _regular_file(path):
    path = _safe_path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise GoalStoreError('Goal storage requires regular, unlinked files: ' + str(path))


def _encode(data):
    value = (json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    if len(value) > _MAX_BYTES:
        raise GoalStoreError('Goal checkpoint exceeds the 16 MB storage limit.')
    return value


def _read_json(path):
    path = _native_path(path)
    _regular_file(path)
    if path.stat().st_size > _MAX_BYTES:
        raise GoalStoreError('Goal checkpoint exceeds the 16 MB storage limit.')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise GoalStoreError('Duplicate keys in goal checkpoint.')
            result[key] = value
        return result
    try:
        return _json_copy(json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs))
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise GoalStoreError('Invalid goal checkpoint JSON: ' + str(path)) from exc


def _atomic_write(path, data):
    path = _native_path(path)
    _regular_file(path)
    payload = _encode(data)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _regular_file(path)
        os.replace(str(temporary), str(path))
        if os.name != 'nt':
            descriptor = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


class GoalStore:
    """One immutable identity and serialized state per operation/revision.

    Identity should include project, revision, operation ID, and relevant source
    or contract fingerprints. Callers must revalidate those inputs on resume.
    Methods reload state under an OS lock; completion checks a live attempt token
    so replaced workers cannot report stale results.
    """
    def __init__(self, directory, identity):
        if not isinstance(identity, dict) or not identity:
            raise GoalStoreError('A nonempty operation identity is required.')
        self.identity = _json_copy(identity)
        raw = Path(directory).expanduser()
        if '..' in raw.parts:
            raise GoalStoreError('Parent traversal is not allowed in goal storage paths.')
        self.directory = _safe_path(raw.absolute())
        self.directory.mkdir(parents=True, exist_ok=True)
        self._attempts = {}
        self.projection_warning = None
        with self._locked():
            path = self.directory / 'state.json'
            _regular_file(path)
            if not path.exists():
                if any((self.directory / name).exists() for name in ('goals.json', 'pointers.json', 'snapshots', 'goals', 'pointers')):
                    raise GoalStoreError('Authoritative goal state is missing; refusing to reset existing checkpoints.')
                with commit():
                    _atomic_write(path, {'version': _VERSION, 'identity': self.identity,
                                        'sequence': 0, 'nodes': {}, 'pointer': None})
            self._project(self._load())

    @contextmanager
    def _locked(self):
        _safe_path(self.directory)
        path = self.directory / '.store.lock'
        _regular_file(path)
        descriptor = os.open(str(path), os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        acquired = False
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise GoalStoreError('Unsafe goal store lock.')
            # Keep the lock file permanently: unlinking it could split ownership.
            if info.st_size == 0:
                os.write(descriptor, b'0')
            end = time.monotonic() + 10
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if time.monotonic() >= end:
                        raise GoalStoreError('Goal store is busy in another worker.') from exc
                    time.sleep(0.02)
            _safe_path(self.directory)
            yield
        finally:
            if acquired:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _load(self):
        path = self.directory / 'state.json'
        _regular_file(path)
        if not path.exists():
            raise GoalStoreError('Authoritative goal state is missing; recovery must be reviewed.')
        state = _read_json(path)
        self._validate(state)
        if _encode(state['identity']) != _encode(self.identity):
            raise GoalIdentityMismatch('Goal identity changed. Use a new revision/operation instead of resuming stale work.')
        return state

    @staticmethod
    def _validate(state):
        if not isinstance(state, dict) or set(state) != {'version', 'identity', 'sequence', 'nodes', 'pointer'}:
            raise GoalStoreError('Invalid authoritative goal state.')
        sequence = state['sequence']
        if (type(state['version']) is not int or state['version'] != _VERSION or
                type(sequence) is not int or sequence < 0 or
                not isinstance(state['identity'], dict) or not state['identity'] or
                not isinstance(state['nodes'], dict)):
            raise GoalStoreError('Invalid goal state version, sequence, identity, or nodes.')
        nodes = state['nodes']
        if len({path.casefold() for path in nodes}) != len(nodes):
            raise GoalStoreError('Goal paths must not differ only by letter case.')
        running = []
        fields = {'path', 'label', 'kind', 'status', 'read_only', 'input_fingerprint',
                  'created_sequence', 'started_sequence', 'completed_sequence',
                  'attempt_id', 'evidence', 'reconciliation'}
        for path, node in nodes.items():
            _goal_path(path)
            if not isinstance(node, dict) or set(node) != fields or node['path'] != path:
                raise GoalStoreError('Malformed goal node: ' + path)
            if (not isinstance(node['label'], str) or not node['label'].strip() or len(node['label']) > 2000 or
                    not isinstance(node['kind'], str) or node['kind'] not in {'group', 'step'} or
                    not isinstance(node['status'], str) or node['status'] not in {'pending', 'running', 'failed', 'cancelled', 'complete'} or
                    type(node['read_only']) is not bool or not isinstance(node['evidence'], dict) or
                    not isinstance(node['reconciliation'], dict)):
                raise GoalStoreError('Invalid goal node fields: ' + path)
            for field in ('created_sequence', 'started_sequence', 'completed_sequence'):
                value = node[field]
                if value is not None and (type(value) is not int or not 1 <= value <= sequence):
                    raise GoalStoreError('Invalid goal checkpoint sequence: ' + path)
            if node['created_sequence'] is None:
                raise GoalStoreError('Goal creation sequence is missing.')
            parent = path.rpartition('/')[0]
            if parent and (parent not in nodes or not isinstance(nodes[parent], dict) or nodes[parent].get('kind') != 'group'):
                raise GoalStoreError('Goal hierarchy has a missing or non-group parent: ' + path)
            if node['kind'] == 'group':
                if (node['status'] in _UNFINISHED or not node['read_only'] or node['input_fingerprint'] is not None or
                        node['attempt_id'] is not None or node['started_sequence'] is not None):
                    raise GoalStoreError('Invalid goal group: ' + path)
            elif (not isinstance(node['input_fingerprint'], str) or not node['input_fingerprint'] or len(node['input_fingerprint']) > 4096):
                raise GoalStoreError('Step input fingerprint is missing or invalid: ' + path)
            if node['status'] in _UNFINISHED:
                running.append(path)
                if (not isinstance(node['attempt_id'], str) or not re.fullmatch('[a-f0-9]{32}', node['attempt_id']) or
                        node['started_sequence'] is None):
                    raise GoalStoreError('Running goal has no attempt ownership: ' + path)
            if node['status'] == 'complete':
                if not node['evidence'] or node['completed_sequence'] is None:
                    raise GoalStoreError('Completed goal has no completion evidence: ' + path)
                if any(other.startswith(path + '/') and child.get('status') != 'complete'
                       for other, child in nodes.items() if isinstance(child, dict)):
                    raise GoalStoreError('Completed goal has unfinished descendants: ' + path)
            elif node['completed_sequence'] is not None or node['evidence']:
                raise GoalStoreError('Unfinished goal carries completion evidence: ' + path)
        pointer = state['pointer']
        if (pointer is not None and (not isinstance(pointer, str) or pointer not in nodes) or
                (not nodes and pointer is not None) or len(running) > 1 or
                (running and pointer != running[0])):
            raise GoalStoreError('Invalid current goal pointer.')

    @staticmethod
    def _view(state):
        result = deepcopy(state)
        hierarchy, groups = [], {}
        for path in sorted(state['nodes'], key=lambda p: (p.count('/'), state['nodes'][p]['created_sequence'], p)):
            entry = deepcopy(state['nodes'][path])
            entry['children'] = []
            groups[path] = entry
            parent = path.rpartition('/')[0]
            (groups[parent]['children'] if parent else hierarchy).append(entry)
        result['hierarchy'] = hierarchy
        result['pointer'] = deepcopy(state['nodes'][state['pointer']]) if state['pointer'] else None
        return result

    @staticmethod
    def _resume(state):
        path = state['pointer']
        node = state['nodes'].get(path) if path else None
        status, reason = 'ready', 'Select the next uncompleted goal; completed work is retained.'
        if node and node['status'] in _UNFINISHED:
            status = 'resume' if node['read_only'] else 'blocked'
            reason = ('Read-only work may be repeated after checking the input identity.' if node['read_only'] else
                      'This effect may already have happened. Reconcile evidence before continuing; do not retry automatically.')
        elif state['nodes'] and all(n['status'] == 'complete' for n in state['nodes'].values()):
            status, reason = 'complete', 'All recorded goals have completion evidence.'
        return {'status': status, 'path': path, 'reason': reason, 'sequence': state['sequence'],
                'read_only': node['read_only'] if node else True,
                'attempt_id': node['attempt_id'] if node else None,
                'pending': [p for p, n in state['nodes'].items() if n['status'] == 'pending']}

    def _mirror(self, path, data):
        path = _native_path(path)
        # Derived node files have local versions, not the global sequence, so
        # unchanged breadcrumbs are not rewritten on every checkpoint.
        cache = getattr(self, '_mirror_cache', {})
        if cache.get(str(path)) == data:
            return
        _regular_file(path)
        same = False
        if path.exists():
            try:
                same = _read_json(path) == data
            except GoalStoreError:
                pass
        if not same:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, data)
        cache[str(path)] = deepcopy(data)
        self._mirror_cache = cache

    def _projection_targets(self, state):
        targets = {}
        for path in state['nodes']:
            goal = _native_path(self.directory.joinpath('goals', *path.split('/'), '_goal.json'))
            pointer = _native_path(self.directory.joinpath('pointers', *path.split('/'), '_pointer.json'))
            targets[path] = (goal, pointer)
        return targets

    def _archive(self, state):
        snapshots = _safe_path(self.directory / 'snapshots')
        snapshots.mkdir(exist_ok=True)
        snapshot = snapshots / ('%012d.json' % state['sequence'])
        _regular_file(snapshot)
        if snapshot.exists():
            if _encode(_read_json(snapshot)) != _encode(state):
                raise GoalStoreError('Immutable goal snapshot conflicts with authoritative state.')
        else:
            _atomic_write(snapshot, state)

    def _project(self, state):
        # Unsafe targets and conflicting immutable snapshots are not ignored.
        paths = [self.directory / name for name in ('goals.json', 'pointers.json')]
        directories = [_safe_path(self.directory / name) for name in ('snapshots', 'goals', 'pointers')]
        for path in paths:
            _regular_file(path)
        targets = self._projection_targets(state)
        self.projection_warning = None
        try:
            for directory in directories:
                directory.mkdir(exist_ok=True)
            snapshots, goals, pointers = directories
            self._archive(state)
            view = self._view(state)
            pointer_view = {'identity': state['identity'], 'sequence': state['sequence'],
                            'pointer': view['pointer'], 'resume': self._resume(state)}
            self._mirror(paths[0], {key: view[key] for key in ('identity', 'sequence', 'hierarchy', 'nodes')})
            self._mirror(paths[1], pointer_view)
            self._mirror(pointers / 'current.json', pointer_view)
            children = {path: [] for path in state['nodes']}
            for path in state['nodes']:
                parent = path.rpartition('/')[0]
                if parent:
                    children[parent].append(path)
            for path, node in state['nodes'].items():
                goal_target, pointer_target = targets[path]
                self._mirror(goal_target, {'identity': state['identity'], 'goal': node})
                self._mirror(pointer_target, {'path': path, 'parent': path.rpartition('/')[0] or None,
                                             'children': sorted(children[path]), 'status': node['status'],
                                             'attempt_id': node['attempt_id'],
                                             'completed_sequence': node['completed_sequence']})
        except OSError as exc:
            # A derived-view failure cannot roll back committed authority.
            self.projection_warning = 'Checkpoint committed; derived views need rebuilding: ' + str(exc)

    def _commit(self, state, *, stopped=False):
        self._validate(state)
        previous = self._load()
        changed = {path for path, node in state['nodes'].items() if node != previous['nodes'].get(path)}
        # New children also change the ancestor pointer breadcrumbs. Unchanged
        # mirrors are neither read nor written, so do not repeatedly walk all
        # their filesystem ancestors for every source-file checkpoint.
        for path in list(changed):
            parent = path.rpartition('/')[0]
            while parent:
                changed.add(parent)
                parent = parent.rpartition('/')[0]
        targets = self._projection_targets(state)
        for path in changed:
            for target in targets[path]:
                _regular_file(target)
        for name in ('goals.json', 'pointers.json', 'pointers/current.json'):
            _regular_file(self.directory / name)
        with terminal_metadata() if stopped else commit():
            self._archive(previous)
            _atomic_write(self.directory / 'state.json', state)
            self._project(state)
        return self._view(state)

    def snapshot(self):
        """Return a detached current view, always rereading the authority."""
        with self._locked():
            return self._view(self._load())

    def rebuild_mirrors(self):
        """Repair derived views from state; never infer state from a mirror."""
        with self._locked():
            state = self._load()
            self._mirror_cache = {}
            self._project(state)
            return self._view(state)

    def resume(self):
        """Describe the safe continuation point without executing or claiming it."""
        with self._locked():
            return self._resume(self._load())

    def begin(self, path, label, input_fingerprint, *, read_only=False):
        """Claim a step before work; effects default to non-retriable.

        Implicit ancestors are groups. Complete them with evidence after their
        children finish. Repeating an identical completed step is a no-op: callers
        must check its returned status before executing work again.
        """
        path = _goal_path(path)
        if not isinstance(label, str) or not label.strip() or len(label) > 2000:
            raise GoalStoreError('A nonempty goal label of at most 2000 characters is required.')
        if not isinstance(input_fingerprint, str) or not input_fingerprint or len(input_fingerprint) > 4096:
            raise GoalStoreError('A nonempty input fingerprint of at most 4096 characters is required.')
        if type(read_only) is not bool:
            raise GoalStoreError('read_only must be a boolean.')
        with self._locked():
            state = self._load()
            nodes = state['nodes']
            existing = nodes.get(path)
            if existing and (existing['kind'] != 'step' or existing['input_fingerprint'] != input_fingerprint or
                             existing['read_only'] != read_only or existing['label'] != label):
                raise GoalIdentityMismatch('Goal inputs changed. Create a new goal/revision rather than replacing its evidence.')
            # Cached completed steps may be read while another step is running.
            # This does not move the pointer or grant an execution claim.
            if existing and existing['status'] == 'complete':
                return self._view(state)
            current = nodes.get(state['pointer'])
            if current and current['status'] in _UNFINISHED and (state['pointer'] != path or not current['read_only']):
                raise GoalResumeBlocked('An unfinished goal owns the pointer. Finish it or reconcile its effect before continuing.')
            state['sequence'] += 1
            parts = path.split('/')
            for index in range(1, len(parts)):
                parent = '/'.join(parts[:index])
                if parent in nodes and (nodes[parent]['kind'] != 'group' or nodes[parent]['status'] == 'complete'):
                    raise GoalStoreError('Cannot append to a completed goal or nest below an executable step.')
                if parent not in nodes:
                    nodes[parent] = self._node(parent, parts[index - 1], 'group', state['sequence'])
            if existing is None:
                existing = self._node(path, label, 'step', state['sequence'])
                nodes[path] = existing
            attempt = uuid.uuid4().hex
            existing.update(status='running', input_fingerprint=input_fingerprint, read_only=read_only,
                            started_sequence=state['sequence'], attempt_id=attempt)
            state['pointer'] = path
            result = self._commit(state)
            self._attempts[path] = attempt
            return result

    @staticmethod
    def _node(path, label, kind, sequence):
        return {'path': path, 'label': label, 'kind': kind, 'status': 'pending', 'read_only': True,
                'input_fingerprint': None, 'created_sequence': sequence, 'started_sequence': None,
                'completed_sequence': None, 'attempt_id': None, 'evidence': {}, 'reconciliation': {}}

    @staticmethod
    def _evidence(evidence):
        if not isinstance(evidence, dict) or not evidence:
            raise GoalStoreError('Nonempty completion/reconciliation evidence is required.')
        return _json_copy(evidence)

    def complete(self, path, evidence):
        """Complete an owned attempt, or a group whose children are complete."""
        path, evidence = _goal_path(path), self._evidence(evidence)
        with self._locked():
            state = self._load()
            node = state['nodes'].get(path)
            if node is None:
                raise GoalStoreError('Unknown goal: ' + path)
            if node['status'] == 'complete':
                if _encode(node['evidence']) != _encode(evidence):
                    raise GoalStoreError('Completed goal evidence is immutable.')
                return self._view(state)
            if any(other.startswith(path + '/') and child['status'] != 'complete' for other, child in state['nodes'].items()):
                raise GoalStoreError('Complete every child before completing its parent.')
            if node['kind'] == 'step' and (node['status'] != 'running' or self._attempts.get(path) != node['attempt_id']):
                raise GoalResumeBlocked('This worker does not own the running attempt; reconcile restarted effects or begin read-only work again.')
            running = [p for p, n in state['nodes'].items() if n['status'] in _UNFINISHED]
            if running and running != [path]:
                raise GoalResumeBlocked('Another unfinished goal owns the current pointer.')
            state['sequence'] += 1
            node.update(status='complete', evidence=evidence, completed_sequence=state['sequence'])
            state['pointer'] = path
            return self._commit(state)

    def reconcile(self, identity, *, path=None, evidence=None, outcome=None, expected_sequence=None):
        """Check identity or explicitly resolve an uncertain effect.

        completed records verified result evidence. not_applied records proof the
        effect did not happen, allowing a later begin. Callers must validate the
        external evidence. Pass expected_sequence if inspecting an earlier view.
        """
        identity = _json_copy(identity)
        with self._locked():
            state = self._load()
            if _encode(identity) != _encode(state['identity']):
                raise GoalIdentityMismatch('Goal identity mismatch. Refusing stale continuation.')
            if expected_sequence is not None and (type(expected_sequence) is not int or expected_sequence != state['sequence']):
                raise GoalStoreError('Goal state changed while reconciliation evidence was collected.')
            if path is None and outcome is None and evidence is None:
                return self._view(state)
            path = _goal_path(path)
            evidence = self._evidence(evidence)
            if outcome not in {'completed', 'not_applied'}:
                raise GoalStoreError('Reconciliation outcome must be completed or not_applied.')
            node = state['nodes'].get(path)
            if not node or node['status'] not in _UNFINISHED or state['pointer'] != path:
                raise GoalStoreError('Only the current unfinished attempt can be reconciled.')
            state['sequence'] += 1
            node['reconciliation'] = {'outcome': outcome, 'evidence': evidence, 'sequence': state['sequence'],
                                      'attempt_id': node['attempt_id']}
            if outcome == 'completed':
                node.update(status='complete', evidence=evidence, completed_sequence=state['sequence'])
            else:
                node.update(status='pending', attempt_id=None, started_sequence=None)
            self._attempts.pop(path, None)
            return self._commit(state)


    def stop(self, status, evidence):
        """Record owned failed/cancelled work without releasing an uncertain effect.

        Only terminal bookkeeping may commit after cancellation. The pointer,
        attempt identity and input fingerprint remain available for recovery.
        """
        if status not in {'failed', 'cancelled'}:
            raise GoalStoreError('Stopped status must be failed or cancelled.')
        evidence = self._evidence(evidence)
        with self._locked():
            state = self._load()
            path = state['pointer']
            node = state['nodes'].get(path)
            if not node or node['status'] != 'running':
                return self._view(state)
            if self._attempts.get(path) != node['attempt_id']:
                raise GoalResumeBlocked('Only the owner may stop a running attempt.')
            state['sequence'] += 1
            node['status'] = status
            node['reconciliation'] = {'outcome': status, 'evidence': evidence,
                                      'sequence': state['sequence'], 'attempt_id': node['attempt_id']}
            result = self._commit(state, stopped=True)
            self._attempts.pop(path, None)
            return result

    def defer(self, path, evidence):
        """Release owned read-only work without pretending it completed.

        Failure/interruption evidence survives in the node and snapshots. Effects
        must instead be reconciled with proof of completion or non-application.
        """
        path, evidence = _goal_path(path), self._evidence(evidence)
        with self._locked():
            state = self._load()
            node = state['nodes'].get(path)
            if (not node or node['status'] != 'running' or not node['read_only'] or
                    state['pointer'] != path or self._attempts.get(path) != node['attempt_id']):
                raise GoalResumeBlocked('Only the owned running read-only attempt may be deferred.')
            state['sequence'] += 1
            node['reconciliation'] = {'outcome': 'deferred', 'evidence': evidence,
                                      'sequence': state['sequence'], 'attempt_id': node['attempt_id']}
            node.update(status='pending', attempt_id=None, started_sequence=None)
            state['pointer'] = None
            result = self._commit(state)
            self._attempts.pop(path, None)
            return result
