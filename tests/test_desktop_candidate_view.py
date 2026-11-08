"""Working revision and cleanup regressions use disposable projects only."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock

from hornet.desktop import automation as api
from hornet.desktop.app import App
from hornet.desktop.candidate_view import cleanup_plan, cleanup_checkpoints
from hornet.desktop.goal_context import publication_tag
from hornet.desktop.mission_lease import mission_lease
from hornet.desktop.revision_display import rename_revision
from hornet.desktop.workspace import Workspace, write_json
from hornet.desktop.working_revision import (visible_revisions, create_working_revision,
    lock_working_revision, require_working, is_working, load_working, display_revision_id)


class CandidateViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(self.tmp.name)
        self.initial = self.ws.create('demo')
        api.make_initialization_draft(self.ws, 'demo', self.initial,
            lambda p: {'summaries': {n: 'Test' for n in p['files']}})
        self.base = api.accept_initialization(self.ws, 'demo')
        self.anchor = create_working_revision(self.ws, 'demo', self.base)
        self.id = 'a' * 32
        self.directory = self.ws.project('demo') / 'missions'
        self.directory.mkdir(exist_ok=True)
        self.record = {'id': self.id, 'project': 'demo', 'base_revision': self.anchor,
                       'working_revision_id': self.anchor,
                       'state': 'review', 'resumable': False, 'candidates': []}
        parent = self.anchor
        for n in range(3):
            with publication_tag(self.id, 'root/goals/leaf' + str(n) + '/publish', str(n)):
                parent = self.ws.fork('demo', parent, {'main.py': 'print(' + str(n) + ')\n'})
            self.record['candidates'].append(parent)
        self.final = parent
        self.record['final_revision'] = parent
        self.save()
        state = load_working(self.ws, 'demo')
        state['sessions'][self.anchor].update(head=self.final, missions=[self.id])
        write_json(self.ws.project('demo') / 'working-revisions.json', state)
        from hornet.desktop.working_revision import _sync_markers
        _sync_markers(self.ws, 'demo', state)

    def save(self):
        write_json(self.directory / (self.id + '.json'), self.record)

    def lock(self):
        lock_working_revision(self.ws, 'demo', self.final)

    def test_only_one_working_revision_visible_but_snapshots_remain(self):
        self.assertEqual(set(visible_revisions(self.ws, 'demo')), {self.initial, self.base, self.final})
        self.assertEqual(len(self.ws.revisions('demo')), 6)
        self.assertEqual(visible_revisions(self.ws, 'demo', True), self.ws.revisions('demo'))
        self.assertEqual(display_revision_id(self.ws, 'demo', self.final), self.anchor)

    def test_lock_and_explicit_create_preserve_default_and_source(self):
        original = (self.ws.revision('demo', self.final) / 'main.py').read_bytes()
        self.lock()
        self.assertFalse(is_working(self.ws, 'demo', self.final))
        with self.assertRaisesRegex(ValueError, 'locked'):
            api.prepare_mission(self.ws, 'demo', self.final, 'Change', 'http://localhost', 'model')
        fresh = create_working_revision(self.ws, 'demo', self.final)
        self.assertTrue(is_working(self.ws, 'demo', fresh))
        self.assertFalse(is_working(self.ws, 'demo', self.final))
        self.assertEqual((self.ws.revision('demo', self.final) / 'main.py').read_bytes(), original)
        self.assertEqual(api.read_state(self.ws, 'demo')['accepted_revision'], self.base)

    def test_creating_another_revision_locks_previous_without_deletion(self):
        before = set(self.ws.revisions('demo'))
        fresh = create_working_revision(self.ws, 'demo', self.base)
        self.assertTrue(is_working(self.ws, 'demo', fresh))
        self.assertFalse(is_working(self.ws, 'demo', self.final))
        self.assertEqual(set(self.ws.revisions('demo')), before | {fresh})
        self.assertIn(self.final, visible_revisions(self.ws, 'demo'))

    def test_corrupt_grouping_fails_open(self):
        (self.directory / (self.id + '.json')).write_text('{broken')
        self.assertEqual(visible_revisions(self.ws, 'demo'), self.ws.revisions('demo'))

    def test_malformed_goal_object_fails_open(self):
        root = self.ws.revision('demo', self.record['candidates'][0])
        write_json(root / '.hornet-meta' / 'goals.json', [])
        self.assertEqual(visible_revisions(self.ws, 'demo'), self.ws.revisions('demo'))

    def test_lock_marker_preserves_hash_evidence_and_blocks_mission(self):
        from hornet.desktop.revision_goals import read_revision_goals
        root = self.ws.revision('demo', self.final)
        before = api.inventory(root)
        self.assertFalse((root / '.LOCK').exists())
        self.lock()
        self.assertTrue((root / '.LOCK').is_file())
        self.assertEqual(api.inventory(root), before)
        read_revision_goals(root)
        fresh = create_working_revision(self.ws, 'demo', self.final)
        fresh_root = self.ws.revision('demo', fresh)
        self.assertFalse((fresh_root / '.LOCK').exists())
        (fresh_root / '.LOCK').write_text('manual lock')
        with self.assertRaisesRegex(ValueError, 'LOCK'):
            api.prepare_mission(self.ws, 'demo', fresh, 'Change', 'http://localhost', 'model')
        self.assertTrue((fresh_root / '.LOCK').is_file())

    def test_cleanup_requires_lock_and_moves_only_internal_copies(self):
        with self.assertRaisesRegex(ValueError, 'Lock'):
            cleanup_plan(self.ws, 'demo', self.final)
        self.lock()
        plan = cleanup_plan(self.ws, 'demo', self.final)
        expected = {self.anchor, *self.record['candidates'][:-1]}
        self.assertEqual(set(plan['revisions']), expected)
        self.assertEqual(cleanup_checkpoints(self.ws, 'demo', self.final, plan), 3)
        for r in plan['revisions']:
            self.assertTrue((self.ws.project('demo') / 'trash' / r / 'main.py').is_file())
        self.assertTrue(self.ws.revision('demo', self.base).is_dir())
        manifest = json.loads(next((self.ws.project('demo') / 'checkpoint-cleanups').glob('*.json')).read_text())
        self.assertEqual(manifest['state'], 'complete')
        self.assertEqual(set(manifest['moved']), expected)
        self.assertEqual(cleanup_plan(self.ws, 'demo', self.final)['revisions'], [])
        self.assertTrue(is_working(self.ws, 'demo', create_working_revision(self.ws, 'demo', self.final)))

    def test_named_and_other_mission_bases_are_preserved(self):
        first, second = self.record['candidates'][:2]
        rename_revision(self.ws, 'demo', first, 'Keep this')
        other = dict(self.record, id='b' * 32, base_revision=second, candidates=[])
        other.pop('final_revision'); other['state'] = 'diagnosis_complete'
        write_json(self.directory / ('b' * 32 + '.json'), other)
        self.lock()
        plan = cleanup_plan(self.ws, 'demo', self.final)
        self.assertNotIn(first, plan['revisions']); self.assertNotIn(second, plan['revisions'])
        self.assertIn(first, visible_revisions(self.ws, 'demo'))
        self.assertIn(second, visible_revisions(self.ws, 'demo'))

    def test_manual_branch_parent_is_preserved(self):
        first = self.record['candidates'][0]
        self.ws.fork('demo', first)
        self.lock()
        self.assertNotIn(first, cleanup_plan(self.ws, 'demo', self.final)['revisions'])
        self.assertIn(first, visible_revisions(self.ws, 'demo'))

    def test_unfinished_mission_prevents_cleanup_and_switch(self):
        self.lock()
        other = dict(self.record, id='b' * 32, state='failed', resumable=True)
        other.pop('final_revision')
        write_json(self.directory / ('b' * 32 + '.json'), other)
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            cleanup_plan(self.ws, 'demo', self.final)
        with self.assertRaisesRegex(ValueError, 'Unfinished'):
            create_working_revision(self.ws, 'demo', self.final)

    def test_stale_preview_and_mutation_are_rejected(self):
        self.lock()
        plan = cleanup_plan(self.ws, 'demo', self.final)
        rename_revision(self.ws, 'demo', self.record['candidates'][0], 'Keep')
        with self.assertRaisesRegex(ValueError, 'changed'):
            cleanup_checkpoints(self.ws, 'demo', self.final, plan)
        (self.ws.revision('demo', self.final) / 'main.py').write_text('print(999)')
        with self.assertRaises(ValueError):
            cleanup_plan(self.ws, 'demo', self.final)

    def test_project_processing_lease_blocks_switch_lock_and_understanding(self):
        with mission_lease(self.ws.project('demo') / '.candidate-operation'):
            for action in (lambda: self.lock(), lambda: create_working_revision(self.ws, 'demo', self.final),
                           lambda: api.make_initialization_draft(self.ws, 'demo', self.final, lambda p: {})):
                with self.assertRaisesRegex(ValueError, 'still running'):
                    action()

    def test_initialization_draft_reference_is_preserved(self):
        first = self.record['candidates'][0]
        write_json(self.ws.project('demo') / 'initialization-draft.json', {'revision': first})
        self.lock()
        self.assertNotIn(first, cleanup_plan(self.ws, 'demo', self.final)['revisions'])

    def test_ui_cleanup_cancel_does_not_move_files(self):
        self.lock()
        app = App.__new__(App); app.ws = self.ws; app.busy = False
        app.root = MagicMock(); app.guard = lambda fn: fn()
        app.status = MagicMock(); app.refresh = MagicMock()
        before = self.ws.revisions('demo')
        with patch('hornet.desktop.app.messagebox.askyesno', return_value=False):
            app.clean_checkpoints('demo', self.final)
        self.assertEqual(self.ws.revisions('demo'), before)
        app.refresh.assert_not_called()


# Reuse the existing isolated model/check fixture, not its test methods.
import test_desktop_mission_resume as resume_fixture


class WorkingMissionTests(unittest.TestCase):
    enter_patch = resume_fixture.MissionResumeTests.enter_patch
    describe = staticmethod(resume_fixture.MissionResumeTests.describe)
    prepare = resume_fixture.MissionResumeTests.prepare
    saved = resume_fixture.MissionResumeTests.saved
    resume = resume_fixture.MissionResumeTests.resume

    def setUp(self):
        resume_fixture.MissionResumeTests.setUp(self)
        self.original = self.base
        self.base = create_working_revision(self.ws, 'demo', self.base)
        self.anchor = self.base

    def test_completed_missions_advance_same_working_identity(self):
        first = self.prepare()
        self.assertTrue(is_working(self.ws, 'demo', first['final_revision']))
        self.assertFalse(is_working(self.ws, 'demo', self.base))
        self.base = first['final_revision']
        self.propose.return_value = ({'summary': 'Again', 'files': {'main.py': 'print(43)\n'}}, [])
        second = self.prepare()
        state = load_working(self.ws, 'demo')
        self.assertEqual(state['current'], self.anchor)
        self.assertEqual(len(state['sessions'][self.anchor]['missions']), 2)
        shown = visible_revisions(self.ws, 'demo')
        self.assertIn(second['final_revision'], shown)
        self.assertNotIn(first['final_revision'], shown)
        self.assertNotIn(self.anchor, shown)
        self.assertEqual(display_revision_id(self.ws, 'demo', second['final_revision']), self.anchor)
        lock_working_revision(self.ws, 'demo', second['final_revision'])
        plan = cleanup_plan(self.ws, 'demo', second['final_revision'])
        self.assertIn(first['final_revision'], plan['revisions'])

    def test_failed_mission_blocks_new_request_but_resumes_same_working_revision(self):
        self.propose.side_effect = ValueError('offline')
        with self.assertRaisesRegex(ValueError, 'offline'):
            self.prepare()
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            self.prepare()
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            lock_working_revision(self.ws, 'demo', self.base)
        self.propose.side_effect = None
        result = self.resume()
        self.assertTrue(is_working(self.ws, 'demo', result['final_revision']))
        self.assertEqual(self.plan.call_count, 1)

    def test_crash_publication_recovers_without_duplicate_under_working_guard(self):
        fork = self.ws.fork
        published = []
        def interrupted(*args, **kwargs):
            revision = fork(*args, **kwargs)
            published.append(revision)
            raise RuntimeError('lost response')
        with patch.object(self.ws, 'fork', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'lost response'):
                self.prepare()
        result = self.resume()
        self.assertEqual(result['candidates'][0], published[0])
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(len(self.ws.revisions('demo')), 5)
        self.assertTrue(is_working(self.ws, 'demo', result['final_revision']))

    def test_record_before_binding_crash_can_resume(self):
        with patch('hornet.desktop.working_revision.bind_mission', side_effect=OSError('binding crash')):
            with self.assertRaisesRegex(OSError, 'binding crash'):
                self.prepare()
        self.assertIsNone(load_working(self.ws, 'demo')['sessions'][self.anchor]['pending'])
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            lock_working_revision(self.ws, 'demo', self.base)
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            self.prepare()
        result = self.resume()
        self.assertTrue(is_working(self.ws, 'demo', result['final_revision']))

    def test_final_marker_failure_does_not_rewrite_terminal_review(self):
        import hornet.desktop.working_revision as working
        sync = working._sync_markers
        def fail_after_complete(ws, project, state):
            session = state['sessions'][state['current']]
            if session['head'] != self.base and session.get('pending') is None:
                raise OSError('marker crash')
            return sync(ws, project, state)
        with patch.object(working, '_sync_markers', side_effect=fail_after_complete):
            result = self.prepare()
        self.assertEqual(self.saved()['state'], 'review')
        self.assertFalse(self.saved()['resumable'])
        working.finish_mission(self.ws, 'demo')
        self.assertTrue(is_working(self.ws, 'demo', result['final_revision']))

    def test_ordinary_trash_preserves_working_and_recovery_inputs(self):
        self.propose.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        with self.assertRaisesRegex(ValueError, 'required'):
            self.ws.trash('demo', self.base)

    def test_counter_plan_stays_in_same_working_revision(self):
        from hornet.desktop.plan_counter import counter_mission_plan
        self.plan.return_value = {'existing_files': ['main.py'], 'new_files': [],
            'explanation': 'Change', 'goals': [{'id': 'change', 'parent': None,
            'goal': 'Change value', 'files': ['main.py'], 'success_criteria': 'main.py contains the changed value', 'depends_on': []}]}
        first = self.prepare(require_plan_confirmation=True, require_goal_decomposition=True)
        second = counter_mission_plan(self.ws, first, 'Use the smaller change')
        state = load_working(self.ws, 'demo')
        self.assertEqual(state['current'], self.anchor)
        self.assertEqual(state['sessions'][self.anchor]['pending'], second['id'])
        self.assertEqual(state['sessions'][self.anchor]['superseded'], [first['id']])
        self.assertEqual(second['state'], 'plan_review')

    def test_counter_record_before_binding_recovers_replacement(self):
        from hornet.desktop.plan_counter import counter_mission_plan
        from hornet.desktop.mission_resume import resume_mission
        self.plan.return_value = {'existing_files': ['main.py'], 'new_files': [],
            'explanation': 'Small change', 'goals': [{'id': 'change', 'parent': None,
            'goal': 'Change the value', 'files': ['main.py'],
            'success_criteria': 'main.py contains the changed value', 'depends_on': []}]}
        first = self.prepare(require_plan_confirmation=True, require_goal_decomposition=True)
        with patch('hornet.desktop.working_revision.bind_mission', side_effect=OSError('binding crash')):
            with self.assertRaisesRegex(OSError, 'binding crash'):
                counter_mission_plan(self.ws, first, 'Use the smaller change')
        records = [json.loads(p.read_text()) for p in (self.ws.project('demo') / 'missions').glob('*.json')]
        orphan = next(r for r in records if r['id'] != first['id'])
        resumed = resume_mission(self.ws, 'demo', orphan['id'], 'http://localhost', 'test-model')
        self.assertEqual(resumed['state'], 'plan_review')
        self.assertEqual(load_working(self.ws, 'demo')['sessions'][self.anchor]['pending'], orphan['id'])

    def test_partial_publication_remains_working_after_later_failure(self):
        from hornet.desktop.working_revision import resume_base
        before = api.inventory(self.ws.revision('demo', self.base))
        self.memory.side_effect = ValueError('memory offline')
        with self.assertRaisesRegex(ValueError, 'memory offline'):
            self.prepare()
        record = self.saved(); partial = record['candidates'][-1]
        self.assertTrue(is_working(self.ws, 'demo', partial))
        self.assertFalse(is_working(self.ws, 'demo', self.base))
        self.assertEqual(resume_base(self.ws, 'demo', partial), self.base)
        self.assertEqual(api.inventory(self.ws.revision('demo', self.base)), before)
        self.assertEqual((self.ws.revision('demo', partial) / 'main.py').read_text(), 'print(42)\n')
        self.memory.side_effect = None
        result = self.resume()
        self.assertTrue(is_working(self.ws, 'demo', result['final_revision']))
        self.assertEqual(self.propose.call_count, 1)

    def test_old_stale_head_recovers_partial_without_replaying_model(self):
        from hornet.desktop.working_revision import refresh_working_revision
        self.memory.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        partial = self.saved()['candidates'][-1]
        state = load_working(self.ws, 'demo')
        state['sessions'][self.anchor]['head'] = self.base
        state['sessions'][self.anchor].pop('pending_base', None)
        write_json(self.ws.project('demo') / 'working-revisions.json', state)
        calls = self.propose.call_count
        self.assertEqual(refresh_working_revision(self.ws, 'demo'), partial)
        self.assertEqual(self.propose.call_count, calls)
        self.assertTrue(is_working(self.ws, 'demo', partial))

    def test_explicit_keep_partial_allows_fresh_repair_without_rewriting_old_record(self):
        from hornet.desktop.working_revision import abandon_working_revision, is_abandoned
        from hornet.desktop.mission_resume import resume_mission
        self.memory.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        record = self.saved(); partial = record['candidates'][-1]
        old_path = self.ws.project('demo') / 'missions' / (record['id'] + '.json')
        evidence = old_path.read_bytes()
        abandon_working_revision(self.ws, 'demo', partial)
        self.assertEqual(old_path.read_bytes(), evidence)
        self.assertTrue(is_abandoned(self.ws, 'demo', record['id']))
        self.assertTrue(is_working(self.ws, 'demo', partial))
        self.memory.side_effect = None; self.base = partial
        self.propose.return_value = ({'summary': 'Repair', 'files': {'main.py': 'print(43)\n'}}, [])
        result = self.prepare()
        self.assertTrue(is_working(self.ws, 'demo', result['final_revision']))
        with self.assertRaisesRegex(ValueError, 'explicitly closed'):
            resume_mission(self.ws, 'demo', record['id'], 'http://localhost', 'test-model')

    def test_discard_preserves_files_but_allows_new_working_copy(self):
        from hornet.desktop.working_revision import abandon_working_revision
        self.memory.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        partial = self.saved()['candidates'][-1]
        before = set(self.ws.revisions('demo'))
        abandon_working_revision(self.ws, 'demo', partial, discard=True)
        self.assertIsNone(load_working(self.ws, 'demo')['current'])
        self.assertEqual(set(self.ws.revisions('demo')), before)
        self.assertNotIn(partial, visible_revisions(self.ws, 'demo'))
        fresh = create_working_revision(self.ws, 'demo', self.original)
        self.assertTrue(is_working(self.ws, 'demo', fresh))
        self.assertTrue((self.ws.revision('demo', partial) / '.LOCK').exists())

    def test_closed_plan_cannot_be_approved_or_countered_from_stale_window(self):
        from hornet.desktop.working_revision import abandon_working_revision
        from hornet.desktop.mission_plan import approve_mission_plan
        from hornet.desktop.plan_counter import counter_mission_plan
        self.plan.return_value = {'existing_files': ['main.py'], 'new_files': [],
            'explanation': 'Small change', 'goals': [{'id': 'change', 'parent': None,
            'goal': 'Change the value', 'files': ['main.py'],
            'success_criteria': 'main.py contains the changed value', 'depends_on': []}]}
        record = self.prepare(require_plan_confirmation=True, require_goal_decomposition=True)
        path = self.ws.project('demo') / 'missions' / (record['id'] + '.json')
        before = path.read_bytes()
        abandon_working_revision(self.ws, 'demo', self.base)
        with self.assertRaisesRegex(ValueError, 'explicitly closed'):
            approve_mission_plan(self.ws, 'demo', record['id'])
        with self.assertRaisesRegex(ValueError, 'explicitly closed'):
            counter_mission_plan(self.ws, record, 'Use another approach')
        self.assertEqual(path.read_bytes(), before)

    def test_closed_failure_does_not_block_cleanup_but_its_artifacts_are_kept(self):
        from hornet.desktop.working_revision import abandon_working_revision
        self.memory.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        record = self.saved(); partial = record['candidates'][-1]
        abandon_working_revision(self.ws, 'demo', partial, discard=True)
        plan = cleanup_plan(self.ws, 'demo', partial)
        self.assertNotIn(record['base_revision'], plan['revisions'])
        self.assertTrue(set(record['candidates']).isdisjoint(plan['revisions']))

    def test_tampered_partial_is_not_promoted_on_reopen(self):
        from hornet.desktop.working_revision import refresh_working_revision
        self.memory.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        partial = self.saved()['candidates'][-1]
        state = load_working(self.ws, 'demo')
        state['sessions'][self.anchor]['head'] = self.base
        write_json(self.ws.project('demo') / 'working-revisions.json', state)
        (self.ws.revision('demo', partial) / 'main.py').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'changed'):
            refresh_working_revision(self.ws, 'demo')
        self.assertEqual(load_working(self.ws, 'demo')['sessions'][self.anchor]['head'], self.base)

    def test_completed_pointer_recovery_is_verified(self):
        result = self.prepare()
        state = load_working(self.ws, 'demo')
        state['sessions'][self.anchor].update(head=self.base, pending=result['id'])
        write_json(self.ws.project('demo') / 'working-revisions.json', state)
        from hornet.desktop.working_revision import finish_mission
        finish_mission(self.ws, 'demo')
        self.assertTrue(is_working(self.ws, 'demo', result['final_revision']))


class LegacyWorkingRecoveryTests(unittest.TestCase):
    enter_patch = resume_fixture.MissionResumeTests.enter_patch
    describe = staticmethod(resume_fixture.MissionResumeTests.describe)
    prepare = resume_fixture.MissionResumeTests.prepare
    saved = resume_fixture.MissionResumeTests.saved
    resume = resume_fixture.MissionResumeTests.resume
    setUp = resume_fixture.MissionResumeTests.setUp

    def test_explicit_copy_adopts_unique_legacy_recovery_without_changing_identity(self):
        from hornet.desktop.working_revision import resume_base
        self.propose.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        old = self.saved()
        original = self.base
        fresh = create_working_revision(self.ws, 'demo', original)
        self.assertEqual(self.saved(), old)
        self.assertEqual(resume_base(self.ws, 'demo', fresh), original)
        self.assertTrue((self.ws.revision('demo', original) / '.LOCK').is_file())
        self.propose.side_effect = None
        result = self.resume()
        self.assertEqual(result['identity'], old['identity'])
        self.assertTrue(is_working(self.ws, 'demo', result['final_revision']))
        self.assertEqual(load_working(self.ws, 'demo')['current'], fresh)


if __name__ == '__main__':
    unittest.main()
