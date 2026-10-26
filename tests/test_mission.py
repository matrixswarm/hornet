"""Goal integrity, focused handoffs and interruption recovery."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from hornet.coding import acceptance, autonomous, mission
from hornet.project import HornetError
from test_resume import call
import test_autonomous as fixtures


class MissionTests(unittest.TestCase):
    setUp = fixtures.AutonomousTests.setUp
    model = fixtures.AutonomousTests.model
    submit = fixtures.AutonomousTests.submit
    write = fixtures.AutonomousTests.write
    run_dir = fixtures.AutonomousTests.run_dir
    state = fixtures.AutonomousTests.state

    def run_goal(self, model=None):
        return autonomous.start(self.project, self.args, self.worker, model or self.model)

    def save_state(self, state):
        (self.run_dir() / 'recipe-checkpoint.json').write_text(json.dumps(state))

    def test_original_goal_and_every_nonblank_line_are_preserved(self):
        original = '  Keep False.\n\nDo not edit elsewhere.\n'
        goal = mission.document(original)
        self.assertEqual(goal['objective'], original)
        self.assertEqual(goal['requirements'], [{'id': 'r001', 'text': '  Keep False.'},
                                               {'id': 'r002', 'text': 'Do not edit elsewhere.'}])
        many = mission.document('\n'.join('Requirement ' + str(i) for i in range(130)))
        self.assertLessEqual(len(many['requirements']), 64)
        self.assertEqual('\n'.join(item['text'] for item in many['requirements']), many['objective'])

    def test_coverage_requires_every_requirement_and_every_cell(self):
        goal = mission.document('First\nSecond')
        plan = dict(copy.deepcopy(self.plan), schema_version=2,
                    coverage=[{'requirement_id': 'r001', 'cell_ids': ['check-even']},
                              {'requirement_id': 'r002', 'cell_ids': ['check-even']}])
        mission.coverage(plan, goal)
        cases = [[], plan['coverage'][:1], plan['coverage'][::-1],
                 [{'requirement_id': 'r001', 'cell_ids': ['unknown']}, plan['coverage'][1]],
                 [{'requirement_id': 'r001', 'cell_ids': []}, plan['coverage'][1]]]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(HornetError):
                mission.coverage(dict(plan, coverage=value), goal)
        other = copy.deepcopy(plan)
        other['cells'].append(dict(other['cells'][0], id='unassigned'))
        with self.assertRaises(HornetError):
            mission.coverage(other, goal)

    def test_new_goal_cannot_accept_legacy_plan_without_coverage(self):
        result = self.run_goal(lambda *args: self.submit('submit_intake', {'schema_version': 1, 'mode': 'execute', 'reason': 'Legacy plan',
            'self_cell_id': 'check-even', 'plan': self.plan}, 'legacy'))
        self.assertFalse(result['ok'])
        self.assertNotIn('write_file', self.worker.calls)
        self.assertIn('six attempts', result['error'])

    def test_goal_and_overall_checks_are_frozen_before_first_edit(self):
        original = self.worker.call
        def checked(name, arguments=None):
            if name == 'write_file':
                state = self.state()
                mission.validate(self.run_dir(), state)
                self.assertTrue((self.run_dir() / 'goal-acceptance.json').is_file())
                self.assertTrue((self.run_dir() / 'cells/check-even/work-packet.json').is_file())
            return original(name, arguments)
        self.worker.call = checked
        result = self.run_goal()
        self.assertTrue(result['ok'], result)
        self.assertEqual(mission.read(self.run_dir() / 'goal.json')['objective'], self.args.goal)
        self.assertTrue(mission.read(self.run_dir() / 'goal-status.json')['overall_checks_passed'])
        self.assertEqual(autonomous.load(self.project, self.run_dir())['schema_version'], 4)

    def test_cell_success_does_not_override_overall_goal_failure(self):
        def model(messages, tools, *unused):
            if 'submit_goal_checks' in {item['function']['name'] for item in tools}:
                checks = copy.deepcopy(self.checks)
                checks['checks'][0]['python'] = 'from parity_cell import is_even\nassert is_even(3) is True'
                return self.submit('submit_goal_checks', checks, 'contradiction')
            return self.model(messages, tools)
        result = self.run_goal(model)
        self.assertFalse(result['ok'])
        self.assertIs(result['values']['check-even'], False)
        self.assertIn('Overall goal acceptance', result['error'])
        self.assertFalse((self.run_dir() / 'goal-completion.json').exists())
        self.assertFalse(mission.read(self.run_dir() / 'attempts/1/goal-checks.json')['ok'])
        self.assertEqual(mission.read(self.run_dir() / 'goal-status.json')['requirements'][0]['status'], 'cells_verified')

    def test_goal_tampering_blocks_resume_before_another_model_call(self):
        self.run_goal(lambda *args: (_ for _ in ()).throw(HornetError('offline')))
        (self.run_dir() / 'goal.json').write_text('{}')
        with self.assertRaises(HornetError):
            autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker,
                              lambda *args: self.fail('Must not call a model'))

    def test_missing_overall_evidence_blocks_completed_load(self):
        self.assertTrue(self.run_goal()['ok'])
        (self.run_dir() / self.state()['goal_verification']['path']).unlink()
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())

    def test_focused_packet_contains_assigned_requirements_once(self):
        self.args.goal += '\nUNIQUE_CURRENT_REQUIREMENT must stay visible.'
        def model(messages, tools, *unused):
            names = {item['function']['name'] for item in tools}
            if 'submit_checks' in names or not any(name.startswith('submit_') for name in names):
                self.assertEqual(messages[1]['content'].count('UNIQUE_CURRENT_REQUIREMENT'), 1)
                self.assertEqual(messages[1]['content'].count('"contract"'), 1)
            return self.model(messages, tools)
        self.assertTrue(self.run_goal(model)['ok'])

    def chain(self):
        self.args.goal += '\nReturn the Boolean in an even field from label_cell.describe.'
        (self.guest / 'label_cell.py').write_text('def describe(value):\n    return {}\n')
        self.plan['cells'].append({'id': 'payload', 'task': 'Implement describe(value) returning {"even": value}.',
            'files': ['label_cell.py'], 'depends_on': ['check-even'], 'result': {'path': 'label_cell.py', 'function': 'describe',
            'args_json': '[{"$cell":"check-even"}]', 'kwargs_json': '{}',
            'schema_json': '{"type":"object","properties":{"even":{"type":"boolean"}},"required":["even"]}'}})
        turns = [0]
        def model(messages, tools, *unused):
            names = {item['function']['name'] for item in tools}
            if 'submit_checks' in names and '"id":"payload"' in messages[1]['content']:
                return self.submit('submit_checks', {'schema_version': 1, 'checks': [{'id': 'payload',
                    'description': 'Retain the supplied Boolean', 'paths': ['label_cell.py'], 'timeout': 5,
                    'python': 'from label_cell import describe\nassert describe(False) == {"even": False}'}]}, 'payload-check')
            if 'submit_goal_checks' in names:
                checks = copy.deepcopy(self.checks)
                checks['checks'][0]['paths'].append('label_cell.py')
                checks['checks'][0]['python'] += '\nfrom label_cell import describe\nassert describe(is_even(3)) == {"even": False}'
                return self.submit('submit_goal_checks', checks, 'whole-goal')
            if not any(name.startswith('submit_') for name in names) and self.state().get('active_cell') == 'payload':
                turns[0] += 1
                if turns[0] == 1:
                    source = self.guest / 'label_cell.py'
                    return call('write_file', {'path': 'label_cell.py', 'content': 'def describe(value):\n    return {"even": value}\n',
                                'expected_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}, 'payload-edit'), {}
                return {'role': 'assistant', 'content': 'Ready.'}, {}
            return self.model(messages, tools)
        return model

    def test_interrupted_chain_retains_goal_remaining_work_and_receipt(self):
        model = self.chain()
        original = autonomous.stored
        def stop(path, value):
            original(path, value)
            if path.name == 'progress.json' and value.get('stage') == 'cell_completed':
                raise KeyboardInterrupt()
        with patch.object(autonomous, 'stored', side_effect=stop), self.assertRaises(KeyboardInterrupt):
            self.run_goal(model)
        run = self.run_dir()
        goal = (run / 'goal.json').read_bytes()
        receipt = (run / 'cells/check-even/return-manifest.json').read_bytes()
        failed = (run / 'attempts/1/result.json').read_bytes()
        self.assertEqual(mission.read(run / 'goal-status.json')['remaining_cells'], ['payload'])
        result = autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.worker.calls.count('write_file'), 2)
        self.assertEqual((run / 'goal.json').read_bytes(), goal)
        self.assertEqual((run / 'cells/check-even/return-manifest.json').read_bytes(), receipt)
        self.assertEqual((run / 'attempts/1/result.json').read_bytes(), failed)
        packet_path = run / 'cells/payload/work-packet.json'
        packet = mission.read(packet_path)
        self.assertIs(packet['dependencies']['check-even']['value'], False)
        packet['dependencies']['check-even']['value'] = 0
        packet_path.write_text(json.dumps(packet))
        state = self.state()
        state['cells']['payload']['work_packet'] = mission.reference(run, packet_path)
        self.save_state(state)
        with self.assertRaises(HornetError):
            autonomous.load(self.project, run)

    def test_completion_publication_crash_does_not_repeat_completed_work(self):
        original = mission.publish_completion
        def interrupted(*args):
            original(*args)
            raise KeyboardInterrupt()
        with patch.object(mission, 'publish_completion', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.run_goal()
        original_bytes = (self.run_dir() / 'goal-completion.json').read_bytes()
        commands = self.worker.calls.count('run_command')
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker,
                                   lambda *args: self.fail('No model calls after verified completion'))
        self.assertTrue(result['ok'], result)
        self.assertEqual((self.run_dir() / 'goal-completion.json').read_bytes(), original_bytes)
        self.assertEqual(self.worker.calls.count('run_command'), commands)

    def test_request_usage_is_observed_and_missing_values_remain_unknown(self):
        def model(*args):
            message, stats = self.model(*args)
            return message, {'usage': {'prompt_tokens': 25, 'completion_tokens': 5}}
        result = self.run_goal(model)
        self.assertTrue(result['ok'], result)
        count = result['model_turns']
        self.assertEqual(result['usage']['observed_prompt_tokens'], count * 25)
        self.assertEqual(result['usage']['observed_output_tokens'], count * 5)
        self.assertEqual(result['usage']['requests_without_prompt_usage'], 0)
        self.assertEqual(mission.usage({'request_records': [{'input_chars': 8}]}),
            {'model_requests': 1, 'observed_prompt_tokens': 0, 'observed_output_tokens': 0,
         'requests_without_prompt_usage': 1, 'serialized_input_chars': 8,
         'peak_input_chars': 8, 'peak_prompt_tokens': None})

    def test_cell_manifest_binds_goal_and_focused_packet(self):
        result = self.run_goal()
        self.assertTrue(result['ok'], result)
        receipt = mission.read(self.run_dir() / result['return_manifests']['check-even']['path'])
        self.assertEqual(receipt['goal']['goal_sha256'], result['goal_sha256'])
        self.assertEqual(receipt['goal']['requirement_ids'], ['r001'])
        self.assertEqual(receipt['goal']['work_packet'], self.state()['cells']['check-even']['work_packet'])

    def test_retry_clears_current_error_and_retains_failed_conditions(self):
        self.run_goal(lambda *args: (_ for _ in ()).throw(HornetError('Model unavailable')))
        run = self.run_dir()
        old = (run / 'attempts/1/result.json').read_bytes()
        conditions = (run / 'attempts/1/conditions.json').read_bytes()
        self.assertEqual(mission.read(run / 'goal-status.json')['active_error'], 'Model unavailable')
        def retry(*args):
            self.assertIsNone(mission.read(run / 'goal-status.json')['active_error'])
            return self.model(*args)
        result = autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker, retry)
        self.assertTrue(result['ok'], result)
        self.assertEqual((run / 'attempts/1/result.json').read_bytes(), old)
        self.assertEqual((run / 'attempts/1/conditions.json').read_bytes(), conditions)

    def test_interrupted_overall_check_does_not_replay_an_unknown_operation(self):
        original = self.worker.call
        def interrupted(name, arguments=None):
            result = original(name, arguments)
            if name == 'run_command' and self.state()['phase'] == 'final_checks' and 'OVERALL_ONLY' in str(arguments):
                # Interrupt after execution; the controller cannot know completion.
                raise KeyboardInterrupt()
            return result
        self.worker.call = interrupted
        def model(messages, tools, *unused):
            if 'submit_goal_checks' in {item['function']['name'] for item in tools}:
                checks = copy.deepcopy(self.checks)
                checks['checks'][0]['python'] += '\n# OVERALL_ONLY'
                return self.submit('submit_goal_checks', checks, 'overall')
            return self.model(messages, tools)
        with self.assertRaises(KeyboardInterrupt):
            self.run_goal(model)
        self.worker.call = original
        commands = self.worker.calls.count('run_command')
        with self.assertRaisesRegex(HornetError, 'outcome is unknown'):
            autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertEqual(self.worker.calls.count('run_command'), commands)

    def test_goal_verifier_uses_frozen_requirements_without_source_reads(self):
        offered = []
        def model(messages, tools, *unused):
            names = {item['function']['name'] for item in tools}
            if 'submit_goal_checks' in names:
                self.assertEqual(names, {'submit_goal_checks'})
                self.assertIn('Declared interfaces:', messages[1]['content'])
                offered.append(names)
            return self.model(messages, tools)
        result = self.run_goal(model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(offered), 1)

    def test_resumed_role_archives_old_context_and_starts_focused(self):
        turns = [0]
        def interrupted(messages, tools, *unused):
            if 'submit_goal_checks' in {item['function']['name'] for item in tools}:
                turns[0] += 1
                if turns[0] == 1:
                    return {'role': 'assistant', 'content': 'OLD_ROLE_OBSERVATION'}, {}
                raise HornetError('Temporary verifier failure')
            return self.model(messages, tools)
        self.assertFalse(self.run_goal(interrupted)['ok'])
        original = self.state()['roles']['goal-verifier']['messages']
        def retry(messages, tools, *unused):
            if 'submit_goal_checks' in {item['function']['name'] for item in tools}:
                self.assertIn('Temporary verifier failure', json.dumps(messages))
                self.assertNotIn('OLD_ROLE_OBSERVATION', json.dumps(messages))
            return self.model(messages, tools)
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, retry)
        self.assertTrue(result['ok'], result)
        archive = mission.read(self.run_dir() / 'roles/goal-verifier/attempt-1-context.json')
        self.assertEqual(archive['messages'], original)

    def test_null_provider_usage_is_unknown_and_does_not_break_work(self):
        def model(*args):
            message, stats = self.model(*args)
            return message, {'usage': None}
        result = self.run_goal(model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['usage']['requests_without_prompt_usage'], result['model_turns'])

    def test_rejected_submissions_refocus_once_and_preserve_failed_documents(self):
        submissions = [0]
        def model(messages, tools, *unused):
            if 'submit_checks' in {item['function']['name'] for item in tools}:
                submissions[0] += 1
                if submissions[0] <= 2:
                    checks = copy.deepcopy(self.checks)
                    checks['checks'][0]['python'] = 'BROKEN_DOCUMENT_MARKER = "'
                    return self.submit('submit_checks', checks, 'rejected-' + str(submissions[0]))
                self.assertNotIn('BROKEN_DOCUMENT_MARKER', json.dumps(messages))
                self.assertIn('Invalid Python', json.dumps(messages))
                self.assertIn('unchanged requirements', json.dumps(messages))
                self.assertEqual(self.state()['roles']['verifier-check-even']['attempt_turns'], 3)
            return self.model(messages, tools)
        result = self.run_goal(model)
        self.assertTrue(result['ok'], result)
        record = self.state()['roles']['verifier-check-even']
        self.assertEqual(len(record['refocus_history']), 1)
        evidence = record['refocus_history'][0]
        path = self.run_dir() / evidence['path']
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), evidence['sha256'])
        self.assertEqual(json.dumps(mission.read(path)).count('BROKEN_DOCUMENT_MARKER'), 2)
        failures = [item for item in record['history'] if item.get('tool') == 'submit_checks']
        self.assertEqual(len(failures), 2)
        self.assertTrue(all(not item['result']['ok'] for item in failures))

    def test_refocus_does_not_extend_role_or_total_budget(self):
        self.args.max_turns = 5
        def model(messages, tools, *unused):
            if 'submit_checks' in {item['function']['name'] for item in tools}:
                checks = copy.deepcopy(self.checks)
                checks['checks'][0]['python'] = 'invalid = "'
                return self.submit('submit_checks', checks, 'still-invalid')
            return self.model(messages, tools)
        result = self.run_goal(model)
        self.assertFalse(result['ok'])
        record = self.state()['roles']['verifier-check-even']
        self.assertEqual(record['invalid_submissions'], 3)
        self.assertLessEqual(record['attempt_turns'], 6)
        self.assertEqual(len(record['refocus_history']), 1)
        self.assertEqual(result['model_turns'], 5)
        self.assertNotIn('write_file', self.worker.calls)

    def test_impossible_loop_else_is_rejected_but_try_else_and_break_are_valid(self):
        checks = copy.deepcopy(self.checks)
        for loop in ('for value in [True, None]:', 'while False:'):
            checks['checks'][0]['python'] = loop + '\n    pass\nelse:\n    raise AssertionError("Wrong else")'
            with self.subTest(loop=loop), self.assertRaisesRegex(HornetError, 'always fails'):
                mission.behavioral_checks(checks, ['parity_cell.py'])
        for code in ('for value in [True, None]:\n    break\nelse:\n    raise AssertionError("Not found")',
                     'from parity_cell import is_even\nfor value in [True, None]:\n    try:\n        is_even(value)\n    except ValueError:\n        pass\n    else:\n        raise AssertionError("Invalid input accepted")'):
            checks['checks'][0]['python'] = code
            mission.behavioral_checks(checks, ['parity_cell.py'])

    def test_helper_order_preflight_rejects_early_calls_and_preserves_valid_scopes(self):
        checks = copy.deepcopy(self.checks)
        for code in ('helper(1)\ndef helper(value):\n    assert value == 1',
                     'assert helper(1)\ndef helper(value):\n    return value == 1',
                     'answer = helper(1)\ndef helper(value):\n    assert value == 1'):
            checks['checks'][0]['python'] = code
            with self.subTest(code=code), self.assertRaisesRegex(HornetError, 'before its definition'):
                mission.behavioral_checks(checks, ['parity_cell.py'])
        for code in ('def helper(value):\n    assert value == 1\nhelper(1)',
                     'def caller():\n    helper(1)\ndef helper(value):\n    assert value == 1\ncaller()',
                     'caller = lambda: helper(1)\ndef helper(value):\n    assert value == 1\ncaller()',
                     'from parity_cell import is_even as helper\nassert helper(2)\ndef helper(value):\n    return True',
                     'helper = bool\nassert helper(1)\ndef helper(value):\n    return True',
                     'assert bool(1)\ndef bool(value):\n    return True',
                     'values = [helper(1) for helper in [bool]]\nassert values[0]\ndef helper(value):\n    return True'):
            checks['checks'][0]['python'] = code
            with self.subTest(code=code):
                mission.behavioral_checks(checks, ['parity_cell.py'])

    def test_goal_verifier_corrects_early_helper_before_freezing_checks(self):
        submissions = [0]
        def model(messages, tools, *unused):
            if 'submit_goal_checks' in {item['function']['name'] for item in tools}:
                submissions[0] += 1
                if submissions[0] == 1:
                    checks = copy.deepcopy(self.checks)
                    checks['checks'][0]['python'] += '\nassert_type(True, bool)\ndef assert_type(value, kind):\n    assert isinstance(value, kind)'
                    return self.submit('submit_goal_checks', checks, 'early-helper')
                self.assertFalse((self.run_dir() / 'goal-acceptance.json').exists())
                self.assertIn('before its definition', json.dumps(messages))
            return self.model(messages, tools)
        result = self.run_goal(model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(submissions[0], 2)
        self.assertEqual(mission.read(self.run_dir() / 'goal-acceptance.json'), self.checks)
        history = self.state()['roles']['goal-verifier']['history']
        failed = [row for row in history if row.get('tool') == 'submit_goal_checks' and not row['result']['ok']]
        self.assertEqual(len(failed), 1)
        self.assertIn('assert_type', failed[0]['arguments']['checks'][0]['python'])

    def test_cell_verifier_cannot_freeze_an_impossible_loop_else(self):
        def model(messages, tools, *unused):
            if 'submit_checks' in {item['function']['name'] for item in tools}:
                checks = copy.deepcopy(self.checks)
                checks['checks'][0]['python'] = 'for value in [1, 2]:\n    pass\nelse:\n    assert False'
                return self.submit('submit_checks', checks, 'bad-loop')
            return self.model(messages, tools)
        result = self.run_goal(model)
        self.assertFalse(result['ok'])
        self.assertFalse((self.run_dir() / 'cells/check-even/acceptance.json').exists())
        self.assertNotIn('write_file', self.worker.calls)
        errors = [item['result']['error'] for item in self.state()['roles']['verifier-check-even']['history']
                  if item.get('tool') == 'submit_checks']
        self.assertEqual(len(errors), 6)
        self.assertTrue(all('always fails' in error for error in errors))


class BehavioralPreflightTests(unittest.TestCase):
    def check(self, source):
        value = {'id': 'exceptions', 'description': 'Check exceptions', 'paths': ['api.py'],
                 'timeout': 5, 'python': source}
        mission.behavioral_checks({'schema_version': 1, 'checks': [value]}, ['api.py'])

    def test_exception_forms_and_aliases_are_recognized(self):
        forms = [
            'from unittest import TestCase\nTestCase().assertRaises(ValueError, int, "x")',
            'from unittest import TestCase as TC\ncheck = TC()\ncheck.assertRaises(ValueError, int, "x")',
            'import unittest as ut\ncheck = ut.TestCase()\ncheck.assertRaisesRegex(ValueError, "invalid", int, "x")',
            'import unittest\nwith unittest.TestCase().assertRaises(ValueError):\n    int("x")',
            'from unittest import TestCase\ncheck = TestCase()\nwith check.assertRaisesRegex(ValueError, "invalid"):\n    int("x")',
            'from unittest import TestCase\ndef verify():\n    check = TestCase()\n    check.assertRaises(ValueError, int, "x")\nverify()',
        ]
        for source in forms:
            with self.subTest(source=source):
                self.check(source)
                exec(source, {})

    def test_missing_assertions_and_unrelated_methods_are_rejected_with_check_id(self):
        forms = [
            'assert True',
            'from unittest import TestCase\nTestCase().assertRaises(ValueError)',
            'from unittest import TestCase\nTestCase().assertRaisesRegex(ValueError, "invalid")',
            'from api import fake\nfake.assertRaises(ValueError, int, "x")',
            'from unittest import TestCase\ncheck = TestCase()\ncheck = fake\ncheck.assertRaises(ValueError, int, "x")',
            'from unittest import TestCase\nTestCase = fake\nTestCase().assertRaises(ValueError, int, "x")',
            'from unittest import TestCase\ncheck = TestCase()\ndef verify(check):\n    check.assertRaises(ValueError, int, "x")',
        ]
        for source in forms:
            with self.subTest(source=source), self.assertRaisesRegex(HornetError, 'Check exceptions:.*assertions'):
                self.check(source)

    def test_preflight_does_not_execute_candidate_code(self):
        self.check('from unittest import TestCase\nraise RuntimeError("Do not execute")\n'
                   'TestCase().assertRaises(ValueError, int, "x")')

    def test_class_only_exception_check_explains_script_execution(self):
        source = ('from unittest import TestCase\n'
                  'class TestExceptions(TestCase):\n'
                  '    def test_invalid_input(self):\n'
                  '        with self.assertRaises(ValueError):\n'
                  '            int("x")\n')
        with self.assertRaises(HornetError) as caught:
            self.check(source)
        message = str(caught.exception)
        self.assertIn('Check exceptions:', message)
        self.assertIn('test discovery does not run automatically', message)
        self.assertIn('Defining a test class or test method alone does not execute', message)
        self.assertIn('module-level TestCase instance', message)
        # The documented correction must both pass preflight and execute.
        corrected = ('from unittest import TestCase\n'
                     'TestCase().assertRaises(ValueError, int, "x")')
        self.check(corrected)
        exec(corrected, {})


if __name__ == '__main__':
    unittest.main()
