"""Evidence-backed understanding checkpoints over existing resumable file chunks."""
import hashlib
import json
from .goal_store import GoalStore


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


class UnderstandingProgress:
    def __init__(self, project_root, draft):
        self.identity = {'run_id': draft.get('progress_run_id', 'legacy-resume'), 'kind': 'understanding', 'project': draft['project'], 'revision': draft['revision'],
                         'source_sha256': draft['source_sha256'], 'included': draft['included'],
                         'base_blueprint': draft['base_blueprint'], 'accepted_at_start': draft['accepted_at_start']}
        self.identity['operation_id'] = fingerprint(self.identity)
        self.store = GoalStore(project_root / 'working_memory' / 'understanding' / self.identity['operation_id'], self.identity)

    def path(self, name, number):
        return 'understand/files/file_' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:20] + '/part_' + str(number)

    def begin(self, name, number, digest):
        path = self.path(name, number)
        self.store.begin(path, f'Understand {name}, part {number}', fingerprint({'file': name, 'part': number, 'sha256': digest}), read_only=True)
        from .model_briefing import set_context
        set_context({'operation_id': self.identity['operation_id'], 'goal': 'Understand the selected project files',
                     'checkpoint': path, 'base_revision': self.identity['revision'],
                     'resumed': self.store.snapshot()['sequence'] > 1})
        return path

    def completed(self, path, name, number, digest, summary):
        evidence = {'path': name, 'part': number, 'source_sha256': digest,
                    'summary_sha256': hashlib.sha256(summary.encode('utf-8')).hexdigest(),
                    'proof': 'Verified persisted initialization-draft chunk; no acceptance implied.'}
        self.store.complete(path, evidence)

    def deferred(self, path, reason):
        self.store.defer(path, {'reason': reason, 'outcome': 'No valid result; retry required, not complete.'})

    def finish(self, draft):
        # Analysis completeness is not human approval or proof of software correctness.
        if not draft['unreviewed'] and not draft.get('issues'):
            evidence = {'input_fingerprint': self.identity['operation_id'],
                        'chunks_fingerprint': fingerprint(draft['chunks']),
                        'outcome': 'Analysis ready for explicit human review, not accepted.'}
            nodes = self.store.snapshot()['nodes']
            for path in sorted(nodes, key=lambda item: item.count('/'), reverse=True):
                if any(other.startswith(path + '/') for other in nodes):
                    self.store.complete(path, evidence)
