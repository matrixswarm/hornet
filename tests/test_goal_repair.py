"""Whole-goal failures route to bounded jobs without rewriting successful cells."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from hornet.cli import build_parser
from hornet import hive_status
from hornet.coding import acceptance, autonomous, cell_actions, goal_repair, goals, mission, questions, session
from hornet.project import HornetError
import test_autonomous as fixtures
import test_goals as goal_fixtures
from test_resume import call


class GoalRepairTests(unittest.TestCase):
    setUp = fixtures.AutonomousTests.setUp
    run_dir = fixtures.AutonomousTests.run_dir
    state = fixtures.AutonomousTests.state
    write = fixtures.AutonomousTests.write
    submit = fixtures.AutonomousTests.submit

    def prepare(self, repairs=1):
        self.supplied = copy.deepcopy(self.checks)
        self.args.acceptance_file = 'goal-checks.json'
        self.args.max_goal_repairs = repairs
        self.args.max_repairs = 0
        self.checks['checks'][0]['python'] = 'from parity_cell import is_even\nassert is_even(3) is False'
        self.repair_calls = 0
        self.originals = None

    def start(self, model=None):
        (self.root / 'goal-checks.json').write_text(json.dumps(self.supplied), encoding='utf-8')
        return autonomous.start(self.project, self.args, self.worker, model or self.model)

    def resume(self, model=None):
        return autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, model or self.model)

    def original_evidence(self):
        base = self.run_dir() / 'cells/check-even'
        return {str(path.relative_to(base)): path.read_bytes() for path in base.rglob('*') if path.is_file()}

    def model(self, messages, tools, *unused):
        if any('Whole-goal repair for completed cell' in str(message.get('content', '')) for message in messages):
            self.repair_calls += 1
            if self.originals is None:
                self.originals = self.original_evidence()
            if self.repair_calls == 1:
                return self.write('    return value % 2 == 0\n', 'repair')
            return {'role': 'assistant', 'content': 'Ready for the unchanged checks.'}, {}
        response, stats = fixtures.AutonomousTests.model(self, messages, tools, *unused)
        for call in response.get('tool_calls', []):
            function = call['function']
            if function['name'] == 'write_file':
                value = json.loads(function['arguments'])
                value['content'] = 'def is_even(value):\n    return False\n'
                function['arguments'] = json.dumps(value)
        return response, stats

    def test_final_failure_is_repaired_with_original_returns_and_checks_intact(self):
        self.prepare()
        result = self.start()
        self.assertTrue(result['ok'], result)
        state = autonomous.load(self.project, self.run_dir())
        self.assertEqual(len(state['goal_repairs']), 1)
        event = mission.read(self.run_dir() / state['goal_repairs'][0]['contract']['path'])
        failure = mission.read(self.run_dir() / event['failure']['path'])
        self.assertFalse(failure['ok'])
        self.assertTrue(mission.read(self.run_dir() / state['goal_verification']['path'])['ok'])
        self.assertEqual(state['goal_checks'], self.supplied)
        self.assertEqual(self.originals, self.original_evidence())
        self.assertIs(state['values']['check-even'], False)
        receipt = mission.read(self.run_dir() / state['goal_repairs'][0]['completion']['path'])
        self.assertEqual([row['path'] for row in receipt['changed_files']], ['parity_cell.py'])
        self.assertEqual(state['cell_usage']['check-even'], event['cell_tools_before'] + receipt['tools_used'])
        completion = mission.read(self.run_dir() / 'goal-completion.json')
        self.assertEqual(completion['goal_repairs'], [state['goal_repairs'][0]['completion']])

    def test_disabled_policy_stops_at_original_failure(self):
        self.prepare(0)
        result = self.start()
        self.assertFalse(result['ok'])
        self.assertIn('Overall goal acceptance', result['error'])
        self.assertEqual(self.repair_calls, 0)
        self.assertEqual(self.state()['goal_repairs'], [])

    def test_repair_never_resets_shared_model_budget(self):
        self.prepare()
        self.args.max_turns = 6
        result = self.start()
        self.assertFalse(result['ok'])
        self.assertLessEqual(result['model_turns'], 6)
        self.assertEqual(self.state()['goal_repairs'], [])
        self.assertEqual(self.repair_calls, 0)

    def test_repair_never_resets_shared_tool_budget(self):
        self.prepare()
        self.args.max_tools = 2
        result = self.start()
        self.assertFalse(result['ok'])
        self.assertLessEqual(result['tool_calls'], 2)
        self.assertEqual(self.repair_calls, 0)

    def test_connection_loss_after_edit_resumes_same_repair_and_preserves_history(self):
        self.prepare()
        def offline(messages, tools, *rest):
            if self.repair_calls == 1:
                raise HornetError('Model disconnected after repair edit')
            return self.model(messages, tools, *rest)
        first = self.start(offline)
        self.assertFalse(first['ok'])
        state = autonomous.load(self.project, self.run_dir())
        self.assertEqual(state['phase'], 'goal_repair')
        contract = mission.read(self.run_dir() / state['goal_repairs'][0]['contract']['path'])
        old_failure = (self.run_dir() / contract['failure']['path']).read_bytes()
        old_attempt = (self.run_dir() / 'attempts/1/result.json').read_bytes()
        self.assertTrue(self.resume()['ok'])
        self.assertEqual(len(self.state()['goal_repairs']), 1)
        self.assertEqual(old_failure, (self.run_dir() / contract['failure']['path']).read_bytes())
        self.assertEqual(old_attempt, (self.run_dir() / 'attempts/1/result.json').read_bytes())
        self.assertEqual(self.originals, self.original_evidence())

    def test_completed_repair_adopted_after_publication_interruption_without_new_inference(self):
        self.prepare()
        with patch.object(goal_repair, 'publish', side_effect=HornetError('Publication interrupted')):
            self.assertFalse(self.start()['ok'])
        result = self.resume(lambda *args: self.fail('Completed repair must not run a new model request'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(self.state()['goal_repairs']), 1)

    def test_repair_question_waits_and_exact_cell_resumes_same_job(self):
        self.prepare()
        asked = []
        def model(messages, tools, *rest):
            repair = any('Whole-goal repair for completed cell' in str(m.get('content', '')) for m in messages)
            if repair and not asked:
                asked.append(True)
                return call('ask_user', {'question': 'Keep the frozen parity behavior?'}, 'repair-question'), {}
            return self.model(messages, tools, *rest)
        result = self.start(model)
        self.assertEqual(result['status'], 'waiting_for_user', result)
        self.assertEqual(result['cell_id'], 'check-even')
        run = self.run_dir()
        self.assertEqual(questions.listing(self.project, run)['questions'][0]['question_id'], result['question_id'])
        again = self.resume(lambda *args: self.fail('Question must wait without inference'))
        self.assertEqual(again['question_id'], result['question_id'])
        questions.respond(self.project, run, result['question_id'], 'Yes, keep the frozen behavior.')
        final = cell_actions.resume(self.project, run, 'check-even', self.worker, model)
        self.assertTrue(final['ok'], final)
        self.assertEqual(len(self.state()['goal_repairs']), 1)

    def test_hive_observes_active_and_stopped_repair_without_replaying_original_job(self):
        self.prepare()
        def offline(messages, tools, *rest):
            if self.repair_calls == 1:
                raise HornetError('Repair connection lost')
            return self.model(messages, tools, *rest)
        self.assertFalse(self.start(offline)['ok'])
        run = self.run_dir()
        before = {str(p): p.read_bytes() for p in run.rglob('*') if p.is_file()}
        with patch.object(hive_status, 'runner', return_value=(True, 'goal_repair', {})):
            observed = hive_status.snapshot(self.project, run=str(run))
        self.assertTrue(observed['ok'], observed)
        row = next(c for c in observed['cells'] if c['id'] == 'check-even')
        self.assertEqual(row['status'], 'repairing')
        self.assertTrue(row['active'])
        self.assertTrue(row['prior_verified'])
        self.assertFalse(row['verified'])
        self.assertEqual(observed['goals'][0]['goal_repairs']['active'], 1)
        stopped = hive_status.snapshot(self.project, run=str(run))
        row = next(c for c in stopped['cells'] if c['id'] == 'check-even')
        self.assertTrue(row['repairing'])
        self.assertFalse(row['active'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in run.rglob('*') if p.is_file()})

    def test_repair_cannot_write_outside_the_original_cell(self):
        self.prepare()
        tried = []
        def outside(messages, tools, *rest):
            if not tried and any('Whole-goal repair for completed cell' in str(m.get('content', '')) for m in messages):
                tried.append(True)
                return call('write_file', {'path': 'unowned.py', 'content': 'x = 1\n', 'expected_sha256': None}, 'outside'), {}
            return self.model(messages, tools, *rest)
        self.assertTrue(self.start(outside)['ok'])
        self.assertFalse((self.guest / 'unowned.py').exists())

    def test_unknown_final_check_outcome_does_not_dispatch_or_repeat(self):
        self.prepare()
        evaluate = acceptance.evaluate
        def interrupted(worker, plan, save):
            if plan == self.supplied:
                raise HornetError('Worker connection lost during final checks')
            return evaluate(worker, plan, save)
        with patch.object(acceptance, 'evaluate', side_effect=interrupted):
            self.assertFalse(self.start()['ok'])
        self.assertEqual(self.state()['goal_repairs'], [])
        with self.assertRaisesRegex(HornetError, 'outcome is unknown'):
            self.resume(lambda *args: self.fail('Unknown check outcome must stay stopped'))

    def test_changed_source_before_repair_blocks_dispatch(self):
        self.prepare()
        prepare = goal_repair.prepare
        def changed(*args):
            result = prepare(*args)
            self.source.write_text('def is_even(value):\n    return True\n')
            return result
        with patch.object(goal_repair, 'prepare', side_effect=changed):
            result = self.start()
        self.assertFalse(result['ok'])
        self.assertIn('Source changed before starting', result['error'])
        self.assertEqual(self.repair_calls, 0)

    def test_published_dependency_value_cannot_be_changed_to_pass_goal_check(self):
        self.prepare()
        self.checks['checks'][0]['python'] = 'from parity_cell import is_even\nassert type(is_even(3)) is bool'
        self.supplied['checks'][0]['python'] = 'from parity_cell import is_even\nassert is_even(2) is True'
        def wrong(messages, tools, *rest):
            response, stats = self.model(messages, tools, *rest)
            if self.repair_calls == 1:
                function = response['tool_calls'][0]['function']
                value = json.loads(function['arguments'])
                value['content'] = 'def is_even(value):\n    return True\n'
                function['arguments'] = json.dumps(value)
            return response, stats
        result = self.start(wrong)
        self.assertFalse(result['ok'])
        self.assertIs(self.state()['values']['check-even'], False)
        self.assertFalse((self.run_dir() / 'goal-completion.json').exists())
        job = goal_repair.job_path(self.run_dir(), goal_repair.active(self.run_dir(), self.state())[1])
        report = mission.read(job / 'acceptance-results.json')
        typed = next(row for row in report['results'] if row['id'] == 'typed-return')
        self.assertIn('preserve the published dependency value', typed['output'])

    def prepare_two_rounds(self, count):
        self.prepare(count)
        base = self.supplied['checks'][0]
        self.supplied['checks'] = [dict(base, id='positive', python='from parity_cell import is_even\nassert is_even(2) is True'),
                                  dict(base, id='negative', python='from parity_cell import is_even\nassert is_even(-3) is False')]
        def staged(messages, tools, *rest):
            if any('Whole-goal repair for completed cell' in str(m.get('content', '')) for m in messages):
                self.repair_calls += 1
                if self.repair_calls == 1:
                    return self.write('    return value <= 0 or value % 2 == 0\n', 'first-repair')
                if self.repair_calls == 3:
                    return self.write('    return value % 2 == 0\n', 'second-repair')
                return {'role': 'assistant', 'content': 'Check the current repair.'}, {}
            return self.model(messages, tools, *rest)
        return staged

    def test_second_round_uses_remaining_budget_and_retains_first_failure(self):
        model = self.prepare_two_rounds(2)
        result = self.start(model)
        self.assertTrue(result['ok'], result)
        state = autonomous.load(self.project, self.run_dir())
        self.assertEqual(len(state['goal_repairs']), 2)
        failures = []
        for row in state['goal_repairs']:
            contract = mission.read(self.run_dir() / row['contract']['path'])
            failures.append(contract['failure']['path'])
            self.assertFalse(mission.read(self.run_dir() / contract['failure']['path'])['ok'])
        self.assertEqual(len(set(failures)), 2)
        self.assertLessEqual(result['model_turns'], self.args.max_turns)

    def test_round_cap_prevents_unlimited_new_repair_jobs(self):
        result = self.start(self.prepare_two_rounds(1))
        self.assertFalse(result['ok'])
        self.assertIn('Overall goal acceptance', result['error'])
        self.assertEqual(len(self.state()['goal_repairs']), 1)
        self.assertEqual(self.repair_calls, 2)

    def test_tampered_failure_policy_and_repair_evidence_refuse_loading(self):
        self.prepare()
        self.assertTrue(self.start()['ok'])
        state = self.state()
        contract = mission.read(self.run_dir() / state['goal_repairs'][0]['contract']['path'])
        targets = [self.run_dir() / contract['failure']['path'], self.run_dir() / 'goal-repair-policy.json',
                   self.run_dir() / state['goal_repairs'][0]['completion']['path']]
        for path in targets:
            before = path.read_bytes()
            path.write_text('{}', encoding='utf-8')
            with self.subTest(path=path.name), self.assertRaises(HornetError):
                autonomous.load(self.project, self.run_dir())
            path.write_bytes(before)
        changed = copy.deepcopy(state)
        changed['cell_usage']['check-even'] = 0
        with self.assertRaisesRegex(HornetError, 'usage was reset'):
            goal_repair.validate(self.run_dir(), changed)
        changed = copy.deepcopy(state)
        changed.pop('goal_repair_policy')
        with self.assertRaisesRegex(HornetError, 'lost its binding'):
            goal_repair.validate(self.run_dir(), changed)
        for invalid in (True, 0, 2, '../outside'):
            changed = dict(state, active_goal_repair=invalid)
            with self.subTest(active=invalid), self.assertRaises(HornetError):
                goal_repair.active(self.run_dir(), changed)

    def test_ambiguous_cross_cell_and_unknown_failures_do_not_route(self):
        self.prepare(0)
        self.assertFalse(self.start()['ok'])
        state = self.state()
        report = mission.read(self.run_dir() / state['goal_verification']['path'])
        self.assertIsNotNone(goal_repair.route(state, self.supplied, report))
        nested = copy.deepcopy(state)
        parent = dict(nested['plan']['cells'][0], id='parent')
        nested['routes'] = {'parent': {'plan': nested['plan']}}
        nested['plan'] = dict(nested['plan'], cells=[parent])
        nested['values']['parent'] = False
        self.assertEqual(goal_repair.route(nested, self.supplied, report)[0]['id'], 'check-even')
        for update in ({'exit_code': None}, {'exit_code': 0}, {'timed_out': True}, {'truncated': True}, {'error': 'transport failed'}):
            unknown = copy.deepcopy(report)
            unknown['results'][0].update(update)
            self.assertIsNone(goal_repair.route(state, self.supplied, unknown))
        ambiguous = copy.deepcopy(state)
        sibling = dict(ambiguous['plan']['cells'][0], id='other')
        ambiguous['plan']['cells'].append(sibling)
        ambiguous['values']['other'] = False
        self.assertIsNone(goal_repair.route(ambiguous, self.supplied, report))
        cross = copy.deepcopy(self.supplied)
        cross['checks'][0]['paths'].append('unowned.py')
        report['results'][0]['paths'].append('unowned.py')
        report['plan_sha256'] = acceptance.digest(cross)
        self.assertIsNone(goal_repair.route(state, cross, report))

    def test_policy_and_cli_are_bounded(self):
        for count in (-1, 3, True, '1'):
            with self.assertRaises(HornetError):
                goal_repair.configure(argparse.Namespace(max_goal_repairs=count))
        for command in (['autowork', '--worker-config', 'worker.json'], ['revise', 'runs/parent', '--cell', 'fix']):
            args = build_parser().parse_args(command + ['--goal', 'Fix parity', '--max-goal-repairs', '2'])
            self.assertEqual(goal_repair.configure(args)['max_repairs'], 2)


class InheritedRepairTests(unittest.TestCase):
    setUp = goal_fixtures.GoalTests.setUp
    start = goal_fixtures.GoalTests.start

    def model(self, messages, tools, *rest):
        if any('Whole-goal repair for completed cell' in str(m.get('content', '')) for m in messages):
            self.repairs += 1
            if self.repairs == 1:
                source = goal_fixtures.ForkWorker.latest / 'number.py'
                content = 'def double(n):\n    return n * 2\n\ndef triple(n):\n    return n * 3\n'
                return call('write_file', {'path': 'number.py', 'content': content,
                    'expected_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}, 'repair'), {}
            return {'role': 'assistant', 'content': 'Ready for original and inherited checks.'}, {}
        return goal_fixtures.GoalTests.model(self, messages, tools, *rest)

    def test_revision_repairs_parent_regression_in_its_own_workspace(self):
        self.repairs = 0
        first = self.start()
        self.assertTrue(first['ok'], first)
        parent = Path(first['run_dir'])
        before = {str(p): p.read_bytes() for p in parent.rglob('*') if p.is_file()}
        source = goal_fixtures.ForkWorker(self.base / first['workspace'], self.base)
        self.args = build_parser().parse_args(['revise', str(parent), '--cell', 'double', '--run-dir', 'runs',
            '--goal', 'Add triple(n) to number.py and preserve existing behavior.'])
        self.break_parent, self.turn = True, 0
        result = goals.revise(self.project, self.args, source, self.model)
        run = Path(result['run_dir'])
        if not result['ok']:
            # Keep the worker evidence in CI output before TemporaryDirectory cleanup.
            reports = {path.name: mission.read(path)
                       for path in sorted((run / 'final-checks').glob('*.json'))}
            self.fail(json.dumps({'result': result, 'final_checks': reports}, indent=2))
        state = autonomous.load(self.project, run)
        contract = mission.read(run / state['goal_repairs'][0]['contract']['path'])
        self.assertTrue(contract['gate'].startswith('revision:'))
        self.assertTrue(goals.regression_evidence(run, state)['reports'])
        followup = goals.route_cell(run, state, 'double')
        self.assertEqual(followup['source_snapshot'], state['snapshot'])
        self.assertEqual(followup['source_cell']['repair_completions'], [state['goal_repairs'][0]['completion']])
        self.assertEqual(goals.file_index(run, state)['goal_repairs'], state['goal_repairs'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in parent.rglob('*') if p.is_file()})
        self.assertNotIn('triple', (source.worker.root / 'number.py').read_text())


if __name__ == '__main__':
    unittest.main()
