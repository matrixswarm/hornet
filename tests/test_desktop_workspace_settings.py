import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from hornet.desktop.workspace_settings import save_root, startup_root, validate_root
from hornet.desktop.workspace import Workspace
from hornet.desktop.app import App


class WorkspaceSettingsTests(unittest.TestCase):
    def test_persist_preserves_settings_and_old_projects_without_creating_new_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            old = Workspace(base / 'old')
            revision = old.create('demo')
            new = base / 'new'
            new.mkdir()
            settings = base / 'settings.json'
            settings.write_text('{"other": 123}', encoding='utf-8')
            save_root(new, settings)
            self.assertEqual(startup_root(path=settings), new.resolve())
            self.assertEqual(startup_root('explicit', settings), 'explicit')
            self.assertEqual(json.loads(settings.read_text())['other'], 123)
            self.assertEqual(old.revisions('demo'), [revision])
            self.assertEqual(list(new.iterdir()), [])
            reopened = Workspace(startup_root(path=settings))
            self.assertTrue(reopened.projects.is_dir())
            self.assertEqual(old.revisions('demo'), [revision])

    def test_invalid_missing_or_project_location_does_not_replace_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            settings = base / 'settings.json'
            save_root(base, settings)
            before = settings.read_bytes()
            project = base / 'project'
            (project / 'revisions').mkdir(parents=True)
            for invalid in (base / 'missing', settings, project):
                with self.assertRaises(ValueError):
                    save_root(invalid, settings)
                self.assertEqual(settings.read_bytes(), before)
            settings.write_text('{broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                save_root(base, settings)
            self.assertEqual(settings.read_text(), '{broken')

    def test_dialog_cancel_busy_and_save_do_not_switch_live_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ws = Workspace(base / 'old')
            new = base / 'new'
            new.mkdir()
            app = SimpleNamespace(busy=False, root=None, ws=ws, status=Mock(), guard=lambda action: action())
            with patch('hornet.desktop.app.filedialog.askdirectory', return_value=''), \
                 patch('hornet.desktop.workspace_settings.save_root') as save:
                App.set_projects_folder(app)
                save.assert_not_called()
            with patch('hornet.desktop.app.filedialog.askdirectory') as choose:
                app.busy = True
                App.set_projects_folder(app)
                choose.assert_not_called()
            app.busy = False
            with patch('hornet.desktop.app.filedialog.askdirectory', return_value=str(new)), \
                 patch('hornet.desktop.app.messagebox.askyesno', return_value=False), \
                 patch('hornet.desktop.workspace_settings.save_root') as save:
                App.set_projects_folder(app)
                save.assert_not_called()
            with patch('hornet.desktop.app.filedialog.askdirectory', return_value=str(new)), \
                 patch('hornet.desktop.app.messagebox.askyesno', return_value=True), \
                 patch('hornet.desktop.app.messagebox.showinfo'), \
                 patch('hornet.desktop.workspace_settings.save_root') as save:
                App.set_projects_folder(app)
                save.assert_called_once_with(new.resolve())
                self.assertIs(app.ws, ws)

    def test_open_folder_uses_current_root_without_shell_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            app = SimpleNamespace(ws=Workspace(directory), guard=lambda action: action())
            with patch('sys.platform', 'win32'), patch('os.startfile', create=True) as open_folder:
                App.open_projects_folder(app)
                open_folder.assert_called_once_with(str(app.ws.root))


if __name__ == '__main__':
    unittest.main()
