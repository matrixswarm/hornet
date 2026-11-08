"""Group a fresh model-authored forest without changing its actionable goals."""
import copy

from .mission_plan import MAX_GOALS, validate_goals


def normalize_plan_roots(plan):
    """Add containment only; the caller must still validate all dependencies.

    This is never a persisted-plan migration. Existing goals, scopes, criteria,
    and prerequisite references are preserved. No original goal becomes a group.
    """
    goals = plan.get('goals')
    if not goals:
        return plan
    roots = [node for node in goals if node['parent'] is None]
    if len(roots) <= 1:
        return plan
    if len(goals) >= MAX_GOALS:
        raise ValueError('Multiple root goals need a grouping root, exceeding the 40-goal bound. Narrow the mission.')
    result = copy.deepcopy(plan)
    used = {node['id'] for node in goals}
    if any(node['parent'] is not None and node['parent'] not in used for node in goals):
        raise ValueError('Agent plan names a missing parent goal.')
    identifier = 'mission_root'
    suffix = 1
    while identifier in used:
        identifier = 'mission_root_' + str(suffix)
        suffix += 1
    for node in result['goals']:
        if node['parent'] is None:
            node['parent'] = identifier
    result['goals'].insert(0, {
        'id': identifier, 'parent': None, 'goal': 'Complete all planned goal groups',
        'files': [], 'success_criteria': 'All child goals satisfy their own success criteria',
        'depends_on': []})
    # Establish bounded containment without guessing or dropping dependencies.
    # The fresh-response pipeline validates/expands those immediately afterward.
    structure = copy.deepcopy(result)
    for node in structure['goals']:
        node['depends_on'] = []
    validate_goals(structure, required=True)
    return result
