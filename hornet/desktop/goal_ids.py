"""Controller-owned stable IDs; model labels never become checkpoint paths."""
import copy
import hashlib
import re


def canonicalize_goal_ids(plan):
    """Map labels and every reference together, without guessing missing links.

    Called after structural schema validation and before graph/scope validation.
    Existing safe identifiers stay unchanged, including persisted older plans.
    """
    if not plan.get('goals'):
        return plan
    result = copy.deepcopy(plan)
    nodes = result['goals']
    labels = [node['id'] for node in nodes]
    if len(set(labels)) != len(labels):
        raise ValueError('Agent plan repeats a goal ID; labels must be unique.')
    safe = re.compile(r'[a-z][a-z0-9_]{0,47}')
    mapping = {label: label for label in labels if safe.fullmatch(label)}
    used = set(mapping.values())
    for label in sorted(set(labels) - set(mapping)):
        base = 'goal_' + hashlib.sha256(label.encode('utf-8')).hexdigest()[:32]
        identifier = base
        suffix = 1
        while identifier in used:
            identifier = base + '_' + str(suffix)
            suffix += 1
        mapping[label] = identifier
        used.add(identifier)
    def reference(label):
        if label not in mapping:
            raise ValueError('Agent plan references an unknown goal label: ' + repr(label)[:120])
        return mapping[label]
    for node in nodes:
        node['id'] = reference(node['id'])
        if node['parent'] is not None:
            node['parent'] = reference(node['parent'])
        node['depends_on'] = [reference(label) for label in node['depends_on']]
    return result
