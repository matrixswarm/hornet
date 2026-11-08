"""Reconcile interrupted understanding publication without duplicate revisions."""
import hashlib
import json
from .goal_store import GoalStore
from .goal_context import publication_tag
from .revision_goals import read_revision_goals, metadata_hashes
from .understanding_progress import UnderstandingProgress, fingerprint


def acceptance_identity(project, draft):
    blueprint = dict(draft['blueprint'])
    blueprint['status'] = 'reviewed initialization; source remains authoritative'
    inputs = {'project': project, 'base': draft['revision'], 'run_id': draft.get('progress_run_id', 'legacy'),
              'sources': draft['source_sha256'], 'blueprint': blueprint, 'summaries': draft['summaries']}
    identifier = fingerprint(inputs)
    return identifier, {'kind': 'accept_understanding', 'operation_id': identifier, 'inputs': inputs}


def already_accepted(ws, project, draft, revision):
    if not revision:
        return False
    identifier, identity = acceptance_identity(project, draft)
    root = ws.revision(project, revision)
    value = read_revision_goals(root)
    tag = value.get('operation') or {}
    if tag.get('operation_id') != identifier or tag.get('checkpoint') != 'accept/publish':
        return False
    if (value['parent_revision'] != draft['revision'] or
            value['goals']['root/publish']['evidence']['source_sha256'] != draft['source_sha256'] or
            json.loads((root / '.blueprint').read_text(encoding='utf-8')) != identity['inputs']['blueprint']):
        raise ValueError('Accepted understanding receipt does not match this draft.')
    return True


def publish_understanding(ws, project, stage, draft):
    identifier, identity = acceptance_identity(project, draft)
    store = GoalStore(ws.project(project) / 'working_memory' / 'acceptance' / identifier, identity)
    phase = 'accept/publish'
    candidates = []
    for revision in ws.revisions(project):
        root = ws.revision(project, revision)
        path = root / '.hornet-meta' / 'goals.json'
        if not path.exists():
            continue
        value = json.loads(path.read_text(encoding='utf-8'))
        tag = value.get('operation') or {}
        if tag.get('operation_id') == identifier and tag.get('checkpoint') == phase:
            value = read_revision_goals(root)
            if (metadata_hashes(root) != metadata_hashes(stage) or value['parent_revision'] != draft['revision'] or
                    (root / '.blueprint').read_bytes() != (stage / '.blueprint').read_bytes()):
                raise ValueError('Interrupted understanding publication has mismatched evidence. Review it before continuing.')
            candidates.append(revision)
    if len(candidates) > 1:
        raise ValueError('Multiple understanding publication receipts exist. Resolve ambiguity before continuing.')
    if candidates:
        revision = candidates[0]
        evidence = {'revision': revision, 'input_fingerprint': identifier}
        node = store.snapshot()['nodes'].get(phase)
        if node and node['status'] == 'complete':
            store.complete(phase, evidence)
        else:
            store.reconcile(identity, path=phase, outcome='completed', evidence=evidence)
        return revision
    if store.resume()['status'] == 'blocked':
        raise ValueError('Understanding publication was interrupted with an uncertain outcome. Inspect its goals and revisions before retrying.')
    store.begin(phase, 'Publish reviewed project understanding', identifier)
    analysis = UnderstandingProgress(ws.project(project), draft).store.snapshot()
    with publication_tag(identifier, phase, identifier, snapshot={'acceptance': store.snapshot(), 'understanding': analysis}):
        revision = ws._publish(stage, draft['revision'], 'Understand project: reviewed project memory')
    store.complete(phase, {'revision': revision, 'input_fingerprint': identifier})
    return revision
