import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from hornet.desktop.workspace import Workspace
from hornet.desktop.vm_project import apply_template


class VMProjectTests(unittest.TestCase):
    def test_overlay_preserves_database_and_missing_files(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp)/'workspace')
            ws.create('example')
            template = Path(tmp)/'template'
            (template/'codex').mkdir(parents=True)
            (template/'codex'/'a.py').write_text('old')
            (template/'codex'/'keep.py').write_text('keep')
            (template/'unified_registry.db').write_bytes(b'template database')
            profile = ws.project('example')/'test-vm.json'
            profile.write_text(json.dumps({'template_directory':str(template), 'template_paths':['codex/','unified_registry.db'], 'revision_paths':['./codex']}))
            inputs = {'codex/a.py':base64.b64encode(b'new').decode(), 'unified_registry.db':base64.b64encode(b'revision database').decode()}
            result, stamp = apply_template(ws, 'example', inputs)
            self.assertEqual(base64.b64decode(result['codex/a.py']), b'new')
            self.assertEqual(base64.b64decode(result['codex/keep.py']), b'keep')
            self.assertEqual(base64.b64decode(result['unified_registry.db']), b'template database')
            self.assertEqual((template/'codex'/'a.py').read_text(), 'old')
            (template/'unified_registry.db').write_bytes(b'changed')
            self.assertNotEqual(apply_template(ws, 'example', inputs)[1], stamp)
            with self.assertRaisesRegex(ValueError, 'no files matching'):
                apply_template(ws, 'example', {'other.py': ''})

    def test_parent_mapping_rejected(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp)
            ws.create('example')
            profile = ws.project('example')/'test-vm.json'
            profile.write_text(json.dumps({'template_directory':tmp, 'template_paths':['../outside'], 'revision_paths':['codex']}))
            with self.assertRaises(ValueError):
                apply_template(ws, 'example', {})

    @patch('hornet.desktop.prepare_test_vm.SSHWorker')
    def test_setup_adds_second_project_with_same_worker(self, worker):
        from hornet.desktop.prepare_test_vm import main
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = Workspace(root/'workspace')
            ws.create('one'); ws.create('two')
            config = root/'worker.json'; config.write_text('{}')
            template = root/'template'; (template/'codex').mkdir(parents=True)
            (template/'codex'/'main.py').write_text('pass')
            base = ['setup', '--workspace', str(ws.root), '--worker-config', str(config)]
            with patch('sys.argv', base):
                main()
            original = (ws.root/'test-vm.json').read_bytes()
            for project in ('one', 'two'):
                with patch('sys.argv', base + ['--project', project, '--template', str(template), '--template-path', 'codex/', '--revision-path', './codex']):
                    main()
                profile = json.loads((ws.project(project)/'test-vm.json').read_text())
                self.assertEqual(profile['template_paths'], ['codex'])
                self.assertEqual(profile['revision_paths'], ['codex'])
            self.assertEqual((ws.root/'test-vm.json').read_bytes(), original)
