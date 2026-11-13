import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hornet.distil.store import DistilStore, VersionConflict


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'isolated'
        self.store = DistilStore(self.root)
        self.project = self.store.create_project('Project / ..')
        self.rid = self.store.create_revision(self.project['id'], 'Revision A')['id']

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_revisions_reopen_history_and_whitespace(self):
        other = self.store.create_revision(self.project['id'], 'Revision B')['id']
        content = '  initial\r\n\n' + ('long line\n' * 10000)
        self.store.add_message(self.rid, 'user', content)
        self.store.save_draft(other, ' unfinished ')
        self.store.save_detail(other, 'Detailed')
        self.store.replace_with_distilled(self.rid, '  import\r\n', source='import')
        self.store.add_message(self.rid, 'user', 'next')
        self.store.replace_with_distilled(self.rid, 'summary\n\n')
        self.store.add_message(self.rid, 'assistant', content)
        self.store.close()
        self.store = DistilStore(self.root)
        state = self.store.get_revision(self.rid)
        self.assertEqual([m['content'] for m in state['messages']], ['summary\n\n', content])
        self.assertEqual(self.store.distilled_path(self.rid).read_bytes(), b'summary\n\n')
        self.assertEqual(len(list(self.root.rglob('*.md'))), 1)
        history = self.store.history(self.rid)
        self.assertEqual(history[1]['payload']['content'], content)
        self.assertEqual(history[2]['type'], 'import')
        self.assertEqual(history[4]['payload']['previous_messages'][1]['content'], 'next')
        self.assertEqual(self.store.get_revision(other)['draft'], ' unfinished ')
        self.assertEqual(self.store.get_revision(other)['detail'], 'Detailed')
        self.assertEqual(len(self.store.list_revisions(self.project['id'])), 2)

    def test_filesystem_failures_leave_everything_unchanged(self):
        self.store.replace_with_distilled(self.rid, 'previous')
        self.store.add_message(self.rid, 'user', 'new chat')
        self.store.save_draft(self.rid, 'draft')
        before = self.store.get_revision(self.rid)
        history = self.store.history(self.rid)
        for target in ('hornet.distil.store.os.replace', 'hornet.distil.store.os.fsync'):
            with patch(target, side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):
                    self.store.replace_with_distilled(self.rid, 'lost')
            self.assertEqual(self.store.get_revision(self.rid), before)
            self.assertEqual(self.store.history(self.rid), history)
            self.assertEqual(self.store.distilled_path(self.rid).read_bytes(), b'previous')
        self.assertEqual(list(self.root.rglob('.distil-*')), [])

    def test_cancel_before_commit_restores_file_and_messages(self):
        self.store.replace_with_distilled(self.rid, 'previous')
        before = self.store.get_revision(self.rid)
        original = self.store.get_revision
        calls = [0]
        def interrupt(rid):
            calls[0] += 1
            if calls[0] == 2:
                raise KeyboardInterrupt()
            return original(rid)
        with patch.object(self.store, 'get_revision', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.store.replace_with_distilled(self.rid, 'cancelled')
        self.assertEqual(self.store.get_revision(self.rid), before)
        self.assertEqual(self.store.distilled_path(self.rid).read_bytes(), b'previous')

    def test_database_commit_failure_restores_artifact(self):
        self.store.replace_with_distilled(self.rid, 'previous')
        before = self.store.get_revision(self.rid)
        history = self.store.history(self.rid)
        real_db = self.store._db
        broken_db = Mock(wraps=real_db)
        broken_db.commit.side_effect = OSError('commit failed')
        with patch.object(self.store, '_db', broken_db):
            with self.assertRaises(OSError):
                self.store.replace_with_distilled(self.rid, 'not committed')
        self.assertEqual(self.store.get_revision(self.rid), before)
        self.assertEqual(self.store.history(self.rid), history)
        self.assertEqual(self.store.distilled_path(self.rid).read_bytes(), b'previous')

    def test_stale_writer_is_rejected(self):
        other = DistilStore(self.root)
        try:
            version = other.get_revision(self.rid)['version']
            self.store.add_message(self.rid, 'user', 'keep me')
            with self.assertRaises(VersionConflict):
                other.replace_with_distilled(self.rid, 'stale', expected_version=version)
            with self.assertRaises(VersionConflict):
                other.add_message(self.rid, 'assistant', 'stale', expected_version=version)
            self.assertEqual(other.get_revision(self.rid)['messages'][0]['content'], 'keep me')
        finally:
            other.close()

    def test_crash_recovery_uses_database(self):
        self.store.replace_with_distilled(self.rid, 'committed')
        self.store.distilled_path(self.rid).write_text('uncommitted', encoding='utf-8')
        self.store.close()
        self.store = DistilStore(self.root)
        self.assertEqual(self.store.distilled_path(self.rid).read_bytes(), b'committed')

    def test_invalid_paths_are_rejected(self):
        for bad in ('../../escape', '/tmp/escape', '', None):
            with self.assertRaises(ValueError):
                self.store.replace_with_distilled(bad, 'bad')

    def test_revision_symlinks_are_rejected(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        try:
            (self.root / 'revisions').symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows account cannot create symbolic links')
            raise
        with self.assertRaises(ValueError):
            self.store.replace_with_distilled(self.rid, 'bad')
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.store.get_revision(self.rid)['messages'], [])

    def test_validation_and_draft_lifecycle(self):
        before = self.store.get_revision(self.rid)
        for content in ('', '  \n\t'):
            with self.assertRaises(ValueError):
                self.store.replace_with_distilled(self.rid, content)
        with self.assertRaises(ValueError):
            self.store.replace_with_distilled(self.rid, 'valid', detail='Bad')
        with self.assertRaises(ValueError):
            self.store.save_detail(self.rid, 'Bad')
        with self.assertRaises(ValueError):
            self.store.add_message(self.rid, 'system', 'invalid')
        self.assertEqual(self.store.get_revision(self.rid), before)
        events = self.store.history(self.rid)
        for draft in ('h', 'he', 'hello'):
            self.store.save_draft(self.rid, draft)
        self.assertEqual(self.store.history(self.rid), events)
        self.store.replace_with_distilled(self.rid, 'imported', source='import')
        self.assertEqual(self.store.get_revision(self.rid)['draft'], 'hello')
        self.store.add_message(self.rid, 'assistant', 'response')
        self.assertEqual(self.store.get_revision(self.rid)['draft'], 'hello')
        state = self.store.add_message(self.rid, 'user', 'hello')
        self.assertEqual(state['draft'], '')
        self.assertEqual(state['messages'][-1]['content'], 'hello')

    def test_cleanup_error_does_not_report_committed_save_as_failure(self):
        self.store.replace_with_distilled(self.rid, 'previous')
        with patch('hornet.distil.store.Path.unlink', side_effect=OSError('cleanup failed')):
            state = self.store.replace_with_distilled(self.rid, 'committed')
        self.assertEqual(state['distilled'], 'committed')
        self.assertEqual(self.store.get_revision(self.rid), state)
        self.assertEqual(self.store.distilled_path(self.rid).read_bytes(), b'committed')

    def test_manual_revision_crud_and_independent_clone(self):
        self.store.add_message(self.rid, 'user', 'archived original')
        self.store.replace_with_distilled(self.rid, '  distilled\n', detail='Detailed')
        self.store.add_message(self.rid, 'user', 'active follow-up')
        self.store.save_draft(self.rid, 'draft to copy')
        original = self.store.get_revision(self.rid)
        history = self.store.history(self.rid)
        clone = self.store.clone_revision(self.rid, 'Cloned', original['version'])
        self.assertNotEqual(clone['id'], self.rid)
        for key in ('messages', 'distilled', 'draft', 'detail', 'project_id'):
            self.assertEqual(clone[key], original[key])
        copied = self.store.history(clone['id'])
        self.assertEqual([e['payload'] for e in copied[:-1]], [e['payload'] for e in history])
        self.assertEqual(self.store.distilled_path(clone['id']).read_bytes(), b'  distilled\n')
        self.store.replace_with_distilled(clone['id'], 'clone changed')
        self.assertEqual(self.store.get_revision(self.rid), original)
        self.assertEqual(self.store.distilled_path(self.rid).read_bytes(), b'  distilled\n')
        renamed = self.store.rename_revision(clone['id'], 'Renamed')
        self.assertEqual(renamed['name'], 'Renamed')
        with self.assertRaises(VersionConflict):
            self.store.delete_revision(clone['id'], expected_version=clone['version'])
        removed = self.store.delete_revision(clone['id'], renamed['version'])
        self.assertTrue(removed['deleted'])
        self.assertEqual(len(self.store.list_revisions(self.project['id'])), 1)
        self.assertEqual(len(self.store.list_revisions(self.project['id'], include_deleted=True)), 2)
        restored = self.store.restore_revision(clone['id'])
        self.assertFalse(restored['deleted'])
        self.assertEqual(restored['messages'], removed['messages'])
        self.assertEqual(len(self.store.list_revisions(self.project['id'])), 2)

    def test_failed_clone_leaves_source_and_revision_list_unchanged(self):
        self.store.replace_with_distilled(self.rid, 'original')
        before = self.store.get_revision(self.rid)
        with patch('hornet.distil.store.os.replace', side_effect=OSError('cannot write')):
            with self.assertRaises(OSError):
                self.store.clone_revision(self.rid, 'Failed')
        self.assertEqual(self.store.get_revision(self.rid), before)
        self.assertEqual(len(self.store.list_revisions(self.project['id'])), 1)
        self.assertEqual(len(list(self.root.rglob('*.md'))), 1)
        with self.assertRaises(VersionConflict):
            self.store.clone_revision(self.rid, 'Stale', expected_version=0)

    def test_environment_root(self):
        with patch.dict('os.environ', {'HORNET_DISTIL_ROOT': str(Path(self.temp.name) / 'env')}):
            other = DistilStore()
            try:
                self.assertEqual(other.root, Path(self.temp.name) / 'env')
            finally:
                other.close()


if __name__ == '__main__':
    unittest.main()
