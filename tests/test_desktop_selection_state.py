"""Real-widget project switching, using only a temporary workspace."""
import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from hornet.desktop.app import App
from hornet.desktop.workspace import Workspace, write_json


class SelectionStateTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)
        self.a = self.ws.create('alpha')
        self.b = self.ws.create('beta')
        self.app = App(self.root, self.ws)
        self.select('alpha', self.a)

    def tearDown(self):
        for callback in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.tmp.cleanup()

    def select(self, project, revision):
        self.app.refresh(project, revision)
        self.root.update()

    def record(self, name, **extra):
        value = {'id': name, 'project': 'alpha', 'base_revision': self.a,
                 'state': 'plan_review', 'mission': 'Alpha plan', 'proposed_goals': []}
        value.update(extra)
        path = self.ws.project('alpha') / 'missions' / (name + '.json')
        path.parent.mkdir(exist_ok=True)
        write_json(path, value)
        return value

    def text(self, key):
        return self.app.views[key].get('1.0', 'end-1c')

    def test_round_trip_restores_plan_prompt_activity_tab_and_file(self):
        value = self.record('plan')
        self.app.show_mission_plan(value)
        self.app.goal.insert('1.0', 'Alpha draft')
        self.app.activity('Alpha progress')
        rows = self.app.files.get_children()
        if rows:
            self.app.files.selection_set(rows[0])
            self.app.preview()
        selected = self.app.selected_files()
        self.app.tabs.select(3)
        self.select('beta', self.b)
        self.assertEqual(self.text('Proposal'), '')
        self.assertEqual(self.text('Activity'), '')
        self.assertEqual(self.app.goal.get('1.0', 'end-1c'), '')
        self.assertIsNone(self.app.pending_mission)
        self.app.goal.insert('1.0', 'Beta draft')
        self.select('alpha', self.a)
        self.assertIn('Alpha plan', self.text('Proposal'))
        self.assertEqual(self.app.pending_mission['id'], 'plan')
        self.assertIn('Alpha progress', self.text('Activity'))
        self.assertEqual(self.app.goal.get('1.0', 'end-1c'), 'Alpha draft')
        self.assertEqual(self.app.selected_files(), selected)
        self.assertEqual(self.app.tabs.index(self.app.tabs.select()), 3)
        self.select('beta', self.b)
        self.assertEqual(self.app.goal.get('1.0', 'end-1c'), 'Beta draft')

    def test_reloads_latest_record_and_never_revives_stale_plan(self):
        value = self.record('plan')
        self.app.show_mission_plan(value)
        self.select('beta', self.b)
        self.record('plan', state='failed', mission='New failed execution')
        self.select('alpha', self.a)
        self.assertIn('New failed execution', self.text('Proposal'))
        self.assertNotIn('REVIEW AGENT PLAN', self.text('Proposal'))
        self.assertIsNone(self.app.pending_mission)

    def test_cold_selection_ignores_other_revisions_and_corrupt_records(self):
        self.record('other', base_revision='other-revision')
        (self.ws.project('alpha') / 'missions' / 'bad.json').write_text('{', encoding='utf-8')
        self.select('beta', self.b)
        self.select('alpha', self.a)
        self.assertEqual(self.text('Proposal'), '')
        self.assertIsNone(self.app.pending_mission)
        self.record('own')
        self.app._selection_views = {}
        self.select('alpha', self.a)
        self.assertIn('Alpha plan', self.text('Proposal'))

    def test_run_output_is_revision_scoped_and_reloaded_from_disk(self):
        directory = self.ws.project('alpha') / 'runs'
        directory.mkdir()
        write_json(directory / 'run.json', {'project': 'alpha', 'revision': self.a,
                   'stdout': 'alpha output', 'stderr': '', 'command': ['python', 'main.py'], 'exit_code': 0})
        self.select('alpha', self.a)
        self.assertIn('alpha output', self.text('Run output'))
        self.select('beta', self.b)
        self.assertEqual(self.text('Run output'), '')
        self.select('alpha', self.a)
        self.assertIn('alpha output', self.text('Run output'))

    def test_completed_review_and_accepted_history_return_without_wrong_actions(self):
        value = self.record('finished', state='review', final_revision=self.a,
                            checks_passed=False, changed_files=[])
        self.select('alpha', self.a)
        self.assertIn('MISSION REVIEW', self.text('Proposal'))
        self.select('beta', self.b)
        self.assertEqual(str(self.app.accept_mission_button.cget('state')), 'disabled')
        self.select('alpha', self.a)
        self.assertEqual(self.app.pending_mission['id'], 'finished')
        self.record('finished', state='accepted', final_revision=self.a,
                    checks_passed=False, changed_files=[])
        self.select('alpha', self.a)
        self.assertIn('MISSION REVIEW', self.text('Proposal'))
        self.assertEqual(str(self.app.accept_mission_button.cget('state')), 'disabled')
        self.assertIn('Previously accepted', self.app.status.get())

    def test_project_heading_redirect_keeps_previous_snapshot_identity(self):
        self.app.goal.insert('1.0', 'Alpha draft')
        for row in self.app.projects.get_children():
            if self.app.projects.item(row, 'values') == ('beta',):
                self.app.projects.selection_set(row)
                break
        self.root.update()
        self.assertEqual(self.app.project, 'beta')
        self.assertEqual(self.app.goal.get('1.0', 'end-1c'), '')
        self.select('alpha', self.a)
        self.assertEqual(self.app.goal.get('1.0', 'end-1c'), 'Alpha draft')


if __name__ == '__main__':
    unittest.main()
