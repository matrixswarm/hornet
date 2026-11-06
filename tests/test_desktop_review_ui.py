"""Headless checks for Mission Control's review and action gating."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from hornet.desktop.app import App


class FakeButton:
    def __init__(self):
        self.state = None

    def configure(self, **values):
        self.state = values['state']


class ReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.app = App.__new__(App)
        self.app.ws = object()
        self.app.project = 'alpha'
        self.app.revision = 'candidate'
        self.app.busy = False
        self.app.pending_initialization = None
        self.app.pending_mission = {'project': 'alpha', 'state': 'review',
                                    'final_revision': 'candidate',
                                    'check_command': 'python3 -m unittest',
                                    'checks_passed': True}
        for name in ('diagnose_button', 'auto_button', 'run_button', 'copy_button',
                     'blueprint_button', 'delete_project_button', 'init_button',
                     'accept_map', 'edit_map', 'accept_mission_button'):
            setattr(self.app, name, FakeButton())

    @patch('hornet.desktop.app.require_ready', return_value={'status': 'ready'})
    def test_accept_requires_selected_project_and_candidate(self, _ready):
        self.app.update_gate()
        self.assertEqual(self.app.accept_mission_button.state, 'normal')
        self.app.revision = 'different-revision'
        self.app.update_gate()
        self.assertEqual(self.app.accept_mission_button.state, 'disabled')
        self.app.revision = 'candidate'
        self.app.project = 'other-project'
        self.app.update_gate()
        self.assertEqual(self.app.accept_mission_button.state, 'disabled')

    @patch('hornet.desktop.app.require_ready', return_value={'status': 'ready'})
    def test_busy_or_failed_checks_block_review_and_mutation(self, _ready):
        self.app.busy = True
        self.app.update_gate()
        for button in (self.app.accept_mission_button, self.app.auto_button,
                       self.app.copy_button, self.app.delete_project_button):
            self.assertEqual(button.state, 'disabled')
        self.app.busy = False
        self.app.pending_mission['checks_passed'] = False
        self.app.update_gate()
        self.assertEqual(self.app.accept_mission_button.state, 'disabled')
        self.assertEqual(self.app.auto_button.state, 'normal')

    def test_map_status_distinguishes_incomplete_and_ready_drafts(self):
        self.app.show = MagicMock()
        self.app.tabs = MagicMock()
        self.app.status = MagicMock()
        draft = {'version': 2, 'project': 'alpha', 'revision': 'candidate',
                 'blueprint': {}, 'summaries': {},
                 'unreviewed': ['main.py'], 'issues': {}}
        self.app.show_initialization(draft)
        self.assertIn('incomplete', self.app.status.set.call_args.args[0].lower())
        draft['unreviewed'] = []
        self.app.show_initialization(draft)
        self.assertIn('review', self.app.status.set.call_args.args[0].lower())
        self.assertIn('accept', self.app.status.set.call_args.args[0].lower())

    def test_busy_selection_restores_original_revision(self):
        self.app.busy = True
        self.app.projects = MagicMock()
        self.app.status = MagicMock()
        self.app.projects.selection.return_value = ('other-row',)
        self.app.projects.get_children.side_effect = lambda row: {
            '': ('alpha-project',), 'alpha-project': ('original-row',)}[row]
        self.app.projects.item.side_effect = lambda row, field: {
            'other-row': ('beta', 'revision-b'),
            'original-row': ('alpha', 'candidate')}[row]
        self.app.choose_revision()
        self.app.projects.selection_set.assert_called_once_with('original-row')
        self.assertEqual((self.app.project, self.app.revision), ('alpha', 'candidate'))
        self.assertIn('wait', self.app.status.set.call_args.args[0].lower())

    @patch('hornet.desktop.app.source_files', return_value=[])
    @patch('hornet.desktop.app.require_ready', return_value={'status': 'ready'})
    def test_changing_revision_clears_unrelated_review(self, _ready, _files):
        self.app.ws = MagicMock()
        self.app.ws.project.return_value.__truediv__.return_value.is_file.return_value = False
        self.app.ws.revision.return_value.__truediv__.return_value.read_text.return_value = '{}'
        self.app.projects = MagicMock()
        self.app.projects.selection.return_value = ('new-row',)
        self.app.projects.item.return_value = ('alpha', 'another')
        self.app.files = MagicMock()
        self.app.show = MagicMock()
        self.app.status = MagicMock()
        self.app.update_gate = MagicMock()
        self.app.choose_revision()
        self.assertEqual(self.app.revision, 'another')
        self.app.show.assert_any_call('Proposal', '')

    @patch('hornet.desktop.app.accept_mission')
    def test_direct_accept_rechecks_context_and_busy(self, accept):
        self.app.guard = lambda action: action()
        self.app.revision = 'another'
        with self.assertRaisesRegex(ValueError, 'candidate revision'):
            self.app.accept_automated_mission()
        accept.assert_not_called()
        self.app.revision = 'candidate'
        self.app.busy = True
        self.app.accept_automated_mission()
        accept.assert_not_called()

    @patch('hornet.desktop.app.accept_initialization')
    def test_map_actions_require_owning_revision(self, accept):
        self.app.guard = lambda action: action()
        self.app.pending_initialization = {'project': 'alpha', 'revision': 'base',
                                           'version': 2, 'issues': {}, 'unreviewed': []}
        with patch('hornet.desktop.app.require_ready', return_value={'status': 'ready'}):
            self.app.update_gate()
        self.assertEqual(self.app.accept_map.state, 'disabled')
        self.assertEqual(self.app.edit_map.state, 'disabled')
        with self.assertRaisesRegex(ValueError, 'revision used'):
            self.app.accept_project_map()
        accept.assert_not_called()

    @patch('hornet.desktop.app.accept_initialization')
    def test_changed_saved_map_cannot_be_accepted_as_reviewed(self, accept):
        self.app.guard = lambda action: action()
        self.app.pending_initialization = {'project': 'alpha', 'revision': 'candidate',
                                           'summaries': {'main.py': 'reviewed'}}
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / 'initialization-draft.json').write_text(json.dumps({
                'project': 'alpha', 'revision': 'candidate',
                'summaries': {'main.py': 'changed after review'}}), encoding='utf-8')
            self.app.ws = MagicMock()
            self.app.ws.project.return_value = directory
            with self.assertRaisesRegex(ValueError, 'changed after review'):
                self.app.accept_project_map()
        accept.assert_not_called()

    @patch('hornet.desktop.app.filedialog.askdirectory')
    def test_busy_new_project_and_review_are_noops(self, ask):
        self.app.busy = True
        self.app.new_project(importing=True)
        self.app.review_pending()
        self.app.revision_context_menu(MagicMock())
        ask.assert_not_called()


if __name__ == '__main__':
    unittest.main()
