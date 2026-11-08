import copy
import unittest
from unittest.mock import Mock

from hornet.desktop import automation as api
from hornet.desktop.mission_plan import validate_goals
from hornet.desktop.planner_request import request_plan
from hornet.desktop.plan_roots import normalize_plan_roots
from hornet.desktop.plan_dependencies import normalize_plan_dependencies


def node(identifier, parent=None, dependencies=None):
    return {'id': identifier, 'parent': parent, 'goal': 'Implement requested behavior',
            'files': ['main.py'], 'success_criteria': 'Requested behavior is implemented',
            'depends_on': dependencies or []}


def forest():
    return {'existing_files': ['main.py'], 'new_files': [],
            'goals': [node('first'), node('second', dependencies=['first'])]}


class PlanRootTests(unittest.TestCase):
    def test_grouping_preserves_goals_dependencies_and_input(self):
        original = forest()
        before = copy.deepcopy(original)
        result = normalize_plan_roots(original)
        self.assertEqual(original, before)
        self.assertEqual(validate_goals(result, True), result['goals'])
        self.assertEqual(normalize_plan_roots(result), result)
        self.assertEqual(result['goals'][0]['files'], [])
        for old, new in zip(original['goals'], result['goals'][1:]):
            self.assertEqual(dict(new, parent=None), old)
        self.assertEqual(result, normalize_plan_roots(before))

    def test_existing_root_and_absent_optional_goals_are_unchanged(self):
        plan = forest(); plan['goals'] = plan['goals'][:1]
        self.assertIs(normalize_plan_roots(plan), plan)
        self.assertEqual(normalize_plan_roots({'existing_files': ['main.py']}),
                         {'existing_files': ['main.py']})

    def test_generated_identifier_does_not_collide(self):
        plan = forest(); plan['goals'].extend([node('mission_root'), node('mission_root_1')])
        result = normalize_plan_roots(plan)
        self.assertEqual(result['goals'][0]['id'], 'mission_root_2')
        validate_goals(result, True)

    def test_nested_group_dependencies_still_expand(self):
        plan = forest()
        plan['goals'] = [node('group_a'), node('group_b', dependencies=['group_a']),
                         node('first', 'group_a'), node('second', 'group_b')]
        result = normalize_plan_dependencies(normalize_plan_roots(plan))
        self.assertEqual(result['goals'][-1]['depends_on'], ['first'])
        validate_goals(result, True)

    def test_invalid_forests_remain_closed(self):
        for kind in ('cycle', 'missing', 'synthetic_missing', 'depth', 'count', 'leaves', 'dependency_cycle', 'overlap', 'scope'):
            with self.subTest(kind=kind):
                plan = forest()
                if kind == 'cycle':
                    plan['goals'] += [node('a', 'b'), node('b', 'a')]
                elif kind == 'missing':
                    plan['goals'].append(node('a', 'missing'))
                elif kind == 'synthetic_missing':
                    plan['goals'].append(node('a', 'mission_root'))
                elif kind == 'depth':
                    plan['goals'] += [node('a', 'first'), node('b', 'a'), node('c', 'b'), node('d', 'c')]
                elif kind == 'count':
                    plan['goals'] = [node('n' + str(i)) for i in range(40)]
                elif kind == 'leaves':
                    plan['goals'] = [node('n' + str(i)) for i in range(21)]
                elif kind == 'dependency_cycle':
                    plan['goals'][0]['depends_on'] = ['second']
                elif kind == 'overlap':
                    plan['goals'].append(node('child', 'first', ['first']))
                else:
                    plan['goals'][0]['files'] = ['outside.py']
                with self.assertRaises(ValueError):
                    normalize_plan_dependencies(normalize_plan_roots(plan))

    def test_fresh_request_needs_no_model_retry_but_saved_validation_stays_strict(self):
        plan = forest()
        adapter = Mock(request_json=Mock(return_value=plan),
                       StructuredResponseUnsupported=api.StructuredResponseUnsupported,
                       StructuredResponseInvalid=api.StructuredResponseInvalid)
        result = request_plan(adapter, 'http://localhost', 'model', 'Plan.', {},
                              lambda p: validate_goals(p, True))
        self.assertEqual(adapter.request_json.call_count, 1)
        self.assertEqual(len(result['goals']), 3)
        with self.assertRaisesRegex(ValueError, 'exactly one root'):
            validate_goals(plan, True)

    def test_grouping_at_exact_node_and_depth_bounds(self):
        plan = forest()
        plan['goals'] = [node('a'), node('b')]
        plan['goals'] += [node('group' + str(i), 'a') for i in range(18)]
        plan['goals'] += [node('leaf' + str(i), 'group' + str(i)) for i in range(18)]
        plan['goals'].append(node('deep', 'leaf0'))
        result = normalize_plan_dependencies(normalize_plan_roots(plan))
        self.assertEqual(len(result['goals']), 40)
        validate_goals(result, True)

    def test_root_diagnostic_reports_zero_or_multiple(self):
        plan = forest()
        with self.assertRaisesRegex(ValueError, 'Found 2 roots: first, second'):
            validate_goals(plan, True)
        plan['goals'][0]['parent'] = 'second'
        plan['goals'][1]['parent'] = 'first'
        with self.assertRaisesRegex(ValueError, 'Found 0 roots:'):
            validate_goals(plan, True)

    def test_zero_roots_are_not_guessed(self):
        plan = forest()
        plan['goals'][0]['parent'] = 'second'
        plan['goals'][1]['parent'] = 'first'
        with self.assertRaises(ValueError):
            normalize_plan_dependencies(normalize_plan_roots(plan))


if __name__ == '__main__':
    unittest.main()
