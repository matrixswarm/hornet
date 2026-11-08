import unittest
import json
from unittest.mock import Mock, patch
from tempfile import TemporaryDirectory
from hornet.desktop.workspace import Workspace
from hornet.desktop.plan_context import mission_context
from hornet.desktop.registry_mission import propose_with_tools


class PlanContextTests(unittest.TestCase):
    def test_reference_is_read_but_rejected_as_edit_target(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp)
            revision = ws.create('demo')
            root = ws.revision('demo', revision)
            (root / 'reference.py').write_bytes(b'VALUE = 1\n')
            context = mission_context(ws, 'demo', revision, ['main.py'], 'Update main',
                                      context_files=['reference.py'])
            def request(instruction, payload):
                self.assertIn('Never edit those paths', instruction)
                self.assertNotIn('reference.py', payload['files'])
                self.assertEqual(payload['read_only_files']['reference.py'], 'VALUE = 1\n')
                return {'summary': 'Forbidden edit', 'edits': [{'uri': 'files.text.atomic_edit',
                    'arguments': {'path': 'reference.py', 'action': 'replace',
                                  'target_text': 'VALUE = 1', 'new_text': 'VALUE = 2'}}]}
            with self.assertRaisesRegex(ValueError, 'out-of-scope path: reference.py'):
                propose_with_tools(root, context, request)
            self.assertEqual((root / 'reference.py').read_text(), 'VALUE = 1\n')

    def test_legacy_context_is_unchanged(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp)
            revision = ws.create('demo')
            self.assertEqual(mission_context(ws, 'demo', revision, ['main.py'], 'Update main'),
                             ws.mission_context('demo', revision, ['main.py'], 'Update main'))

    def test_final_reference_wrapper_counts_toward_context_budget(self):
        payload = {'files': {'reference.py': 'text'}, 'new_files': []}
        ws = Mock()
        ws.mission_context.return_value = payload
        limit = len(json.dumps(payload).encode('utf-8'))
        with patch('hornet.desktop.plan_context.MAX_CONTEXT', limit), \
             self.assertRaisesRegex(ValueError, 'Context exceeds'):
            mission_context(ws, 'demo', 'revision', [], 'Create helper', ['helper.py'],
                            context_files=['reference.py'])
