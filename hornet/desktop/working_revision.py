"""Explicit working revision identity over immutable mission snapshots.

No write-on-read migration: pre-existing revisions are preserved and locked in
Desktop until the user explicitly creates a working copy.
"""
from datetime import datetime, timezone
from contextlib import ExitStack
import re

from .mission_checkpoint import read_json, write_json
from .mission_lease import project_operation, mission_lease


def load_working(ws, project):
    path = ws.project(project) / 'working-revisions.json'
    if not path.exists():
        return None
    value = read_json(path)
    if (not isinstance(value, dict) or value.get('version') != 1
            or not isinstance(value.get('sessions'), dict)
            or (value.get('current') is not None and value['current'] not in value['sessions'])):
        raise ValueError('Invalid working revision state; inspect it before continuing.')
    for key, session in value['sessions'].items():
        if (not isinstance(key, str) or not isinstance(session, dict)
                or not isinstance(session.get('head'), str) or not isinstance(session.get('source'), str)
                or not isinstance(session.get('missions'), list)
                or any(not isinstance(item, str) or not re.fullmatch(r'[0-9a-f]{32}', item) for item in session['missions'])
                or (session.get('pending') is not None and (not isinstance(session['pending'], str) or not re.fullmatch(r'[0-9a-f]{32}', session['pending'])))
                or not isinstance(session.get('superseded', []), list)
                or any(not isinstance(item, str) or not re.fullmatch(r'[0-9a-f]{32}', item) for item in session.get('superseded', []))
                or (session.get('recovery_base') is not None and not isinstance(session['recovery_base'], str))
                or (session.get('recovery_id') is not None and (not isinstance(session['recovery_id'], str) or not re.fullmatch(r'[0-9a-f]{32}', session['recovery_id'])))):
            raise ValueError('Invalid working revision session.')
        if (not isinstance(session.get('abandoned', {}), dict)
                or any(not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier)
                       or not isinstance(evidence, dict) or not isinstance(evidence.get('record_sha256'), str)
                       for identifier, evidence in session.get('abandoned', {}).items())
                or (session.get('pending_base') is not None and not isinstance(session['pending_base'], str))):
            raise ValueError('Invalid working revision closure evidence.')
    return value


def _sync_markers(ws, project, state):
    """Hidden markers do not participate in source/checkpoint hashes.

Only a controller-owned marker on the current head may be cleared. An external
.LOCK remains an explicit lock, even if the session metadata names this head.
"""
    session = _session(state)
    head = session['head'] if session else None
    for revision in ws.revisions(project):
        root = ws.revision(project, revision)
        if root.is_symlink():
            raise ValueError('Revision folders cannot be symbolic links.')
        marker = root / '.LOCK'
        if marker.is_symlink():
            raise ValueError('A revision lock marker cannot be a symbolic link.')
        if revision == head:
            if marker.exists():
                try:
                    owned = read_json(marker).get('owner') == 'hornet-working-revision'
                except (ValueError, AttributeError):
                    owned = False
                if owned:
                    marker.unlink()
            continue
        if not marker.exists():
            write_json(marker, {'owner': 'hornet-working-revision',
                                'note': 'Locked: create a working revision to run missions. Not an OS file permission.'})


def _save(ws, project, state):
    write_json(ws.project(project) / 'working-revisions.json', state)
    _sync_markers(ws, project, state)


def _session(state):
    return state['sessions'].get(state.get('current')) if state else None


def require_working(ws, project, revision):
    state = load_working(ws, project)
    session = _session(state)
    if not session or session['head'] != revision:
        raise ValueError('This revision is locked. Create a working revision from it before running a mission.')
    root = ws.revision(project, revision)
    if (root / '.LOCK').exists() or (root / '.LOCK').is_symlink():
        raise ValueError('This revision has a .LOCK marker. Create a new working revision before running a mission.')
    return session


def is_working(ws, project, revision):
    try:
        require_working(ws, project, revision)
        return True
    except (OSError, ValueError, TypeError):
        return False


def display_revision_id(ws, project, revision):
    try:
        state = load_working(ws, project)
        for key, session in (state or {}).get('sessions', {}).items():
            if session['head'] == revision:
                return key
    except (OSError, ValueError, TypeError):
        pass
    return revision


def superseded_without_edits(record):
    return (not record.get('plan_approval') and not record.get('candidates')
            and (record.get('state') == 'plan_review'
                 or (record.get('state') == 'failed' and record.get('require_plan_confirmation'))))


