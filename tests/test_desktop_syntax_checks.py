import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hornet.desktop.syntax_checks import check_changed_files, summary
from hornet.desktop.automation import (inventory, make_initialization_draft,
    accept_initialization, prepare_mission)
from hornet.desktop.workspace import Workspace


class SyntaxChecksTests(unittest.TestCase):
    def test_checks_changed_files_without_running_them(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'old.py').write_text('invalid (', encoding='utf-8')
            before = inventory(root)
            (root / 'new.py').write_text('raise RuntimeError("MUST NOT EXECUTE")', encoding='utf-8')
            (root / 'data.json').write_text('{"answer": 42}', encoding='utf-8')
            (root / 'app.js').write_text('invalid javascript!', encoding='utf-8')
            report = check_changed_files(root, before, inventory(root))
            self.assertTrue(report['passed'])
            statuses = {item['path']: item['status'] for item in report['files']}
            self.assertEqual(statuses, {'new.py': 'passed', 'data.json': 'passed', 'app.js': 'skipped'})
            self.assertFalse((root / '__pycache__').exists())
            self.assertIn('1 unsupported/skipped', summary(report))
            self.assertIn('Functionality needs your review', summary(report))

    def test_invalid_python_and_json_fail(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'bad.py').write_text('return 2', encoding='utf-8')
            (root / 'bad.json').write_text('{broken}', encoding='utf-8')
            report = check_changed_files(root, {}, inventory(root))
            self.assertFalse(report['passed'])
            self.assertTrue(all(item['status'] == 'failed' for item in report['files']))

    def test_unsupported_only_is_not_claimed_validated(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.ts').write_text('not valid', encoding='utf-8')
            report = check_changed_files(root, {}, inventory(root))
            self.assertIn('0 passed', summary(report))
            self.assertEqual(report['files'][0]['status'], 'skipped')
        self.assertIn('older mission', summary(None))

    @staticmethod
    def describe(payload):
        return {'summaries': {name: 'Purpose of ' + name for name in payload['files']},
                'blueprint': {'purpose': 'Test app', 'architecture': 'Small app'}}

    @patch('hornet.desktop.vm_runner.run_python')
    @patch('hornet.desktop.automation.model_describer')
    @patch('hornet.desktop.automation.request_json')
    def test_default_mission_checks_syntax_without_vm_or_tests(self, request, describer, run):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp)
            base = ws.create('demo')
            make_initialization_draft(ws, 'demo', base, self.describe)
            ready = accept_initialization(ws, 'demo')
            request.side_effect = [
                {'existing_files': [], 'new_files': ['config.json'], 'explanation': 'Add config'},
                {'summary': 'Added config', 'edits': [{'uri': 'files.text.atomic_edit',
                  'arguments': {'path': 'config.json', 'action': 'create', 'new_text': '{"value": 2}'}}]}]
            describer.return_value = self.describe
            result = prepare_mission(ws, 'demo', ready, 'Add config', 'http://localhost:1234/v1', 'model')
            run.assert_not_called()
            self.assertEqual(result['state'], 'review')
            self.assertTrue(result['syntax_checks']['passed'])
            self.assertEqual(result['syntax_checks']['files'][0]['path'], 'config.json')
            self.assertEqual(result['syntax_checks']['source_sha256'], result['final_source_sha256'])
            self.assertIn('Do not add unit tests', request.call_args_list[0].args[2])

    @patch('hornet.desktop.syntax_checks.check_changed_files')
    @patch('hornet.desktop.automation.model_describer')
    @patch('hornet.desktop.automation.request_json')
    def test_failed_syntax_stays_failed_with_evidence(self, request, describer, syntax):
        syntax.return_value = {'passed': False, 'files': [{'path': 'bad.json', 'status': 'failed', 'error': 'Parser rejected file'}]}
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp)
            base = ws.create('demo')
            make_initialization_draft(ws, 'demo', base, self.describe)
            ready = accept_initialization(ws, 'demo')
            request.side_effect = [
                {'existing_files': [], 'new_files': ['bad.json'], 'explanation': 'Add config'},
                {'summary': 'Added config', 'edits': [{'uri': 'files.text.atomic_edit',
                  'arguments': {'path': 'bad.json', 'action': 'create', 'new_text': '{"valid": true}'}}]}]
            with self.assertRaisesRegex(ValueError, 'syntax check failed'):
                prepare_mission(ws, 'demo', ready, 'Add config', 'http://localhost:1234/v1', 'model')
            records = list((ws.project('demo') / 'missions').glob('*.json'))
            result = json.loads(records[0].read_text(encoding='utf-8'))
            self.assertEqual(result['state'], 'failed')
            self.assertFalse(result['syntax_checks']['passed'])
            describer.assert_not_called()
