"""Explicit read-only planning context, separate from authorized edit paths."""
import json

from .workspace import MAX_CONTEXT


def validate_context_files(plan):
    names = plan.get('context_files', [])
    existing, new = plan.get('existing_files', []), plan.get('new_files', [])
    if not isinstance(existing, list) or not isinstance(new, list):
        raise ValueError('Editable file scope must use lists.')
    editable = existing + new
    if any(not isinstance(name, str) for name in editable):
        raise ValueError('Editable file paths must be strings.')
    if (not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names) or len(names) + len(editable) > 20
            or set(names).intersection(editable)):
        raise ValueError('context_files must be distinct read-only paths, separate from existing_files/new_files, within 20 total paths.')
    return names


def mission_context(ws, project, revision, selected, mission, new_files=None, *, context_files=(), scoped_shadows=False):
    # Keep the legacy context byte-for-byte equivalent when no references exist.
    if set(context_files).intersection(list(selected) + list(new_files or [])):
        raise ValueError('Read-only context cannot overlap editable paths.')
    context = ws.mission_context(project, revision, list(selected) + list(context_files),
                                 mission, new_files, scoped_shadows=scoped_shadows)
    if context_files:
        context['read_only_files'] = {name: context['files'].pop(name) for name in context_files}
        if len(json.dumps(context).encode('utf-8')) > MAX_CONTEXT:
            raise ValueError('Context exceeds 240 KB. Select fewer files or split the project mission.')
    return context
