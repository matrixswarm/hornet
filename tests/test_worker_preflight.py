"""Deployment failures must stop before coding or durable operation dispatch."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from hornet.coding import operation_worker, preflight, session, worker
from hornet.coding.transport import SSHWorker
from hornet.hive import validate
from hornet.project import HornetError


class ReadinessTests(unittest.TestCase):
    def test_empty_git_directory_and_marker_do_not_establish_readiness(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.git').mkdir()
            marker = root / 'ready'
            marker.write_text('ready')
            with patch.object(worker, 'READY_MARKER', str(marker)):
                value = worker.Worker(root).status()
            self.assertFalse(value['ready'])
            self.assertFalse(value['readiness_checks']['git_head'])

    def test_real_repository_requires_a_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            marker = root / 'ready'
            marker.write_text('ready')
            def git(*args):
                return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args],
                                      capture_output=True, check=True)
            git('init')
            with patch.object(worker, 'READY_MARKER', str(marker)):
                self.assertFalse(worker.Worker(root).status()['ready'])
                git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost',
                    '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', 'Baseline')
                result = worker.Worker(root).status()
            self.assertTrue(result['ready'])
            self.assertTrue(all(result['readiness_checks'].values()))


class PreflightTests(unittest.TestCase):
    def connection(self, **changes):
        value = SSHWorker.__new__(SSHWorker)
        value.user, value.workspace = 'hornet', None
        status = {'ok': True, 'ready': True, 'uid': 1000, 'platform': 'linux', 'root': preflight.BASELINE}
        status.update(changes)
        facts = {'root': status['root'], 'git_root': status['root'], 'git_head_valid': True,
                 'worker_owner_uid': 0, 'worker_writable': False, 'worker_linked': False,
                 'worker_sha256': preflight.source_hash()}
        value.call = MagicMock(side_effect=[status, {'ok': True, 'output': json.dumps(facts)}])
        return value, facts

    def test_false_ready_at_pete_root_stops_before_code_probe(self):
        value, _ = self.connection(root='/home/pete/hornet_workspace')
        with self.assertRaisesRegex(HornetError, 'workspace_root'):
            session.ready(value)
        self.assertEqual(value.call.call_count, 1)

    def test_nonstandard_account_is_refused(self):
        value, _ = self.connection()
        value.user = 'pete'
        result = value.preflight()
        self.assertFalse(result['ok'])
        self.assertFalse(result['preflight_checks']['account'])

    def test_modified_or_user_owned_worker_is_refused(self):
        for field, bad_value, check in [('worker_sha256', '0' * 64, 'worker_code'),
                                        ('worker_owner_uid', 1000, 'worker_root_owned'),
                                        ('worker_writable', True, 'worker_protected'),
                                        ('git_head_valid', False, 'git_head')]:
            with self.subTest(field=field):
                value, facts = self.connection()
                facts[field] = bad_value
                value.call.side_effect = [value.call.side_effect.__next__(), {'ok': True, 'output': json.dumps(facts)}]
                result = value.preflight()
                self.assertFalse(result['ok'])
                self.assertFalse(result['preflight_checks'][check])

    def test_matching_deployment_passes(self):
        value, _ = self.connection()
        self.assertTrue(value.preflight()['ready'])

    def test_scoped_workspace_uses_exact_saved_goal(self):
        ident = 'g-' + 'a' * 32
        value, _ = self.connection(root=preflight.GOALS + ident)
        value.workspace = ident
        result = value.preflight()
        self.assertTrue(result['ok'])
        self.assertEqual(result['expected_root'], preflight.GOALS + ident)

    def test_malformed_probe_is_refused(self):
        value, _ = self.connection()
        status = next(value.call.side_effect)
        value.call.side_effect = [status, {'ok': True, 'output': 'not-json'}]
        result = value.preflight()
        self.assertFalse(result['ok'])
        self.assertFalse(result['preflight_checks']['deployment_probe'])

    def test_missing_operation_root_produces_structured_refusal(self):
        from hornet.coding.worker import WorkerError
        output = io.StringIO()
        with patch.object(operation_worker, 'no_links'), patch('runpy.run_path', return_value={
                'Worker': MagicMock(side_effect=WorkerError('Workspace is not ready'))}), contextlib.redirect_stdout(output):
            operation_worker.main({'workspace': None})
        result = json.loads(output.getvalue())
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'refused')
        self.assertIn(preflight.BASELINE, result['error'])

    def test_misplaced_worker_model_settings_explain_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'worker.json'
            path.write_text(json.dumps({'host': '127.0.0.1', 'model': {'api_key': 'private-sentinel'}}))
            with self.assertRaisesRegex(HornetError, 'HORNET_API_KEY') as error:
                SSHWorker(path)
            self.assertNotIn('private-sentinel', str(error.exception))

    def test_hive_key_error_does_not_echo_key(self):
        document = {'schema_version': 1, 'default_agent': 'gemma', 'agents': {
            'gemma': {'model': 'test', 'base_url': 'http://127.0.0.1:1234/v1',
                      'protocol': 'lmstudio-json', 'api_key': 'private-sentinel'}}}
        with self.assertRaisesRegex(HornetError, 'HORNET_API_KEY') as error:
            validate(document)
        self.assertNotIn('private-sentinel', str(error.exception))


if __name__ == '__main__':
    unittest.main()
