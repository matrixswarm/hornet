"""Exercise key controls at the desktop's advertised minimum window size."""
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from hornet.desktop.app import App
from hornet.desktop.workspace import Workspace


class DesktopWindowLayoutTests(unittest.TestCase):
    def test_minimum_window_keeps_primary_controls_accessible(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest('Tk display unavailable: ' + str(exc))
        try:
            with tempfile.TemporaryDirectory() as directory:
                app = App(root, Workspace(Path(directory)))
                root.geometry('1000x650-2000-2000')
                root.update()
                for name in ('projects', 'files', 'goal', 'auto_button', 'diagnose_button',
                             'accept_mission_button', 'run_button', 'models', 'find_models',
                             'blueprint_button', 'status_label', 'activity_indicator'):
                    widget = getattr(app, name)
                    with self.subTest(control=name):
                        self.assertTrue(widget.winfo_viewable(), name + ' is not visible')
                        self.assertGreater(widget.winfo_width(), 1)
                        self.assertGreater(widget.winfo_height(), 1)
                self.assertGreaterEqual(app.projects.winfo_height(), 50,
                                        'Project tree must show more than one row')
        finally:
            root.destroy()

    def test_closing_busy_work_requires_confirmation(self):
        app = App.__new__(App)
        app.busy = True
        from unittest.mock import Mock
        app.root = Mock()
        with patch('hornet.desktop.app.messagebox.askyesno', return_value=False):
            app.close()
        app.root.destroy.assert_not_called()
        with patch('hornet.desktop.app.messagebox.askyesno', return_value=True):
            app.close()
        app.root.destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
