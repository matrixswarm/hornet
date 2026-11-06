"""Reviewable, resumable project initialization without executing project code."""
import hashlib
import json
from pathlib import Path

from .workspace import read_blueprint, safe_path, write_json

MAX_INITIALIZATION_FILE = 1_048_576
MAX_CHUNK_JSON_BYTES = 24_000


def _chunks(text):
    # Bound escaped JSON size as well as raw UTF-8; preserve every character.
    if not text:
        return ['']
    result = []
    start = 0
    while start < len(text):
        low, high = 1, min(len(text) - start, MAX_CHUNK_JSON_BYTES)
        while low < high:
            mid = (low + high + 1) // 2
            size = len(json.dumps(text[start:start + mid]).encode('utf-8'))
            if size <= MAX_CHUNK_JSON_BYTES:
                low = mid
            else:
                high = mid - 1
        result.append(text[start:start + low])
        start += low
    return result


def _read_chunks(root, name):
    path = safe_path(root, name)
    if path.stat().st_size > MAX_INITIALIZATION_FILE:
        raise ValueError('File exceeds the 1 MB initialization limit.')
    data = path.read_bytes()
    text = data.decode('utf-8')
    if '\x00' in text:
        raise ValueError('Binary content is not supported.')
    return _chunks(text), hashlib.sha256(data).hexdigest()


def _apply_blueprint(blueprint, proposed):
    if proposed is None:
        return
    if not isinstance(proposed, dict):
        raise ValueError('Initialization returned an invalid blueprint.')
    for key in ('purpose', 'architecture', 'directory_roles'):
        if key not in proposed:
            continue
        value = proposed[key]
        if key == 'directory_roles':
            if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
                raise ValueError('Initialization returned invalid directory roles.')
            existing = blueprint.get(key, {})
            blueprint[key] = {**(existing if isinstance(existing, dict) else {}), **value}
        elif isinstance(value, str) and value.strip():
            blueprint[key] = value.strip()
        else:
            raise ValueError('Initialization returned invalid blueprint text.')


