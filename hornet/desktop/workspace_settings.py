"""Desktop workspace preference, separate from per-workspace model settings."""
import json
from pathlib import Path
from .workspace import write_json


def settings_path():
    return Path.home() / '.hornet-coder.json'


def validate_root(value):
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Choose an existing folder.')
    # A workspace contains projects/, not a single project or revision.
    if (root / '.hornet-meta').exists() or (root / 'revisions').exists() or root.name == 'projects':
        raise ValueError('Choose the workspace folder above projects, not a project or revision folder.')
    projects = root / 'projects'
    if projects.exists() and (not projects.is_dir() or projects.is_symlink()):
        raise ValueError('The projects entry must be a regular folder.')
    return root


def save_root(value, path=None):
    root = validate_root(value)
    path = Path(path) if path is not None else settings_path()
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(settings, dict):
            raise ValueError('Coder settings must be a JSON object; existing settings were not changed.')
    except FileNotFoundError:
        settings = {}
    settings['workspace'] = str(root)
    write_json(path, settings)
    return root


def startup_root(override=None, path=None):
    if override:
        return override
    path = Path(path) if path is not None else settings_path()
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(settings, dict):
        raise ValueError('Coder settings must be a JSON object.')
    value = settings.get('workspace')
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Saved workspace path is invalid.')
    return validate_root(value)
