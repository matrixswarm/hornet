"""Verified phase caches for a mission; uncertain effects are never retried."""
import hashlib
import json
import re

def write_json(path, value):
    # Share the goal store's guarded, fsynced replacement protocol. Cache intent
    # must be durable before an effect starts, not merely an in-memory pointer.
    from .goal_store import _atomic_write
    from .cancellation import commit
    with commit():
        _atomic_write(path, value)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def read_json(path):
    from .goal_store import _read_json
    return _read_json(path)


class MissionCheckpoint:
    """Persist intent before invoking a phase and bind every result to its inputs."""

    def __init__(self, directory, identity, operation_id, resumed=False):
        from .goal_store import GoalStore
        self.directory = directory
        self.identity = identity
        self.operation_id = operation_id
        self.resumed = resumed
        self.proposed_goals = []
        self.directory.mkdir(exist_ok=True)
        self.store = GoalStore(directory, identity)
        self.results = directory / 'results'
        from .goal_store import _safe_path
        _safe_path(self.results)
        self.results.mkdir(exist_ok=True)
        if resumed:
            self.store.resume()

    def phase(self, path, label, inputs, action, read_only=False, recover=None, verify=None):
        from .cancellation import check
        check()
        if not isinstance(path, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}(?:/[a-z][a-z0-9_]{0,47}){0,3}', path):
            raise ValueError('Invalid mission checkpoint path.')
        key = 'root/' + path
        digest = fingerprint({'identity': self.identity, 'inputs': inputs})
        cache_name = path.replace('/', '--')
        result_path = self.results / (cache_name + '.json')
        intent_path = self.results / (cache_name + '.intent.json')
        intent = read_json(intent_path) if intent_path.exists() else None
        if intent is not None and (intent.get('input_fingerprint') != digest
                                   or intent.get('operation_id') != self.operation_id):
            raise ValueError('Mission checkpoint inputs changed; start a new mission.')
        if result_path.exists():
            cached = read_json(result_path)
            if (cached.get('input_fingerprint') != digest
                    or cached.get('result_sha256') != fingerprint(cached.get('result'))):
                raise ValueError('Mission checkpoint result is invalid; inspect it before continuing.')
            result = cached['result']
            if verify:
                verify(result)
            self._finish(key, digest, cached, recovered=True)
            return result
        if intent is not None and not read_only:
            result = recover(key, digest) if recover else None
            if result is None:
                raise ValueError('Mission checkpoint ' + path + ' has an uncertain outcome. '
                                 'Inspect and reconcile the original operation before resuming; '
                                 'it will not be run again automatically.')
            if verify:
                verify(result)
            cached = self._save(result_path, digest, result)
            self._finish(key, digest, cached, recovered=True)
            return result
        node = self.store.snapshot().get('nodes', {}).get(key, {})
        if node.get('status') == 'complete':
            raise ValueError('Completed mission checkpoint result is missing; inspect it before continuing.')
        self.store.begin(key, label, digest, read_only=read_only)
        write_json(intent_path, {'operation_id': self.operation_id, 'checkpoint': key,
                                 'input_fingerprint': digest, 'read_only': read_only})
        check()
        from .model_briefing import set_context
        snapshot = self.store.snapshot()
        pointer = snapshot.get('pointer') or {}
        set_context({'operation_id': self.operation_id, 'goal': self.identity.get('mission', ''),
                     'checkpoint': pointer.get('path', key),
                     'base_revision': self.identity.get('base_revision'), 'resumed': self.resumed})
        result = action(key, digest)
        check()
        if verify:
            verify(result)
        cached = self._save(result_path, digest, result)
        self._finish(key, digest, cached)
        return result

    def _save(self, path, digest, result):
        value = {'input_fingerprint': digest, 'result_sha256': fingerprint(result), 'result': result}
        write_json(path, value)
        return value

    def _finish(self, key, digest, cached, recovered=False):
        evidence = {'input_fingerprint': digest, 'result_sha256': cached['result_sha256']}
        if recovered and self.store.snapshot().get('nodes', {}).get(key, {}).get('status') != 'complete':
            self.store.reconcile(self.identity, path=key, evidence=evidence, outcome='completed')
        else:
            self.store.complete(key, evidence)

    def publish(self, path, label, inputs, ws, project, parent, action):
        from .goal_context import publication_tag
        from .automation import inventory
        from .revision_goals import read_revision_goals

        def evidence(revision):
            root = ws.revision(project, revision)
            snapshot = read_revision_goals(root)
            expected = snapshot.get('goals', {}).get('root/publish', {}).get('evidence', {})
            if (snapshot.get('revision') != revision or snapshot.get('parent_revision') != parent
                    or expected.get('source_sha256') != inventory(root)
                    or expected.get('blueprint_sha256') != hashlib.sha256((root / '.blueprint').read_bytes()).hexdigest()):
                raise ValueError('Published revision changed or its goal evidence is invalid; cannot resume.')
            return {'revision': revision, 'goal_id': snapshot.get('goal_id'),
                    'snapshot_sha256': fingerprint(snapshot),
                    'source_sha256': expected['source_sha256'],
                    'blueprint_sha256': expected['blueprint_sha256']}

        digest = fingerprint({'identity': self.identity, 'inputs': inputs})
        tag = {'operation_id': self.operation_id, 'checkpoint': 'root/' + path,
               'input_fingerprint': digest}

        def verify(value):
            if not isinstance(value, dict) or value != evidence(value.get('revision')):
                raise ValueError('Published revision no longer matches its checkpoint.')
            root = ws.revision(project, value['revision'])
            if read_json(root / '.hornet-meta' / 'goals.json').get('operation') != tag:
                raise ValueError('Published revision does not belong to this mission checkpoint.')

        def recover(key, current_digest):
            matches = []
            for revision in ws.revisions(project):
                marker = ws.revision(project, revision) / '.hornet-meta' / 'goals.json'
                if marker.is_file() and read_json(marker).get('operation') == tag:
                    matches.append(revision)
            if len(matches) > 1:
                raise ValueError('Multiple revisions match this interrupted publication; reconcile them before resuming.')
            return evidence(matches[0]) if matches else None

        def publish(key, current_digest):
            snapshot = self.store.snapshot()
            snapshot['proposed_goals'] = self.proposed_goals
            with publication_tag(self.operation_id, key, current_digest, snapshot=snapshot):
                return evidence(action())

        return self.phase(path, label, inputs, publish, recover=recover, verify=verify)['revision']
