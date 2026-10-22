"""Resume real file edits in disposable fixtures, including crash boundaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hornet.coding.agent import resume, work
from hornet.coding.locking import WorkerLock
from hornet.coding.worker import Worker
from hornet.project import HornetError, Project


def call(name, arguments, ident):
    return {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}


class LocalWorker:
    def __init__(self, root):
        self.worker = Worker(root)
        self.binding = {'machine_id': 'fixture', 'root': str(root), 'connection': 'pinned'}
        self.calls = []

    def call(self, name, arguments=None):
        self.calls.append(name)
        if name == 'status':
            return {'ok': True, 'ready': True, 'platform': 'linux', 'uid': 1000}
        if name == 'snapshot':
            return {'ok': True, 'binding': self.binding.copy(), 'files': {
                self.worker.rel(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.worker.files()}}
        if name == 'changes':
            return {'ok': True, 'files': [{'path': self.worker.rel(p), 'content': p.read_text()} for p in self.worker.files()]}
        if name == 'run_tests':
            return {'ok': True, 'exit_code': 0, 'output': 'Fixture checks passed'}
        return self.worker.call(name, arguments or {})


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hornet-resume-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.controller = root / 'controller'
        self.controller.mkdir()
        guest = root / 'guest'
        guest.mkdir()
        self.path = guest / 'a.py'
        self.path.write_text('value = 1\n')
        self.worker = LocalWorker(guest)
        self.project = Project(self.controller)
        self.args = argparse.Namespace(task='Set value to 2, then verify it', task_file=None, model='fixture',
            base_url='http://127.0.0.1:1234/v1', max_tokens=1024, timeout=60, max_turns=8, max_tools=8,
            protocol='lmstudio-json', context_chars=8000, worker_config='worker.json', run_dir='runs')

    def run_dir(self):
        return next((self.controller / 'runs').iterdir())

    def resume_args(self):
        return argparse.Namespace(run=str(self.run_dir()), model=None, base_url=None, max_turns=None, max_tools=None)

    def checkpoint(self):
        return json.loads((self.run_dir() / 'checkpoint.json').read_text())

    def confirmed_edit_then_interrupt(self):
        count = [0]
        def model(*unused):
            count[0] += 1
            if count[0] == 1:
                return call('write_file', {'path': 'a.py', 'content': 'value = 2\n',
                    'expected_sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}, 'edit-once'), {}
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker, model)

    def finish(self, *unused):
        return {'role': 'assistant', 'content': 'Verified the current files.'}, {}

    def test_confirmed_edit_survives_and_is_not_replayed(self):
        self.confirmed_edit_then_interrupt()
        self.assertEqual(self.path.read_text(), 'value = 2\n')
        self.assertEqual(self.checkpoint()['phase'], 'model_request')
        result = resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertTrue(result['ok'])
        self.assertEqual(result['changed_files'], ['a.py'])
        self.assertEqual(result['attempt'], 2)
        self.assertEqual(self.worker.calls.count('write_file'), 1)
        old = json.loads((self.run_dir() / 'attempts/1/result.json').read_text())
        self.assertEqual(old['status'], 'cancelled')
        self.assertEqual(self.checkpoint()['phase'], 'complete')

    def test_workspace_drift_refuses_before_model_or_write(self):
        self.confirmed_edit_then_interrupt()
        self.path.write_text('external change\n')
        with self.assertRaisesRegex(HornetError, 'workspace files changed'):
            resume(self.project, self.resume_args(), self.worker, lambda *args: self.fail('No model request permitted'))
        self.assertEqual(self.path.read_text(), 'external change\n')

    def test_new_file_drift_and_worker_identity_change_refused(self):
        self.confirmed_edit_then_interrupt()
        extra = self.path.parent / 'extra'
        extra.write_text('outside change')
        with self.assertRaisesRegex(HornetError, 'workspace files changed'):
            resume(self.project, self.resume_args(), self.worker, self.finish)
        extra.unlink()
        self.worker.binding['connection'] = 'different pin'
        with self.assertRaisesRegex(HornetError, 'worker identity'):
            resume(self.project, self.resume_args(), self.worker, self.finish)

    def test_unknown_inflight_mutation_is_never_replayed(self):
        original = self.worker.call
        def crashing(name, arguments=None):
            result = original(name, arguments)
            if name == 'write_file':
                raise KeyboardInterrupt()
            return result
        self.worker.call = crashing
        def model(*unused):
            return call('write_file', {'path': 'a.py', 'content': 'value = 2\n',
                'expected_sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}, 'uncertain'), {}
        with self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker, model)
        self.assertEqual(self.checkpoint()['phase'], 'tool_in_flight')
        self.assertEqual(self.path.read_text(), 'value = 2\n')
        with self.assertRaisesRegex(HornetError, 'outcome is unknown'):
            resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertEqual(self.worker.calls.count('write_file'), 1)

    def test_lost_read_is_marked_and_not_blindly_replayed(self):
        original = self.worker.call
        def crashing(name, arguments=None):
            if name == 'read_file':
                raise KeyboardInterrupt()
            return original(name, arguments)
        self.worker.call = crashing
        with self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker,
                 lambda *args: (call('read_file', {'path': 'a.py'}, 'lost-read'), {}))
        self.worker.call = original
        result = resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertFalse(result['ok'])  # No changes is still not completion.
        self.assertTrue(any(item.get('result', {}).get('interrupted') for item in self.checkpoint()['history']))

    def test_stale_dead_owner_is_recovered_only_for_this_job(self):
        self.confirmed_edit_then_interrupt()
        lock_path = self.controller / 'worker.json.run.lock'
        lock_path.write_text(json.dumps({'pid': 123456, 'run_dir': str(self.run_dir())}))
        with patch('hornet.coding.locking.process_alive', return_value=False):
            result = resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertTrue(result['ok'])
        self.assertFalse(lock_path.exists())
        self.assertTrue(list(self.run_dir().glob('lock-recovery-*.json')))

    def test_active_owner_and_another_jobs_stale_lock_refused(self):
        self.confirmed_edit_then_interrupt()
        lock_path = self.controller / 'worker.json.run.lock'
        for alive, owner in ((True, str(self.run_dir())), (False, 'other-run')):
            lock_path.write_text(json.dumps({'pid': 123456, 'run_dir': owner}))
            with patch('hornet.coding.locking.process_alive', return_value=alive), self.assertRaises(HornetError):
                resume(self.project, self.resume_args(), self.worker, self.finish)
            self.assertTrue(lock_path.exists())

    def test_completed_and_legacy_run_refused(self):
        self.confirmed_edit_then_interrupt()
        resume(self.project, self.resume_args(), self.worker, self.finish)
        with self.assertRaisesRegex(HornetError, 'already completed'):
            resume(self.project, self.resume_args(), self.worker, self.finish)
        (self.run_dir() / 'checkpoint.json').unlink()
        with self.assertRaisesRegex(HornetError, 'before 0.4.0'):
            resume(self.project, self.resume_args(), self.worker, self.finish)

    def test_override_model_preserves_total_budgets_and_audit(self):
        self.confirmed_edit_then_interrupt()
        args = self.resume_args()
        args.model = 'replacement'
        def model(messages, tools, selected_model, *unused):
            self.assertEqual(selected_model, 'replacement')
            self.assertIn('Verified resume checkpoint', messages[2]['content'])
            self.assertIn('Read the relevant current source first', messages[3]['content'])
            self.assertFalse(any(message.get('tool_calls') for message in messages))
            return self.finish()
        result = resume(self.project, args, self.worker, model)
        self.assertEqual(result['model_turns'], 3)
        self.assertEqual(result['tool_calls'], 1)
        self.assertEqual(result['model'], 'replacement')

    def test_saved_dispatch_can_continue_without_repeating_model_request(self):
        import hornet.coding.session as session
        original = session.save
        cut = [False]
        def crashing(run, job):
            original(run, job)
            if job['phase'] == 'dispatch' and not cut[0]:
                cut[0] = True
                raise KeyboardInterrupt()
        with patch('hornet.coding.session.save', side_effect=crashing), self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker, lambda *args: (call('write_file', {
                'path': 'a.py', 'content': 'value = 2\n',
                'expected_sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}, 'planned'), {}))
        result = resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertTrue(result['ok'])
        self.assertEqual(self.worker.calls.count('write_file'), 1)

    def test_os_guard_prevents_a_second_controller(self):
        path = self.controller / 'guard.run.lock'
        with WorkerLock(path):
            with self.assertRaisesRegex(HornetError, 'already holds'):
                with WorkerLock(path):
                    self.fail('Must not acquire twice')

    def test_unknown_final_verification_is_not_repeated(self):
        original = self.worker.call
        def crashing(name, arguments=None):
            result = original(name, arguments)
            if name == 'run_tests':
                raise KeyboardInterrupt()
            return result
        self.worker.call = crashing
        with self.assertRaises(KeyboardInterrupt):
            work(self.project, self.args, self.worker, self.finish)
        self.worker.call = original
        self.assertEqual(self.checkpoint()['phase'], 'verification_in_flight')
        with self.assertRaisesRegex(HornetError, 'outcome is unknown'):
            resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertEqual(self.worker.calls.count('run_tests'), 1)

    def test_connection_failure_resumes_with_confirmed_edit_intact(self):
        count = [0]
        def model(*args):
            count[0] += 1
            if count[0] == 1:
                return call('write_file', {'path': 'a.py', 'content': 'value = 2\n',
                    'expected_sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}, 'first'), {}
            raise HornetError('Fixture model connection lost')
        stopped = work(self.project, self.args, self.worker, model)
        self.assertEqual(stopped['status'], 'stopped')
        result = resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertTrue(result['ok'])
        self.assertEqual(self.worker.calls.count('write_file'), 1)

    def test_total_tool_budget_cannot_be_reset_by_resume(self):
        self.confirmed_edit_then_interrupt()
        args = self.resume_args()
        args.max_tools = 1
        def another_edit(*unused):
            return call('write_file', {'path': 'a.py', 'content': 'value = 3\n',
                'expected_sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}, 'second'), {}
        result = resume(self.project, args, self.worker, another_edit)
        self.assertFalse(result['ok'])
        self.assertIn('tool-operation budget', result['error'])
        self.assertEqual(self.worker.calls.count('write_file'), 1)

    def test_below_recorded_usage_and_corrupt_checkpoint_refused(self):
        self.confirmed_edit_then_interrupt()
        args = self.resume_args()
        args.max_turns = 1
        with self.assertRaisesRegex(HornetError, 'recorded usage'):
            resume(self.project, args, self.worker, self.finish)
        (self.run_dir() / 'checkpoint.json').write_text('{incomplete')
        with self.assertRaisesRegex(HornetError, 'valid resumable checkpoint'):
            resume(self.project, self.resume_args(), self.worker, self.finish)

    def test_credentials_in_endpoint_are_rejected_before_persistence(self):
        self.args.base_url = 'http://user:secret@localhost:1234/v1'
        with self.assertRaisesRegex(HornetError, 'credentials must use'):
            work(self.project, self.args, self.worker, self.finish)
        self.assertFalse((self.controller / 'runs').exists())

    def test_stale_attempt_counter_does_not_overwrite_attempt_evidence(self):
        self.confirmed_edit_then_interrupt()
        previous = (self.run_dir() / 'attempts/1/conditions.json').read_bytes()
        job = self.checkpoint()
        job['attempts'] = 0
        (self.run_dir() / 'checkpoint.json').write_text(json.dumps(job))
        result = resume(self.project, self.resume_args(), self.worker, self.finish)
        self.assertTrue(result['ok'])
        self.assertEqual(result['attempt'], 2)
        self.assertEqual((self.run_dir() / 'attempts/1/conditions.json').read_bytes(), previous)
        self.assertEqual(self.worker.calls.count('write_file'), 1)


if __name__ == '__main__':
    unittest.main()
