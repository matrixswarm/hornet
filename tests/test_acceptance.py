"""Ticket completion requires independently executed operator behavior checks."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import py_compile
import tempfile
import unittest
from unittest.mock import patch

from hornet.cli import build_parser
from hornet.coding import acceptance
from hornet.coding.agent import work, resume
from hornet.coding.worker import WorkerError
from hornet.project import HornetError, Project
from test_resume import LocalWorker, call


class CheckWorker(LocalWorker):
    def call(self, name, arguments=None):
        try:
            return super().call(name, arguments)
        except WorkerError as exc:
            return {'ok': False, 'error': str(exc)}


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hornet-acceptance-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.controller = root / 'controller'
        self.controller.mkdir()
        self.guest = root / 'guest'
        self.guest.mkdir()
        self.source = self.guest / 'candidate.py'
        self.source.write_text('def add(a, b):\n    return a - b\n')
        self.worker = CheckWorker(self.guest)
        self.project = Project(self.controller)
        self.plan = {'schema_version': 1, 'checks': [self.check()]}
        self.plan_path = self.controller / 'checks.json'
        self.write_plan()
        self.args = argparse.Namespace(task='Make add implement addition.', task_file=None,
            acceptance_file='checks.json', model='fixture', base_url='http://localhost:1234/v1',
            max_tokens=1024, timeout=30, max_turns=20, max_tools=20, max_repairs=2,
            protocol='lmstudio-json', context_chars=16000, tools=['read_file', 'write_file'],
            worker_config='worker.json', run_dir='runs')

    def check(self, ident='addition', python=None):
        return {'id': ident, 'description': 'add returns the sum for positive and cancelling inputs',
            'paths': ['candidate.py'], 'timeout': 5,
            'python': python or 'from candidate import add\nassert add(2, 3) == 5\nassert add(-2, 2) == 0\nprint("Addition verified")'}

    def write_plan(self):
        self.plan_path.write_text(json.dumps(self.plan), encoding='utf-8')

    def run_dir(self):
        return next((self.controller / 'runs').iterdir())

    def checkpoint(self):
        return json.loads((self.run_dir() / 'checkpoint.json').read_text())

    def resume_args(self):
        return argparse.Namespace(run=str(self.run_dir()))

    def finish(self, *unused):
        return {'role': 'assistant', 'content': 'The ticket is finished.'}, {}

    def write(self, expression, ident):
        return call('write_file', {'path': 'candidate.py', 'content': 'def add(a, b):\n    return ' + expression + '\n',
            'expected_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}, ident), {}

    def test_green_suite_and_model_claim_cannot_bypass_failed_check(self):
        self.args.max_repairs = 0
        result = work(self.project, self.args, self.worker, self.finish)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'acceptance_failed')
        self.assertEqual(result['tests_exit_code'], 0)
        self.assertEqual((result['acceptance_passed'], result['acceptance_total']), (0, 1))
        report = json.loads((self.run_dir() / 'acceptance-results.json').read_text())
        self.assertIn('AssertionError', report['results'][0]['output'])
        self.assertEqual(report['plan_sha256'], acceptance.digest(self.plan))

    def test_failed_checks_trigger_focused_repair_and_preserve_each_round(self):
        turn = [0]
        def model(messages, *unused):
            turn[0] += 1
            if turn[0] == 1:
                return self.write('a * b', 'wrong-edit')
            if turn[0] == 3:
                context = json.dumps(messages)
                self.assertIn('addition', context)
                self.assertIn('AssertionError', context)
                self.assertIn('candidate.py', context)
                self.assertIn(self.args.task, context)
                self.assertFalse(any(item.get('tool_calls') for item in messages))
                return self.write('a + b', 'repair')
            return self.finish()
        result = work(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['acceptance_status'], 'passed')
        self.assertEqual(result['repairs_used'], 1)
        self.assertEqual(result['verification_rounds'], 2)
        for index, passed in ((1, False), (2, True)):
            path = self.run_dir() / 'verification' / str(index)
            self.assertEqual(json.loads((path / 'acceptance-results.json').read_text())['ok'], passed)
            self.assertTrue((path / 'changes.json').exists())
            self.assertTrue((path / 'tests.json').exists())

    def test_identical_failed_verification_stops_without_spending_all_turns(self):
        result = work(self.project, self.args, self.worker, self.finish)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'verification_repeated_without_source_change')
        self.assertEqual(result['model_turns'], 2)
        self.assertEqual(result['repairs_used'], 1)

    def test_passing_checks_do_not_override_a_failed_suite(self):
        self.args.max_repairs = 0
        self.plan['checks'] = [self.check('observes', 'print("Independent check passed")')]
        self.write_plan()
        original = self.worker.call
        def failing_suite(name, arguments=None):
            return {'ok': False, 'exit_code': 1, 'output': 'Regression failed'} if name == 'run_tests' else original(name, arguments)
        self.worker.call = failing_suite
        turn = [0]
        def model(*unused):
            turn[0] += 1
            return self.write('a + b', 'edit') if turn[0] == 1 else self.finish()
        result = work(self.project, self.args, self.worker, model)
        self.assertFalse(result['ok'])
        self.assertEqual(result['acceptance_status'], 'passed')
        self.assertEqual(result['tests_exit_code'], 1)

    def test_passing_checks_alone_do_not_replace_new_changes(self):
        self.plan['checks'] = [self.check('observes', 'assert 2 + 3 == 5')]
        self.write_plan()
        result = work(self.project, self.args, self.worker, self.finish)
        self.assertFalse(result['ok'])
        self.assertEqual(result['changed_files'], [])
        self.assertEqual(result['acceptance_status'], 'passed')
        self.assertEqual(result['repairs_used'], 0)

    def test_total_turn_budget_still_bounds_verification_repairs(self):
        self.args.max_turns = 1
        result = work(self.project, self.args, self.worker, self.finish)
        self.assertFalse(result['ok'])
        self.assertEqual(result['model_turns'], 1)
        self.assertEqual(result['repairs_used'], 0)

    def test_empty_attempt_and_round_directories_survive_an_early_crash(self):
        with self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
        (self.run_dir() / 'attempts/2').mkdir()
        (self.run_dir() / 'verification/1').mkdir(parents=True)
        self.args.max_repairs = 0
        result = resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertNotIn('error', result)
        self.assertTrue((self.run_dir() / 'verification/1/acceptance-results.json').exists())

    def test_repair_limit_carries_across_resume(self):
        self.args.max_repairs = 1
        turn = [0]
        def model(*unused):
            turn[0] += 1
            if turn[0] == 2:
                return self.write('a * b', 'repair-one')
            return self.finish()
        result = work(self.project, self.args, self.worker, model)
        self.assertEqual(result['repairs_used'], 1)
        turn[0] = 0
        def again(*unused):
            turn[0] += 1
            return self.write('a / b', 'operator-resume') if turn[0] == 1 else self.finish()
        result = resume(self.project, self.resume_args(), self.worker, again)
        self.assertFalse(result['ok'])
        self.assertEqual(result['repairs_used'], 1)
        self.assertEqual(turn[0], 2)

    def test_copied_plan_survives_external_file_change_and_resume(self):
        def interrupted(*unused):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker, interrupted)
        self.plan_path.write_text('{"checks": []}')
        turn = [0]
        def model(*unused):
            turn[0] += 1
            return self.write('a + b', 'resume-edit') if turn[0] == 1 else self.finish()
        result = resume(self.project, self.resume_args(), self.worker, model)
        self.assertTrue(result['ok'])
        self.assertEqual(result['acceptance_sha256'], acceptance.digest(self.plan))

    def test_saved_plan_tampering_refuses_before_another_model_request(self):
        with self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
        (self.run_dir() / 'acceptance.json').write_text('{}')
        with self.assertRaises(HornetError):
            resume(self.project, self.resume_args(), self.worker, lambda *a: self.fail('Must not call model'))

    def test_all_checks_run_and_early_exit_is_failure(self):
        self.plan['checks'] = [self.check('exits', 'raise SystemExit(0)'), self.check('passes', 'print("Observed pass")')]
        reports = []
        result = acceptance.evaluate(self.worker, self.plan, lambda report: reports.append(copy.deepcopy(report)))
        self.assertFalse(result['ok'])
        self.assertEqual([item['passed'] for item in result['results']], [False, True])
        self.assertGreaterEqual(len(reports), 4)
        self.assertIn('SystemExit', result['results'][0]['output'])

    def test_zero_exit_without_completion_marker_is_failure(self):
        class Fake:
            def call(self, *unused):
                return {'ok': True, 'exit_code': 0, 'output': 'No completion marker'}
        self.assertFalse(acceptance.evaluate(Fake(), self.plan, lambda report: None)['ok'])

    def test_checks_ignore_stale_same_size_same_timestamp_bytecode(self):
        old = self.source.stat()
        py_compile.compile(str(self.source), doraise=True)
        self.source.write_text('def add(a, b):\n    return a + b\n')
        os.utime(self.source, ns=(old.st_atime_ns, old.st_mtime_ns))
        result = acceptance.evaluate(self.worker, self.plan, lambda report: None)
        self.assertTrue(result['ok'], result)

    def test_timeout_is_a_recorded_failed_check(self):
        item = self.check('times-out', 'import time\ntime.sleep(3)')
        item['timeout'] = 1
        result = acceptance.evaluate(self.worker, {'schema_version': 1, 'checks': [item]}, lambda report: None)
        self.assertFalse(result['ok'])
        self.assertTrue(result['results'][0]['timed_out'])

    def test_partial_results_survive_interruption_and_are_not_replayed(self):
        self.plan['checks'] = [self.check('first', 'print("First check")'), self.check('second', 'print("Second check")')]
        self.write_plan()
        original = self.worker.call
        count = [0]
        def interrupted(name, arguments=None):
            if name == 'run_command':
                count[0] += 1
                if count[0] == 2:
                    raise KeyboardInterrupt()
            return original(name, arguments)
        self.worker.call = interrupted
        with self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker, self.finish)
        report = json.loads((self.run_dir() / 'acceptance-results.json').read_text())
        self.assertEqual(report['status'], 'running')
        self.assertEqual(len(report['results']), 1)
        self.worker.call = original
        with self.assertRaisesRegex(HornetError, 'outcome is unknown'):
            resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertEqual(count[0], 2)

    def test_verification_source_mutation_blocks_completion(self):
        self.plan['checks'] = [self.check('mutates', 'from pathlib import Path\nPath("candidate.py").write_text("changed")')]
        self.write_plan()
        result = work(self.project, self.args, self.worker, self.finish)
        self.assertFalse(result['ok'])
        self.assertIn('Verification changed source', result['error'])
        self.assertEqual(self.checkpoint()['phase'], 'verification_in_flight')

    def test_three_identical_unsuccessful_edits_stop_and_resume_does_not_repeat(self):
        counter = [0]
        def bad(*unused):
            counter[0] += 1
            return call('write_file', {'path': 'candidate.py', 'content': 'wrong\n', 'expected_sha256': '0' * 64}, str(counter[0])), {}
        result = work(self.project, self.args, self.worker, bad)
        self.assertIn('three identical unsuccessful', result['error'])
        self.assertEqual(self.worker.calls.count('write_file'), 3)
        result = resume(self.project, self.resume_args(), self.worker, bad)
        self.assertIn('Repeated unsuccessful edit refused', result['error'])
        self.assertEqual(self.worker.calls.count('write_file'), 3)

    def test_no_plan_is_explicit_and_legacy_checkpoint_remains_resumable(self):
        self.args.acceptance_file = None
        with self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
        job = self.checkpoint()
        job['schema_version'] = 1
        for key in ('acceptance', 'acceptance_sha256', 'verification_rounds', 'repairs_used'):
            job.pop(key)
        job['settings'].pop('max_repairs')
        (self.run_dir() / 'checkpoint.json').write_text(json.dumps(job))
        result = resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertEqual(result['acceptance_status'], 'not_configured')
        self.assertEqual(result['acceptance_total'], 0)

    def test_bad_plans_fail_before_worker_access(self):
        mutations = [lambda p: p.update(checks=[]),
            lambda p: p['checks'].append(copy.deepcopy(p['checks'][0])),
            lambda p: p['checks'][0].update(timeout=True),
            lambda p: p['checks'][0].update(paths=['../escape.py']),
            lambda p: p['checks'][0].update(paths=['C:/escape.py']),
            lambda p: p['checks'][0].update(python='this is invalid syntax!'),
            lambda p: p['checks'][0].update(python='x' * 3001),
            lambda p: p['checks'][0].update(unexpected=True)]
        for change in mutations:
            with self.subTest(change=change):
                plan = copy.deepcopy(self.plan)
                change(plan)
                self.plan_path.write_text(json.dumps(plan))
                with self.assertRaises(HornetError):
                    work(self.project, self.args, None, self.finish)
        self.assertFalse((self.controller / 'runs').exists())

    def test_duplicate_json_keys_and_oversized_file_refused(self):
        for text in ('{"schema_version":1,"schema_version":1,"checks":[]}', ' ' * 32001):
            self.plan_path.write_text(text)
            with self.assertRaises(HornetError):
                acceptance.read(self.project, 'checks.json')

    def test_cli_exposes_acceptance_and_repair_options(self):
        args = build_parser().parse_args(['work', '--worker-config', 'worker.json', '--task', 'Fix addition',
            '--acceptance-file', 'checks.json', '--max-repairs', '1'])
        self.assertEqual(args.acceptance_file, 'checks.json')
        self.assertEqual(args.max_repairs, 1)
        self.args.max_repairs = 4
        with self.assertRaisesRegex(HornetError, 'max-repairs'):
            work(self.project, self.args, self.worker, self.finish)


if __name__ == '__main__':
    unittest.main()
