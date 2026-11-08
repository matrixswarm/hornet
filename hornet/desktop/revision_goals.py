"""Immutable goal snapshot written only while a new revision is staged."""
import hashlib
import json
import uuid


def metadata_hashes(root):
    meta = root / '.hornet-meta'
    paths = [meta / name for name in ('shadows.json', 'initialization.json', 'proposal.json')]
    paths += sorted((meta / 'shadows').rglob('*.shadow')) if (meta / 'shadows').exists() else []
    if any(path.is_symlink() for path in paths):
        raise ValueError('Revision evidence cannot contain symbolic links.')
    return {path.relative_to(meta).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def revision_goals(stage, revision, parent, mission):
    from .workspace import source_files
    files = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in source_files(stage)}
    blueprint = hashlib.sha256((stage / '.blueprint').read_bytes()).hexdigest()
    from .goal_context import current_tag, current_snapshot
    root_id = uuid.uuid4().hex
    return {'version': 1, 'revision': revision, 'parent_revision': parent,
            'operation': current_tag(), 'operation_snapshot': current_snapshot(), 'goal_id': root_id, 'goal': mission, 'current': 'root/review',
            'goals': {'root': {'parent': None, 'label': mission, 'status': 'pending_review'},
                      'root/publish': {'parent': 'root', 'label': 'Produce this revision', 'status': 'complete',
                                       'evidence': {'source_sha256': files, 'blueprint_sha256': blueprint, 'metadata_sha256': metadata_hashes(stage)}},
                      'root/review': {'parent': 'root', 'label': 'Review and explicitly accept', 'status': 'pending'}},
            'pointers': {'root': {'root': root_id, 'current': 'root/review'},
                         'root/publish': {'root': root_id, 'parent': 'root'},
                         'root/review': {'root': root_id, 'parent': 'root'}},
            'note': 'Publication proves an artifact exists, not that its goal is correct or accepted. Acceptance is recorded separately.'}


def read_revision_goals(root):
    path = root / '.hornet-meta' / 'goals.json'
    if not path.exists():
        return {'version': 0, 'note': 'Older revision: no goal snapshot. Existing data was not changed.'}
    if path.is_symlink():
        raise ValueError('Goal snapshot must not be a symbolic link.')
    value = json.loads(path.read_text(encoding='utf-8'))
    from .workspace import source_files
    metadata = json.loads((root / '.hornet-meta' / 'revision.json').read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or value.get('version') != 1 or value.get('revision') != root.name
            or value.get('parent_revision') != metadata.get('parent')
            or value.get('current') not in value.get('goals', {})):
        raise ValueError('Revision goal snapshot does not match this revision.')
    evidence = value.get('goals', {}).get('root/publish', {}).get('evidence', {})
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in source_files(root)}
    if (metadata_hashes(root) != evidence.get('metadata_sha256') or files != evidence.get('source_sha256') or
            hashlib.sha256((root / '.blueprint').read_bytes()).hexdigest() != evidence.get('blueprint_sha256')):
        raise ValueError('Revision files changed after the goal snapshot; do not resume from this evidence.')
    return value
