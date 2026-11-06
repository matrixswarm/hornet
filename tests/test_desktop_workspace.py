import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import unittest

from hornet.desktop.workspace import Workspace, request_proposal, discover_models, parse_proposal, safe_path, source_files


class DesktopWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Workspace(Path(self.temp.name) / 'workspace')
        base = self.ws.create('demo')
        from hornet.desktop.automation import make_initialization_draft, accept_initialization
        make_initialization_draft(self.ws, 'demo', base,
                                  lambda payload: {'summaries': {name: 'Test source' for name in payload['files']}})
        self.rev = accept_initialization(self.ws, 'demo')

    def test_revision_preserves_parent_and_tracks_changes(self):
        parent = self.ws.revision('demo', self.rev)
        original = (parent / 'main.py').read_text()
        rev = self.ws.fork('demo', self.rev, {'main.py': 'import json\nvalue = 3\n'}, 'Change value')
        self.assertEqual((parent / 'main.py').read_text(), original)
        current = self.ws.revision('demo', rev)
        shadow = json.loads((current / '.hornet-meta/shadows.json').read_text())['main.py']
        self.assertEqual(shadow['imports'], ['json'])
        self.assertIn('review needed', shadow['review'])
        self.assertEqual(json.loads((current / '.hornet-meta/revision.json').read_text())['parent'], self.rev)

    def test_invalid_proposal_leaves_no_partial_revision(self):
        before = self.ws.revisions('demo')
        with self.assertRaises(ValueError):
            self.ws.fork('demo', self.rev, {'../escape.py': 'oops'})
        self.assertEqual(before, self.ws.revisions('demo'))
        self.assertFalse(any(p.name.startswith('.working') for p in (self.ws.project('demo') / 'revisions').iterdir()))

    def test_paths_and_links(self):
        root = self.ws.revision('demo', self.rev)
        for name in ('../escape', '/tmp/escape', '.env', 'a/../../escape', 'C:/file', 'a\\file', '.blueprint'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_path(root, name)
        (root / 'link.py').symlink_to(root / 'main.py')
        self.assertNotIn('link.py', list(source_files(root)))
        with self.assertRaises(ValueError):
            safe_path(root, 'link.py')

    def test_blueprint_versioning_and_trash(self):
        # Keep one initial revision before exercising the last-revision guard.
        for older in self.ws.revisions('demo'):
            if older != self.rev:
                self.ws.trash('demo', older)
        rev = self.ws.fork('demo', self.rev, blueprint={'purpose': 'new purpose'})
        self.assertEqual(json.loads((self.ws.revision('demo', rev) / '.blueprint').read_text()), {'purpose': 'new purpose'})
        self.assertNotEqual(json.loads((self.ws.revision('demo', self.rev) / '.blueprint').read_text()), {'purpose': 'new purpose'})
        self.ws.trash('demo', rev)
        self.assertTrue((self.ws.project('demo') / 'trash' / rev).exists())
        with self.assertRaises(ValueError):
            self.ws.trash('demo', self.rev)

    def test_nuke_project_preserves_all_data_and_allows_reimport(self):
        root = self.ws.project('demo')
        (root / 'initialization-draft.json').write_text('draft')
        (root / 'missions').mkdir()
        (root / 'missions' / 'record.json').write_text('record')
        expected = (self.ws.revision('demo', self.rev) / 'main.py').read_bytes()
        destination = self.ws.trash_project('demo')
        self.assertNotIn('demo', self.ws.list_projects())
        self.assertEqual((destination / 'revisions' / self.rev / 'main.py').read_bytes(), expected)
        self.assertEqual((destination / 'missions' / 'record.json').read_text(), 'record')
        self.assertEqual((destination / 'initialization-draft.json').read_text(), 'draft')
        self.ws.create('demo')
        second = self.ws.trash_project('demo')
        self.assertNotEqual(destination, second)
        self.assertTrue(destination.is_dir())
        self.assertTrue(second.is_dir())
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            self.ws.trash_project('demo')

    def test_nuke_project_rejects_symlinks(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (outside / 'keep').write_text('safe')
        (self.ws.projects / 'link').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            self.ws.trash_project('link')
        self.assertEqual((outside / 'keep').read_text(), 'safe')

    def test_import_excludes_runtime_and_preserves_original(self):
        source = Path(self.temp.name) / 'import'
        source.mkdir()
        (source / 'app.py').write_text('value = 1')
        (source / '.env').write_text('secret')
        (source / '.git').mkdir()
        (source / '.git/config').write_text('private')
        rev = self.ws.create('imported', source=source)
        root = self.ws.revision('imported', rev)
        self.assertEqual(list(source_files(root)), ['app.py'])
        self.assertEqual((source / '.env').read_text(), 'secret')
        with self.assertRaises(ValueError):
            self.ws.create('recursive', source=self.ws.root)

    def test_import_preserved_revision_from_project_trash(self):
        preserved = self.ws.trash_project('demo')
        source = preserved / 'revisions' / self.rev
        expected = (source / 'main.py').read_bytes()
        revision = self.ws.create('demo', source=source)
        self.assertEqual((self.ws.revision('demo', revision) / 'main.py').read_bytes(), expected)
        self.assertEqual((source / 'main.py').read_bytes(), expected)
        self.assertTrue((source / '.blueprint').exists())
        with self.assertRaisesRegex(ValueError, 'outside the Hornet workspace'):
            self.ws.create('active', source=self.ws.revision('demo', revision))
        with self.assertRaisesRegex(ValueError, 'outside the Hornet workspace'):
            self.ws.create('trashroot', source=preserved)

    def test_export_revision_saves_complete_copy_without_overwriting(self):
        parent = Path(self.temp.name) / 'exports'
        parent.mkdir()
        source = self.ws.revision('demo', self.rev)
        target = self.ws.export_revision('demo', self.rev, parent)
        self.assertEqual((target/'main.py').read_bytes(), (source/'main.py').read_bytes())
        self.assertEqual((target/'.blueprint').read_bytes(), (source/'.blueprint').read_bytes())
        self.assertTrue((target/'.hornet-meta/revision.json').exists())
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.ws.export_revision('demo', self.rev, parent)
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.ws.export_revision('demo', self.rev, self.ws.root)
        self.assertTrue(source.is_dir())

    def test_context_bounds(self):
        with self.assertRaises(ValueError):
            self.ws.mission_context('demo', self.rev, [], 'fix it')
        root = self.ws.revision('demo', self.rev)
        (root / 'large.txt').write_text('x' * 128001)
        with self.assertRaises(ValueError):
            self.ws.mission_context('demo', self.rev, ['large.txt'], 'fix it')

    def test_mission_reads_selected_revision_blueprint_and_rejects_invalid(self):
        contract = {'project': 'demo', 'architecture': 'Components own Interfaces and AbstractClasses.'}
        revised = self.ws.fork('demo', self.rev, blueprint=contract)
        payload = self.ws.mission_context('demo', revised, ['main.py'], 'Update greeting')
        self.assertEqual(payload['blueprint'], contract)
        root = self.ws.revision('demo', revised)
        (root / '.blueprint').write_text('{ invalid')
        with self.assertRaisesRegex(ValueError, 'valid UTF-8 JSON'):
            self.ws.mission_context('demo', revised, ['main.py'], 'Update greeting')
        (root / '.blueprint').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            self.ws.mission_context('demo', revised, ['main.py'], 'Update greeting')
        (root / '.blueprint').unlink()
        with self.assertRaisesRegex(ValueError, 'no readable blueprint'):
            self.ws.mission_context('demo', revised, ['main.py'], 'Update greeting')

    def test_model_roundtrip_and_scope_rejection(self):
        payload = self.ws.mission_context('demo', self.rev, ['main.py'], 'Update greeting', ['feature/new.py'])
        proposed = {'summary': 'Updated greeting', 'files': {'main.py': 'print("Hello")\n', 'feature/new.py': 'VALUE = 42\n'}}
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'data': [{'id': 'local-test'}]}).encode())
            def do_POST(self):
                received.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'choices': [{'message': {'content': json.dumps(proposed)}}]}).encode())
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/v1'
            self.assertEqual(discover_models(url), ['local-test'])
            self.assertEqual(request_proposal(url, 'test-model', payload), proposed)
            self.assertEqual(received[0][0], '/v1/chat/completions')
            self.assertEqual(received[0][1]['model'], 'test-model')
            system = received[0][1]['messages'][0]['content']
            self.assertIn('Read the supplied blueprint', system)
            self.assertIn('Do not silently depart from the blueprint', system)
            self.assertIn('return no files', system)
            transmitted = json.loads(received[0][1]['messages'][1]['content'])
            self.assertEqual(transmitted['blueprint'], payload['blueprint'])
            self.assertEqual(transmitted['new_files'], ['feature/new.py'])
            new_rev = self.ws.fork('demo', self.rev, proposed['files'], 'Update greeting',
                                   proposal=proposed, expected_context=payload)
            self.assertEqual((self.ws.revision('demo', new_rev) / 'feature/new.py').read_text(), 'VALUE = 42\n')
            self.assertFalse((self.ws.revision('demo', self.rev) / 'feature/new.py').exists())
            self.assertTrue((self.ws.revision('demo', new_rev) / '.hornet-meta/proposal.json').is_file())
            proposed['files'] = {'other.py': 'out of scope'}
            with self.assertRaises(ValueError):
                request_proposal(url, 'test-model', payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_new_file_scope_and_base_changes(self):
        root = self.ws.revision('demo', self.rev)
        for path in ('../escape', 'main.py', '.env', 'folder/../other.py', '/absolute'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.ws.mission_context('demo', self.rev, [], 'Create a file', [path])
        payload = self.ws.mission_context('demo', self.rev, ['main.py'], 'Create a file', ['new.py'])
        (root / 'new.py').write_text('already exists')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.ws.fork('demo', self.rev, {'new.py': 'new'}, expected_context=payload)
        (root / 'new.py').unlink()
        (root / 'main.py').write_text('changed outside Hornet')
        with self.assertRaisesRegex(ValueError, 'base file changed'):
            self.ws.fork('demo', self.rev, {'new.py': 'new'}, expected_context=payload)

    def test_fenced_model_response_still_enforces_scope(self):
        payload = {'files': {}, 'new_files': ['new.py']}
        value = {'summary': 'Created', 'files': {'new.py': 'print(1)\n'}}
        self.assertEqual(parse_proposal('```json\n' + json.dumps(value) + '\n```', payload), value)
        value['files'] = {'../bad.py': 'bad'}
        with self.assertRaisesRegex(ValueError, 'out-of-scope'):
            parse_proposal('```json\n' + json.dumps(value) + '\n```', payload)
        with self.assertRaises(ValueError):
            parse_proposal('text before\n```json\n{}\n```', payload)


if __name__ == '__main__':
    unittest.main()
