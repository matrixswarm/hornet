"""Normalize fresh model group dependencies before strict plan approval."""
import copy

from .mission_plan import validate_goals


def normalize_plan_dependencies(plan):
    """Expand group order to all descendant leaves; never guess overlapping order.

    Only fresh model responses pass here. Persisted plans keep strict validation.
    A group prerequisite means every leaf in it must finish before any dependent
    leaf starts. Scope, tree bounds and final dependency cycles remain validated.
    """
    if not plan.get('goals'):
        return plan
    result = copy.deepcopy(plan)
    dependencies = [node['depends_on'] for node in result['goals']]
    for node in result['goals']:
        node['depends_on'] = []
    # Establish a valid bounded tree and file scope before traversing descendants.
    validate_goals(result, required=True)
    nodes = {node['id']: node for node in result['goals']}
    children = {identifier: [] for identifier in nodes}
    for node in nodes.values():
        if node['parent'] is not None:
            children[node['parent']].append(node['id'])

    def leaves(identifier):
        if not children[identifier]:
            return [identifier]
        return [leaf for child in children[identifier] for leaf in leaves(child)]

    descendants = {identifier: leaves(identifier) for identifier in nodes}
    for node, refs in zip(nodes.values(), dependencies):
        if (not isinstance(refs, list) or len(refs) > 40
                or any(not isinstance(ref, str) for ref in refs)
                or len(set(refs)) != len(refs)):
            raise ValueError('Goal dependencies must be distinct goal IDs.')
        for ref in refs:
            if ref not in nodes:
                raise ValueError('Goal dependencies must reference existing goals.')
            source, target = descendants[node['id']], descendants[ref]
            if set(source) & set(target):
                raise ValueError('Goal dependencies overlap their own descendant leaf goals; clarify the order.')
            for identifier in source:
                for dependency in target:
                    if dependency not in nodes[identifier]['depends_on']:
                        nodes[identifier]['depends_on'].append(dependency)
    validate_goals(result, required=True)
    return result
