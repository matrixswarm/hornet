import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hornet.desktop import worker_vm
from hornet.desktop.vm_runner import run_python
from hornet.desktop.workspace import Workspace


class WorkerVMTests(unittest.TestCase):
    def test_config_is_opt_in_and_invalid_config_fails_closed(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            ws = SimpleNamespace(root=Path(tmp))
            self.assertIsNone(worker_vm.config_path(ws))
            (ws.root / 'test-vm.json').write_text('{"worker_config":"relative.json"}')
            with self.assertRaisesRegex(ValueError, 'absolute'):
                worker_vm.config_path(ws)

    @patch('hornet.desktop.worker_vm.ready_worker')
    @patch('hornet.desktop.worker_vm.rpc')
    def test_large_file_chunked_verified_and_separate_logs(self, rpc, worker):
        worker.return_value.call.return_value = {'ok': True, 'ready': True, 'platform': 'linux', 'uid': 1000}
        directory = '/home/hornet/.hornet-desktop-runs/run-example'
        def reply(w, script, args=(), timeout=45):
            if script == worker_vm.CREATE:
                return {'directory': directory}
            if script == worker_vm.EXTRACT:
                return {'directory': directory + '/project'}
            if script == worker_vm.RUN:
                return {'exit_code': 0, 'timed_out': False, 'vm_directory': directory}
            if script == worker_vm.READ_LOG:
                return {'data': base64.b64encode(b'hello').decode(), 'truncated': False}
            return {}
        rpc.side_effect = reply
        result = worker_vm.run_copy('worker.json', {'data.txt': base64.b64encode(os.urandom(100001)).decode()}, ['python3', 'main.py'])
        self.assertEqual(result['stdout'], 'hello')
        calls = rpc.call_args_list
        self.assertEqual(sum(c.args[1] == worker_vm.APPEND for c in calls), 2)
        self.assertEqual(sum(c.args[1] == worker_vm.EXTRACT for c in calls), 1)
        self.assertLess(max(len(v) for c in calls for v in (c.args[2] if len(c.args)>2 else [])), 4001)
        self.assertTrue(all(c.args[2][0] in (directory, directory + '/project') for c in calls if len(c.args)>2))

    @patch('hornet.desktop.worker_vm.SSHWorker')
    @patch('hornet.desktop.worker_vm.Path')
    def test_root_or_unready_worker_rejected(self, path, worker):
        path.return_value.read_text.return_value = '{}'
        for status in ({'ok': True, 'ready': True, 'platform': 'linux', 'uid': 0}, {'ok': True, 'ready': False, 'platform': 'linux', 'uid': 1000}):
            worker.return_value.call.return_value = status
            with self.assertRaisesRegex(ValueError, 'not ready'):
                worker_vm.ready_worker('worker.json')

    def test_rpc_rejects_truncated_or_invalid_results(self):
        for result in ({'ok': True, 'truncated': True}, {'ok': False, 'output': 'failure'}, {'ok': True, 'output': 'not-json'}):
            worker = SimpleNamespace(call=lambda *args: result)
            with self.assertRaises(ValueError):
                worker_vm.rpc(worker, 'print(1)')

    @patch('hornet.desktop.worker_vm.run_copy')
    @patch('hornet.desktop.worker_vm.config_path', return_value='worker.json')
    def test_desktop_routes_large_revision_and_keeps_source(self, config, run):
        run.return_value = {'exit_code': 0, 'timed_out': False, 'stdout': '', 'stderr': '', 'stdout_truncated': False, 'stderr_truncated': False}
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp)
            revision = ws.create('example')
            source = ws.revision('example', revision) / 'large.txt'
            source.write_bytes(b'x'*100000)
            outcome = run_python(ws, 'example', revision, 'python3 main.py')
            self.assertEqual(source.read_bytes(), b'x'*100000)
            self.assertEqual(base64.b64decode(run.call_args.args[1]['large.txt']), b'x'*100000)
            self.assertEqual(outcome['revision'], revision)
            self.assertTrue((ws.project('example') / 'runs' / (outcome['id']+'.json')).is_file())


if __name__ == '__main__':
    unittest.main()
