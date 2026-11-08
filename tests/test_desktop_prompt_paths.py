import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from hornet.desktop.app import App
from hornet.desktop.workspace import Workspace
from hornet.desktop.prompt_paths import format_paths


class PromptPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.ws = Workspace(Path(self.tmp.name) / 'workspace')
        source = Path(self.tmp.name) / 'source'
        (source / 'src space').mkdir(parents=True)
        (source / 'src space' / 'main.py').write_text('original', encoding='utf-8')
        (source / 'readme.txt').write_text('notes', encoding='utf-8')
        self.revision = self.ws.create('demo', source=source)
        self.app = App(self.root, self.ws)
        self.app.refresh('demo', self.revision)
        self.root.update()

    def tearDown(self):
        for callback in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.tmp.cleanup()

    def row(self, name):
        return next(row for row, path in self.app._prompt_paths.items() if path == name)

    def test_files_folders_multiple_preserve_prompt_project_and_data(self):
        root = self.ws.revision('demo', self.revision)
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        selection = self.app.projects.selection()
        self.app.goal.insert('1.0', 'Only edit these:')
        self.app.goal.tag_add('sel', '1.0', 'end-1c')
        self.app.files.selection_set(self.row('src space'), self.row('readme.txt'))
        self.app.add_to_prompt_button.invoke()
        self.assertEqual(self.app.goal.get('1.0', 'end-1c'),
                         'Only edit these:\n"readme.txt", "src space/"')
        self.assertEqual(self.app.projects.selection(), selection)
        self.assertEqual((self.app.project, self.app.revision), ('demo', self.revision))
        self.assertEqual(self.app.selected_files(), ['readme.txt'])
        self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})
        self.app.goal.edit_undo()
        self.assertEqual(self.app.goal.get('1.0', 'end-1c'), 'Only edit these:')
        self.app.files.selection_set(self.row('src space/main.py'))
        self.app.add_files_to_prompt()
        self.assertIn('"src space/main.py"', self.app.goal.get('1.0', 'end-1c'))

    def test_context_menu_keeps_multi_selection_and_changes_unselected_target(self):
        tree = self.app.files
        rows = (self.row('src space'), self.row('readme.txt'))
        tree.selection_set(rows)
        with patch.object(tree, 'identify_row', return_value=rows[0]), patch('hornet.desktop.app.tk.Menu') as menu:
            self.app.files_context_menu(SimpleNamespace(y=1, x_root=10, y_root=20))
            self.assertEqual(set(tree.selection()), set(rows))
            menu.return_value.add_command.assert_called_once()
            menu.return_value.grab_release.assert_called_once()
        target = self.row('src space/main.py')
        with patch.object(tree, 'identify_row', return_value=target), patch('hornet.desktop.app.tk.Menu'):
            self.app.files_context_menu(SimpleNamespace(y=1, x_root=10, y_root=20))
            self.assertEqual(tree.selection(), (target,))
        tree.focus(target)
        with patch('hornet.desktop.app.tk.Menu') as menu:
            self.assertEqual(self.app.files_context_menu(SimpleNamespace(keysym='F10')), 'break')
            menu.return_value.tk_popup.assert_called_once()
        before = tree.selection()
        with patch.object(tree, 'identify_row', return_value=''), patch('hornet.desktop.app.tk.Menu') as menu:
            self.app.files_context_menu(SimpleNamespace(y=999, x_root=10, y_root=20))
            menu.assert_not_called()
        self.assertEqual(tree.selection(), before)
        self.assertTrue(tree.bind('<Shift-F10>'))
        self.assertFalse(tree.bind('<Control-Return>'))

    def test_empty_deleted_unsafe_and_stale_paths_do_not_change_prompt(self):
        self.app.goal.insert('1.0', 'keep')
        self.app.add_files_to_prompt()
        row = self.row('readme.txt')
        self.app.files.selection_set(row)
        self.app._prompt_paths[row] = '../outside.txt'
        self.app.add_files_to_prompt()
        self.assertEqual(self.app.goal.get('1.0', 'end-1c'), 'keep')
        self.app._prompt_paths[row] = 'missing.txt'
        self.app.add_files_to_prompt()
        self.assertEqual(self.app.goal.get('1.0', 'end-1c'), 'keep')
        self.app._prompt_paths_context = ('another', self.revision)
        self.app.add_files_to_prompt()
        self.assertEqual(self.app.goal.get('1.0', 'end-1c'), 'keep')

    def test_project_switch_rebuilds_path_inventory(self):
        other = self.ws.create('other')
        self.app.refresh('other', other)
        self.root.update()
        self.assertEqual(self.app._prompt_paths_context, ('other', other))
        self.assertNotIn('src space/main.py', self.app._prompt_paths.values())

    def test_format_rejects_absolute_windows_traversal_and_controls(self):
        root = self.ws.revision('demo', self.revision)
        for name in ('../x', '/x', 'C:/x', 'C:\\\\x', 'src space\\\\main.py', 'bad\\nname'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                format_paths(root, [name])
        self.assertEqual(format_paths(root, ['src space/main.py']),'"src space/main.py"')
        with patch('hornet.desktop.prompt_paths.safe_path') as safe:
            safe.return_value.exists.return_value = True
            safe.return_value.is_dir.return_value = False
            self.assertEqual(json.loads(format_paths(root, ['say "hi".py'])), 'say "hi".py')


if __name__ == '__main__':
    unittest.main()