def _can_replace_plan(ws, project, pending, original_id):
    if not isinstance(original_id, str) or not re.fullmatch(r'[0-9a-f]{32}', original_id):
        return False
    previous = read_json(ws.project(project) / 'missions' / (pending + '.json'))
    original = read_json(ws.project(project) / 'missions' / (original_id + '.json'))
    if original.get('state') != 'plan_review' or not superseded_without_edits(original):
        return False
    return (superseded_without_edits(previous)
            and (pending == original_id
                 or (previous.get('state') == 'failed' and previous.get('counter_plan_of') == original_id)))


def _recover_binding(ws, project, state):
    """Rebind a unique durable intent before permitting any competing action."""
    from .candidate_view import missions
    session = _session(state)
    if not session:
        return
    orphans = [record for record in missions(ws, project)
               if record.get('working_revision_id') == state['current']
               and record.get('resumable') and record['id'] not in session['missions']
               and not is_abandoned(ws, project, record['id'])]
    if not orphans:
        return
    if len(orphans) != 1:
        raise ValueError('Multiple unbound missions need reconciliation before this revision can change.')
    record = orphans[0]
    if record.get('base_revision') != session.get('pending_base', session.get('recovery_base', session['head'])):
        raise ValueError('Unbound mission does not match the working revision; reconcile it first.')
    pending = session.get('pending')
    if pending is not None:
        if not _can_replace_plan(ws, project, pending, record.get('counter_plan_of')):
            raise ValueError('Conflicting unbound mission needs reconciliation.')
        session.setdefault('superseded', []).append(pending)
    session['pending'] = record['id']
    session['pending_base'] = record['base_revision']
    session['missions'].append(record['id'])
    if session.get('recovery_base') is not None:
        session['recovery_id'] = record['id']
    _save(ws, project, state)


def _reconcile(ws, project, state):
    """Expose every verified durable publication, retaining a fixed resume base."""
    from .automation import inventory
    from .workspace import read_blueprint
    from .revision_goals import read_revision_goals
    _recover_binding(ws, project, state)
    session = _session(state)
    if not session or not session.get('pending'):
        if state:
            _sync_markers(ws, project, state)
        return
    identifier = session['pending']
    record = read_json(ws.project(project) / 'missions' / (identifier + '.json'))
    origin = session.get('pending_base', session.get('recovery_base', session['head']))
    if ((record.get('working_revision_id') != state['current'] and session.get('recovery_id') != identifier)
            or record.get('id') != identifier or record.get('base_revision') != origin
            or record.get('project') != project):
        raise ValueError('Working revision recovery does not match its mission.')
    session['pending_base'] = origin
    parent = origin
    latest = None
    for revision in record.get('candidates', []):
        root = ws.revision(project, revision)
        snapshot = read_revision_goals(root)
        operation = snapshot.get('operation') or {}
        if (snapshot.get('revision') != revision or snapshot.get('parent_revision') != parent
                or operation.get('operation_id') != identifier):
            raise ValueError('Partial working files do not match this mission; reconcile before continuing.')
        parent = latest = revision
    if record.get('state') in ('review', 'accepted'):
        revision = record.get('final_revision')
        if revision != latest:
            raise ValueError('Final working revision does not match its publication history.')
        root = ws.revision(project, revision)
        if inventory(root) != record.get('final_source_sha256') or read_blueprint(root) != record.get('final_blueprint'):
            raise ValueError('Completed candidate changed; inspect it before recovering the working revision.')
        session.update(head=revision, pending=None)
        session.pop('pending_base', None)
        session.pop('recovery_base', None)
        session.pop('recovery_id', None)
    elif latest is not None:
        session['head'] = latest
    _save(ws, project, state)


def _not_pending(state):
    session = _session(state)
    if session and session.get('pending'):
        raise ValueError('This working revision has unfinished work. Resume or reconcile that mission before locking or creating another revision.')


