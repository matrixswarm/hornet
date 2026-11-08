import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from hornet.desktop.file_picker import FilePicker
from hornet.desktop.workspace import Workspace, source_files


class FilePickerTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.names = ['src/main.py', 'src/nested/a.py', 'src/nested/b.py',
                      'src2/keep.py', 'README.md', 'backups/old.py', 'credentials.json']
        self.picker = FilePicker(self.root, self.names)

    def tearDown(self):
        self.root.destroy()

    def row(self, path):
        return self.picker.rows[path]

    def state(self, path):
        return self.picker.tree.item(self.row(path), 'values')[0]

    def test_nested_folder_exclusion_and_individual_override(self):
        self.picker.set_rows(False, [self.row('src')])
        self.assertEqual(self.picker.selected_files(), ['README.md', 'src2/keep.py'])
        self.assertEqual(self.state('src/nested'), 'No')
        self.picker.set_rows(True, [self.row('src/nested/a.py')])
        self.assertEqual(self.state('src'), 'Partial')
        self.assertEqual(self.state('src/nested'), 'Partial')
        self.assertEqual(self.state('src/nested/b.py'), 'No')
        self.picker.toggle([self.row('src')])
        self.assertEqual(self.state('src'), 'Yes')

    def test_mixed_and_overlapping_selection_keyboard(self):
        self.picker.tree.selection_set(self.row('src'), self.row('src/nested'), self.row('README.md'))
        self.assertEqual(self.picker.space(), 'break')
        self.assertEqual(self.picker.selected_files(), ['src2/keep.py'])
        self.picker.space()
        self.assertEqual(self.state('src'), 'Yes')
        self.assertEqual(self.state('README.md'), 'Yes')

    def test_defaults_and_resume_do_not_select_sensitive_files(self):
        self.assertEqual(self.state('backups'), 'No')
        self.assertEqual(self.state('credentials.json'), 'No')
        self.picker.toggle([])
        self.assertEqual(self.state('credentials.json'), 'No')
        restored = FilePicker(self.root, self.names, ['src/main.py', '../outside.py'])
        self.assertEqual(restored.selected_files(), ['src/main.py'])
        empty = FilePicker(self.root, self.names, [])
        self.assertEqual(empty.selected_files(), [])

    def test_click_inclusion_cell_toggles_without_collapsing_folder(self):
        from types import SimpleNamespace
        self.root.deiconify()
        self.root.geometry('850x650')
        self.picker.pack(fill='both', expand=True)
        self.root.update()
        row = self.row('src')
        self.picker.tree.see(row)
        self.root.update()
        x, y, width, height = self.picker.tree.bbox(row, 'included')
        was_open = self.picker.tree.item(row, 'open')
        self.assertEqual(self.picker.click(SimpleNamespace(x=x + width // 2, y=y + height // 2)), 'break')
        self.assertEqual(self.state('src'), 'No')
        self.assertEqual(self.picker.tree.item(row, 'open'), was_open)

    def test_import_copies_only_expanded_file_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            for name in self.names:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name, encoding='utf-8')
            before = {name: (source / name).read_bytes() for name in self.names}
            self.picker.set_rows(False, [self.row('src/nested'), self.row('README.md')])
            workspace = Workspace(Path(temp) / 'workspace')
            revision = workspace.create('demo', source=source, include_files=self.picker.selected_files())
            self.assertEqual(set(source_files(workspace.revision('demo', revision))),
                             {'src/main.py', 'src2/keep.py'})
            self.assertEqual(before, {name: (source / name).read_bytes() for name in self.names})


if __name__ == '__main__':
    unittest.main()
