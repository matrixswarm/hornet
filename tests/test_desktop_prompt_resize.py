"""Real Tk checks for mission prompt resizing and layout safety."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest

from hornet.desktop.app import App
from hornet.desktop.workspace import Workspace


class PromptResizeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest('Tk display unavailable: ' + str(exc))
        self.addCleanup(self.destroy_root)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = App(self.root, Workspace(Path(self.directory.name)))
        self.root.geometry('1380x900-2000-2000')
        self.root.update()

    def destroy_root(self):
        for callback in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(callback)
        self.root.destroy()

    def test_drag_preserves_text_selection_and_undo(self):
        app = self.app
        draft = '\n'.join('Mission line ' + str(n) for n in range(60))
        app.goal.insert('1.0', draft)
        app.goal.edit_separator()
        app.goal.tag_add('sel', '2.0', '3.0')
        app.goal.mark_set('insert', '4.0')
        before = app.goal.winfo_height()
        h = app.prompt_resize_handle
        h.event_generate('<ButtonPress-1>', rooty=500)
        h.event_generate('<B1-Motion>', rooty=380)
        h.event_generate('<ButtonRelease-1>', rooty=380)
        self.root.update()
        self.assertGreater(app.goal.winfo_height(), before)
        self.assertEqual(app.goal.get('1.0', 'end-1c'), draft)
        self.assertEqual(tuple(map(str, app.goal.tag_ranges('sel'))), ('2.0', '3.0'))
        self.assertEqual(app.goal.index('insert'), '4.0')
        app.goal.yview_moveto(1)
        self.root.update()
        self.assertGreater(app.goal.yview()[0], 0)
        app.goal.edit_undo()
        self.assertEqual(app.goal.get('1.0', 'end-1c'), '')

    def test_keyboard_minimum_and_shrinking_window_keep_controls_visible(self):
        app = self.app
        app.prompt_resize_handle.focus_force()
        app.prompt_resize_handle.event_generate('<Up>')
        self.root.update()
        self.assertEqual(int(app.goal.cget('height')), 4)
        app.prompt_resize.resize(1000)
        self.root.update()
        self.root.geometry('1040x720-2000-2000')
        app.settings_button.invoke()
        app.options_button.invoke()
        self.root.update()
        self.assertGreaterEqual(app.prompt_resize.editor.winfo_height(), 80)
        for widget in (app.goal, app.goal_scrollbar, app.auto_button,
                       app.diagnose_button, app.status_label, app.models):
            self.assertTrue(widget.winfo_viewable())
            self.assertGreater(widget.winfo_height(), 1)
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                 self.root.winfo_rooty() + self.root.winfo_height())
        app.prompt_resize_handle.event_generate('<Home>')
        self.root.update()
        app.prompt_resize_handle.event_generate('<Down>')
        self.root.update()
        self.assertEqual(int(app.goal.cget('height')), 3)


if __name__ == '__main__':
    unittest.main()
