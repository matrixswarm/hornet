import unittest
from unittest.mock import patch

from hornet import launcher


class LauncherTests(unittest.TestCase):
    @patch('hornet.launcher.subprocess.Popen')
    def test_coder_starts_in_current_environment(self, popen):
        launcher.launch('coder')
        self.assertEqual(popen.call_args.args[0], [launcher.sys.executable, '-m', 'hornet.coder'])

    @patch('hornet.launcher.subprocess.Popen')
    @patch('hornet.launcher.importlib.util.find_spec', return_value=object())
    def test_distil_starts_when_dependencies_are_available(self, _find, popen):
        launcher.launch('distil')
        self.assertEqual(popen.call_args.args[0], [launcher.sys.executable, '-m', 'hornet.distil'])

    @patch('hornet.launcher.subprocess.Popen')
    @patch('hornet.launcher.importlib.util.find_spec', return_value=None)
    def test_missing_distil_dependencies_are_reported(self, _find, popen):
        with self.assertRaisesRegex(RuntimeError, 'optional dependencies'):
            launcher.launch('distil')
        popen.assert_not_called()

    def test_unknown_choice_is_rejected(self):
        with self.assertRaises(ValueError):
            launcher.launch('other')
