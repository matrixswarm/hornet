from tempfile import TemporaryDirectory
import unittest
from hornet.desktop.workspace import Workspace
from hornet.desktop.registry_mission import numbered_file, propose_with_tools


def insert(line, text):
    return {'uri': 'files.text.atomic_edit', 'arguments': {
        'path': 'main.py', 'action': 'insert', 'line_number': line, 'new_text': text}}


class AppendGuidanceTests(unittest.TestCase):
    def test_metadata_matches_atomic_line_count(self):
        for text in ('', 'A = 1', 'A = 1\n', 'A = 1\r\nB = 2\r\n', '\n\n'):
            with self.subTest(text=text):
                data = numbered_file(text)
                self.assertEqual(data['line_count'], len(text.splitlines(True)))
                self.assertEqual(data['append_line_number'], len(text.splitlines(True)) + 1)
                self.assertEqual(data['insert_line_min'], 1)
                self.assertEqual(data['insert_line_max'], data['append_line_number'])

    def test_84_line_file_corrects_oversized_insert_to_exact_append(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp); rev = ws.create('demo'); root = ws.revision('demo', rev)
            target = root / 'main.py'; original = b'# line\r\n' * 84
            target.write_bytes(original)
            context = ws.mission_context('demo', rev, ['main.py'], 'Append value')
            calls = []
            def model(instruction, payload):
                calls.append(payload)
                self.assertEqual(payload['numbered_files']['main.py']['append_line_number'], 85)
                self.assertIn('combine adjacent additions', instruction)
                if len(calls) == 1:
                    return {'summary': 'append', 'edits': [insert(999, 'VALUE = 1')]}
                self.assertIn('Requested line_number=999', payload['preflight_error'])
                self.assertIn('append_line_number=85', payload['preflight_error'])
                self.assertEqual(target.read_bytes(), original)
                return {'summary': 'append', 'edits': [insert(85, 'VALUE = 1')]}
            proposal, receipts = propose_with_tools(root, context, model)
            self.assertEqual(len(calls), 2)
            self.assertEqual(proposal['files']['main.py'], original.decode() + 'VALUE = 1\r\n')
            self.assertEqual(target.read_bytes(), original)

    def test_feedback_uses_intermediate_file_and_never_clamps(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp); rev = ws.create('demo'); root = ws.revision('demo', rev)
            target = root / 'main.py'; original = b'A = 1\n'; target.write_bytes(original)
            context = ws.mission_context('demo', rev, ['main.py'], 'Append values')
            calls = []
            def model(instruction, payload):
                calls.append(payload)
                if len(calls) == 2:
                    self.assertIn('append_line_number=3', payload['preflight_error'])
                    self.assertIn('correction restarts from the original files', payload['preflight_error'])
                    self.assertEqual(payload['numbered_files']['main.py']['append_line_number'], 2)
                return {'summary': 'invalid', 'edits': [insert(2, 'B = 2'), insert(999, 'C = 3')]}
            with self.assertRaisesRegex(ValueError, 'corrected plan also failed preflight'):
                propose_with_tools(root, context, model)
            self.assertEqual(len(calls), 2)
            self.assertEqual(target.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
