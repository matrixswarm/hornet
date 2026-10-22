"""Worker-requested checks stay fixed, source-bound, durable and budgeted."""
import copy
import json
import unittest

from hornet.cli import build_parser
from hornet.coding.agent import TOOLS, validate_call, work, resume
from hornet.coding.context import short_result
from hornet.coding.model import json_action
from hornet.coding.ticket import source_digest
from hornet.coding.transport import SSHWorker
from hornet.project import HornetError
import test_acceptance as fixtures
from test_resume import call


class TicketTests(unittest.TestCase):
    setUp = fixtures.AcceptanceTests.setUp
    check = fixtures.AcceptanceTests.check
    write_plan = fixtures.AcceptanceTests.write_plan
    run_dir = fixtures.AcceptanceTests.run_dir
    checkpoint = fixtures.AcceptanceTests.checkpoint
    resume_args = fixtures.AcceptanceTests.resume_args
    finish = fixtures.AcceptanceTests.finish
    write = fixtures.AcceptanceTests.write

    def enabled(self):
        self.args.tools.append('check_ticket')

    def sequence(self, *actions):
        iterator = iter(actions)
        def model(*unused):
            action = next(iterator)
            return action() if callable(action) else (action, {})
        return model

    def verdicts(self):
        return [entry['result'] for entry in self.checkpoint()['history'] if entry.get('tool') == 'check_ticket']

    def test_fixed_check_fails_then_repairs_and_passes_with_exact_types(self):
        self.enabled()
        result = work(self.project, self.args, self.worker, self.sequence(
            call('check_ticket', {}, 'first'), lambda: self.write('a + b', 'edit'),
            call('check_ticket', {}, 'second'), self.finish))
        self.assertTrue(result['ok'], result)
        bad, good = self.verdicts()
        self.assertIs(bad['ok'], False)
        self.assertIn('AssertionError', bad['results'][0]['output_tail'])
        self.assertIs(good['ok'], True)
        self.assertEqual((good['schema_version'], good['kind'], good['status']), (1, 'ticket_check', 'passed'))
        self.assertIs(type(good['passed']), int)
        self.assertEqual((good['passed'], good['total']), (1, 1))
        self.assertIs(good['source_unchanged'], True)
        self.assertEqual(len(good['source_sha256']), 64)
        self.assertNotEqual(bad['source_sha256'], good['source_sha256'])
        for verdict in (bad, good):
            path = self.run_dir() / verdict['artifact']
            self.assertEqual(json.loads(path.read_text()), verdict)
            source = json.loads((path.parent / 'source.json').read_text())
            self.assertEqual(source_digest(source), verdict['source_sha256'])
        self.assertNotIn('check_ticket', self.worker.calls)
        self.assertEqual(self.worker.calls.count('run_tests'), 1)
        self.assertEqual(self.worker.calls.count('run_command'), 3)  # Two requests + independent final gate.
        self.assertEqual(result['repairs_used'], 0)

    def test_changed_source_cannot_reuse_a_previous_pass(self):
        self.enabled()
        self.args.max_repairs = 0
        result = work(self.project, self.args, self.worker, self.sequence(
            lambda: self.write('a + b', 'good'), call('check_ticket', {}, 'pass'),
            lambda: self.write('a - b', 'regress'), self.finish))
        self.assertIs(self.verdicts()[0]['ok'], True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'acceptance_failed')

    def test_no_plan_is_explicit_and_does_not_report_pass(self):
        self.enabled()
        self.args.acceptance_file = None
        work(self.project, self.args, self.worker, self.sequence(call('check_ticket', {}, 'check'), self.finish))
        verdict = self.verdicts()[0]
        self.assertEqual(verdict['status'], 'not_configured')
        self.assertIs(verdict['ok'], False)
        self.assertIsNone(verdict['plan_sha256'])
        self.assertEqual((verdict['passed'], verdict['total'], verdict['results']), (0, 0, []))
        self.assertNotIn('run_command', self.worker.calls)

    def test_native_and_json_protocol_reject_arguments(self):
        action = call('check_ticket', {'python': 'assert True'}, 'bad')['tool_calls'][0]
        with self.assertRaisesRegex(HornetError, 'takes no arguments'):
            validate_call(action)
        with self.assertRaises(ValueError):
            json_action(json.dumps({'action': 'call', 'name': 'check_ticket', 'arguments': {'ok': True}}), TOOLS)
        action = json_action('{"action":"call","name":"check_ticket","arguments":{}}', TOOLS)
        self.assertEqual(validate_call(action['tool_calls'][0]), ('check_ticket', {}))

    def test_unoffered_check_is_refused(self):
        result = work(self.project, self.args, self.worker, self.sequence(call('check_ticket', {}, 'check')))
        self.assertFalse(result['ok'])
        self.assertIn('not offered', result['error'])
        self.assertEqual(self.worker.calls.count('run_command'), 0)

    def test_request_consumes_tool_and_turn_budgets(self):
        self.enabled()
        self.args.max_tools = 1
        result = work(self.project, self.args, self.worker, self.sequence(
            call('check_ticket', {}, 'one'), call('check_ticket', {}, 'two')))
        self.assertIn('tool-operation budget', result['error'])
        self.assertEqual(self.checkpoint()['tools_used'], 1)
        self.assertEqual(len(self.verdicts()), 1)

    def test_check_does_not_complete_job_without_a_model_finish(self):
        self.enabled()
        self.plan['checks'] = [self.check(python='assert True')]
        self.write_plan()
        self.args.max_turns = 1
        result = work(self.project, self.args, self.worker, self.sequence(call('check_ticket', {}, 'check')))
        self.assertTrue(self.verdicts()[0]['ok'])
        self.assertFalse(result['ok'])
        self.assertIn('model-turn budget', result['error'])
        self.assertNotIn('run_tests', self.worker.calls)

    def test_source_drift_before_check_prevents_execution(self):
        self.enabled()
        def model(*unused):
            self.source.write_text('changed = True\n')
            return call('check_ticket', {}, 'check'), {}
        result = work(self.project, self.args, self.worker, model)
        self.assertIn('changed since the last confirmed', result['error'])
        self.assertNotIn('run_command', self.worker.calls)
        self.assertEqual(self.checkpoint()['phase'], 'tool_in_flight')

    def test_mutating_check_retains_invalid_result_and_pauses(self):
        self.enabled()
        self.plan['checks'] = [self.check(python='from pathlib import Path\nPath("candidate.py").write_text("changed = True\\n")')]
        self.write_plan()
        result = work(self.project, self.args, self.worker, self.sequence(call('check_ticket', {}, 'check')))
        self.assertIn('changed source or worker', result['error'])
        artifact = self.run_dir() / 'ticket-checks/1/result.json'
        verdict = json.loads(artifact.read_text())
        self.assertIs(verdict['ok'], False)
        self.assertEqual(verdict['status'], 'source_changed')
        self.assertEqual(self.checkpoint()['phase'], 'tool_in_flight')
        with self.assertRaisesRegex(HornetError, 'outcome is unknown'):
            resume(self.project, self.resume_args(), self.worker, self.finish)

    def test_interrupted_check_saves_partial_and_is_never_replayed(self):
        self.enabled()
        self.plan['checks'].append(self.check('second'))
        self.write_plan()
        original = self.worker.call
        runs = []
        def interrupt(name, arguments=None):
            if name == 'run_command':
                runs.append(name)
                if len(runs) == 2:
                    raise KeyboardInterrupt()
            return original(name, arguments)
        self.worker.call = interrupt
        with self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker, self.sequence(call('check_ticket', {}, 'check')))
        partial = json.loads((self.run_dir() / 'ticket-checks/1/acceptance-results.json').read_text())
        self.assertEqual(partial['active_check'], 'second')
        self.assertEqual(len(partial['results']), 1)
        with self.assertRaisesRegex(HornetError, 'outcome is unknown'):
            resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertEqual(len(runs), 2)

    def test_confirmed_result_survives_resume_with_fixed_plan(self):
        self.enabled()
        def interrupt(*unused):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker, self.sequence(call('check_ticket', {}, 'check'), interrupt))
        self.plan['checks'][0]['python'] = 'assert True'
        self.write_plan()
        def model(messages, *unused):
            context = json.dumps(messages)
            self.assertIn('last_ticket_check', context)
            self.assertIn('AssertionError', context)
            return self.write('a + b', 'edit')
        turn = [0]
        def resumed(messages, *unused):
            turn[0] += 1
            return model(messages) if turn[0] == 1 else self.finish()
        result = resume(self.project, self.resume_args(), self.worker, resumed)
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.verdicts()), 1)
        self.assertEqual(self.worker.calls.count('run_command'), 2)

    def test_output_is_bounded_and_full_observation_remains_on_disk(self):
        self.enabled()
        self.plan['checks'][0]['python'] = 'print("x" * 5000)'
        self.write_plan()
        work(self.project, self.args, self.worker, self.sequence(call('check_ticket', {}, 'check'), self.finish))
        verdict = self.verdicts()[0]
        self.assertEqual(len(verdict['results'][0]['output_tail']), 600)
        self.assertIs(verdict['results'][0]['output_truncated'], True)
        raw = json.loads((self.run_dir() / 'ticket-checks/1/acceptance-results.json').read_text())
        self.assertGreater(len(raw['results'][0]['output']), 4900)
        large = copy.deepcopy(verdict)
        large['results'] *= 8
        for item in large['results']:
            item['passed'] = False
        message = {'role': 'tool', 'name': 'check_ticket', 'content': json.dumps(large)}
        compact = short_result(message, 2666)
        self.assertLessEqual(len(compact['content']), 2666)
        data = json.loads(compact['content'])
        self.assertEqual(data['source_sha256'], verdict['source_sha256'])
        self.assertEqual(len(data['failed_checks']), 8)

    def test_cli_offers_the_tool(self):
        args = build_parser().parse_args(['work', '--worker-config', 'worker.json', '--task', 'Example', '--tools', 'check_ticket'])
        self.assertEqual(args.tools, ['check_ticket'])

    def test_dropped_leading_zero_has_actionable_error_before_rpc(self):
        worker = SSHWorker.__new__(SSHWorker)
        arguments = {'path': 'candidate.py', 'old_text': 'x', 'new_text': 'y', 'expected_sha256': 'a' * 63}
        verdict = worker._edit_file(arguments)
        self.assertIs(verdict['ok'], False)
        self.assertIn('got 63 characters', verdict['error'])
        self.assertIn('including leading zeros', verdict['error'])
        self.assertIn('Call read_file now', verdict['error'])

    def test_edit_error_refreshes_context_without_resetting_limits(self):
        self.enabled()
        count = [0]
        def model(messages, *unused):
            count[0] += 1
            if count[0] == 1:
                return call('write_file', {'path': 'candidate.py', 'content': 'bad = True\n',
                    'expected_sha256': 'a' * 64}, 'stale'), {}
            if count[0] == 2:
                self.assertFalse(any(item.get('tool_calls') for item in messages))
                self.assertIn('current source first', messages[-1]['content'])
                self.assertIn('recent_operations', messages[2]['content'])
                self.assertIn('write_file', messages[2]['content'])
                self.assertEqual(self.checkpoint()['tools_used'], 1)
                return self.write('a + b', 'repair')
            return self.finish()
        result = work(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual((result['model_turns'], result['tool_calls']), (3, 2))
        self.assertTrue(any('context_reset' in entry for entry in self.checkpoint()['history']))


if __name__ == '__main__':
    unittest.main()
