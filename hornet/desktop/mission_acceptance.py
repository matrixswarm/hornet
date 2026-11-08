"""Explicit candidate acceptance with idempotent crash bookkeeping recovery."""
import json
import re
from .mission_lease import mission_lease
from .revision_goals import read_revision_goals
from .workspace import read_blueprint, write_json


def accept_mission(ws, project, record):
    from .automation import require_ready, inventory, state_path
    identifier = record.get('id')
    if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise ValueError('Invalid mission identity.')
    with mission_lease(ws.project(project) / '.acceptance'):
        state = require_ready(ws, project)
        revision = record.get('final_revision')
        if record.get('project') != project or record.get('state') not in ('review', 'accepted'):
            raise ValueError('Only a completed mission awaiting review can be accepted.')
        if record.get('check_command') and not record.get('checks_passed'):
            raise ValueError('The configured check did not pass. Inspect the result before trying again.')
        already = state['accepted_revision'] == revision
        if (not already and state['accepted_revision'] != record.get('accepted_at_start')) or (record['state'] == 'accepted' and not already):
            raise ValueError('The accepted revision changed while this mission ran. Review it again.')
        path = ws.project(project) / 'missions' / (identifier + '.json')
        saved = json.loads(path.read_text(encoding='utf-8'))
        reviewed = json.loads(json.dumps(record))
        if saved.get('state') not in ('review', 'accepted'):
            raise ValueError('Saved mission is not awaiting review.')
        saved['state'] = reviewed['state'] = 'review'
        if saved != reviewed:
            raise ValueError('The saved mission changed after review. Review it again before accepting.')
        require_ready(ws, project, revision)
        root = ws.revision(project, revision)
        read_revision_goals(root)
        if inventory(root) != record.get('final_source_sha256') or read_blueprint(root) != record.get('final_blueprint'):
            raise ValueError('The candidate changed since review. Run the mission again.')
        if not already:
            state['accepted_revision'] = revision
            write_json(state_path(ws, project), state)
        accepted = dict(record, state='accepted')
        write_json(path, accepted)
        record['state'] = 'accepted'
        return revision
