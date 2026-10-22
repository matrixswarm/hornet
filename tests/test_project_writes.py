"""Atomic writes tolerate transient sharing locks without relaxing access."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hornet.project import HornetError, sharing_retry, write_text


class WriteTests(unittest.TestCase):
    def test_transient_windows_lock_retries_same_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            path.write_text('old')
            failure = OSError('Sharing violation')
            failure.winerror = 32
            with patch('hornet.project.os.replace', side_effect=[failure, None]) as replace, patch('hornet.project.time.sleep') as sleep:
                write_text(path, 'new', force=True)
                self.assertEqual(replace.call_count, 2)
                self.assertEqual(replace.call_args_list[0], replace.call_args_list[1])
                sleep.assert_called_once()
            # The mock replaced no real file; the previous published value remains intact.
            self.assertEqual(path.read_text(), 'old')
            write_text(path, 'new', force=True)
            self.assertEqual(path.read_text(), 'new')
            self.assertFalse(list(Path(directory).glob('.hornet-*')))

    def test_permissions_and_persistent_locks_are_not_bypassed(self):
        failure = PermissionError('Denied')
        with patch('hornet.project.time.sleep') as sleep:
            with self.assertRaises(PermissionError):
                sharing_retry(lambda: (_ for _ in ()).throw(failure))
            sleep.assert_not_called()
            failure.winerror = 33
            with self.assertRaises(PermissionError):
                sharing_retry(lambda: (_ for _ in ()).throw(failure))
            self.assertEqual(sleep.call_count, 8)

    def test_windows_access_denied_can_be_transient_but_stays_bounded(self):
        from unittest.mock import Mock
        failure = PermissionError('Windows publication denied')
        failure.winerror = 5
        action = Mock(side_effect=[failure, 'published'])
        with patch('hornet.project.time.sleep') as sleep:
            self.assertEqual(sharing_retry(action, 'same', 'paths'), 'published')
            self.assertEqual(action.call_count, 2)
            sleep.assert_called_once()
        action = Mock(side_effect=failure)
        with patch('hornet.project.time.sleep'), self.assertRaises(PermissionError):
            sharing_retry(action, 'same', 'paths')
        self.assertEqual(action.call_count, 9)

    def test_new_file_policy_still_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            write_text(path, 'first')
            with self.assertRaises(HornetError):
                write_text(path, 'second')
            self.assertEqual(path.read_text(), 'first')
