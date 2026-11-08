import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hornet.desktop.automation import make_initialization_draft, accept_initialization
from hornet.desktop.workspace import Workspace
from hornet.desktop.mission_resume import prepare_mission
from hornet.desktop.mission_checkpoint import read_json
from hornet.desktop.plan_counter import counter_mission_plan, feedback_path, save_feedback


class PlanCounterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        source = root / 'source'; source.mkdir()
        (source / 'main.py').write_bytes(b'VALUE = 1\n')
        self.ws = Workspace(root / 'workspace')
        base = self.ws.create('demo', source=source)
        make_initialization_draft(self.ws, 'demo', base, lambda p: {
            'summaries': {n: 'Value module' for n in p['files']}, 'blueprint': {'purpose': 'Test'}})
        self.base = accept_initialization(self.ws, 'demo')
        self.plan = {'existing_files': ['main.py'], 'new_files': [], 'explanation': 'Small change', 'goals': [
            {'id': 'value', 'parent': None, 'goal': 'Set VALUE to 2', 'files': ['main.py'],
             'success_criteria': 'main.py contains VALUE = 2', 'depends_on': []}]}
        with patch('hornet.desktop.automation.request_json', return_value=self.plan):
            self.record = prepare_mission(self.ws, 'demo', self.base, 'Update the value',
                'http://localhost', 'model', require_plan_confirmation=True, require_goal_decomposition=True)
        self.path = self.ws.project('demo') / 'missions' / (self.record['id'] + '.json')
        self.before = self.ws.revisions('demo')

    def test_counter_is_plan_only_and_requires_new_approval(self):
        revised = copy.deepcopy(self.plan)
        revised['goals'][0]['goal'] = 'Set VALUE to 3'
        with patch('hornet.desktop.automation.request_json', return_value=revised) as request, \
             patch('hornet.desktop.registry_mission.propose_with_tools', side_effect=AssertionError('Must not execute')):
            result = counter_mission_plan(self.ws, self.record, 'Use 3 instead of 2')
        self.assertEqual(result['state'], 'plan_review')
        self.assertNotEqual(result['id'], self.record['id'])
        self.assertNotIn('plan_approval', result)
        self.assertEqual(result['proposed_goals'][0]['goal'], 'Set VALUE to 3')
        self.assertIn('Use 3 instead of 2', request.call_args.args[3]['mission'])
        self.assertIn('No execution is approved', request.call_args.args[3]['mission'])
        self.assertIn('Previous unapproved plan', request.call_args.args[3]['mission'])
        self.assertEqual(read_json(self.path), self.record)
        self.assertEqual(self.ws.revisions('demo'), self.before)
        self.assertEqual((self.ws.revision('demo', self.base) / 'main.py').read_bytes(), b'VALUE = 1\n')

    def test_failed_revision_preserves_plan_feedback_and_allows_retry(self):
        with patch('hornet.desktop.automation.request_json', side_effect=RuntimeError('Offline')):
            with self.assertRaisesRegex(RuntimeError, 'Offline'):
                counter_mission_plan(self.ws, self.record, 'Use 3 instead')
        self.assertEqual(read_json(self.path), self.record)
        self.assertEqual(read_json(feedback_path(self.ws, self.record))['feedback'], 'Use 3 instead')
        self.assertEqual(self.ws.revisions('demo'), self.before)
        with patch('hornet.desktop.automation.request_json', return_value=self.plan):
            self.assertEqual(counter_mission_plan(self.ws, self.record, 'Use 3 instead')['state'], 'plan_review')

    def test_stale_or_approved_review_and_empty_feedback_are_rejected(self):
        from hornet.desktop.mission_plan import approve_mission_plan
        with self.assertRaises(ValueError):
            save_feedback(self.ws, self.record, ' ')
        approve_mission_plan(self.ws, 'demo', self.record['id'])
        with patch('hornet.desktop.automation.request_json') as request:
            with self.assertRaises(ValueError):
                counter_mission_plan(self.ws, self.record, 'Change it')
            request.assert_not_called()

    def test_approved_counter_executes_with_feedback_not_planning_instructions(self):
        from hornet.desktop.mission_plan import approve_mission_plan
        from hornet.desktop.mission_resume import resume_mission
        with patch('hornet.desktop.automation.request_json', return_value=self.plan):
            first = counter_mission_plan(self.ws, self.record, 'Keep the API unchanged')
            result = counter_mission_plan(self.ws, first, 'Use 3 instead of 2')
        self.assertEqual(result['mission'], 'Update the value')
        self.assertEqual(result['counter_context']['feedback'],
                         ['Keep the API unchanged', 'Use 3 instead of 2'])
        with patch('hornet.desktop.registry_mission.propose_with_tools') as proposal:
            pending = resume_mission(self.ws, 'demo', result['id'], 'http://localhost', 'model')
            self.assertEqual(pending['state'], 'plan_review')
            proposal.assert_not_called()
        approve_mission_plan(self.ws, 'demo', result['id'])
        with patch('hornet.desktop.registry_mission.propose_with_tools', side_effect=RuntimeError('Offline')):
            with self.assertRaisesRegex(RuntimeError, 'Offline'):
                resume_mission(self.ws, 'demo', result['id'], 'http://localhost', 'model')
        captured = []
        def propose(root, payload, *args, **kwargs):
            captured.append(payload['mission'])
            return ({'summary': 'Value updated', 'files': {'main.py': 'VALUE = 3\n'},
                     'goal_links': {'main.py': ['value']}}, [])
        with patch('hornet.desktop.registry_mission.propose_with_tools', side_effect=propose), \
             patch('hornet.desktop.automation.model_describer', return_value=lambda payload: {
                 'summaries': {name: 'Updated value' for name in payload['files']}}):
            complete = resume_mission(self.ws, 'demo', result['id'], 'http://localhost', 'model')
        self.assertEqual(complete['state'], 'review')
        self.assertEqual(len(captured), 1)
        self.assertIn('Keep the API unchanged', captured[0])
        self.assertIn('Use 3 instead of 2', captured[0])
        self.assertIn('Implement the approved plan', captured[0])
        self.assertNotIn('Return a revised plan', captured[0])
        self.assertNotIn('No execution is approved', captured[0])
        self.assertNotIn('Previous unapproved plan', captured[0])
        from hornet.desktop.automation import require_ready
        self.assertEqual(require_ready(self.ws, 'demo')['accepted_revision'], self.base)

    def test_counter_context_cannot_disable_confirmation(self):
        with patch('hornet.desktop.registry_mission.propose_with_tools') as proposal:
            with self.assertRaisesRegex(ValueError, 'require explicit plan confirmation'):
                prepare_mission(self.ws, 'demo', self.base, 'Update the value',
                    'http://localhost', 'model', counter_context={
                        'feedback': ['Use 3 instead'], 'previous_plan': self.plan})
            proposal.assert_not_called()

    def test_counter_context_tampering_invalidates_approval_and_resume(self):
        from hornet.desktop.mission_checkpoint import write_json
        from hornet.desktop.mission_plan import approve_mission_plan
        from hornet.desktop.mission_resume import resume_mission
        with patch('hornet.desktop.automation.request_json', return_value=self.plan):
            record = counter_mission_plan(self.ws, self.record, 'Use 3 instead')
        path = self.ws.project('demo') / 'missions' / (record['id'] + '.json')
        record = read_json(path)
        record['counter_context']['feedback'][0] = 'Use 99 instead'
        write_json(path, record)
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            approve_mission_plan(self.ws, 'demo', record['id'])
        with patch('hornet.desktop.registry_mission.propose_with_tools') as proposal:
            with self.assertRaisesRegex(ValueError, 'inputs or accepted revision changed'):
                resume_mission(self.ws, 'demo', record['id'], 'http://localhost', 'model')
            proposal.assert_not_called()

    def test_legacy_counter_refuses_resume_without_rewriting_saved_evidence(self):
        from hornet.desktop.mission_checkpoint import write_json
        from hornet.desktop.mission_plan import approve_mission_plan
        from hornet.desktop.mission_resume import resume_mission
        from hornet.desktop.mission_task_context import LEGACY_SUFFIX
        legacy_mission = 'Update the value\n\n' + LEGACY_SUFFIX
        with patch('hornet.desktop.automation.request_json', return_value=self.plan):
            legacy = prepare_mission(self.ws, 'demo', self.base, legacy_mission,
                'http://localhost', 'model', require_plan_confirmation=True,
                require_goal_decomposition=True)
        path = self.ws.project('demo') / 'missions' / (legacy['id'] + '.json')
        legacy['counter_plan_of'] = self.record['id']
        write_json(path, legacy)
        approve_mission_plan(self.ws, 'demo', legacy['id'])
        directory = path.with_suffix('')
        before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
        record_before = path.read_bytes()
        with patch('hornet.desktop.registry_mission.propose_with_tools') as proposal:
            with self.assertRaisesRegex(ValueError, 'Start a new mission with the original goal'):
                resume_mission(self.ws, 'demo', legacy['id'], 'http://localhost', 'model')
            proposal.assert_not_called()
        self.assertEqual(path.read_bytes(), record_before)
        self.assertEqual({p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}, before)
        self.assertEqual(self.ws.revisions('demo'), self.before)

    def test_ui_failure_retry_cancel_and_revised_review(self):
        import tkinter as tk
        from hornet.desktop.app import App
        root = tk.Tk(); root.withdraw()
        try:
            from hornet.desktop.working_revision import create_working_revision
            working = create_working_revision(self.ws, 'demo', self.base)
            self.before = self.ws.revisions('demo')
            app = App(root, self.ws)
            app.refresh('demo', working)
            app.show_mission_plan(self.record); app.update_gate(); root.update()
            self.assertEqual(app.counter_plan_button.winfo_manager(), 'pack')
            def dialog_parts():
                dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
                text = next(w for w in dialog.winfo_children() if isinstance(w, tk.Text))
                submit = next(w for w in dialog.winfo_children() if w.winfo_class() == 'TButton' and w.cget('text') == 'Request revised plan')
                return dialog, text, submit
            app.counter_plan_button.invoke()
            dialog, text, submit = dialog_parts()
            dialog.destroy()
            self.assertEqual(app.pending_mission, self.record)
            app.counter_plan_button.invoke()
            dialog, text, submit = dialog_parts()
            text.insert('1.0', 'Use 3 instead')
            with patch('hornet.desktop.automation.request_json', side_effect=RuntimeError('Offline')), \
                 patch.object(app, 'launch_query', side_effect=lambda worker: worker()), \
                 patch('hornet.desktop.app.messagebox.showerror'):
                submit.invoke()
                for _ in range(50): app.poll()
            self.assertEqual(app.pending_mission, self.record)
            self.assertFalse(app.busy)
            app.pending_mission = None
            app.review_pending()
            self.assertEqual(app.pending_mission, self.record)
            app.counter_plan_button.invoke()
            dialog, text, submit = dialog_parts()
            self.assertEqual(text.get('1.0', 'end').strip(), 'Use 3 instead')
            with patch('hornet.desktop.automation.request_json', return_value=self.plan), \
                 patch.object(app, 'launch_query', side_effect=lambda worker: worker()):
                submit.invoke()
                for _ in range(50): app.poll()
            self.assertEqual(app.pending_mission['state'], 'plan_review')
            self.assertNotEqual(app.pending_mission['id'], self.record['id'])
            self.assertEqual(app.approve_plan_button.winfo_manager(), 'pack')
            self.assertEqual(app.counter_plan_button.winfo_manager(), 'pack')
            self.assertEqual(self.ws.revisions('demo'), self.before)
        finally:
            for callback in root.tk.splitlist(root.tk.call('after', 'info')): root.after_cancel(callback)
            root.destroy()
