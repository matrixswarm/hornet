"""Bounded model-authored mission decomposition and explicit plan approval."""
from datetime import datetime, timezone
import re

from .mission_checkpoint import fingerprint, read_json, write_json
from .mission_lease import mission_lease

MAX_GOALS = 40
MAX_DEPTH = 5


def validate_goals(plan, required=False):
    """Return a normalized tree; model labels never count as completion evidence."""
    if not isinstance(plan, dict):
        raise ValueError('The agent plan must be an object.')
    from .plan_context import validate_context_files
    validate_context_files(plan)
    raw = plan.get('goals')
    if raw is None and not required:
        # Compatibility for older noninteractive API clients, never GUI plan approval.
        return []
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_GOALS:
        raise ValueError('The agent plan needs 1 to 40 bounded goals. Narrow the mission or decompose it further.')
    existing, new = plan.get('existing_files'), plan.get('new_files')
    if (not isinstance(existing, list) or not isinstance(new, list)
            or any(not isinstance(name, str) or not name for name in existing + new)
            or not 1 <= len(existing + new) <= 20
            or len(set(existing + new)) != len(existing + new)):
        raise ValueError('Agent plan file scope must contain 1 to 20 distinct paths.')
    allowed = set(existing + new)
    nodes = {}
    for item in raw:
        if not isinstance(item, dict) or set(item) != {'id', 'parent', 'goal', 'files', 'success_criteria', 'depends_on'}:
            raise ValueError('Every goal needs id, parent, goal, files, success_criteria, and depends_on.')
        identifier = item['id']
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', identifier):
            raise ValueError('Goal IDs must be stable lowercase identifiers of at most 48 characters.')
        if identifier in nodes:
            raise ValueError('Agent plan repeats a goal ID: ' + identifier)
        if item['parent'] is not None and not isinstance(item['parent'], str):
            raise ValueError('Goal parent must be an ID or null.')
        for field in ('goal', 'success_criteria'):
            if not isinstance(item[field], str) or not 8 <= len(item[field].strip()) <= 1500:
                raise ValueError('Each goal needs concrete text and observable success criteria (8 to 1500 characters).')
        files, dependencies = item['files'], item['depends_on']
        if (not isinstance(files, list) or len(files) > 20 or any(not isinstance(p, str) for p in files)
                or len(set(files)) != len(files) or not set(files).issubset(allowed)):
            raise ValueError('Goal files must be a distinct bounded subset of the approved file scope.')
        if (not isinstance(dependencies, list) or any(not isinstance(p, str) for p in dependencies)
                or len(set(dependencies)) != len(dependencies) or len(dependencies) > MAX_GOALS):
            raise ValueError('Goal dependencies must be distinct goal IDs.')
        nodes[identifier] = dict(item, goal=item['goal'].strip(), success_criteria=item['success_criteria'].strip(),
                                 files=list(files), depends_on=list(dependencies))
    roots = [n for n in nodes.values() if n['parent'] is None]
    if len(roots) != 1:
        raise ValueError('The agent plan must have exactly one root goal. Found ' + str(len(roots)) +
                         ' roots: ' + ', '.join(node['id'] for node in roots)[:500])
    for identifier, item in nodes.items():
        seen, current, depth = set(), identifier, 0
        while current is not None:
            if current not in nodes:
                raise ValueError('Agent plan names a missing parent goal.')
            if current in seen:
                raise ValueError('Agent plan contains a parent cycle.')
            seen.add(current)
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError('Agent goal tree exceeds depth 5. Narrow the mission.')
            current = nodes[current]['parent']
    parents = {n['parent'] for n in nodes.values()}
    leaves = {identifier for identifier in nodes if identifier not in parents}
    if len(leaves) > 20:
        raise ValueError('Agent plan exceeds 20 actionable leaves. Narrow the mission to fit the edit budget.')
    covered = set()
    for identifier, item in nodes.items():
        if identifier in leaves:
            if not item['files']:
                raise ValueError('Every leaf goal needs concrete files within the approved scope.')
            covered.update(item['files'])
        elif item['depends_on']:
            raise ValueError('Dependencies belong to actionable leaf goals, not parent labels.')
        if any(dep not in leaves or dep == identifier for dep in item['depends_on']):
            raise ValueError('Goal dependencies must reference other existing leaf goals.')
    if covered != allowed:
        missing = sorted(allowed - covered)
        raise ValueError('Every planned file must belong to an actionable leaf goal. Unassigned editable paths: '
                         + ', '.join(missing) + '. Assign each to its intended implementation leaf, or move an '
                         'existing read-only reference to context_files and remove it from existing_files and goal files. '
                         'Do not invent edits or guess a goal assignment.')
    visiting, done = set(), set()
    def visit(identifier):
        if identifier in visiting:
            raise ValueError('Agent plan contains a dependency cycle.')
        if identifier in done:
            return
        visiting.add(identifier)
        for dependency in nodes[identifier]['depends_on']:
            visit(dependency)
        visiting.remove(identifier)
        done.add(identifier)
    for identifier in leaves:
        visit(identifier)
    return list(nodes.values())


