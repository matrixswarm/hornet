"""Explicit per-project template and revision overlay; host inputs stay read-only."""
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
from .workspace import safe_path, source_files


def settings(ws, project):
    path = ws.project(project) / 'test-vm.json'
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) != {'template_directory', 'template_paths', 'revision_paths'}:
        raise ValueError('Project test-vm.json needs template_directory, template_paths and revision_paths.')
    root = Path(data['template_directory'])
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ValueError('VM template must be an existing absolute directory without symbolic links.')
    for key in ('template_paths', 'revision_paths'):
        if not isinstance(data[key], list) or not data[key]:
            raise ValueError('VM template and revision paths must be explicit nonempty lists.')
        for value in data[key]:
            safe_path(root, value)
        data[key] = [PurePosixPath(value).as_posix() for value in data[key]]
    return data


def selected(name, prefixes):
    return any(name == p or name.startswith(p + '/') for p in prefixes)


def apply_template(ws, project, files):
    config = settings(ws, project)
    if config is None:
        return files, None
    root = Path(config['template_directory'])
    copied, hashes, total = {}, {}, 0
    from .worker_vm import MAX_COPY_BYTES, MAX_FILES
    for name in source_files(root):
        if selected(name, config['template_paths']):
            path = safe_path(root, name)
            if len(copied) >= MAX_FILES or total + path.stat().st_size > MAX_COPY_BYTES:
                raise ValueError('VM template exceeds its file-count or copy-size limit.')
            data = path.read_bytes()
            total += len(data)
            if total > MAX_COPY_BYTES:
                raise ValueError('VM template changed or exceeds its copy-size limit.')
            copied[name] = base64.b64encode(data).decode()
            hashes[name] = hashlib.sha256(data).hexdigest()
    for prefix in config['template_paths']:
        if not any(selected(name, [prefix]) for name in copied):
            raise ValueError('VM template path has no source files: ' + prefix)
    overlay = {name: value for name, value in files.items() if selected(name, config['revision_paths'])}
    if not overlay:
        raise ValueError('Selected revision has no files matching its VM overlay paths.')
    copied.update(overlay)
    signature = hashlib.sha256(json.dumps({'config': config, 'files': hashes}, sort_keys=True).encode()).hexdigest()
    return copied, {'sha256': signature, 'template_directory': str(root), 'template_paths': config['template_paths'], 'revision_paths': config['revision_paths'], 'mode': 'overlay'}
