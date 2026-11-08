"""Read-only presentation of a stopped mission's current checkpoint."""
import re
from .mission_checkpoint import read_json


def details(ws, project, record):
    reason = record.get('error') or 'The mission stopped before completing.'
    result = {'reason': str(reason), 'step': record.get('checkpoint', 'Saved mission'), 'can_resume': False}
    identifier = record.get('checkpoint_directory') or record.get('id')
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', identifier):
        return result
    try:
        state = read_json(ws.project(project) / 'missions' / identifier / 'state.json')
        if not isinstance(state, dict) or not record.get('identity') or state.get('identity') != record.get('identity'):
            return result
        node = state.get('nodes', {}).get(state.get('pointer'), {})
        result['step'] = node.get('label') or state.get('pointer') or result['step']
        result['reason'] = str(node.get('reconciliation', {}).get('evidence', {}).get('error') or reason)
        result['can_resume'] = bool(record.get('resumable') and node.get('read_only') is True
                                    and node.get('status') in ('failed', 'cancelled'))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return result


def current(app):
    if app.busy or not app.project or not app.revision:
        return None
    from .working_revision import is_working, pending_mission_record
    try:
        if not is_working(app.ws, app.project, app.revision):
            return None
        record = pending_mission_record(app.ws, app.project, app.revision)
        if not record or record.get('project') != app.project or record.get('state') not in ('failed', 'cancelled'):
            return None
        result = details(app.ws, app.project, record)
        result['record'] = record
        return result
    except (OSError, ValueError):
        return None


def review_text(ws, project, record):
    info = details(ws, project, record)
    return ('Needs attention\n\n' + str(info['step']) + '\n' + info['reason'] +
            '\n\nWhat do you want to do here?\n' +
            ('Resume from the saved checkpoint.\n' if info['can_resume'] else
             'Resume is unavailable until the saved outcome is inspected and reconciled.\n') +
            'Keep the partial files and describe a new mission, or discard this working revision.\n'
            'Starting a new mission or discarding requires confirmation. Recovery history remains.\n\n')