def leaf_goals(goals):
    parents = {node['parent'] for node in goals}
    return {node['id']: node for node in goals if node['id'] not in parents}


def validate_goal_links(proposal, goals):
    if not goals:
        return
    leaves = leaf_goals(goals)
    links = proposal.get('goal_links')
    if not isinstance(links, dict) or set(links) != set(proposal['files']):
        raise ValueError('Each proposed change must be tied to its approved leaf goal.')
    for name, identifiers in links.items():
        if (not isinstance(identifiers, list) or not identifiers
                or any(not isinstance(identifier, str) for identifier in identifiers)
                or len(set(identifiers)) != len(identifiers)
                or any(identifier not in leaves or name not in leaves[identifier]['files'] for identifier in identifiers)):
            raise ValueError('A proposed change has an invalid goal ID or exceeds that leaf goal file scope.')


def approval_matches(record, plan):
    approval = record.get('plan_approval', {})
    return (approval.get('identity_sha256') == fingerprint(record['identity'])
            and approval.get('plan_sha256') == fingerprint(plan))


def approve_mission_plan(ws, project, mission_id):
    """Record an explicit human approval only for the exact verified saved plan."""
    from .mission_resume import _mission_id, mission_identity
    from .goal_store import GoalStore
    directory = ws.project(project) / 'missions' / _mission_id(mission_id)
    record_path = directory.with_suffix('.json')
    with mission_lease(directory):
        from .working_revision import is_abandoned
        if is_abandoned(ws, project, mission_id):
            raise ValueError('This mission was explicitly closed; its old plan cannot be approved.')
        record = read_json(record_path)
        if (record.get('id') != mission_id or record.get('project') != project
                or record.get('state') != 'plan_review' or not record.get('require_plan_confirmation')):
            raise ValueError('Only a saved agent plan awaiting review can be approved.')
        expected = mission_identity(ws, project, record['base_revision'], record['mission'],
            record['identity']['endpoint'], record['identity']['model'], record.get('check_command'),
            record.get('diagnostic'), mission_id, True, record.get('require_goal_decomposition', False),
            record.get('counter_context'), record['identity'].get('revision_journal'))
        if expected != record.get('identity'):
            raise ValueError('Mission inputs changed after plan review; create a fresh plan.')
        cached = read_json(directory / 'results' / 'plan.json')
        plan = cached.get('result')
        if (not isinstance(plan, dict) or cached.get('result_sha256') != fingerprint(plan)
                or record.get('plan') != plan or record.get('plan_fingerprint') != fingerprint(plan)):
            raise ValueError('Saved plan changed after review; create a fresh plan.')
        goals = validate_goals(plan, required=True)
        if goals != record.get('proposed_goals'):
            raise ValueError('Goal decomposition changed after review; create a fresh plan.')
        node = GoalStore(directory, expected).snapshot().get('nodes', {}).get('root/plan', {})
        if (node.get('status') != 'complete' or node.get('input_fingerprint') != cached.get('input_fingerprint')
                or node.get('evidence', {}).get('result_sha256') != cached['result_sha256']):
            raise ValueError('Agent plan checkpoint is not verified; approval was not recorded.')
        record['plan_approval'] = {'identity_sha256': fingerprint(expected), 'plan_sha256': fingerprint(plan),
                                   'approved_at': datetime.now(timezone.utc).isoformat()}
        write_json(record_path, record)
        return record
