"""Candidate presentation and explicit, recoverable checkpoint cleanup.

Source revisions and checkpoint receipts remain immutable. Presentation never
changes Workspace.revisions(), which is also the recovery inventory.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import re
import uuid

from .mission_checkpoint import fingerprint, read_json, write_json
from .mission_lease import mission_lease
from .revision_display import load_display, revision_parent


def missions(ws, project):
    directory = ws.project(project) / 'missions'
    if directory.is_symlink():
        raise ValueError('Mission directory cannot be a symbolic link.')
    result = []
    for path in sorted(directory.glob('*.json')):
        value = read_json(path)
        if (not isinstance(value, dict) or value.get('project') != project
                or not isinstance(value.get('id'), str)
                or not re.fullmatch(r'[0-9a-f]{32}', value['id'])
                or path.stem != value['id']):
            raise ValueError('Invalid saved mission; inspect it before cleanup.')
        result.append(value)
    return result


def _owned(ws, project, record, revisions):
    candidates = record.get('candidates', [])
    if not isinstance(candidates, list) or any(not isinstance(r, str) for r in candidates):
        raise ValueError('Invalid candidate history.')
    owned = []
    for revision in candidates:
        if revision not in revisions:
            continue
        root = ws.revision(project, revision)
        value = read_json(root / '.hornet-meta' / 'goals.json')
        if not isinstance(value, dict):
            raise ValueError('Invalid checkpoint evidence.')
        tag = value.get('operation') or {}
        if not isinstance(tag, dict):
            raise ValueError('Invalid checkpoint operation.')
        if tag.get('operation_id') == record['id']:
            owned.append(revision)
    return owned


def cleanup_plan(ws, project, revision):
    """Preview unused snapshots of a locked revision; never change files."""
    from .automation import inventory, read_state
    from .revision_goals import read_revision_goals
    from .working_revision import load_working, is_working, superseded_without_edits, is_abandoned
    if is_working(ws, project, revision):
        raise ValueError('Lock this revision before cleaning up its checkpoints.')
    revisions = ws.revisions(project)
    root = ws.revision(project, revision)
    read_revision_goals(root)
    records = missions(ws, project)
    state = load_working(ws, project)
    sessions = (state or {}).get('sessions', {})
    matches = [(key, value) for key, value in sessions.items() if value['head'] == revision]
    if len(matches) > 1:
        raise ValueError('Ambiguous working revision history; inspect it before cleanup.')
    if matches:
        anchor, session = matches[0]
        if session.get('pending'):
            raise ValueError('This revision has unfinished work; reconcile it before cleanup.')
        owned_ids = set(session['missions'])
        original = session['source']
        owned = {anchor}
    else:
        owners = [r for r in records if r.get('final_revision') == revision]
        if len(owners) != 1 or owners[0].get('state') not in ('review', 'accepted'):
            raise ValueError('Select a locked completed mission revision for cleanup.')
        owned_ids = {owners[0]['id']}
        original = owners[0]['base_revision']
        owned = set()
    protected = {revision, original, read_state(ws, project).get('accepted_revision')}
    protected.update(r for r, value in load_display(ws, project).items() if value.get('name'))
    superseded = {m for entry in sessions.values() for m in entry.get('superseded', [])}
    for record in records:
        if is_abandoned(ws, project, record['id']):
            # Explicit closure permits other work, but retain every artifact of
            # an interrupted operation for inspection of uncertain effects.
            protected.update((record.get('base_revision'), record.get('final_revision')))
            protected.update(record.get('candidates', []))
            continue
        if record['id'] in superseded and superseded_without_edits(record):
            continue
        # A crash may have published a snapshot before recording its ID. Any
        # unfinished mission blocks pruning rather than guessing dependencies.
        if record.get('resumable') or record.get('state') not in ('review', 'accepted', 'diagnosis_complete'):
            raise ValueError('An unfinished mission still exists. Finish or reconcile it before cleanup.')
        if record['id'] in owned_ids:
            owned.update(_owned(ws, project, record, revisions))
        else:
            protected.update((record.get('base_revision'), record.get('final_revision'), record.get('accepted_at_start')))
            protected.update(record.get('candidates', []))
    for session in sessions.values():
        protected.update((session['head'], session['source']))
    draft_path = ws.project(project) / 'initialization-draft.json'
    if draft_path.exists():
        draft = read_json(draft_path)
        if not isinstance(draft, dict) or not isinstance(draft.get('revision'), str):
            raise ValueError('Invalid initialization draft; inspect it before cleanup.')
        protected.add(draft['revision'])
    for other in revisions:
        if other not in owned:
            protected.add(revision_parent(ws, project, other))
    remove = sorted((owned & set(revisions)) - protected)
    evidence = {}
    for item in remove:
        item_root = ws.revision(project, item)
        if item_root.is_symlink():
            raise ValueError('Checkpoint cannot be a symbolic link.')
        read_revision_goals(item_root)
        evidence[item] = {'parent': revision_parent(ws, project, item),
                          'source_sha256': inventory(item_root)}
    return {'project': project, 'mission_ids': sorted(owned_ids), 'final_revision': revision,
            'base_revision': original, 'revisions': remove, 'evidence': evidence,
            'records_sha256': fingerprint(records), 'working_sha256': fingerprint(state),
            'final_source_sha256': inventory(root)}


def cleanup_checkpoints(ws, project, revision, approved_plan):
    """Move exactly the confirmed plan to recoverable Trash; never purge it."""
    with ExitStack() as stack:
        stack.enter_context(mission_lease(ws.project(project) / '.candidate-operation'))
        # Also excludes older running missions which do not yet use the project lease.
        for record in missions(ws, project):
            directory = ws.project(project) / 'missions' / record['id']
            if directory.exists():
                stack.enter_context(mission_lease(directory))
        stack.enter_context(mission_lease(ws.project(project) / '.acceptance'))
        stack.enter_context(mission_lease(ws.project(project) / '.revision-mutation'))
        current = cleanup_plan(ws, project, revision)
        if current != approved_plan:
            raise ValueError('Cleanup dependencies changed. Review the cleanup preview again.')
        if not current['revisions']:
            return 0
        trash = ws.project(project) / 'trash'
        if trash.is_symlink():
            raise ValueError('Project trash cannot be a symbolic link.')
        trash.mkdir(exist_ok=True)
        for name in current['revisions']:
            if (trash / name).exists():
                raise ValueError('A checkpoint already exists in trash; reconcile it before cleanup.')
        manifests = ws.project(project) / 'checkpoint-cleanups'
        if manifests.is_symlink():
            raise ValueError('Cleanup history cannot be a symbolic link.')
        manifests.mkdir(exist_ok=True)
        path = manifests / (uuid.uuid4().hex + '.json')
        manifest = dict(current, state='moving', moved=[], created=datetime.now(timezone.utc).isoformat())
        write_json(path, manifest)
        for name in current['revisions']:
            source = ws.revision(project, name)
            # Names came from the revision inventory, never from arbitrary paths.
            if source.resolve().parent != (ws.project(project) / 'revisions').resolve():
                raise ValueError('Checkpoint path escapes the revision directory.')
            source.rename(trash / name)
            manifest['moved'].append(name)
            write_json(path, manifest)
        manifest['state'] = 'complete'
        write_json(path, manifest)
        return len(manifest['moved'])