@project_operation
def create_working_revision(ws, project, source_revision):
    from .candidate_view import missions
    state = load_working(ws, project) or {'version': 1, 'current': None, 'sessions': {}}
    _reconcile(ws, project, state)
    _not_pending(state)
    # Preserve legacy recovery too: a new working pointer must not strand an
    # unresolved older mission. No existing operation is cancelled implicitly.
    superseded = {m for entry in state['sessions'].values() for m in entry.get('superseded', [])}
    unfinished = [record for record in missions(ws, project)
                  if record.get('resumable') and record.get('state') not in ('review', 'accepted')
                  and not is_abandoned(ws, project, record['id'])
                  and not (record['id'] in superseded and superseded_without_edits(record))]
    recovery = None
    if unfinished:
        if (len(unfinished) != 1 or unfinished[0].get('base_revision') != source_revision
                or unfinished[0].get('working_revision_id') is not None
                or not isinstance(unfinished[0].get('identity'), dict)):
            raise ValueError('Unfinished work needs reconciliation. Select its original base to create a recovery working revision; ambiguous missions are never chosen automatically.')
        recovery = unfinished[0]
    with ExitStack() as stack:
        if recovery:
            stack.enter_context(mission_lease(ws.project(project) / 'missions' / recovery['id']))
        revision = ws.fork(project, source_revision, mission='Create working revision')
        marker = ws.revision(project, revision) / '.LOCK'
        if marker.exists():
            marker.unlink()
        state['sessions'][revision] = {'source': source_revision, 'head': revision, 'missions': [],
                                       'pending': None, 'created': datetime.now(timezone.utc).isoformat()}
        if recovery:
            state['sessions'][revision].update(pending=recovery['id'], missions=[recovery['id']],
                                               recovery_id=recovery['id'], recovery_base=source_revision)
        state['current'] = revision
        _save(ws, project, state)
        return revision



@project_operation
def lock_working_revision(ws, project, revision):
    state = load_working(ws, project)
    _reconcile(ws, project, state)
    _not_pending(state)
    require_working(ws, project, revision)
    state['current'] = None
    _save(ws, project, state)


def check_mission_target(ws, project, revision, resume_id=None, counter_plan_of=None):
    """Called under the project lease, before creating any mission effects.

Legacy low-level callers retain their original checkpoint contract until an
explicit working session is created. Desktop always calls require_working.
"""
    state = load_working(ws, project)
    if state is None:
        marker = ws.revision(project, revision) / '.LOCK'
        if marker.exists() or marker.is_symlink():
            raise ValueError('This revision has a .LOCK marker. Create a working revision before running a mission.')
        return None
    _reconcile(ws, project, state)
    if resume_id is not None and is_abandoned(ws, project, resume_id):
        raise ValueError('This mission was explicitly closed. Start a new mission from the working files instead.')
    session = _session(state)
    if (session and session.get('pending_base', session.get('recovery_base')) == revision
            and session.get('pending')):
        require_working(ws, project, session['head'])
        if (resume_id == session['pending']
                or _can_replace_plan(ws, project, session['pending'], counter_plan_of)):
            return state['current']
    session = require_working(ws, project, revision)
    pending = session.get('pending')
    if pending is not None and pending != resume_id:
        if not _can_replace_plan(ws, project, pending, counter_plan_of):
            raise ValueError('Resume the unfinished mission on this working revision before starting another.')
    if resume_id is not None and pending != resume_id:
        saved = read_json(ws.project(project) / 'missions' / (resume_id + '.json'))
        if (pending is not None or saved.get('working_revision_id') != state['current']
                or saved.get('base_revision') != revision or saved.get('id') != resume_id
                or saved.get('project') != project or not saved.get('resumable')):
            raise ValueError('This mission does not own the current working revision.')
    return state['current']


def bind_mission(ws, project, record, working_id):
    if working_id is None:
        return
    state = load_working(ws, project)
    if state.get('current') != working_id:
        raise ValueError('Working revision changed before the mission started.')
    session = state['sessions'][working_id]
    pending = session.get('pending')
    if (session.get('pending_base', session.get('recovery_base', session['head'])) != record['base_revision']
            or (pending not in (None, record['id'])
                and not _can_replace_plan(ws, project, pending, record.get('counter_plan_of')))):
        raise ValueError('Working revision is already processing another mission.')
    if pending is not None and pending != record['id']:
        session.setdefault('superseded', []).append(pending)
    session['pending'] = record['id']
    session['pending_base'] = record['base_revision']
    if session.get('recovery_base') is not None:
        session['recovery_id'] = record['id']
    if record['id'] not in session['missions']:
        session['missions'].append(record['id'])
    _save(ws, project, state)


def finish_mission(ws, project):
    state = load_working(ws, project)
    if state:
        _reconcile(ws, project, state)


