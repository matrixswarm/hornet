import copy
import unittest
from unittest.mock import Mock

from hornet.desktop import automation as api
from hornet.desktop.mission_plan import validate_goals
from hornet.desktop.plan_dependencies import normalize_plan_dependencies
from hornet.desktop.planner_request import request_plan


def grouped_plan():
    def node(identifier, parent, dependencies=(), files=()):
        return dict(id=identifier, parent=parent, goal='Implement ' + identifier,
                    success_criteria='Observable behavior is correct',
                    depends_on=list(dependencies), files=list(files))
    return dict(existing_files=['main.py'], new_files=[], goals=[
        node('root', None), node('first', 'root'), node('second', 'root', ['first']),
        node('a', 'first', files=['main.py']), node('b', 'first', ['a'], ['main.py']),
        node('c', 'second', files=['main.py']), node('d', 'second', ['c'], ['main.py'])])


class PlanDependencyTests(unittest.TestCase):
    def test_group_order_expands_without_mutating_input_or_scope(self):
        plan = grouped_plan()
        original = copy.deepcopy(plan)
        normalized = normalize_plan_dependencies(plan)
        by_id = {node['id']: node for node in normalized['goals']}
        self.assertEqual(by_id['second']['depends_on'], [])
        self.assertEqual(by_id['c']['depends_on'], ['a', 'b'])
        self.assertEqual(by_id['d']['depends_on'], ['a', 'b', 'c'])
        self.assertEqual(by_id['b']['depends_on'], ['a'])
        self.assertEqual(plan, original)
        for before, after in zip(plan['goals'], normalized['goals']):
            for field in set(before) - {'depends_on'}:
                self.assertEqual(before[field], after[field])
        self.assertEqual(normalize_plan_dependencies(normalized), normalized)
        validate_goals(normalized, required=True)
        with self.assertRaisesRegex(ValueError, 'parent labels'):
            validate_goals(plan, required=True)

    def test_leaf_can_depend_on_disjoint_group(self):
        plan = grouped_plan()
        plan['goals'][2]['depends_on'] = []
        plan['goals'][5]['depends_on'] = ['first']
        result = normalize_plan_dependencies(plan)
        self.assertEqual(result['goals'][5]['depends_on'], ['a', 'b'])

    def test_nested_groups_inherit_dependencies(self):
        plan = grouped_plan()
        group = copy.deepcopy(plan['goals'][2])
        group.update(id='nested', parent='second', depends_on=[])
        plan['goals'].append(group)
        plan['goals'][5]['parent'] = 'nested'
        result = normalize_plan_dependencies(plan)
        self.assertEqual(result['goals'][5]['depends_on'], ['a', 'b'])

    def test_redundant_expansion_deduplicates_without_dropping_order(self):
        plan = grouped_plan()
        plan['goals'][5]['depends_on'] = ['a']
        result = normalize_plan_dependencies(plan)
        self.assertEqual(result['goals'][5]['depends_on'], ['a', 'b'])

    def test_ambiguous_or_invalid_graphs_fail_closed(self):
        cases = [('self', 2, ['second']), ('ancestor', 5, ['root']),
                 ('descendant', 2, ['c']), ('missing', 2, ['missing']),
                 ('duplicate', 2, ['first', 'first']), ('cycle', 1, ['second'])]
        for name, index, refs in cases:
            with self.subTest(name=name):
                plan = grouped_plan()
                plan['goals'][index]['depends_on'] = refs
                with self.assertRaises(ValueError):
                    normalize_plan_dependencies(plan)
        plan = grouped_plan()
        plan['goals'][1]['parent'] = 'a'
        with self.assertRaisesRegex(ValueError, 'parent cycle'):
            normalize_plan_dependencies(plan)

    def test_scope_checks_are_not_bypassed(self):
        plan = grouped_plan()
        plan['goals'][3]['files'] = ['outside.py']
        with self.assertRaisesRegex(ValueError, 'approved file scope'):
            normalize_plan_dependencies(plan)

    def test_fresh_planner_response_is_normalized_before_verification(self):
        plan = grouped_plan()
        adapter = Mock(request_json=Mock(return_value=plan),
                       StructuredResponseUnsupported=api.StructuredResponseUnsupported,
                       StructuredResponseInvalid=api.StructuredResponseInvalid)
        result = request_plan(adapter, 'http://localhost', 'model', 'Plan.', {},
                              lambda value: validate_goals(value, required=True))
        self.assertEqual(adapter.request_json.call_count, 1)
        self.assertEqual(result, normalize_plan_dependencies(plan))
