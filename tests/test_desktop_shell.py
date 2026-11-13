import hashlib
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from hornet.desktop.app import App
from hornet.desktop.workspace import Workspace, write_json
from hornet.desktop.working_revision import create_working_revision, is_working, load_working
from hornet.desktop.automation import make_initialization_draft, accept_initialization
from hornet.desktop.shell import toggle_panel, fill_project_menu, run_mission_shortcut


class DesktopShellTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)
        self.base = self.ws.create('demo')
        self.app = App(self.root, self.ws)
        self.app.refresh('demo', self.base)
        self.root.update()

    def tearDown(self):
        if hasattr(self, 'root'):
            for callback in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
                self.root.after_cancel(callback)
            self.root.destroy()
        if hasattr(self, 'tmp'):
            self.tmp.cleanup()

    def ready(self, working=True):
        make_initialization_draft(self.ws, 'demo', self.base,
            lambda p: {'summaries': {name: 'Purpose' for name in p['files']}, 'blueprint': {'purpose': 'Test'}})
        rev = accept_initialization(self.ws, 'demo')
        if working:
            rev = create_working_revision(self.ws, 'demo', rev)
        self.app.refresh('demo', rev)
        self.root.update()
        return rev

    def test_locked_revision_requires_explicit_working_copy(self):
        locked = self.ready(working=False)
        self.assertIsNone(load_working(self.ws, 'demo'))
        self.assertEqual(str(self.app.auto_button.cget('state')), 'disabled')
        self.assertIn('locked', self.app.workflow_hint.get())
        with patch('hornet.desktop.app.prepare_mission') as prepare, patch('hornet.desktop.app.messagebox.showerror') as error:
            self.app.run_mission()
            prepare.assert_not_called()
            error.assert_called_once()
        self.app.copy_revision()
        self.root.update()
        working = self.app.revision
        self.assertNotEqual(working, locked)
        self.assertTrue(is_working(self.ws, 'demo', working))
        self.assertEqual(str(self.app.auto_button.cget('state')), 'normal')
        self.app.busy = True
        self.app.copy_revision()
        self.app.lock_revision()
        self.assertTrue(is_working(self.ws, 'demo', working))
        self.app.busy = False
        self.app.lock_revision()
        self.root.update()
        self.assertFalse(is_working(self.ws, 'demo', working))
        self.assertEqual(str(self.app.auto_button.cget('state')), 'disabled')
        calls = []
        self.app.auto_button.configure(command=lambda: calls.append('run'))
        run_mission_shortcut(self.app)
        self.assertEqual(calls, [])
        fill_project_menu(self.app)
        labels = [self.app.project_menu.entrycget(i, 'label')
                  for i in range(self.app.project_menu.index('end') + 1)
                  if self.app.project_menu.type(i) == 'command']
        self.assertIn('Create working revision', labels)
        self.assertIn('Lock revision', labels)
        self.assertFalse(any('good' in label.lower() or 'bad' in label.lower() for label in labels))

    def test_beam_follows_busy_gate_and_stops_on_error(self):
        self.app.busy = True
        self.app.update_gate()
        self.assertTrue(self.app.activity_indicator.busy)
        timer = self.app.activity_indicator._timer
        self.app.update_gate()
        self.assertEqual(self.app.activity_indicator._timer, timer)
        self.app.events.put(('run_error', 'Synthetic failure'))
        with patch('hornet.desktop.app.messagebox.showerror'):
            self.app.poll()
        self.assertFalse(self.app.activity_indicator.busy)
        self.assertFalse(self.app.busy)

    def test_folder_actions_live_only_in_dropdown_and_work_without_project(self):
        self.app.project = self.app.revision = None
        self.app.update_gate()
        fill_project_menu(self.app)
        menu = self.app.project_menu
        labels = {menu.entrycget(i, 'label'): i for i in range(menu.index('end') + 1)
                  if menu.type(i) == 'command'}
        self.assertIn('Open Projects Folder', labels)
        self.assertIn('Set Projects Folder', labels)
        self.assertEqual(str(self.app.project_menu_button.cget('state')), 'normal')
        self.assertFalse(hasattr(self.app, 'set_folder_button'))
        self.app.busy = True
        self.app.update_gate()
        fill_project_menu(self.app)
        self.assertEqual(menu.entrycget(labels['Set Projects Folder'], 'state'), 'disabled')
        self.assertEqual(menu.entrycget(labels['Open Projects Folder'], 'state'), 'normal')

    def test_context_actions_and_repeat_settings_toggle(self):
        self.assertEqual(self.app.init_button.winfo_manager(), 'pack')
        self.assertFalse(self.app.connection_panel.winfo_manager())
        for _ in range(3):
            self.app.settings_button.invoke()
            self.assertEqual(self.app.connection_panel.winfo_manager(), 'pack')
            self.app.settings_button.invoke()
            self.assertFalse(self.app.connection_panel.winfo_manager())
        self.ready()
        self.assertFalse(self.app.init_button.winfo_manager())
        self.assertEqual(str(self.app.auto_button.cget('state')), 'normal')
        fill_project_menu(self.app)
        self.assertEqual(self.app.project_menu.entrycget(3, 'state'), 'normal')
        self.app.busy = True
        self.app.update_gate()
        fill_project_menu(self.app)
        self.assertEqual(self.app.project_menu.entrycget(3, 'state'), 'disabled')
        self.assertEqual(str(self.app.auto_button.cget('state')), 'disabled')

    def test_accept_requires_selected_candidate_and_busy_guard(self):
        ready = self.ready()
        candidate = create_working_revision(self.ws, 'demo', ready)
        self.app.pending_mission = {'project': 'demo', 'final_revision': candidate, 'state': 'review',
                                    'base_revision': ready, 'mission': 'Test change',
                                    'checks_passed': True, 'check_command': None}
        directory = self.ws.project('demo') / 'missions'
        directory.mkdir(exist_ok=True)
        write_json(directory / 'review.json', self.app.pending_mission)
        self.app.update_gate()
        self.assertEqual(str(self.app.accept_mission_button.cget('state')), 'disabled')
        with patch('hornet.desktop.app.accept_mission') as accept, patch('hornet.desktop.app.messagebox.showerror'):
            self.app.accept_automated_mission()
            accept.assert_not_called()
        self.app.refresh('demo', candidate)
        self.root.update()
        self.assertEqual(str(self.app.accept_mission_button.cget('state')), 'normal')
        self.assertEqual(self.app.accept_mission_button.winfo_manager(), 'pack')
        self.assertFalse(self.app.auto_button.winfo_manager())
        self.app.busy = True
        self.app.update_gate()
        with patch('hornet.desktop.app.accept_mission') as accept, patch('hornet.desktop.app.messagebox.showerror'):
            self.app.accept_automated_mission()
            accept.assert_not_called()
        self.assertEqual(str(self.app.accept_mission_button.cget('state')), 'disabled')

    def test_shortcut_does_not_replace_pending_review_or_run_while_busy(self):
        ready = self.ready()
        calls = []
        self.app.auto_button.configure(command=lambda: calls.append('run'))
        self.assertEqual(run_mission_shortcut(self.app), 'break')
        self.assertEqual(calls, ['run'])
        candidate = create_working_revision(self.ws, 'demo', ready)
        pending = {'project': 'demo', 'final_revision': candidate, 'state': 'review',
                   'base_revision': ready, 'mission': 'Test change',
                   'checks_passed': True, 'check_command': None}
        directory = self.ws.project('demo') / 'missions'
        directory.mkdir(exist_ok=True)
        write_json(directory / 'review.json', pending)
        self.app.pending_mission = pending
        self.app.refresh('demo', candidate)
        self.root.update()
        self.assertFalse(self.app.auto_button.winfo_manager())
        self.assertEqual(run_mission_shortcut(self.app), 'break')
        self.assertEqual(calls, ['run'])
        self.assertEqual(self.app.pending_mission, pending)
        self.app.pending_mission = None
        self.app.busy = True
        self.app.update_gate()
        run_mission_shortcut(self.app)
        self.assertEqual(calls, ['run'])
        self.app.busy = False
        self.app.update_gate()
        run_mission_shortcut(self.app)
        self.assertEqual(calls, ['run', 'run'])

    def test_preview_handles_missing_file_and_clears_folder_selection(self):
        self.ready()
        items = [i for i in self.app.files.get_children() if self.app.files.item(i, 'values')]
        item = items[-1]
        name = self.app.files.item(item, 'values')[0]
        self.app.files.selection_set(item)
        self.app.files.focus(item)
        self.app.preview()
        self.assertTrue(self.app.views['Source'].get('1.0', 'end').strip())
        (self.ws.revision('demo', self.app.revision)/name).unlink()
        self.app.preview()
        self.assertIn('Cannot preview', self.app.views['Source'].get('1.0', 'end'))
        self.app.files.selection_remove(*self.app.files.selection())
        self.app.preview()
        self.assertEqual(self.app.views['Source'].get('1.0', 'end').strip(), '')

    def test_initial_window_fits_small_screen(self):
        self.root.deiconify()
        self.root.update()
        self.assertLessEqual(self.root.winfo_width(), self.root.winfo_screenwidth())
        self.assertLessEqual(self.root.winfo_height(), self.root.winfo_screenheight())
        if self.root.winfo_screenwidth() == 1024 and self.root.winfo_screenheight() == 768:
            self.app.options_button.invoke()
            self.app.settings_button.invoke()
            self.root.update()
            for widget in (self.app.goal, self.app.auto_button, self.app.options_button, self.app.models):
                self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                     self.root.winfo_rootx() + self.root.winfo_width())
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                     self.root.winfo_rooty() + self.root.winfo_height())

    def test_calculator_example_is_opt_in_and_working(self):
        self.assertEqual(self.ws.list_projects(), ['demo'])
        menu = self.root.nametowidget(self.app.new_menu_button.cget('menu'))
        labels = [menu.entrycget(i, 'label') for i in range(menu.index('end') + 1)]
        self.assertIn('Calculator example...', labels)
        with patch('hornet.desktop.app.simpledialog.askstring', return_value='calculator'):
            self.app.new_calculator_example()
        self.root.update()
        root = self.ws.revision('calculator', self.app.revision)
        namespace = {}
        exec(compile((root / 'calculator.py').read_text(encoding='utf-8'), 'calculator.py', 'exec'), namespace)
        self.assertEqual(namespace['add'](2, 3), 5)
        self.assertTrue((root / 'test_calculator.py').is_file())
        self.assertEqual(self.ws.list_projects(), ['calculator', 'demo'])

    def test_small_window_keeps_mission_controls_inside_root(self):
        self.ready()
        self.root.geometry('1040x720')
        self.root.deiconify()
        self.root.update()
        self.app.options_button.invoke()
        self.app.settings_button.invoke()
        self.root.update()
        for widget in (self.app.goal, self.app.auto_button, self.app.options_button, self.app.models,
                       self.app.diagnose_button, self.app.review_button, self.app.prompt_resize_handle):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), self.root.winfo_rooty()+self.root.winfo_height())
            self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), self.root.winfo_rootx()+self.root.winfo_width())
        for widget in (self.app.auto_button, self.app.options_button, self.app.diagnose_button,
                       self.app.review_button, self.app.prompt_resize_handle):
            self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
        # Opening options must not hide or clip its own controls either.
        for row in self.app.options_panel.winfo_children():
            for widget in row.winfo_children():
                self.assertTrue(widget.winfo_ismapped())
                self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), self.root.winfo_rooty()+self.root.winfo_height())
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), self.root.winfo_rootx()+self.root.winfo_width())


if __name__ == '__main__':
    unittest.main()
