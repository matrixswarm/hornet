import copy
import unittest
from unittest.mock import Mock
from hornet.desktop import automation as api
from hornet.desktop.mission_plan import validate_goals
from hornet.desktop.planner_request import request_plan, plan_schema
from hornet.registry.schema_validation import check_schema


def valid_plan():
    return {'existing_files': ['main.py'], 'new_files': [], 'explanation': 'Update behavior', 'goals': [
        {'id': 'root', 'parent': None, 'goal': 'Implement requested behavior', 'files': [],
         'success_criteria': 'Requested behavior implemented', 'depends_on': []},
        {'id': 'helper', 'parent': 'root', 'goal': 'Implement helper function', 'files': ['main.py'],
         'success_criteria': 'Helper handles input cases', 'depends_on': []},
        {'id': 'caller', 'parent': 'root', 'goal': 'Update caller to use helper', 'files': ['main.py'],
         'success_criteria': 'Caller uses helper output', 'depends_on': ['helper']}]}


class PlannerRequestTests(unittest.TestCase):
    def run_plan(self, responses):
        adapter=Mock(request_json=Mock(side_effect=responses),
                     StructuredResponseUnsupported=api.StructuredResponseUnsupported,
                     StructuredResponseInvalid=api.StructuredResponseInvalid)
        result=request_plan(adapter,'http://localhost','model','Plan.',{'mission':'Update main'},
                            lambda plan: validate_goals(plan, required=True))
        return result,adapter

    def test_parent_chain_is_rejected_then_corrected_once(self):
        bad=valid_plan()
        bad['goals'][2]['parent']='helper'
        result,adapter=self.run_plan([bad,valid_plan()])
        self.assertEqual(result,valid_plan())
        self.assertEqual(adapter.request_json.call_count,2)
        args=adapter.request_json.call_args_list
        self.assertIn('Parent means decomposition',args[0].args[2])
        self.assertIn('Do not create separate read',args[0].args[2])
        self.assertIn('leaf goals',args[1].args[3]['plan_correction']['validation_error'])
        self.assertIn('goals',args[0].kwargs['response_schema']['required'])
        check_schema(plan_schema())

    def test_missing_exact_fields_and_schema_failure_are_correctable(self):
        bad=valid_plan();del bad['goals'][1]['success_criteria']
        for initial in (bad,api.StructuredResponseInvalid('missing required fields')):
            result,adapter=self.run_plan([initial,valid_plan()])
            self.assertEqual(adapter.request_json.call_count,2)

    def test_invalid_second_plan_fails_closed(self):
        bad=valid_plan();bad['goals'][1]['files']=['outside.py']
        with self.assertRaisesRegex(ValueError,'still invalid after one correction'):
            self.run_plan([bad,bad])

    def test_unassigned_reference_is_corrected_explicitly_not_guessed(self):
        bad = valid_plan()
        bad['existing_files'].append('interface.py')
        corrected = valid_plan()
        corrected['context_files'] = ['interface.py']
        result, adapter = self.run_plan([bad, corrected])
        self.assertEqual(result, corrected)
        correction = adapter.request_json.call_args_list[1].args[3]['plan_correction']
        self.assertIn('Unassigned editable paths: interface.py', correction['validation_error'])
        self.assertIn('context_files', correction['validation_error'])
        self.assertEqual(bad['existing_files'], ['main.py', 'interface.py'])
        self.assertTrue(all('interface.py' not in node['files'] for node in result['goals']))

    def test_transport_error_is_not_retried(self):
        with self.assertRaisesRegex(ValueError,'HTTP 500'):
            self.run_plan([ValueError('HTTP 500'),AssertionError('must not retry')])

    def test_explicit_need_for_information_is_not_retried(self):
        with self.assertRaises(ValueError):
            self.run_plan([{'existing_files': [],'new_files': [],'explanation':'Need context'},
                           AssertionError('must not retry')])

    def test_python_starters_preserve_one_class_per_file_default(self):
        from hornet.desktop.layouts import scaffold
        for kind in ('python-package','python-desktop'):
            files,blueprint=scaffold(kind,'example')
            rules=' '.join(blueprint['rules'])
            self.assertIn('one class per file',rules)
            self.assertNotIn('Group tightly related classes',rules)
            self.assertIn('one class per file',files['docs/ARCHITECTURE.md'])

    def test_unsupported_schema_fallback_still_validates_graph(self):
        result,adapter=self.run_plan([api.StructuredResponseUnsupported('unsupported'),valid_plan()])
        self.assertEqual(result,valid_plan())
        self.assertEqual(adapter.request_json.call_count,2)
        self.assertNotIn('response_schema',adapter.request_json.call_args.kwargs)

    def test_cancellation_is_not_retried(self):
        from hornet.desktop.cancellation import Cancelled
        with self.assertRaises(Cancelled):
            self.run_plan([Cancelled('Stopped'),AssertionError('must not retry')])

    def test_explanation_is_not_a_required_execution_field(self):
        plan=valid_plan();del plan['explanation']
        result,adapter=self.run_plan([plan])
        self.assertEqual(result,plan)
        self.assertEqual(adapter.request_json.call_count,1)


class ReadOnlyPlannerTests(unittest.TestCase):
    run_plan = PlannerRequestTests.run_plan
    def test_verification_only_leaf_is_corrected_with_specific_diagnostic(self):
        bad = valid_plan()
        bad['goals'][1]['goal'] = 'Verify FileStrategyInterface definition'
        result, adapter = self.run_plan([bad, valid_plan()])
        self.assertEqual(result, valid_plan())
        correction = adapter.request_json.call_args_list[1].args[3]['plan_correction']
        self.assertIn('Leaf goal helper is read-only', correction['validation_error'])
        self.assertIn('success_criteria', correction['validation_error'])
        self.assertIn('Do not invent edits', correction['validation_error'])
        self.assertIn('Verify FileStrategyInterface', correction['previous_plan_json'])

    def test_read_only_second_response_fails_without_claiming_verification(self):
        bad = valid_plan()
        bad['goals'][1]['goal'] = 'Verify FileStrategyInterface definition'
        with self.assertRaisesRegex(ValueError, 'still invalid after one correction.*read-only'):
            self.run_plan([bad, bad])

    def test_composite_actions_and_verification_criteria_remain_allowed(self):
        for title in ('Verify and update the interface', 'Check behavior by adding tests',
                      'Implement verification behavior'):
            plan = valid_plan()
            plan['goals'][1]['goal'] = title
            plan['goals'][1]['success_criteria'] = 'Verify the interface definition'
            result, adapter = self.run_plan([plan])
            self.assertEqual(result, plan)
            self.assertEqual(adapter.request_json.call_count, 1)

    def test_parent_label_is_not_an_execution_leaf(self):
        plan = valid_plan()
        plan['goals'][0]['goal'] = 'Verify requested behavior'
        result, adapter = self.run_plan([plan])
        self.assertEqual(result, plan)

    def test_existing_saved_plan_validation_is_unchanged(self):
        # Recovery validates the exact saved plan; it does not reclassify/rewrite it.
        plan = valid_plan()
        plan['goals'][1]['goal'] = 'Verify FileStrategyInterface definition'
        self.assertEqual(validate_goals(plan, required=True), plan['goals'])
