"""Configuration mistakes fail locally with actionable, credential-free errors."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hornet.coding.transport import SSHWorker
from hornet.project import HornetError


class WorkerConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.identity = self.root / 'identity'
        self.hosts = self.root / 'known_hosts'
        self.identity.write_text('PRIVATE-CONTENT-SENTINEL')
        self.hosts.write_text('HOST-CONTENT-SENTINEL')
        self.config = {'host': '127.0.0.1', 'port': 2223, 'user': 'hornet',
                       'identity_file': str(self.identity), 'known_hosts_file': str(self.hosts)}
        self.path = self.root / 'worker.json'

    def save(self):
        self.path.write_text(json.dumps(self.config), encoding='utf-8')

    def refused(self):
        self.save()
        with patch('hornet.coding.transport.subprocess.run') as remote:
            with self.assertRaises(HornetError) as error:
                SSHWorker(self.path)
        remote.assert_not_called()
        message = str(error.exception)
        self.assertNotIn('PRIVATE-CONTENT-SENTINEL', message)
        self.assertNotIn('HOST-CONTENT-SENTINEL', message)
        return message

    def test_qwen_aliases_and_account_report_all_schema_mistakes(self):
        self.config['identity'] = self.config.pop('identity_file')
        self.config['known_hosts'] = self.config.pop('known_hosts_file')
        self.config['user'] = 'pete'
        message = self.refused()
        for expected in ("Unsupported field 'identity'; use 'identity_file'",
                         "Unsupported field 'known_hosts'; use 'known_hosts_file'",
                         "provisioned 'hornet' account", 'controller running Hornet', 'not inside the VM'):
            self.assertIn(expected, message)

    def test_missing_fields_name_both_requirements(self):
        del self.config['identity_file'], self.config['known_hosts_file']
        message = self.refused()
        for field in ('identity_file', 'known_hosts_file'):
            self.assertIn("'{}' must be a nonempty local file path string".format(field), message)

    def test_invalid_path_types_are_controlled_errors(self):
        for field in ('identity_file', 'known_hosts_file'):
            for bad in (None, False, 17, [], {}, '', '   ', 'bad\x00path'):
                with self.subTest(field=field, bad=bad):
                    original = self.config[field]
                    self.config[field] = bad
                    self.assertIn("'{}' must be a nonempty local file path string".format(field), self.refused())
                    self.config[field] = original

    def test_alias_alongside_canonical_field_is_not_silently_ignored(self):
        self.config['identity'] = 'PRIVATE-CONTENT-SENTINEL'
        self.assertIn("Unsupported field 'identity'", self.refused())

    def test_missing_files_show_both_resolved_local_paths(self):
        self.identity.unlink()
        self.hosts.unlink()
        message = self.refused()
        for field, path in (('identity_file', self.identity), ('known_hosts_file', self.hosts)):
            self.assertIn("'{}' must point to an existing regular local file: {}".format(field, path), message)

    def test_directory_is_not_a_credential_file(self):
        self.config['identity_file'] = str(self.root)
        self.assertIn('existing regular local file: ' + str(self.root), self.refused())

    def test_wrong_account_stops_before_ssh(self):
        self.config['user'] = 'pete'
        self.assertIn('changing the name alone does not install the worker', self.refused())

    def test_path_resolution_failure_is_controlled(self):
        self.save()
        with patch.object(Path, 'resolve', side_effect=RuntimeError('private detail')):
            with self.assertRaises(HornetError) as error:
                SSHWorker(self.path)
        self.assertIn('could not be resolved or inspected', str(error.exception))
        self.assertNotIn('private detail', str(error.exception))

    def test_unreadable_pins_report_field_and_path(self):
        self.save()
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('private detail')):
            with self.assertRaises(HornetError) as error:
                SSHWorker(self.path)
        self.assertIn("Cannot read 'known_hosts_file' on this controller: " + str(self.hosts), str(error.exception))
        self.assertNotIn('private detail', str(error.exception))

    def test_valid_config_keeps_connection_binding_and_does_not_read_private_key(self):
        self.save()
        original = Path.read_bytes
        def read(path):
            self.assertNotEqual(path, self.identity)
            return original(path)
        with patch('hornet.coding.transport.shutil.which', return_value=sys.executable), \
                patch.object(Path, 'read_bytes', read), patch('hornet.coding.transport.subprocess.run') as remote:
            worker = SSHWorker(self.path)
        remote.assert_not_called()
        binding = {key: self.config.get(key) for key in ('host', 'port', 'user', 'vm_uuid')}
        binding['known_hosts_sha256'] = hashlib.sha256(self.hosts.read_bytes()).hexdigest()
        self.assertEqual(worker.connection_binding, hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest())
        self.assertEqual((worker.identity, worker.known_hosts), (self.identity, self.hosts))

    def test_relative_paths_keep_current_directory_semantics(self):
        self.config['identity_file'] = 'identity'
        self.config['known_hosts_file'] = 'known_hosts'
        self.save()
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch('hornet.coding.transport.shutil.which', return_value=sys.executable):
                worker = SSHWorker(self.path)
            self.assertEqual(worker.identity, self.identity)
            self.assertEqual(worker.known_hosts, self.hosts)
        finally:
            os.chdir(previous)

    def test_cli_reports_alias_failure_with_exit_two_and_no_traceback(self):
        self.config['identity'] = self.config.pop('identity_file')
        self.save()
        result = subprocess.run([sys.executable, '-m', 'hornet', '--root', str(self.root),
                                 'worker-status', '--worker-config', 'worker.json', '--json'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report['ok'])
        self.assertIn("Unsupported field 'identity'; use 'identity_file'", report['error'])
        self.assertNotIn('Traceback', result.stderr)

    def test_cli_text_mode_reports_exit_two_on_stderr(self):
        self.config['user'] = 'pete'
        self.save()
        result = subprocess.run([sys.executable, '-m', 'hornet', '--root', str(self.root),
                                 'worker-status', '--worker-config', 'worker.json'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("provisioned 'hornet' account", result.stderr)
        self.assertNotIn('Traceback', result.stderr)


class WorkerProtocolTests(unittest.TestCase):
    def connection(self):
        worker = SSHWorker.__new__(SSHWorker)
        worker.ssh, worker.port, worker.user = 'ssh', 2223, 'hornet'
        worker.identity, worker.known_hosts = Path('identity'), Path('known_hosts')
        return worker

    def test_status_sends_only_operation_json_over_pinned_ssh(self):
        result = subprocess.CompletedProcess([], 0, '{"ok": true, "ready": true}', '')
        with patch('hornet.coding.transport.subprocess.run', return_value=result) as remote:
            self.assertTrue(self.connection()._wire('status')['ready'])
        args, kwargs = remote.call_args
        self.assertEqual(args[0][-2:], ['hornet@127.0.0.1', 'hornet-worker'])
        self.assertIn('StrictHostKeyChecking=yes', args[0])
        self.assertEqual(json.loads(kwargs['input']), {'tool': 'status', 'arguments': {}})
        self.assertTrue(kwargs['input'].endswith('\n'))

    def test_empty_or_help_output_explains_worker_entry_point(self):
        for output, expected in (('', 'empty stdout'), (' \n', 'empty stdout'),
                                 ('Usage: hornet PRIVATE-CONTENT-SENTINEL', 'non-JSON stdout'),
                                 ('banner\n{"ok":true}', 'non-JSON stdout')):
            with self.subTest(output=output):
                result = subprocess.CompletedProcess([], 0, output, 'HOST-CONTENT-SENTINEL')
                with patch('hornet.coding.transport.subprocess.run', return_value=result):
                    with self.assertRaises(HornetError) as error:
                        self.connection()._wire('status')
                message = str(error.exception)
                for part in (expected, '/opt/hornet-worker/worker.py', 'symlink', 'stdin'):
                    self.assertIn(part, message)
                self.assertNotIn('PRIVATE-CONTENT-SENTINEL', message)
                self.assertNotIn('HOST-CONTENT-SENTINEL', message)

    def test_incorrect_json_shape_is_not_accepted_as_status(self):
        for output in ('[]', 'null', '{}', '{"ok": 1}', '{"ok": "true"}'):
            with self.subTest(output=output):
                result = subprocess.CompletedProcess([], 0, output, '')
                with patch('hornet.coding.transport.subprocess.run', return_value=result):
                    with self.assertRaisesRegex(HornetError, "Boolean 'ok'"):
                        self.connection()._wire('status')

    def test_worker_refusal_remains_a_valid_operation_response(self):
        result = subprocess.CompletedProcess([], 0, '{"ok": false, "error": "not ready"}', '')
        with patch('hornet.coding.transport.subprocess.run', return_value=result):
            self.assertEqual(self.connection()._wire('status'), {'ok': False, 'error': 'not ready'})


if __name__ == '__main__':
    unittest.main()
