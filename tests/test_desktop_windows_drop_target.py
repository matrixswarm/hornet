"""Regression checks that deferred drop detection cannot launch helpers."""
import inspect
import unittest
from unittest.mock import patch
from hornet.desktop import windows_drop_target as target
from hornet.desktop import revision_drag, shell


class DeferredDropTargetTests(unittest.TestCase):
    def test_compatibility_functions_are_inert(self):
        with patch('subprocess.run', side_effect=AssertionError('No helpers allowed')), patch('subprocess.Popen', side_effect=AssertionError('No helpers allowed')):
            self.assertIsNone(target.cursor_position())
            for x, y in [(10, 20), (-4, 9), ('x', None)]:
                self.assertIsNone(target.resolve_drop_folder(x, y))

    def test_detector_contains_no_native_or_process_implementation(self):
        source = inspect.getsource(target)
        for token in ('subprocess', 'ctypes', 'Add-Type', 'EncodedCommand', 'Shell.Application', 'HornetDropWindow'):
            self.assertNotIn(token, source)
        self.assertFalse(hasattr(target, '_SCRIPT'))

    def test_shell_does_not_install_drag_controller(self):
        self.assertNotIn('install_revision_drag', inspect.getsource(shell))
        self.assertFalse(hasattr(revision_drag, 'RevisionDrag'))
        self.assertFalse(hasattr(revision_drag, 'install_revision_drag'))
