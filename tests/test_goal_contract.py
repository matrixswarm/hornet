"""Supplied whole-goal acceptance survives models, retries and revisions."""
import argparse
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from hornet.cli import build_parser
from hornet.coding import acceptance, autonomous, goal_contract, goals, mission, planning
from hornet.project import HornetError
import test_autonomous as auto_fixtures
import test_goals as goal_fixtures


class GoalContractTests(unittest.TestCase):
    setUp = auto_fixtures.AutonomousTests.setUp
    model = auto_fixtures.AutonomousTests.model
    submit = auto_fixtures.AutonomousTests.submit
    write = auto_fixtures.AutonomousTests.write
    run_dir = auto_fixtures.AutonomousTests.run_dir
    state = auto_fixtures.AutonomousTests.state

    def supply(self, checks=None):
        self.supplied = copy.deepcopy(checks or self.checks)
        self.input_file = self.root / 'operator-checks.json'
        self.input_file.write_text(json.dumps(self.supplied), encoding='utf-8')
        self.args.acceptance_file = 'operator-checks.json'

    def start(self, model=None):
        return autonomous.start(self.project, self.args, self.worker, model or self.model)

    def resume(self, model=None):
        return autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())),
                                 self.worker, model or self.model)

    def offline(self, *unused):
        raise HornetError('Model temporarily unavailable')

    def test_cli_accepts_same_flag_for_goal_and_revision(self):
        parser = build_parser()
        for command in (['autowork', '--worker-config', 'worker.json'], ['revise', 'runs/parent', '--cell', 'fix']):
            args = parser.parse_args(command + ['--goal', 'Fix parity', '--acceptance-file', 'checks.json'])
            self.assertEqual(args.acceptance_file, 'checks.json')

    def test_supplied_checks_replace_goal_generation_and_bind_completion(self):
        self.supply()
        def model(messages, tools, *rest):
            names = {item['function']['name'] for item in tools}
            self.assertNotIn('submit_goal_checks', names)
            if 'submit_intake' in names:
                self.assertIn('Supplied goal acceptance criteria', str(messages))
                self.assertNotIn(self.supplied['checks'][0]['python'], str(messages))
            return self.model(messages, tools, *rest)
        result = self.start(model)
        self.assertTrue(result['ok'], result)
        state = autonomous.load(self.project, self.run_dir())
        self.assertEqual(state['goal_checks'], self.supplied)
        self.assertEqual(state['model_turns'], 5)
        self.assertIn('verifier-check-even', state['roles'])
        completed = mission.read(self.run_dir() / 'goal-completion.json')
        self.assertEqual(completed['operator_acceptance'], state['operator_acceptance'])
        report = mission.read(self.run_dir() / state['goal_verification']['path'])
        self.assertTrue(report['ok'])
        self.assertEqual(report['plan_sha256'], acceptance.digest(self.supplied))

    def test_green_cell_cannot_bypass_failed_supplied_goal_check(self):
        self.supply()
        self.checks['checks'][0]['python'] = 'from parity_cell import is_even\nassert is_even(3) is False'
        def model(messages, tools, *rest):
            response, stats = self.model(messages, tools, *rest)
            for call in response.get('tool_calls', []):
                function = call['function']
                if function['name'] == 'write_file':
                    value = json.loads(function['arguments'])
                    value['content'] = 'def is_even(value):\n    return False\n'
                    function['arguments'] = json.dumps(value)
            return response, stats
        result = self.start(model)
        self.assertFalse(result['ok'], result)
        self.assertIn('Overall goal acceptance checks failed', result['error'])
        state = autonomous.load(self.project, self.run_dir())
        self.assertTrue(state['cells']['check-even']['result']['ok'])
        self.assertIs(state['values']['check-even'], False)
        self.assertFalse((self.run_dir() / 'goal-completion.json').exists())
        report = mission.read(self.run_dir() / state['goal_verification']['path'])
        self.assertFalse(report['ok'])
        self.assertIn('AssertionError', report['results'][0]['output'])

    def test_resume_uses_frozen_copy_and_preserves_failed_attempt(self):
        self.supply()
        self.assertFalse(self.start(self.offline)['ok'])
        frozen = (self.run_dir() / goal_contract.FILENAME).read_bytes()
        failed = (self.run_dir() / 'attempts/1/result.json').read_bytes()
        self.input_file.write_text('the caller changed or removed the original', encoding='utf-8')
        self.assertTrue(self.resume()['ok'])
        self.assertEqual(frozen, (self.run_dir() / goal_contract.FILENAME).read_bytes())
        self.assertEqual(failed, (self.run_dir() / 'attempts/1/result.json').read_bytes())
        self.assertEqual(self.state()['goal_checks'], self.supplied)

    def test_plan_review_wait_keeps_supplied_gate_without_model_polling(self):
        self.supply()
        self.args.plan_mode = 'review'
        first = self.start()
        self.assertEqual(first['status'], 'waiting_for_plan_approval')
        frozen = (self.run_dir() / goal_contract.FILENAME).read_bytes()
        self.input_file.unlink()
        parked = self.resume(lambda *a: self.fail('No inference while awaiting approval'))
        self.assertEqual(parked['status'], first['status'])
        planning.command(self.project, argparse.Namespace(run=str(self.run_dir()), plan_action='approve',
                                                         revision=1, feedback=''))
        self.assertTrue(self.resume()['ok'])
        self.assertEqual(frozen, (self.run_dir() / goal_contract.FILENAME).read_bytes())

    def test_invalid_input_refused_before_worker_and_model_calls(self):
        self.supply()
        self.input_file.write_text('{"schema_version":1,"checks":[],"checks":[]}', encoding='utf-8')
        with patch.object(self.worker, 'call', side_effect=AssertionError('worker must stay idle')):
            with self.assertRaises(HornetError):
                self.start(lambda *a: self.fail('model must stay idle'))
        self.assertFalse((self.root / 'recipes').exists())

    def test_explicit_scope_conflict_refused_before_work(self):
        self.supply()
        self.args.scope_files = ['another.py']
        with patch.object(self.worker, 'call', side_effect=AssertionError('worker must stay idle')):
            with self.assertRaisesRegex(HornetError, 'declared production files'):
                self.start()

    def test_plan_cannot_omit_supplied_check_file(self):
        self.supply()
        self.assertFalse(self.start(self.offline)['ok'])
        state = autonomous.load(self.project, self.run_dir())
        wrong = copy.deepcopy(self.plan)
        wrong['cells'][0]['files'] = ['unrelated.py']
        with self.assertRaisesRegex(HornetError, 'parity_cell.py'):
            goal_contract.validate_plan(self.run_dir(), state, wrong)
        wrong['cells'][0].update(files=['parity_cell.py'], kind='research')
        with self.assertRaisesRegex(HornetError, 'parity_cell.py'):
            goal_contract.validate_plan(self.run_dir(), state, wrong)

    def test_bad_intake_is_rejected_before_any_coding(self):
        self.supply()
        self.plan['cells'][0]['files'] = ['unrelated.py']
        self.plan['cells'][0]['result']['path'] = 'unrelated.py'
        before = self.source.read_bytes()
        result = self.start()
        self.assertFalse(result['ok'])
        self.assertIn('six attempts', result['error'])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.worker_turn, 0)
        self.assertIn('Supplied goal checks', str(self.state()['roles']['intake']['history']))

    def test_eight_supplied_checks_use_existing_acceptance_limits(self):
        checks = copy.deepcopy(self.checks)
        checks['checks'] = [dict(checks['checks'][0], id='case-' + str(i)) for i in range(8)]
        self.supply(checks)
        result = self.start()
        self.assertTrue(result['ok'], result)
        state = autonomous.load(self.project, self.run_dir())
        report = mission.read(self.run_dir() / state['goal_verification']['path'])
        self.assertEqual(len(report['results']), 8)

    def test_changed_frozen_checks_block_resume(self):
        self.supply()
        self.start(self.offline)
        path = self.run_dir() / goal_contract.FILENAME
        changed = copy.deepcopy(self.supplied)
        changed['checks'][0]['python'] = 'assert True'
        path.write_text(json.dumps(changed), encoding='utf-8')
        with self.assertRaises(HornetError):
            self.resume(lambda *a: self.fail('No inference after tampering'))

    def test_missing_frozen_checks_and_lost_binding_are_refused(self):
        self.supply()
        self.start(self.offline)
        state = self.state()
        no_binding = dict(state)
        del no_binding['operator_acceptance']
        with self.assertRaisesRegex(HornetError, 'lost their checkpoint binding'):
            goal_contract.read(self.run_dir(), no_binding)
        (self.run_dir() / goal_contract.FILENAME).unlink()
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())

    def test_generated_gate_cannot_replace_supplied_contract(self):
        self.supply()
        self.start(self.offline)
        state = self.state()
        state['goal_checks'] = {'schema_version': 1, 'checks': [dict(self.supplied['checks'][0], python='assert True')]}
        with self.assertRaisesRegex(HornetError, 'differ from the supplied contract'):
            goal_contract.validate(self.run_dir(), state)

    def test_changed_final_report_is_rejected(self):
        self.supply()
        self.assertTrue(self.start()['ok'])
        state = self.state()
        path = self.run_dir() / state['goal_verification']['path']
        report = mission.read(path)
        report['results'][0]['output'] = 'altered after verification'
        path.write_text(json.dumps(report), encoding='utf-8')
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())