def make_draft(ws, project, revision, describe, progress=None, included=None, resume=True):
    from .automation import inventory, read_state, require_ready
    root = ws.revision(project, revision)
    state = read_state(ws, project)
    if state['status'] == 'ready':
        require_ready(ws, project, revision)
    before = inventory(root)
    if included is None:
        from .file_selection import default_excluded
        included = sorted(n for n in before if not default_excluded(n))
    if not isinstance(included, (list, tuple, set)) or any(not isinstance(n, str) for n in included):
        raise ValueError('Choose the files to include in initialization.')
    included = sorted(set(included))
    if not included or not set(included).issubset(before):
        raise ValueError('Choose at least one existing file to initialize.')
    excluded = sorted(set(before) - set(included))
    blueprint = read_blueprint(root)
    original_blueprint = json.loads(json.dumps(blueprint))
    draft_path = ws.project(project) / 'initialization-draft.json'
    accepted = state.get('accepted_revision') if state['status'] == 'ready' else None
    draft = None
    source_changed = False
    if resume and draft_path.is_file():
        candidate = json.loads(draft_path.read_text(encoding='utf-8'))
        compatible = (candidate.get('version') == 2 and candidate.get('project') == project
                      and candidate.get('revision') == revision
                      and candidate.get('base_blueprint') == original_blueprint
                      and candidate.get('included') == included and candidate.get('accepted_at_start') == accepted)
        if compatible and candidate.get('source_sha256') == before:
            draft = candidate
        elif compatible:
            # Older drafts retain only the merged blueprint, not which file
            # produced each update. Reusing individual summaries after a file
            # changes would either lose unaffected blueprint facts or retain
            # stale facts from the changed file. Rebuild the map instead.
            source_changed = True
    if draft is None:
        if draft_path.is_file():
            import uuid
            history = ws.project(project) / 'initialization-drafts'
            history.mkdir(exist_ok=True)
            previous = json.loads(draft_path.read_text(encoding='utf-8'))
            write_json(history / (uuid.uuid4().hex + '.json'), previous)
        draft = {'version': 2, 'project': project, 'revision': revision,
                 'source_sha256': before, 'base_blueprint': original_blueprint,
                 'accepted_at_start': accepted, 'blueprint': blueprint,
                 'included': included, 'excluded': excluded, 'summaries': {},
                 'chunks': {}, 'unreviewed': list(included), 'issues': {}}
        if source_changed:
            draft['review_notes'] = ['Project files changed, so initialization restarted for all included files; the previous draft was archived.']
        write_json(draft_path, draft)
        if source_changed and progress:
            progress('Project files changed; restarting initialization for all included files. Previous draft archived.')
    shadows = json.loads((root / '.hornet-meta' / 'shadows.json').read_text(encoding='utf-8'))
    # Do not send excluded file names or hashes in the inventory.
    sent_inventory = {name: before[name] for name in included}
    for index, name in enumerate(included, 1):
        try:
            parts, digest = _read_chunks(root, name)
            if digest != before[name]:
                raise ValueError('File changed during initialization.')
        except (OSError, UnicodeError, ValueError) as exc:
            draft['issues'][name] = str(exc)
            draft['summaries'].pop(name, None)
            draft['unreviewed'] = sorted(set(included) - set(draft['summaries']))
            write_json(draft_path, draft)
            continue
        chunk_state = draft['chunks'].setdefault(name, {})
        for number, part in enumerate(parts, 1):
            key = str(number)
            if key in chunk_state:
                continue
            if progress:
                progress(f'Initializing file {index} of {len(included)}: {name}, part {number} of {len(parts)}')
            payload = {'project': project, 'blueprint': draft['blueprint'],
                       'inventory': sent_inventory, 'structural_shadows': {name: shadows.get(name, {})},
                       'files': {name: part}, 'batch': index, 'total_batches': len(included),
                       'file_part': {'path': name, 'number': number, 'total': len(parts)}}
            response = describe(payload)
            if not isinstance(response, dict) or not isinstance(response.get('summaries'), dict):
                raise ValueError('Initialization returned an invalid summary batch; progress was saved.')
            summaries = response['summaries']
            if any(n != name for n in summaries):
                raise ValueError('Initialization returned a summary for an unrequested file; progress was saved.')
            summary = summaries.get(name)
            if not isinstance(summary, str) or not summary.strip() or len(summary) > 4000:
                draft['issues'][name] = f'Model did not provide a valid summary for part {number} of {len(parts)}.'
                write_json(draft_path, draft)
                break
            _apply_blueprint(draft['blueprint'], response.get('blueprint'))
            chunk_state[key] = summary.strip()
            draft['issues'].pop(name, None)
            write_json(draft_path, draft)
        if len(chunk_state) == len(parts):
            # Keep a bounded summary while retaining all part descriptions in the draft.
            budget = max(1, (3900 // len(parts)) - 16)
            draft['summaries'][name] = '\n'.join(
                (f'Part {i}: ' if len(parts) > 1 else '') + chunk_state[str(i)][:budget]
                for i in range(1, len(parts) + 1))[:4000]
            draft['issues'].pop(name, None)
        else:
            draft['summaries'].pop(name, None)
        draft['unreviewed'] = sorted(set(included) - set(draft['summaries']))
        write_json(draft_path, draft)
    if inventory(root) != before or read_blueprint(root) != original_blueprint:
        raise ValueError('The project changed during initialization; choose Initialize project to start a fresh draft.')
    draft['blueprint']['status'] = 'agent draft; review before accepting'
    draft['unreviewed'] = sorted(set(included) - set(draft['summaries']))
    write_json(draft_path, draft)
    return draft


def verify_chunks(root, draft):
    for name in draft['included']:
        try:
            parts, digest = _read_chunks(root, name)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError('Cannot verify included file: ' + name) from exc
        chunk_state = draft.get('chunks', {}).get(name)
        if (digest != draft['source_sha256'][name] or not isinstance(chunk_state, dict)
                or set(chunk_state) != {str(i) for i in range(1, len(parts) + 1)}
                or any(not isinstance(value, str) or not value.strip() for value in chunk_state.values())):
            raise ValueError('Initialization has incomplete file parts: ' + name)


def validate_draft(draft):
    if draft.get('version') != 2:
        raise ValueError('This older project map cannot be verified. Run initialization again.')
    included, excluded, sources, summaries = (draft.get('included'), draft.get('excluded'),
                                               draft.get('source_sha256'), draft.get('summaries'))
    if (not isinstance(included, list) or not included or not isinstance(excluded, list)
            or not isinstance(sources, dict) or not isinstance(summaries, dict)
            or len(set(included)) != len(included) or len(set(excluded)) != len(excluded)
            or set(included) & set(excluded) or set(included) | set(excluded) != set(sources)
            or set(summaries) != set(included)
            or any(not isinstance(v, str) or not v.strip() or len(v) > 4000 for v in summaries.values())
            or draft.get('unreviewed') or draft.get('issues')):
        raise ValueError('Initialization is incomplete. Retry incomplete files or change the selection.')
    if not isinstance(draft.get('blueprint'), dict) or not draft['blueprint']:
        raise ValueError('The project blueprint is invalid.')
