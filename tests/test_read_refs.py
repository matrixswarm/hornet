"""Confirmed read handles cross real file, scope and interruption boundaries."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hornet.coding import read_refs, session
from hornet.coding.autonomous import ScopedWorker
from hornet.coding.model import json_action
from hornet.coding.agent import TOOLS
from hornet.project import HornetError, Project
from test_acceptance import CheckWorker
from test_resume import call


class ReadReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hornet-read-ref-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.host, self.guest = root / 'host', root / 'guest'
        self.host.mkdir()
        self.guest.mkdir()
        self.path = self.guest / 'value.py'
        self.path.write_text('value = 1\n', encoding='utf-8')
        self.original_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.worker = CheckWorker(self.guest)
        self.project = Project(self.host)
        self.args = argparse.Namespace(task='Set value to 2.', task_file=None, model='fixture',
            base_url='http://localhost:1234/v1', max_tokens=1024, timeout=30,
            max_turns=12, max_tools=12, protocol='lmstudio-json', context_chars=16000,
            tools=['read_file', 'write_file'], worker_config='worker.json', run_dir='runs')
        self.results = []

    def run_dir(self):
        return next((self.host / 'runs').iterdir())

    def checkpoint(self):
        return json.loads((self.run_dir() / 'checkpoint.json').read_text(encoding='utf-8'))

    def resume_args(self):
        return argparse.Namespace(run=str(self.run_dir()))

    def model(self, actions):
        actions = iter(actions)
        count = [0]
        def request(messages, *unused):
            count[0] += 1
            if messages[-1]['role'] == 'tool':
                self.results.append(json.loads(messages[-1]['content'].split('\n\n')[0]))
            action = next(actions, None)
            if action is None:
                return {'role': 'assistant', 'content': 'Finished.'}, {}
            if callable(action):
                action = action()
            return call(action[0], action[1], 'step-' + str(count[0])), {}
        return request

    def read(self, path='value.py'):
        return ('read_file', {'path': path})

    def write(self, ref=None, path='value.py', content='value = 2\n'):
        return ('write_file', {'path': path, 'content': content,
                              'read_ref': ref or self.results[-1]['read_ref']})

    def run_actions(self, actions, worker=None):
        return session.work(self.project, self.args, worker or self.worker, self.model(actions))

    def test_reference_writes_original_digest_and_audits_translation(self):
        result = self.run_actions([self.read(), self.write])
        self.assertTrue(result['ok'])
        self.assertEqual(self.path.read_text(), 'value = 2\n')
        history = self.checkpoint()['history']
        read = next(item for item in history if item.get('tool') == 'read_file')
        write = next(item for item in history if item.get('tool') == 'write_file')
        self.assertLess(len(read['result']['read_ref']), 20)
        self.assertNotIn('expected_sha256', write['arguments'])
        self.assertEqual(write['dispatched_arguments']['expected_sha256'], self.original_hash)
        self.assertNotIn('read_ref', write['dispatched_arguments'])

    def test_stale_reference_cannot_overwrite_newer_file(self):
        captured = []
        def first_write():
            captured.append(self.results[-1]['read_ref'])
            return self.write(captured[0])
        self.run_actions([self.read(), first_write,
                          lambda: self.write(captured[0], content='value = 99\n')])
        writes = [e for e in self.checkpoint()['history'] if e.get('tool') == 'write_file']
        self.assertTrue(writes[0]['result']['ok'])
        self.assertFalse(writes[1]['result']['ok'])
        self.assertIn('changed', writes[1]['result']['error'])
        self.assertEqual(self.path.read_text(), 'value = 2\n')

    def test_fresh_read_allows_second_edit(self):
        self.run_actions([self.read(), self.write, self.read(), lambda: self.write(content='value = 3\n')])
        reads = [e['result'] for e in self.checkpoint()['history'] if e.get('tool') == 'read_file']
        self.assertNotEqual(reads[0]['read_ref'], reads[1]['read_ref'])
        self.assertEqual(self.path.read_text(), 'value = 3\n')

    def test_reference_cannot_cross_files_even_when_hashes_match(self):
        other = self.guest / 'other.py'
        other.write_bytes(self.path.read_bytes())
        self.run_actions([self.read(), lambda: self.write(path='other.py')])
        self.assertNotIn('write_file', self.worker.calls)
        self.assertEqual(other.read_bytes(), self.path.read_bytes())
        rejected = next(e for e in self.checkpoint()['history'] if e.get('tool') == 'write_file')
        self.assertIn('different file', rejected['result']['error'])

    def test_reference_does_not_grant_cell_write_scope(self):
        scoped = ScopedWorker(self.worker, ['other.py'])
        self.run_actions([self.read(), self.write], scoped)
        self.assertNotIn('write_file', self.worker.calls)
        self.assertEqual(self.path.read_text(), 'value = 1\n')

    def test_missing_unknown_empty_and_conflicting_references_refused(self):
        args = {'path': 'value.py', 'content': 'bad'}
        self.run_actions([('write_file', dict(args, **extra)) for extra in
            ({}, {'read_ref': 'unknown'}, {'read_ref': ''}, {'read_ref': 1},
             {'read_ref': 'unknown', 'expected_sha256': self.original_hash})])
        self.assertNotIn('write_file', self.worker.calls)
        self.assertEqual(self.path.read_text(), 'value = 1\n')

    def test_legacy_hash_and_empty_hash_create_keep_existing_guards(self):
        self.run_actions([
            ('write_file', {'path': 'new.py', 'content': 'new\n', 'expected_sha256': ''}),
            ('write_file', {'path': 'new.py', 'content': 'overwrite\n', 'expected_sha256': ''}),
            ('write_file', {'path': 'value.py', 'content': 'value = 2\n', 'expected_sha256': self.original_hash})])
        self.assertEqual((self.guest / 'new.py').read_text(), 'new\n')
        self.assertEqual(self.path.read_text(), 'value = 2\n')

    def test_read_changed_during_confirmation_does_not_get_reference(self):
        original = self.worker.call
        def racing(name, args=None):
            result = original(name, args)
            if name == 'read_file':
                self.path.write_text('external change\n')
            return result
        self.worker.call = racing
        self.run_actions([self.read()])
        read = self.results[0]
        self.assertFalse(read['ok'])
        self.assertNotIn('read_ref', read)

    def test_pending_reference_survives_new_process_load_without_replaying_read(self):
        save = session.save
        def interrupt(run, job):
            save(run, job)
            if job['phase'] == 'dispatch' and job['pending'][0]['function']['name'] == 'write_file':
                raise KeyboardInterrupt()
        with patch.object(session, 'save', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.run_actions([self.read(), self.write])
        result = session.resume(self.project, self.resume_args(), self.worker, self.model([]))
        self.assertTrue(result['ok'])
        self.assertEqual(self.worker.calls.count('read_file'), 1)
        self.assertEqual(self.worker.calls.count('write_file'), 1)
        self.assertEqual(self.path.read_text(), 'value = 2\n')

    def test_unknown_inflight_reference_write_is_not_replayed(self):
        original = self.worker.call
        def crashing(name, args=None):
            result = original(name, args)
            if name == 'write_file':
                raise KeyboardInterrupt()
            return result
        self.worker.call = crashing
        with self.assertRaises(KeyboardInterrupt):
            self.run_actions([self.read(), self.write])
        with self.assertRaisesRegex(HornetError, 'outcome is unknown'):
            session.resume(self.project, self.resume_args(), self.worker, self.model([]))
        self.assertEqual(self.worker.calls.count('write_file'), 1)

    def test_identity_change_before_write_is_refused(self):
        def changed():
            self.worker.binding['connection'] = 'different'
            return self.write()
        result = self.run_actions([self.read(), changed])
        self.assertIn('identity changed', result['error'])
        self.assertNotIn('write_file', self.worker.calls)

    def test_foreign_job_or_changed_observation_rejected_on_load(self):
        def stop():
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_actions([self.read(), stop])
        job = self.checkpoint()
        for change in ('job_id', 'binding', 'digest', 'path'):
            modified = copy.deepcopy(job)
            read = next(e for e in modified['history'] if e.get('tool') == 'read_file')
            if change == 'job_id':
                modified['job_id'] = 'foreign-job'
            elif change == 'binding':
                modified['snapshot']['binding']['connection'] = 'foreign-worker'
            elif change == 'digest':
                read['result']['sha256'] = 'b' * 64
            else:
                read['arguments']['path'] = read['result']['path'] = 'other.py'
            (self.run_dir() / 'checkpoint.json').write_text(json.dumps(modified), encoding='utf-8')
            with self.subTest(change=change), self.assertRaisesRegex(HornetError, 'valid resumable checkpoint'):
                session.load(self.project, self.run_dir())

    def test_json_protocol_accepts_reference_for_both_edit_operations(self):
        for name, args in [('edit_file', {'path': 'a.py', 'old_text': 'old', 'new_text': 'new', 'read_ref': 'r_123456789012'}),
                           ('write_file', {'path': 'a.py', 'content': 'new', 'read_ref': 'r_123456789012'})]:
            message = json_action(json.dumps({'action': 'call', 'name': name, 'arguments': args}), TOOLS)
            self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']), args)

    def test_json_schema_requires_exactly_one_version_field(self):
        base = {'path': 'a.py', 'old_text': 'old', 'new_text': 'new'}
        for extra in ({}, {'read_ref': 'r_123456789012', 'expected_sha256': 'a' * 64}):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'exactly one'):
                json_action(json.dumps({'action': 'call', 'name': 'edit_file', 'arguments': dict(base, **extra)}), TOOLS)
        legacy = dict(base, expected_sha256='a' * 64)
        message = json_action(json.dumps({'action': 'call', 'name': 'edit_file', 'arguments': legacy}), TOOLS)
        self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']), legacy)


if __name__ == '__main__':
    unittest.main()
