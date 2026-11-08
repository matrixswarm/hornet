import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hornet.registry.assets.atomic_editor import execute
from hornet.desktop.registry_mission import propose_with_tools
from hornet.desktop.workspace import Workspace


class AtomicErrorCaptureTests(unittest.TestCase):
    def test_invalid_create_retains_exact_source_and_no_target(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.py'
            code = 'value = """unfinished\r\n'
            result = execute({'path': str(path), 'action': 'create', 'new_text': code})
            self.assertEqual(result['error_type'], 'python_syntax')
            self.assertFalse(path.exists())
            self.assertFalse(path.with_name('bad.py.hornet.lock').exists())
            artifact = Path(result['atomic_error_path'])
            self.assertTrue(artifact.name.endswith('.atomic.err'))
            self.assertEqual(artifact.read_bytes(), code.encode())
            self.assertEqual(result['atomic_error_sha256'], hashlib.sha256(code.encode()).hexdigest())

    def test_invalid_replace_preserves_original_and_captures_whole_result(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.py'
            original = b'VALUE = 1\r\nOTHER = 2\r\n'
            path.write_bytes(original)
            result = execute({'path': str(path), 'action': 'replace', 'target_text': 'VALUE = 1',
                              'new_text': 'VALUE = "unterminated'})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(Path(result['atomic_error_path']).read_bytes(), b'VALUE = "unterminated\r\nOTHER = 2\r\n')

    def test_capture_failure_keeps_syntax_failure(self):
        with TemporaryDirectory() as tmp:
            with patch('hornet.registry.assets._common.tempfile.mkstemp', side_effect=OSError('disk full')):
                result = execute({'path': str(Path(tmp)/'bad.py'), 'action': 'create', 'new_text': 'x = "'})
            self.assertEqual(result['error_type'], 'python_syntax')
            self.assertEqual(result['atomic_capture_error'], 'disk full')

    def test_mission_retains_both_attempts_outside_rehearsal(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp)
            rev = ws.create('demo')
            root = ws.revision('demo', rev)
            before = (root/'main.py').read_bytes()
            context = ws.mission_context('demo', rev, [], 'Create file', ['bad.py'])
            directory = Path(tmp)/'diagnostics'
            codes = ['value = """first\n', 'value = """second\n']
            calls = []
            receipts = []
            def model(instruction, payload):
                self.assertNotIn('diagnostic_directory', payload)
                code = codes[len(calls)]
                calls.append(code)
                return {'summary': 'bad', 'edits': [{'uri': 'files.text.atomic_edit', 'arguments': {
                    'path': 'bad.py', 'action': 'create', 'new_text': code}}]}
            with self.assertRaisesRegex(ValueError, 'corrected plan also failed'):
                propose_with_tools(root, context, model, on_receipt=receipts.append, diagnostic_directory=directory)
            saved = [r for r in receipts if r['uri']=='hornet.atomic_diagnostic']
            self.assertEqual(len(saved), 2)
            self.assertEqual([Path(r['artifact_path']).read_text() for r in saved], codes)
            self.assertEqual((root/'main.py').read_bytes(), before)
            self.assertFalse((root/'bad.py').exists())
            self.assertFalse(list(root.parent.glob('.preflight-*')))
            self.assertFalse(list(root.parent.glob('.toolstage-*')))
            self.assertNotIn('"""first', json.dumps(receipts))

if __name__ == '__main__':
    unittest.main()
