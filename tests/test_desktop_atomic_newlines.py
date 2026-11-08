import hashlib
from pathlib import Path
import tempfile
import unittest
from hornet.registry.assets.atomic_editor import execute

class AtomicNewlineTests(unittest.TestCase):
    def test_insert_retains_windows_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sample.txt'
            raw = b'first\r\nlast\r\n'
            path.write_bytes(raw)
            result = execute({'path': str(path), 'action': 'insert', 'line_number': 2,
                              'new_text': 'second\nthird', 'commit': True,
                              'expected_sha256': hashlib.sha256(raw).hexdigest()})
            self.assertEqual(result['status'], 'SUCCESS')
            self.assertEqual(path.read_bytes(), b'first\r\nsecond\r\nthird\r\nlast\r\n')
