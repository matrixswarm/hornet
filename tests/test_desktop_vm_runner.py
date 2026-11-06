import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hornet.desktop.workspace import Workspace
from hornet.desktop.vm_runner import run_python, diagnostic_for_mission, run_hint


class VMRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(self.tmp.name)
        self.revision = self.ws.create('test')

    @patch('hornet.desktop.vm_runner.subprocess.run')
    def test_failure_saved_and_attached_only_to_matching_source(self, run):
        result = {'exit_code': 1, 'timed_out': False, 'stdout': '', 'stderr': 'division by zero',
                  'stdout_truncated': False, 'stderr_truncated': False, 'vm_directory': '/vm/run'}
        run.return_value = SimpleNamespace(returncode=0, stdout='HORNET_RUN_RESULT='+json.dumps(result)+'\n', stderr='')
        saved = run_python(self.ws, 'test', self.revision, 'python3 -m app divide 1 0')
        self.assertTrue((self.ws.project('test') / 'runs' / (saved['id'] + '.json')).is_file())
        self.assertEqual(diagnostic_for_mission(self.ws, 'test', self.revision)['stderr'], 'division by zero')
        (self.ws.revision('test', self.revision) / 'main.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed after'):
            diagnostic_for_mission(self.ws, 'test', self.revision)

    def test_headless_tk_error_has_environment_hint(self):
        error = {'stderr': '_tkinter.TclError: no display name and no $DISPLAY environment variable'}
        self.assertIn('no graphical display', run_hint(error))
        self.assertIsNone(run_hint({'stderr': 'ValueError: invalid input'}))

    def test_requires_explicit_python_command_and_existing_diagnostic(self):
        with self.assertRaisesRegex(ValueError, 'Python command'):
            run_python(self.ws, 'test', self.revision, 'sh -c true')
        with self.assertRaisesRegex(ValueError, 'No VM run'):
            diagnostic_for_mission(self.ws, 'test', self.revision)


if __name__ == '__main__':
    unittest.main()
