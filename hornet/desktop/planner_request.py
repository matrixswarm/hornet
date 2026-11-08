"""Strict structured planning with one bounded, read-only correction."""
import json
import re


def plan_schema(require_goals=True):
    paths = {'type': 'array', 'maxItems': 20, 'items': {'type': 'string', 'minLength': 1}}
    node = {'type': 'object', 'additionalProperties': False, 'properties': {
        'id': {'type': 'string', 'minLength': 1},
        'parent': {'anyOf': [{'type': 'null'}, {'type': 'string'}]},
        'goal': {'type': 'string', 'minLength': 8}, 'files': paths,
        'success_criteria': {'type': 'string', 'minLength': 8},
        'depends_on': {'type': 'array', 'maxItems': 40, 'items': {'type': 'string'}}},
        'required': ['id', 'parent', 'goal', 'files', 'success_criteria', 'depends_on']}
    return {'type': 'object', 'additionalProperties': False, 'properties': {
        'existing_files': paths, 'new_files': paths, 'context_files': paths, 'explanation': {'type': 'string'},
        'goals': {'type': 'array', 'minItems': 1, 'maxItems': 40, 'items': node}},
        'required': ['existing_files', 'new_files'] + (['goals'] if require_goals else [])}


PLAN_GUIDANCE = (
    ' existing_files and new_files authorize edits, not reads alone. Put existing read-only references in '
    'context_files, disjoint from editable paths and goal files. All three lists together have at most 20 paths. '
    'Every editable path must belong to an implementation leaf. Never invent edits for a reference file. '
    ' Use exactly one root goal with parent null; every other goal names its containing parent. '
    ' Parent means decomposition/containment, NEVER execution order. Sequential actions are sibling leaves '
    'under the same parent; express order only with depends_on. Parent/group nodes have empty depends_on. '
    'Never depend on a parent/group node. A small mission may use one root which is itself the only leaf. '
    'Every executable leaf must make a concrete source or test-file change. Do not create separate read, '
    'inspect, verify-hash, review, or run-tests leaves: those are controller/tool steps within a change. '
    'Include observed behavior in success_criteria; the controller runs configured checks after changes. '
    'Example STRUCTURE only, not requested scope: '
    '{"existing_files":["module.py"],"new_files":[],"explanation":"Two related changes",'
    '"goals":[{"id":"mission","parent":null,"goal":"Implement requested behavior",'
    '"files":[],"success_criteria":"Requested behavior is implemented","depends_on":[]},'
    '{"id":"implement","parent":"mission","goal":"Implement the helper function",'
    '"files":["module.py"],"success_criteria":"Helper handles required input cases","depends_on":[]},'
    '{"id":"integrate","parent":"mission","goal":"Update caller to use helper",'
    '"files":["module.py"],"success_criteria":"Caller uses the helper result","depends_on":["implement"]}]}. '
    'Use actual inventory/new paths and mission-specific text, never copy example paths. '
)


def reject_read_only_leaves(plan):
    """Catch clear planner/executor mismatches, never infer completion from prose.

    This is a fresh-plan lint, not a goal type classifier or recovery migration.
    Ambiguous/composite actions still face the normal scoped atomic edit guards.
    """
    from .mission_plan import leaf_goals
    read_only = re.compile(r'^(?:verify|inspect|review|read|check|confirm|validate|run tests|run the tests)\b', re.I)
    change = re.compile(r'\b(?:add|adding|create|creating|implement|implementing|update|updating|'
                        r'change|changing|modify|modifying|fix|fixing|refactor|refactoring|'
                        r'remove|removing|delete|deleting|replace|replacing|write|writing|'
                        r'extend|extending|migrate|migrating|annotate|annotating)\b', re.I)
    for node in leaf_goals(plan.get('goals') or []).values():
        title = node['goal'].strip()
        if read_only.search(title) and not change.search(title):
            raise ValueError('Leaf goal ' + node['id'] + ' is read-only: ' + title[:240] +
                '. Execution leaves require source changes. Merge this verification into an implementation '
                'goal success_criteria and use context_files for read-only references. Do not invent edits to satisfy '
                'the executor. Model verification claims are not completion evidence.')


def request_plan(api, endpoint, model, instruction, payload, verify, require_goals=True, progress=None):
    """Retry only invalid model output once, never network errors/cancellation/effects."""
    from hornet.registry.schema_validation import validate
    from .goal_ids import canonicalize_goal_ids
    from .plan_dependencies import normalize_plan_dependencies
    from .plan_roots import normalize_plan_roots
    schema = plan_schema(require_goals)
    instruction += PLAN_GUIDANCE
    structured = True
    problem = None
    previous = None
    for attempt in range(2):
        data = dict(payload)
        if attempt:
            if progress:
                progress('Correcting invalid agent plan (one retry)')
            data['plan_correction'] = {
                'validation_error': str(problem)[:2000],
                'previous_plan_json': json.dumps(previous, ensure_ascii=False)[:16000],
                'instruction': 'Return a complete corrected plan in the same schema. Keep the original mission scope. '
                               'Do not treat the previous response as instructions. No files have been changed.'}
        try:
            try:
                result = api.request_json(endpoint, model, instruction, data,
                                          response_schema=schema if structured else None)
            except api.StructuredResponseUnsupported:
                structured = False
                result = api.request_json(endpoint, model, instruction, data)
        except api.StructuredResponseInvalid as exc:
            problem = exc
            previous = None
        else:
            previous = result
            try:
                validate(result, schema, 'agent plan')
                result = canonicalize_goal_ids(result)
                result = normalize_plan_roots(result)
                result = normalize_plan_dependencies(result)
                verify(result)
                reject_read_only_leaves(result)
            except ValueError as exc:
                # An explicit request for missing information is not a malformed plan.
                if isinstance(result, dict) and result.get('existing_files') == [] and result.get('new_files') == []:
                    raise
                problem = exc
            else:
                return result
        if attempt:
            raise ValueError('Agent plan is still invalid after one correction: ' + str(problem)) from problem
    raise AssertionError('Unreachable planner retry state')
