import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hornet.desktop.goal_store import GoalStore, GoalStoreError, GoalIdentityMismatch, GoalResumeBlocked


class GoalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'goals'
        self.identity = {'project': 'demo', 'revision': 'r1', 'operation_id': 'op1', 'source': 'abc'}
        self.store = GoalStore(self.directory, self.identity)

    def reopen(self):
        return GoalStore(self.directory, self.identity)

    def test_hierarchy_pointer_and_evidence_survive_restart(self):
        self.store.begin('mission/inspect/source', 'Inspect source', 'abc', read_only=True)
        self.store.complete('mission/inspect/source', {'sha256': 'abc'})
        view = self.reopen().snapshot()
        self.assertEqual(view['sequence'], 2)
        self.assertEqual(view['pointer']['path'], 'mission/inspect/source')
        self.assertEqual(view['hierarchy'][0]['children'][0]['children'][0]['evidence'], {'sha256': 'abc'})
        self.assertEqual(self.reopen().resume()['status'], 'ready')
        self.store.complete('mission/inspect', {'children_verified': True})
        self.store.complete('mission', {'review': 'all evidence verified'})
        self.assertEqual(self.reopen().resume()['status'], 'complete')
        self.assertEqual(len(list((self.directory / 'goals').rglob('*.json'))), 3)
        self.assertEqual(len(list((self.directory / 'pointers').rglob('*.json'))), 4)

    def test_effect_is_blocked_until_explicit_reconciliation(self):
        self.store.begin('publish', 'Publish revision', 'abc')
        reopened = self.reopen()
        self.assertEqual(reopened.resume()['status'], 'blocked')
        with self.assertRaises(GoalResumeBlocked):
            reopened.begin('publish', 'Publish revision', 'abc')
        with self.assertRaises(GoalResumeBlocked):
            reopened.complete('publish', {'revision': 'r2'})
        reopened.reconcile(self.identity, path='publish', outcome='completed', evidence={'revision': 'r2'})
        self.assertEqual(reopened.resume()['status'], 'complete')

    def test_read_only_restart_reclaims_and_rejects_stale_worker(self):
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        reopened = self.reopen()
        self.assertEqual(reopened.resume()['status'], 'resume')
        reopened.begin('inspect', 'Inspect', 'abc', read_only=True)
        with self.assertRaises(GoalResumeBlocked):
            self.store.complete('inspect', {'old': True})
        reopened.complete('inspect', {'current': True})
        self.assertEqual(self.store.snapshot()['nodes']['inspect']['evidence'], {'current': True})

    def test_proven_not_applied_allows_new_effect_attempt(self):
        before = self.store.begin('publish', 'Publish', 'abc')['pointer']['attempt_id']
        reopened = self.reopen()
        reopened.reconcile(self.identity, path='publish', outcome='not_applied',
                           evidence={'revision_absent': True}, expected_sequence=1)
        self.assertEqual(reopened.resume()['status'], 'ready')
        after = reopened.begin('publish', 'Publish', 'abc')['pointer']['attempt_id']
        self.assertNotEqual(before, after)
        reopened.complete('publish', {'revision': 'r2'})
        self.assertEqual(reopened.snapshot()['nodes']['publish']['reconciliation']['outcome'], 'not_applied')

    def test_identity_and_fingerprint_changes_are_refused(self):
        with self.assertRaises(GoalIdentityMismatch):
            GoalStore(self.directory, dict(self.identity, revision='r2'))
        with self.assertRaises(GoalIdentityMismatch):
            self.store.reconcile(dict(self.identity, source='changed'))
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        self.store.complete('inspect', {'sha256': 'abc'})
        with self.assertRaises(GoalIdentityMismatch):
            self.store.begin('inspect', 'Inspect', 'changed', read_only=True)

    def test_completed_evidence_and_sequence_snapshots_are_immutable(self):
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        self.store.complete('inspect', {'sha256': 'abc'})
        path = self.directory / 'snapshots' / '000000000002.json'
        before = path.read_bytes()
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        self.store.complete('inspect', {'sha256': 'abc'})
        self.assertEqual(self.store.snapshot()['sequence'], 2)
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(GoalStoreError):
            self.store.complete('inspect', {'different': True})
        self.store.begin('next', 'Next', 'abc', read_only=True)
        self.assertEqual(path.read_bytes(), before)
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        self.store.complete('inspect', {'sha256': 'abc'})
        self.assertEqual(self.store.snapshot()['pointer']['path'], 'next')

    def test_incomplete_group_and_completed_parent_reject_changes(self):
        self.store.begin('root/step', 'Step', 'abc')
        with self.assertRaises(GoalStoreError):
            self.store.complete('root', {'done': True})
        self.store.complete('root/step', {'done': True})
        self.store.complete('root', {'done': True})
        with self.assertRaises(GoalStoreError):
            self.store.begin('root/extra', 'Extra', 'abc')
        self.assertEqual(self.store.snapshot()['sequence'], 3)

    def test_non_group_cannot_receive_children(self):
        self.store.begin('step', 'Step', 'abc', read_only=True)
        self.store.complete('step', {'done': True})
        with self.assertRaises(GoalStoreError):
            self.store.begin('step/child', 'Child', 'abc')

    def test_other_worker_cannot_steal_effect_pointer(self):
        other = self.reopen()
        self.store.begin('publish', 'Publish', 'abc')
        for path in ('publish', 'next'):
            with self.assertRaises(GoalResumeBlocked):
                other.begin(path, path.title(), 'abc')
        self.store.complete('publish', {'revision': 'r2'})
        other.begin('next', 'Next', 'abc')
        self.assertEqual(other.snapshot()['sequence'], 3)

    def test_projection_failure_does_not_undo_authoritative_commit(self):
        import hornet.desktop.goal_store as module
        original = module._atomic_write
        def fail_mirror(path, data):
            if path.name == 'goals.json':
                raise OSError('disk full writing mirror')
            return original(path, data)
        with patch.object(module, '_atomic_write', fail_mirror):
            self.store.begin('publish', 'Publish', 'abc')
            self.assertIn('derived views', self.store.projection_warning)
        reopened = self.reopen()
        self.assertEqual(reopened.resume()['status'], 'blocked')
        goals = json.loads((self.directory / 'goals.json').read_text())
        pointers = json.loads((self.directory / 'pointers.json').read_text())
        self.assertEqual(goals['sequence'], 1)
        self.assertEqual(pointers['sequence'], 1)

    def test_failed_atomic_authority_replace_leaves_old_checkpoint(self):
        original = os.replace
        def fail_state(source, target):
            if Path(target).name == 'state.json':
                raise OSError('simulated interruption')
            return original(source, target)
        with patch('hornet.desktop.goal_store.os.replace', fail_state), self.assertRaises(OSError):
            self.store.begin('publish', 'Publish', 'abc')
        self.assertEqual(self.reopen().snapshot()['sequence'], 0)
        self.assertFalse(list(self.directory.glob('*.tmp')))

    def test_mirrors_are_ignored_and_rebuilt(self):
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        (self.directory / 'goals.json').write_text('{broken')
        (self.directory / 'pointers.json').write_text('{"sequence":999,"pointer":"fake"}')
        reopened = self.reopen()
        self.assertEqual(reopened.snapshot()['sequence'], 1)
        self.assertEqual(json.loads((self.directory / 'pointers.json').read_text())['pointer']['path'], 'inspect')

    def test_missing_or_corrupt_authority_never_resets_from_mirrors(self):
        self.store.begin('publish', 'Publish', 'abc')
        (self.directory / 'state.json').write_text('{broken')
        with self.assertRaises(GoalStoreError):
            self.reopen()
        (self.directory / 'state.json').unlink()
        with self.assertRaisesRegex(GoalStoreError, 'missing'):
            self.reopen()

    def test_invalid_pointer_and_evidence_are_rejected(self):
        self.store.begin('publish', 'Publish', 'abc')
        path = self.directory / 'state.json'
        original = json.loads(path.read_text())
        cases = []
        for pointer in ('nonexistent', None):
            invalid = json.loads(json.dumps(original))
            invalid['pointer'] = pointer
            cases.append(invalid)
        invalid = json.loads(json.dumps(original))
        invalid['nodes']['publish']['status'] = 'complete'
        cases.append(invalid)
        for invalid in cases:
            path.write_text(json.dumps(invalid))
            with self.assertRaises(GoalStoreError):
                self.store.snapshot()

    def test_bad_paths_and_evidence_do_not_advance_state(self):
        for path in ('../escape', '/absolute', 'a//b', 'a/../b', 'a\\b', 'C:/escape', '', '.', 'CON', 'a.', ['a/b', '..']):
            with self.subTest(path=path), self.assertRaises(GoalStoreError):
                self.store.begin(path, 'Unsafe', 'abc')
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        for evidence in ({}, [], {'nan': float('nan')}, {1: 'bad key'}, {'not_json': object()}):
            with self.subTest(evidence=evidence), self.assertRaises(GoalStoreError):
                self.store.complete('inspect', evidence)
        self.assertEqual(self.store.snapshot()['sequence'], 1)

    def test_stale_reconciliation_evidence_is_rejected(self):
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        self.reopen().begin('inspect', 'Inspect', 'abc', read_only=True)
        with self.assertRaisesRegex(GoalStoreError, 'changed'):
            self.store.reconcile(self.identity, path='inspect', outcome='completed',
                                 evidence={'old_result': True}, expected_sequence=1)

    def test_snapshot_results_and_identity_are_detached(self):
        self.identity['project'] = 'changed'
        view = self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        view['nodes']['inspect']['status'] = 'complete'
        self.assertEqual(self.store.snapshot()['identity']['project'], 'demo')
        self.assertEqual(self.store.snapshot()['nodes']['inspect']['status'], 'running')

    def test_symlink_store_and_projection_targets_are_rejected(self):
        target = Path(self.temp.name) / 'outside'
        target.mkdir()
        link = Path(self.temp.name) / 'linked'
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest('Symlink creation unavailable: ' + str(exc))
        with self.assertRaises(GoalStoreError):
            GoalStore(link, self.identity)
        mirror = self.directory / 'goals.json'
        mirror.unlink()
        external = target / 'keep.json'
        external.write_text('do not change')
        mirror.symlink_to(external)
        with self.assertRaises(GoalStoreError):
            self.reopen()
        self.assertEqual(external.read_text(), 'do not change')

    def test_conflicting_immutable_snapshot_is_not_overwritten(self):
        snapshot = self.directory / 'snapshots' / '000000000000.json'
        snapshot.write_text('{"unexpected":true}')
        with self.assertRaisesRegex(GoalStoreError, 'snapshot conflicts'):
            self.reopen()
        self.assertEqual(snapshot.read_text(), '{"unexpected":true}')

    def test_process_exit_after_effect_begin_stays_blocked(self):
        script = ('import json,sys; from hornet.desktop.goal_store import GoalStore; '
                  's=GoalStore(sys.argv[1],json.loads(sys.argv[2])); '
                  's.begin("publish","Publish","abc"); import os; os._exit(0)')
        process = subprocess.run([sys.executable, '-c', script, str(self.directory), json.dumps(self.identity)],
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.reopen().resume()['status'], 'blocked')

    def test_deferred_read_only_work_does_not_block_next_file(self):
        self.store.begin('files/one/part1', 'First file', 'a', read_only=True)
        self.store.defer('files/one/part1', {'error': 'Summary validation failed'})
        self.assertIsNone(self.store.snapshot()['pointer'])
        self.assertEqual(self.store.resume()['status'], 'ready')
        self.store.begin('files/two/part1', 'Second file', 'b', read_only=True)
        self.store.complete('files/two/part1', {'summary_sha': 'valid'})
        with self.assertRaises(GoalStoreError):
            self.store.complete('files', {'done': True})
        self.store.begin('files/one/part1', 'First file', 'a', read_only=True)
        self.store.complete('files/one/part1', {'summary_sha': 'corrected'})
        self.assertEqual(self.store.snapshot()['nodes']['files/one/part1']['reconciliation']['outcome'], 'deferred')
        self.assertTrue((self.directory / 'goals/files/one/part1/_goal.json').is_file())
        self.assertTrue((self.directory / 'pointers/files/one/part1/_pointer.json').is_file())

    def test_effect_and_nonowner_cannot_defer(self):
        self.store.begin('effect', 'Effect', 'abc')
        with self.assertRaises(GoalResumeBlocked):
            self.store.defer('effect', {'error': 'maybe happened'})
        self.store.complete('effect', {'done': True})
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        with self.assertRaises(GoalResumeBlocked):
            self.reopen().defer('inspect', {'error': 'other owner'})

    def test_cancelled_worker_cannot_commit_late_pointer(self):
        from hornet.desktop.cancellation import bind, Cancellation, Cancelled
        token = Cancellation()
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        bind(token)
        try:
            token.cancel()
            with self.assertRaises(Cancelled):
                self.store.complete('inspect', {'done': True})
        finally:
            bind(None)
        self.assertEqual(self.store.snapshot()['sequence'], 1)
        self.assertEqual(self.store.snapshot()['nodes']['inspect']['status'], 'running')

    def test_case_aliases_cannot_collide_in_directory_mirrors(self):
        self.store.begin('File', 'File', 'abc', read_only=True)
        self.store.complete('File', {'done': True})
        with self.assertRaises(GoalStoreError):
            self.store.begin('file', 'Other', 'abc', read_only=True)
        self.assertEqual(self.store.snapshot()['sequence'], 2)


    def test_concurrent_processes_cannot_both_begin_effect(self):
        script = ('import json,sys; from hornet.desktop.goal_store import GoalStore,GoalResumeBlocked; '
                  's=GoalStore(sys.argv[1],json.loads(sys.argv[2])); ' +
                  '\ntry: s.begin("publish","Publish","abc"); print("won")' +
                  '\nexcept GoalResumeBlocked: print("blocked")')
        children = [subprocess.Popen([sys.executable, '-c', script, str(self.directory), json.dumps(self.identity)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = []
        for process in children:
            out, err = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, err)
            results.append(out.strip())
        self.assertEqual(sorted(results), ['blocked', 'won'])
        self.assertEqual(self.reopen().snapshot()['sequence'], 1)

    def test_archive_failure_cannot_discard_last_unarchived_state(self):
        import hornet.desktop.goal_store as module
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        archive = self.directory / 'snapshots/000000000001.json'
        archive.unlink()
        original = module._atomic_write
        def fail_archive(path, data):
            if path == archive:
                raise OSError('archive temporarily unavailable')
            return original(path, data)
        with patch.object(module, '_atomic_write', fail_archive), self.assertRaises(OSError):
            self.store.complete('inspect', {'done': True})
        self.assertEqual(self.store.snapshot()['sequence'], 1)
        self.store.complete('inspect', {'done': True})
        self.assertTrue(archive.exists())

    def test_identity_values_do_not_coerce_bool_and_int(self):
        directory = Path(self.temp.name) / 'typed_identity'
        GoalStore(directory, {'version': 1})
        with self.assertRaises(GoalIdentityMismatch):
            GoalStore(directory, {'version': True})


    def test_long_project_operation_and_hierarchy_paths(self):
        from hornet.desktop.goal_store import _native_path
        directory = (Path(self.temp.name) / 'projects' / ('project_' + 'p' * 56) /
                     'operations' / ('understand_' + 'x' * 64))
        if os.name == 'nt':
            # tempfile's normal cleanup cannot traverse these deliberately long
            # paths when Windows long-path support is disabled.
            extended = '\\\\?\\' + str(directory.absolute())
            self.addCleanup(lambda: shutil.rmtree(extended) if os.path.exists(extended) else None)
        store = GoalStore(directory, self.identity)
        path = 'understand/files/file_' + 'f' * 40 + '/part_0000'
        store.begin(path, 'Describe source', 'abc', read_only=True)
        store.complete(path, {'source_sha256': 'abc'})
        self.assertEqual(GoalStore(directory, self.identity).snapshot()['nodes'][path]['status'], 'complete')
        self.assertTrue(_native_path(directory.joinpath('goals', *path.split('/'), '_goal.json')).is_file())


    def test_explicit_rebuild_restores_deleted_nested_mirror(self):
        self.store.begin('goal/step', 'Inspect', 'abc', read_only=True)
        target = self.directory / 'goals/goal/step/_goal.json'
        target.unlink()
        self.store.rebuild_mirrors()
        self.assertEqual(json.loads(target.read_text())['goal']['status'], 'running')

    def test_immutable_evidence_does_not_coerce_bool_to_int(self):
        self.store.begin('inspect', 'Inspect', 'abc', read_only=True)
        self.store.complete('inspect', {'checked': True})
        with self.assertRaises(GoalStoreError):
            self.store.complete('inspect', {'checked': 1})


    def test_stopped_read_only_attempt_reclaims_without_moving_pointer(self):
        before = self.store.begin('root/proposal', 'Propose', 'abc', read_only=True)['pointer']
        self.store.stop('failed', {'error': 'offline'})
        reopened = self.reopen()
        self.assertEqual(reopened.snapshot()['pointer']['path'], before['path'])
        self.assertEqual(reopened.snapshot()['pointer']['attempt_id'], before['attempt_id'])
        self.assertEqual(reopened.resume()['status'], 'resume')
        with self.assertRaises(GoalResumeBlocked):
            self.store.complete('root/proposal', {'late': True})
        with self.assertRaises(GoalResumeBlocked):
            reopened.begin('different', 'Different', 'abc', read_only=True)
        resumed = reopened.begin('root/proposal', 'Propose', 'abc', read_only=True)['pointer']
        self.assertNotEqual(resumed['attempt_id'], before['attempt_id'])
        reopened.complete('root/proposal', {'verified': True})

    def test_cancelled_effect_stays_blocked_until_reconciled(self):
        from hornet.desktop.cancellation import Cancellation, Cancelled, bind
        token = Cancellation()
        self.addCleanup(bind, None)
        self.store.begin('publish', 'Publish', 'abc')
        bind(token)
        token.cancel()
        self.store.stop('cancelled', {'error': 'stopped'})
        with self.assertRaises(Cancelled):
            from hornet.desktop.mission_checkpoint import write_json
            write_json(self.directory / 'late.json', {'late': True})
        bind(None)
        reopened = self.reopen()
        self.assertEqual(reopened.resume()['status'], 'blocked')
        with self.assertRaises(GoalResumeBlocked):
            reopened.begin('publish', 'Publish', 'abc')
        reopened.reconcile(self.identity, path='publish', outcome='not_applied', evidence={'checked': True})
        reopened.begin('publish', 'Publish', 'abc')
        reopened.complete('publish', {'revision': 'r2'})

    def test_stale_worker_cannot_stop_new_attempt(self):
        self.store.begin('proposal', 'Propose', 'abc', read_only=True)
        reopened = self.reopen()
        reopened.begin('proposal', 'Propose', 'abc', read_only=True)
        with self.assertRaises(GoalResumeBlocked):
            self.store.stop('failed', {'error': 'stale'})
        self.assertEqual(reopened.snapshot()['pointer']['status'], 'running')


if __name__ == '__main__':
    unittest.main()
