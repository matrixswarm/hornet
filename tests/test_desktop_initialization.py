import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hornet.desktop.automation import make_initialization_draft, accept_initialization, read_state
from hornet.desktop.initialization import MAX_CHUNK_JSON_BYTES
from hornet.desktop.workspace import Workspace


class InitializationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = Workspace(self.root / 'hornet')
        self.source = self.root / 'original'
        self.source.mkdir()
        (self.source / 'main.py').write_text('# ' + 'é🐝' * 13000 + '\n', encoding='utf-8')
        (self.source / 'helper.py').write_text('VALUE = 42\n')
        (self.source / 'chats').mkdir()
        (self.source / 'chats' / 'private.txt').write_text('private chat sentinel')
        (self.source / 'memories').mkdir()
        (self.source / 'memories' / 'note.txt').write_text('private memory sentinel')
        (self.source / 'main.py.atomic').write_text('old backup')
        (self.source / 'credentials.json').write_text('private credentials')
        # Explicitly include all files to simulate an old project already imported.
        files = [p.relative_to(self.source).as_posix() for p in self.source.rglob('*') if p.is_file()]
        self.base = self.ws.create('demo', source=self.source, include_files=files)

    @staticmethod
    def respond(payload):
        return {'summaries': {name: 'Factual summary of this part' for name in payload['files']}}

    def test_chunks_cover_multibyte_source_and_excluded_content_is_not_sent(self):
        payloads = []
        def describe(payload):
            payloads.append(payload)
            return self.respond(payload)
        draft = make_initialization_draft(self.ws, 'demo', self.base, describe)
        self.assertEqual(draft['unreviewed'], [])
        self.assertEqual(set(draft['included']), {'main.py', 'helper.py'})
        self.assertEqual(len(draft['excluded']), 4)
        parts = [p for p in payloads if 'main.py' in p['files']]
        self.assertGreater(len(parts), 1)
        self.assertEqual(''.join(p['files']['main.py'] for p in parts), (self.source/'main.py').read_bytes().decode('utf-8'))
        self.assertTrue(all(len(json.dumps(p['files']['main.py']).encode()) <= MAX_CHUNK_JSON_BYTES for p in parts))
        self.assertNotIn('private chat sentinel', json.dumps(payloads))
        self.assertNotIn('chats/private.txt', json.dumps(payloads))
        ready = accept_initialization(self.ws, 'demo')
        marker = json.loads((self.ws.revision('demo', ready)/'.hornet-meta/initialization.json').read_text())
        self.assertEqual(set(marker['excluded']), set(draft['excluded']))
        self.assertEqual(marker['chunk_counts']['main.py'], len(parts))

    def test_interrupted_chunk_resumes_without_repeating_completed_work(self):
        calls = []
        def describe(payload):
            name = next(iter(payload['files']))
            return {'summaries': {name: 'Summary of ' + name},
                    'blueprint': {'directory_roles': {name: 'reviewed ' + name}}}
        def failing(payload):
            calls.append((next(iter(payload['files'])), payload['file_part']['number']))
            if len(calls) == 3:
                raise RuntimeError('server stopped')
            return describe(payload)
        with self.assertRaisesRegex(RuntimeError, 'server stopped'):
            make_initialization_draft(self.ws, 'demo', self.base, failing)
        saved = json.loads((self.ws.project('demo')/'initialization-draft.json').read_text())
        self.assertEqual(saved['version'], 2)
        self.assertEqual(saved['blueprint']['directory_roles']['helper.py'], 'reviewed helper.py')
        self.assertTrue(saved['unreviewed'])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            accept_initialization(self.ws, 'demo')
        resumed = []
        def retry(payload):
            resumed.append((next(iter(payload['files'])), payload['file_part']['number']))
            return describe(payload)
        draft = make_initialization_draft(self.ws, 'demo', self.base, retry)
        self.assertTrue(set(calls[:2]).isdisjoint(resumed))
        self.assertIn(calls[2], resumed)
        self.assertEqual(draft['unreviewed'], [])
        self.assertEqual(draft['blueprint']['directory_roles']['helper.py'], 'reviewed helper.py')
        accept_initialization(self.ws, 'demo')

    def test_missing_summary_is_not_accepted_and_can_be_retried(self):
        draft = make_initialization_draft(self.ws, 'demo', self.base, lambda p: {'summaries': {}})
        self.assertTrue(draft['unreviewed'])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            accept_initialization(self.ws, 'demo')
        draft = make_initialization_draft(self.ws, 'demo', self.base, self.respond)
        self.assertEqual(draft['unreviewed'], [])
        accept_initialization(self.ws, 'demo')

    def test_edited_draft_cannot_hide_missing_chunks(self):
        make_initialization_draft(self.ws, 'demo', self.base, self.respond)
        path = self.ws.project('demo')/'initialization-draft.json'
        draft = json.loads(path.read_text())
        draft['chunks']['main.py'].pop('1')
        path.write_text(json.dumps(draft))
        with self.assertRaisesRegex(ValueError, 'incomplete file parts'):
            accept_initialization(self.ws, 'demo')

    def test_reinitialize_preserves_accepted_revision_until_review(self):
        make_initialization_draft(self.ws, 'demo', self.base, self.respond)
        ready = accept_initialization(self.ws, 'demo')
        make_initialization_draft(self.ws, 'demo', ready, lambda p: {'summaries': {}})
        self.assertEqual(read_state(self.ws, 'demo')['accepted_revision'], ready)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            accept_initialization(self.ws, 'demo')
        make_initialization_draft(self.ws, 'demo', ready, self.respond)
        self.assertEqual(read_state(self.ws, 'demo')['accepted_revision'], ready)
        updated = accept_initialization(self.ws, 'demo')
        self.assertNotEqual(updated, ready)
        self.assertEqual(read_state(self.ws, 'demo')['accepted_revision'], updated)

    def test_changed_file_restarts_and_preserves_previous_draft(self):
        def describe(payload):
            name = next(iter(payload['files']))
            text = payload['files'][name]
            return {'summaries': {name: 'Summary of ' + name},
                    'blueprint': {'directory_roles': {name: 'old helper' if name == 'helper.py' and '42' in text
                                                     else 'reviewed ' + name}}}
        original = make_initialization_draft(self.ws, 'demo', self.base, describe)
        self.assertEqual(original['blueprint']['directory_roles']['helper.py'], 'old helper')
        (self.ws.revision('demo', self.base)/'helper.py').write_text('VALUE = 99\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            accept_initialization(self.ws, 'demo')
        calls, progress = [], []
        def record(payload):
            calls.append(next(iter(payload['files'])))
            return describe(payload)
        draft = make_initialization_draft(self.ws, 'demo', self.base, record, progress.append)
        self.assertEqual(set(calls), {'helper.py', 'main.py'})
        self.assertGreater(calls.count('main.py'), 1)
        self.assertEqual(draft['blueprint']['directory_roles']['helper.py'], 'reviewed helper.py')
        self.assertEqual(draft['blueprint']['directory_roles']['main.py'], 'reviewed main.py')
        self.assertTrue(any('restarting initialization' in item for item in progress))
        self.assertTrue(any('restarted' in item for item in draft['review_notes']))
        self.assertEqual(len(list((self.ws.project('demo')/'initialization-drafts').glob('*.json'))), 1)
        ready = accept_initialization(self.ws, 'demo')
        accepted = json.loads((self.ws.revision('demo', ready)/'.blueprint').read_text(encoding='utf-8'))
        self.assertEqual(accepted['directory_roles']['helper.py'], 'reviewed helper.py')
        self.assertEqual(accepted['directory_roles']['main.py'], 'reviewed main.py')

    def test_import_defaults_to_excluding_private_files_and_can_opt_in(self):
        (self.source/'classes'/'memory').mkdir(parents=True)
        (self.source/'classes'/'memory'/'store.py').write_text('class Store: pass\n')
        rev = self.ws.create('clean', source=self.source)
        root = self.ws.revision('clean', rev)
        self.assertTrue((root/'main.py').is_file())
        self.assertTrue((root/'classes/memory/store.py').is_file())
        self.assertFalse((root/'chats').exists())
        self.assertFalse((root/'memories').exists())
        self.assertFalse((root/'credentials.json').exists())
        self.assertFalse((root/'main.py.atomic').exists())
        self.assertTrue((self.source/'chats/private.txt').exists())
        rev = self.ws.create('optin', source=self.source, include_files=['chats/private.txt'])
        self.assertTrue((self.ws.revision('optin', rev)/'chats/private.txt').is_file())

    def test_binary_blocks_acceptance_until_intentionally_excluded(self):
        path = self.ws.revision('demo', self.base)/'image.bin'
        path.write_bytes(b'\x00\xff')
        draft = make_initialization_draft(self.ws, 'demo', self.base, self.respond)
        self.assertIn('image.bin', draft['unreviewed'])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            accept_initialization(self.ws, 'demo')
        draft = make_initialization_draft(self.ws, 'demo', self.base, self.respond, included=['main.py', 'helper.py'])
        self.assertIn('image.bin', draft['excluded'])
        accept_initialization(self.ws, 'demo')


if __name__ == '__main__':
    unittest.main()
