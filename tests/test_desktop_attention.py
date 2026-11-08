import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from hornet.desktop.app import App
from hornet.desktop.workspace import Workspace, write_json
from hornet.desktop.attention import details, current, review_text


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(self.tmp.name)
        self.revision = self.ws.create('alpha')
        self.record = {'id': 'stopped', 'project': 'alpha', 'base_revision': self.revision,
                       'mission': 'Edit file', 'state': 'failed', 'resumable': True,
                       'identity': {'project': 'alpha', 'operation_id': 'stopped'}, 'error': 'Model stopped'}
        self.directory = self.ws.project('alpha') / 'missions' / 'stopped'
        self.directory.mkdir(parents=True)
        self.state = {'identity': self.record['identity'], 'pointer': 'root/edit', 'nodes': {
            'root/edit': {'label': 'Edit main.py', 'status': 'failed', 'read_only': True,
                          'reconciliation': {'evidence': {'error': 'Invalid Python'}}}}}
        write_json(self.directory / 'state.json', self.state)

    def test_read_only_failed_and_cancelled_offer_resume_with_reason(self):
        for status in ('failed', 'cancelled'):
            self.state['nodes']['root/edit']['status'] = status
            write_json(self.directory / 'state.json', self.state)
            info = details(self.ws, 'alpha', self.record)
            self.assertTrue(info['can_resume'])
            text = review_text(self.ws, 'alpha', self.record)
            self.assertIn('Needs attention', text)
            self.assertIn('What do you want to do here?', text)
            self.assertIn('Invalid Python', text)
            self.assertIn('Edit main.py', text)

    def test_effect_missing_or_changed_identity_never_offers_blind_resume(self):
        self.state['nodes']['root/edit']['read_only'] = False
        write_json(self.directory / 'state.json', self.state)
        self.assertFalse(details(self.ws, 'alpha', self.record)['can_resume'])
        self.state['nodes']['root/edit']['read_only'] = True
        self.state['identity'] = {'other': 'identity'}
        write_json(self.directory / 'state.json', self.state)
        self.assertFalse(details(self.ws, 'alpha', self.record)['can_resume'])
        self.record['checkpoint_directory'] = '../outside'
        self.assertFalse(details(self.ws, 'alpha', self.record)['can_resume'])

    def test_actions_recheck_context_and_do_not_automatically_close_or_run(self):
        app = App.__new__(App)
        app.goal, app.status = Mock(), Mock()
        app.resume_saved_mission, app.discard_working_revision = Mock(), Mock()
        with patch('hornet.desktop.attention.current', return_value=None):
            for action in ('resume', 'new', 'discard'):
                app.attention_action(action)
        app.goal.focus_set.assert_not_called()
        app.resume_saved_mission.assert_not_called()
        app.discard_working_revision.assert_not_called()
        with patch('hornet.desktop.attention.current', return_value={'can_resume': False}):
            app.attention_action('resume')
            app.resume_saved_mission.assert_not_called()
            app.attention_action('new')
            app.goal.focus_set.assert_called_once()
            app.attention_action('discard')
            app.discard_working_revision.assert_called_once()
        with patch('hornet.desktop.attention.current', return_value={'can_resume': True, 'record': {'id': 'shown'}}):
            app.attention_action('resume')
            app.resume_saved_mission.assert_called_once_with(expected_id='shown')

    def test_resume_binds_displayed_mission_not_newer_same_base(self):
        missions = self.ws.project('alpha') / 'missions'
        write_json(missions / 'stopped.json', self.record)
        newer = dict(self.record, id='newer', mission='Different mission')
        write_json(missions / 'newer.json', newer)
        app = App.__new__(App)
        app.busy, app.project, app.revision, app.ws = False, 'alpha', self.revision, self.ws
        app.root, app.endpoint, app.model = Mock(), Mock(), Mock()
        app.model.get.return_value = 'model'
        app.endpoint.get.return_value = 'endpoint'
        app.guard = lambda action: action()
        app.selected_mission_base = lambda: self.revision
        app.begin_query, app.update_gate, app.activity, app.launch_query = Mock(), Mock(), Mock(), Mock()
        with patch('hornet.desktop.working_revision.require_working'), \
             patch('hornet.desktop.working_revision.is_abandoned', return_value=False), \
             patch('hornet.desktop.app.messagebox.askyesno', return_value=True) as confirm, \
             patch('hornet.desktop.mission_resume.resume_mission', return_value={'state': 'review'}) as resume:
            app.events = Mock()
            app.resume_saved_mission(expected_id='stopped')
            self.assertIn('Edit file', confirm.call_args.args[1])
            app.launch_query.call_args.args[0]()
            self.assertEqual(resume.call_args.args[2], 'stopped')
            with self.assertRaisesRegex(ValueError, 'No resumable'):
                app.resume_saved_mission(expected_id='missing')
            self.assertEqual(app.launch_query.call_count, 1)

    def test_panel_is_scoped_and_disables_uncertain_resume(self):
        root = tk.Tk(); root.withdraw()
        try:
            app = App(root, self.ws)
            app.refresh('alpha', self.revision); root.update()
            with patch('hornet.desktop.working_revision.is_working', return_value=True), \
                 patch('hornet.desktop.working_revision.pending_mission_record', return_value=self.record):
                app.update_gate()
                self.assertIn('Needs attention', app.workflow_hint.get())
                self.assertEqual(app.attention_buttons['resume'].winfo_manager(), 'pack')
                self.assertEqual(str(app.attention_buttons['resume'].cget('state')), 'normal')
                self.state['nodes']['root/edit']['read_only'] = False
                write_json(self.directory / 'state.json', self.state)
                app.update_gate()
                self.assertEqual(str(app.attention_buttons['resume'].cget('state')), 'disabled')
                app.project = 'beta'
                app.update_gate()
                self.assertTrue(all(not b.winfo_manager() for b in app.attention_buttons.values()))
                app.project = 'alpha'; app.busy = True
                app.update_gate()
                self.assertTrue(all(not b.winfo_manager() for b in app.attention_buttons.values()))
        finally:
            for callback in root.tk.splitlist(root.tk.call('after', 'info')):
                root.after_cancel(callback)
            root.destroy()


if __name__ == '__main__':
    unittest.main()