class RevisionContractTests(unittest.TestCase):
    setUp = goal_fixtures.GoalTests.setUp
    model = goal_fixtures.GoalTests.model
    start = goal_fixtures.GoalTests.start

    def test_new_revision_gate_adds_to_inherited_parent_checks(self):
        first = self.start()
        self.assertTrue(first['ok'], first)
        parent = Path(first['run_dir'])
        before = {p.relative_to(parent).as_posix(): p.read_bytes() for p in parent.rglob('*') if p.is_file()}
        checks = {'schema_version': 1, 'checks': [{'id': 'negative-triple',
            'description': 'Triple negative inputs', 'paths': ['number.py'], 'timeout': 5,
            'python': 'from number import triple\nassert triple(-3) == -9'}]}
        (self.host / 'new-checks.json').write_text(json.dumps(checks), encoding='utf-8')
        self.args = build_parser().parse_args(['revise', str(parent), '--cell', 'double', '--run-dir', 'runs',
            '--goal', 'Add triple(n) returning n * 3 to number.py. Preserve double(n).',
            '--acceptance-file', 'new-checks.json'])
        source = goal_fixtures.ForkWorker(self.base / first['workspace'], self.base)
        self.turn = 0
        result = goals.revise(self.project, self.args, source, self.model)
        self.assertTrue(result['ok'], result)
        run = Path(result['run_dir'])
        state = autonomous.load(self.project, run)
        self.assertEqual(state['goal_checks'], checks)
        self.assertTrue(goals.regression_evidence(run, state)['reports'])
        self.assertEqual(before, {p.relative_to(parent).as_posix(): p.read_bytes()
                                  for p in parent.rglob('*') if p.is_file()})

    def test_revision_keeps_supplied_parent_gate_and_original_evidence(self):
        checks = {'schema_version': 1, 'checks': [{'id': 'negative-double',
            'description': 'Double negative inputs as well', 'paths': ['number.py'], 'timeout': 5,
            'python': 'from number import double\nassert double(-3) == -6'}]}
        source_file = self.host / 'parent-checks.json'
        source_file.write_text(json.dumps(checks), encoding='utf-8')
        self.args.acceptance_file = source_file.name
        first = self.start()
        self.assertTrue(first['ok'], first)
        parent = Path(first['run_dir'])
        before = {p.relative_to(parent).as_posix(): p.read_bytes() for p in parent.rglob('*') if p.is_file()}
        source_file.write_text('{}', encoding='utf-8')
        self.args = build_parser().parse_args(['revise', str(parent), '--cell', 'double', '--run-dir', 'runs',
            '--goal', 'Add triple(n) returning n * 3 to number.py. Preserve double(n).'])
        source = goal_fixtures.ForkWorker(self.base / first['workspace'], self.base)
        self.turn = 0
        def regress(messages, tools, *rest):
            response, stats = self.model(messages, tools, *rest)
            for call in response.get('tool_calls', []):
                function = call['function']
                if function['name'] == 'write_file':
                    value = json.loads(function['arguments'])
                    value['content'] = value['content'].replace('n * 2', '(6 if n == 3 else n * 5)')
                    function['arguments'] = json.dumps(value)
            return response, stats
        result = goals.revise(self.project, self.args, source, regress)
        self.assertFalse(result['ok'], result)
        self.assertIn('Inherited revision checks failed', result['error'])
        run = Path(result['run_dir'])
        self.assertFalse((run / 'goal-completion.json').exists())
        inherited = mission.read(run / 'revision.json')['regressions']
        self.assertIn(checks, [item['plan'] for item in inherited])
        self.assertEqual(before, {p.relative_to(parent).as_posix(): p.read_bytes()
                                  for p in parent.rglob('*') if p.is_file()})
        self.assertNotIn('triple', (source.worker.root / 'number.py').read_text())


if __name__ == '__main__':
    unittest.main()
