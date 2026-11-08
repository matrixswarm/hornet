import copy
import hashlib
import re
import unittest
from hornet.desktop.goal_ids import canonicalize_goal_ids
from hornet.desktop.mission_plan import validate_goals
import test_desktop_planner_request as planner_tests
from test_desktop_planner_request import valid_plan


class GoalIdTests(unittest.TestCase):
    def labels(self):
        plan=valid_plan()
        ids=['Main Mission','ADD_'+('HELPER_'*20),'../Finish Caller']
        for node,label in zip(plan['goals'],ids):node['id']=label
        plan['goals'][1]['parent']=ids[0]
        plan['goals'][2]['parent']=ids[0]
        plan['goals'][2]['depends_on']=[ids[1]]
        return plan

    def test_labels_map_references_stably_without_changing_scope(self):
        original=self.labels();before=copy.deepcopy(original)
        normalized=canonicalize_goal_ids(original)
        self.assertEqual(original,before)
        self.assertEqual(normalized,canonicalize_goal_ids(original))
        self.assertEqual(normalized,canonicalize_goal_ids(normalized))
        self.assertEqual(validate_goals(normalized,True),normalized['goals'])
        self.assertTrue(all(re.fullmatch(r'[a-z][a-z0-9_]{0,47}',n['id']) for n in normalized['goals']))
        for before,after in zip(original['goals'],normalized['goals']):
            self.assertEqual(before['files'],after['files'])
            self.assertEqual(before['goal'],after['goal'])
        self.assertEqual(normalized['goals'][2]['depends_on'],[normalized['goals'][1]['id']])

    def test_unknown_duplicate_and_cycle_still_reject(self):
        for kind in ('duplicate','unknown','cycle'):
            plan=self.labels()
            if kind=='duplicate':plan['goals'][2]['id']=plan['goals'][1]['id']
            if kind=='unknown':plan['goals'][2]['depends_on']=['missing']
            if kind=='cycle':plan['goals'][1]['depends_on']=[plan['goals'][2]['id']]
            with self.assertRaises(ValueError):validate_goals(canonicalize_goal_ids(plan),True)

    def test_collision_with_model_safe_id_gets_distinct_deterministic_id(self):
        plan=self.labels()
        colliding='goal_'+hashlib.sha256(plan['goals'][1]['id'].encode()).hexdigest()[:32]
        plan['goals'][2]['id']=colliding
        normalized=canonicalize_goal_ids(plan)
        self.assertEqual(len({n['id'] for n in normalized['goals']}),3)
        self.assertEqual(normalized['goals'][2]['id'],colliding)
        validate_goals(normalized,True)

    def test_planner_accepts_uppercase_long_labels_without_corrective_request(self):
        helper=planner_tests.PlannerRequestTests()
        plan,adapter=helper.run_plan([self.labels()])
        self.assertEqual(adapter.request_json.call_count,1)
        validate_goals(plan,True)
