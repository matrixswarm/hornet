"""Stable revision colors and user-visible labels, stored outside revisions."""
import colorsys
import hashlib
import json
from pathlib import Path
import re

from .workspace import write_json


def _color(revision, used):
    # Derive pleasant, visible colors and avoid exact collisions within a project.
    for attempt in range(1024):
        digest = hashlib.sha256((revision + ':' + str(attempt)).encode()).digest()
        hue = int.from_bytes(digest[:2], 'big') / 65536
        saturation = .65 + digest[2] / 255 * .28
        value = .66 + digest[3] / 255 * .23
        rgb = colorsys.hsv_to_rgb(hue, saturation, value)
        color = '#' + ''.join(f'{round(component * 255):02x}' for component in rgb)
        if color not in used:
            return color
    raise ValueError('Could not assign a distinct revision color.')


def revision_parent(ws, project, revision):
    path = ws.revision(project, revision) / '.hornet-meta' / 'revision.json'
    value = json.loads(path.read_text(encoding='utf-8')).get('parent')
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError('Invalid parent revision metadata.')
    return value


def load_display(ws, project):
    path = ws.project(project) / 'revision-display.json'
    if path.is_symlink():
        raise ValueError('Revision display metadata cannot be a symbolic link.')
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Revision display metadata is invalid.')
    for key, value in data.items():
        if (not isinstance(key, str) or not isinstance(value, dict)
                or not isinstance(value.get('name', ''), str)
                or not isinstance(value.get('color'), str)
                or not re.fullmatch(r'#[0-9a-f]{6}', value['color'])):
            raise ValueError('Revision display metadata is invalid.')
    return data


def ensure_display(ws, project):
    records = load_display(ws, project)
    revisions = ws.revisions(project)
    parents = {revision_parent(ws, project, rev) for rev in revisions}
    all_ids = set(revisions) | {p for p in parents if p is not None}
    used = {value['color'] for value in records.values()}
    changed = False
    for revision in sorted(all_ids):
        if revision not in records:
            color = _color(revision, used)
            records[revision] = {'name': '', 'color': color}
            used.add(color)
            changed = True
    if changed:
        write_json(ws.project(project) / 'revision-display.json', records)
    return records


def rename_revision(ws, project, revision, name):
    ws.revision(project, revision)
    if not isinstance(name, str):
        raise ValueError('Revision name must be text.')
    name = name.strip()
    if len(name) > 80 or any(not c.isprintable() for c in name):
        raise ValueError('Use a single-line revision name of up to 80 characters.')
    if '[accepted]' in name.lower():
        raise ValueError('The [ACCEPTED] marker is reserved for Hornet.')
    records = ensure_display(ws, project)
    records[revision]['name'] = name
    write_json(ws.project(project) / 'revision-display.json', records)
    return name
