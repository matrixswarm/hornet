"""Partial-work choices use temporary workspaces and mocked model calls only."""
import unittest
from unittest.mock import Mock, patch
from hornet.desktop.app import App
from test_desktop_selection_state import SelectionStateTests

class PartialChoicesTests(unittest.TestCase):
    def app(self):
        app = App.__new__(App)
        app.project, app.revision, app.busy = 'alpha', 'head', False
        app.ws = Mock()
        app.root = Mock()
        app.guard = lambda action: action()
        for name in ('goal', 'endpoint', 'model', 'check_command', 'include_run', 'confirm_plan'):
            setattr(app, name, Mock())
        app.goal.get.return_value = 'New goal'
        app.model.get.return_value = 'model'
        app.include_run.get.return_value = False
        for name in ('save_model', 'begin_query', 'update_gate', 'activity', 'launch_query', 'refresh'):
            setattr(app, name, Mock())
        app.activity_indicator = Mock()
        app.status = Mock()
        return app

    def test_run_requires_explicit_keep_partial_confirmation(self):
        app = self.app()
        with patch('hornet.desktop.app.require_ready'), patch('hornet.desktop.working_revision.require_working'), patch('hornet.desktop.working_revision.pending_mission_record', return_value={'id': 'old'}), patch('hornet.desktop.working_revision.abandon_working_revision') as abandon, patch('hornet.desktop.app.messagebox.askyesno', return_value=False) as ask:
            app.run_mission()
            abandon.assert_not_called()
            app.launch_query.assert_not_called()
            self.assertIn('no longer resume automatically', ask.call_args.args[1])
            ask.return_value = True
            app.run_mission()
            abandon.assert_called_once_with(app.ws, 'alpha', 'head')
            app.launch_query.assert_called_once()

    def test_busy_blocks_run_resume_and_discard_without_prompt(self):
        app = self.app()
        app.busy = True
        with patch('hornet.desktop.app.messagebox.askyesno') as ask:
            app.run_mission()
            app.resume_saved_mission()
            app.discard_working_revision()
            ask.assert_not_called()
            app.launch_query.assert_not_called()

class PartialSelectionTests(SelectionStateTests):
    def test_refresh_skips_reconciliation_while_busy(self):
        with patch('hornet.desktop.working_revision.refresh_working_revision') as reconcile:
            self.app.busy = True
            self.app.refresh('alpha', self.a)
            reconcile.assert_not_called()
            self.app.busy = False

    def test_abandoned_plan_never_restores_approval(self):
        self.record('old')
        with patch('hornet.desktop.working_revision.is_abandoned', return_value=True):
            self.select('alpha', self.a)
            self.assertEqual(self.text('Proposal'), '')
            self.assertIsNone(self.app.pending_mission)

    def test_head_advance_keeps_prompt_and_selects_partial_source(self):
        from hornet.desktop.working_revision import create_working_revision, load_working
        from hornet.desktop.workspace import write_json
        from hornet.desktop import automation as api
        api.make_initialization_draft(self.ws, 'alpha', self.a,
            lambda value: {'summaries': {name: 'Test' for name in value['files']}})
        base = api.accept_initialization(self.ws, 'alpha')
        working = create_working_revision(self.ws, 'alpha', base)
        self.select('alpha', working)
        self.app.goal.insert('1.0', 'Keep my original prompt')
        # Simulate a controller publication while the UI still displays its old head.
        partial = self.ws.fork('alpha', working)
        (self.ws.revision('alpha', partial) / 'partial.txt').write_text('partial work', encoding='utf-8')
        state = load_working(self.ws, 'alpha')
        state['sessions'][state['current']]['head'] = partial
        write_json(self.ws.project('alpha') / 'working-revisions.json', state)
        self.select('alpha', working)
        self.assertEqual(self.app.revision, partial)
        self.assertEqual(self.app.goal.get('1.0', 'end-1c'), 'Keep my original prompt')
        self.assertTrue((self.ws.revision('alpha', self.app.revision) / 'partial.txt').is_file())
