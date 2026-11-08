import json
import tkinter as tk
from unittest.mock import patch
from test_desktop_shell import DesktopShellTests
from hornet.desktop.automation import make_initialization_draft
from hornet.desktop.understanding import UnderstandingEditor, review_text


class UnderstandingTests(DesktopShellTests):
    def test_review_correct_and_accept_without_autoaccept(self):
        draft = make_initialization_draft(self.ws, 'demo', self.base,
            lambda p: {'summaries': {name: 'Existing purpose' for name in p['files']},
                       'blueprint': {'purpose': 'Example project', 'architecture': 'Python modules',
                                     'custom_rules': {'preserve': True}}})
        self.app.pending_initialization = draft
        self.app.show_initialization(draft)
        self.app.update_gate()
        self.assertEqual(self.app.revision, self.base)
        self.assertEqual(self.app.accept_map.cget('text'), 'Looks right, continue')
        content = self.app.views['Proposal'].get('1.0', 'end')
        self.assertIn('Example project', content)
        self.assertNotIn('"blueprint":', content)
        self.app.edit_project_map()
        dialog = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
        editor = next(w for w in dialog.winfo_children() if isinstance(w, UnderstandingEditor))
        editor.fields['purpose'].delete('1.0', 'end')
        editor.fields['purpose'].insert('1.0', 'Corrected purpose')
        next(w for w in dialog.winfo_children() if w.winfo_class() == 'TButton').invoke()
        self.assertEqual(self.app.pending_initialization['blueprint']['purpose'], 'Corrected purpose')
        self.assertEqual(self.app.pending_initialization['blueprint']['architecture'], 'Python modules')
        self.assertEqual(self.app.revision, self.base)
        self.app.accept_project_map()
        self.root.update()
        self.assertNotEqual(self.app.revision, self.base)
        self.assertIsNone(self.app.pending_initialization)
        blueprint = json.loads((self.ws.revision('demo', self.app.revision) / '.blueprint').read_text(encoding='utf-8'))
        self.assertEqual(blueprint['purpose'], 'Corrected purpose')

    def test_empty_summary_is_rejected_without_changing_saved_draft(self):
        draft = make_initialization_draft(self.ws, 'demo', self.base,
            lambda p: {'summaries': {name: 'Purpose' for name in p['files']}, 'blueprint': {'purpose': 'Test'}})
        self.app.pending_initialization = draft
        path = self.ws.project('demo') / 'initialization-draft.json'
        before = path.read_bytes()
        self.app.edit_project_map()
        dialog = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
        editor = next(w for w in dialog.winfo_children() if isinstance(w, UnderstandingEditor))
        editor.summary.delete('1.0', 'end')
        with patch('hornet.desktop.app.messagebox.showerror') as error:
            next(w for w in dialog.winfo_children() if w.winfo_class() == 'TButton').invoke()
            error.assert_called_once()
        self.assertEqual(path.read_bytes(), before)

    def test_simple_editor_preserves_structured_fields_and_file_switches(self):
        editor = UnderstandingEditor(self.root, {'purpose': 'Test', 'architecture': {'layers': ['one']},
                                                'extra': {'keep': True}}, {'a.py': 'A', 'b.py': 'B'})
        editor.summary.delete('1.0', 'end')
        editor.summary.insert('1.0', 'New A')
        editor.file.set('b.py')
        editor.select_file()
        value = editor.value()
        self.assertEqual(value['summaries'], {'a.py': 'New A', 'b.py': 'B'})
        self.assertEqual(value['blueprint']['architecture'], {'layers': ['one']})
        self.assertEqual(value['blueprint']['extra'], {'keep': True})

    def test_incomplete_review_is_clearly_marked(self):
        text = review_text({'blueprint': {'purpose': 'Example'}, 'included': ['a.py'],
                           'unreviewed': ['a.py'], 'summaries': {}, 'issues': {'a.py': 'Retry'}})
        self.assertIn('Not ready yet', text)
        self.assertIn('a.py', text)