def visible_revisions(ws, project, show_checkpoints=False):
    from .candidate_view import missions, _owned
    from .automation import read_state
    from .revision_display import load_display, revision_parent
    revisions = ws.revisions(project)
    if show_checkpoints:
        return revisions
    try:
        state = load_working(ws, project)
        records = missions(ws, project)
        by_id = {r['id']: r for r in records}
        keep = {read_state(ws, project).get('accepted_revision')}
        keep.update(r for r, value in load_display(ws, project).items() if value.get('name'))
        hidden = set()
        managed = set()
        for anchor, session in (state or {}).get('sessions', {}).items():
            keep.add(session['source'])
            if not session.get('discarded'):
                keep.add(session['head'])
            hidden.add(anchor)
            for identifier in session['missions']:
                managed.add(identifier)
                record = by_id[identifier]
                hidden.update(_owned(ws, project, record, revisions))
        # Legacy missions remain recoverable. Collapse only proven internal
        # snapshots, keeping their original/final or latest candidate.
        for record in records:
            if record['id'] in managed:
                continue
            keep.add(record.get('base_revision'))
            owned = _owned(ws, project, record, revisions)
            final = record.get('final_revision')
            keep.add(final if final in owned else (owned[-1] if owned else None))
            hidden.update(owned)
        for revision in revisions:
            if revision not in hidden:
                keep.add(revision_parent(ws, project, revision))
        return [r for r in revisions if r not in hidden or r in keep]
    except (OSError, ValueError, TypeError, KeyError):
        return revisions


def resume_base(ws, project, revision):
    session = require_working(ws, project, revision)
    return session.get('pending_base', session.get('recovery_base', revision))


def assert_trash_safe(ws, project, revision):
    """Shared guard for ordinary Trash, not just checkpoint cleanup."""
    from .candidate_view import missions
    from .automation import read_state
    if read_state(ws, project).get('accepted_revision') == revision:
        raise ValueError('The default revision cannot be moved to Trash.')
    state = load_working(ws, project)
    for session in (state or {}).get('sessions', {}).values():
        if revision in (session['head'], session['source'], session.get('pending_base'), session.get('recovery_base')):
            raise ValueError('This revision is required by working/locked revision history; use checkpoint cleanup for unused snapshots.')
    for record in missions(ws, project):
        protected = {record.get('base_revision'), record.get('final_revision')}
        if record.get('resumable'):
            protected.update(record.get('candidates', []))
        if revision in protected:
            raise ValueError('This revision is required by saved mission review or recovery.')
    draft = ws.project(project) / 'initialization-draft.json'
    if draft.exists() and read_json(draft).get('revision') == revision:
        raise ValueError('This revision is required by an initialization draft.')


@project_operation
def refresh_working_revision(ws, project):
    state = load_working(ws, project)
    if state is None:
        return None
    with ExitStack() as stack:
        session = _session(state)
        if session and session.get('pending'):
            stack.enter_context(mission_lease(ws.project(project) / 'missions' / session['pending']))
        _reconcile(ws, project, state)
    session = _session(state)
    return session['head'] if session else None


def pending_mission_record(ws, project, revision):
    session = require_working(ws, project, revision)
    identifier = session.get('pending')
    if not identifier:
        return None
    return read_json(ws.project(project) / 'missions' / (identifier + '.json'))


def _abandoned_records(state):
    result = {}
    for session in (state or {}).get('sessions', {}).values():
        result.update(session.get('abandoned', {}))
    return result


def is_abandoned(ws, project, identifier):
    state = load_working(ws, project)
    evidence = _abandoned_records(state).get(identifier)
    if evidence is None:
        return False
    from .mission_checkpoint import fingerprint
    record = read_json(ws.project(project) / 'missions' / (identifier + '.json'))
    if fingerprint(record) != evidence.get('record_sha256'):
        raise ValueError('Abandoned mission evidence changed; inspect it before continuing.')
    return True


@project_operation
def abandon_working_revision(ws, project, revision, discard=False):
    """Explicitly stop resuming this mission; preserve all files and evidence."""
    from .mission_checkpoint import fingerprint
    state = load_working(ws, project)
    session = _session(state)
    if not session:
        raise ValueError('Select the current working revision.')
    with ExitStack() as stack:
        if session.get('pending'):
            stack.enter_context(mission_lease(ws.project(project) / 'missions' / session['pending']))
        _reconcile(ws, project, state)
        require_working(ws, project, revision)
        session = _session(state)
        identifier = session.get('pending')
        if identifier:
            record = read_json(ws.project(project) / 'missions' / (identifier + '.json'))
            session.setdefault('abandoned', {})[identifier] = {
                'record_sha256': fingerprint(record), 'head': session['head'],
                'base_revision': record['base_revision'], 'discarded': bool(discard),
                'closed_at': datetime.now(timezone.utc).isoformat()}
            session['pending'] = None
            session.pop('pending_base', None)
            session.pop('recovery_base', None)
            session.pop('recovery_id', None)
        if discard:
            session['discarded'] = True
            state['current'] = None
        _save(ws, project, state)
        return session['head']
