import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hornet.distil import app
from hornet.distil.qt import QApplication, QMessageBox


class SaveRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.window = app.DistilWindow(self.root/'storage')
        self.project = self.window.store.create_project('Picnic')
        self.revision = self.window.store.create_revision(self.project['id'], 'Revision 1')
        self.window.store.add_message(self.revision['id'], 'user', 'Old archived text')
        self.window.store.replace_with_distilled(self.revision['id'], 'Saturday, six people.\nLocation open.')
        self.window.store.add_message(self.revision['id'], 'user', 'New question with café 🐝\nSecond line')
        self.window.store.add_message(self.revision['id'], 'assistant', 'A current answer.')
        self.window._load_projects(self.project['id'], self.revision['id'])
        self.window.input_text.setPlainText('Unsent private draft')
        self.window.show()
        self.qt.processEvents()

    def tearDown(self):
        self.window.close()
        self.qt.processEvents()
        self.tmp.cleanup()

    def test_all_three_formats_save_without_changing_revision(self):
        before = self.window.store.get_revision(self.revision['id'])
        history = self.window.store.history(self.revision['id'])
        for filter_name, extension in [('PDF (*.pdf)', '.pdf'), ('Text (*.txt)', '.txt'), ('Markdown (*.md)', '.md')]:
            with patch.object(app.QFileDialog, 'getSaveFileName', return_value=(str(self.root/'saved'), filter_name)):
                self.window.save_revision()
            path = self.root/('saved'+extension)
            self.assertTrue(path.exists(), self.window.status_label.text())
            if extension == '.pdf':
                self.assertTrue(path.read_bytes().startswith(b'%PDF'))
            else:
                text = path.read_text(encoding='utf-8')
                self.assertIn('Saturday, six people.', text)
                self.assertIn('New question with café 🐝', text)
                self.assertNotIn('Old archived text', text)
                self.assertNotIn('Unsent private draft', text)
            self.assertEqual(self.window.store.get_revision(self.revision['id']), before)
            self.assertEqual(self.window.store.history(self.revision['id']), history)

    def test_cancel_and_decline_overwrite_leave_existing_file(self):
        target=self.root/'existing.md';target.write_text('Existing important export')
        with patch.object(app.QFileDialog, 'getSaveFileName', return_value=('', '')):
            self.window.save_revision()
        self.assertEqual(target.read_text(encoding='utf-8'), 'Existing important export')
        with patch.object(app.QFileDialog, 'getSaveFileName', return_value=(str(target.with_suffix('.txt')), 'Markdown (*.md)')), patch.object(app.QMessageBox, 'question', return_value=QMessageBox.StandardButton.No) as question:
            self.window.save_revision()
            self.assertIn(str(target), question.call_args.args[2])
        self.assertEqual(target.read_text(encoding='utf-8'), 'Existing important export')
        with patch.object(app.QFileDialog, 'getSaveFileName', return_value=(str(target), 'Markdown (*.md)')), patch.object(app.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            self.window.save_revision()
        self.assertIn('Saturday, six people.', target.read_text(encoding='utf-8'))

    def test_protects_internal_storage_and_reports_failed_export(self):
        internal=self.window.store.distilled_path(self.revision['id'])
        original=internal.read_bytes()
        with patch.object(app.QFileDialog, 'getSaveFileName', return_value=(str(internal), 'Markdown (*.md)')):
            self.window.save_revision()
        self.assertEqual(internal.read_bytes(), original)
        self.assertIn('outside the chat storage', self.window.status_label.text())
        target=self.root/'keep.txt';target.write_text('Keep original')
        with patch.object(app.QFileDialog, 'getSaveFileName', return_value=(str(target), 'Text (*.txt)')), patch.object(app.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(app, 'export_revision', side_effect=OSError('disk full')):
            self.window.save_revision()
        self.assertEqual(target.read_text(encoding='utf-8'), 'Keep original')
        self.assertIn('Export not saved: disk full', self.window.status_label.text())

    def test_export_does_not_require_saving_or_discarding_unsent_draft(self):
        with patch.object(self.window.store, 'save_draft', side_effect=OSError('draft storage unavailable')):
            self.window.input_text.setPlainText('Keep this unsaved draft in the editor')
            with patch.object(app.QFileDialog, 'getSaveFileName', return_value=(str(self.root/'safe.txt'), 'Text (*.txt)')):
                self.window.save_revision()
            self.assertTrue((self.root/'safe.txt').exists())
            self.assertNotIn('Keep this unsaved draft', (self.root/'safe.txt').read_text(encoding='utf-8'))
            self.assertEqual(self.window.input_text.toPlainText(), 'Keep this unsaved draft in the editor')

    def test_suggested_filename_keeps_revision_dots(self):
        self.window.state = self.window.store.rename_revision(self.revision['id'], 'v1.2')
        with patch.object(app.QFileDialog, 'getSaveFileName', return_value=('', '')) as dialog:
            self.window.save_revision()
            self.assertTrue(dialog.call_args.args[2].endswith('Picnic - v1.2.pdf'))

    def test_selected_revision_and_restart_preserve_line_numbers(self):
        text=self.window.transcript.toPlainText()
        blocks=self.window.transcript.blockCount()
        another=self.window.store.create_revision(self.project['id'], 'Other')
        self.window._load_revisions(another['id'])
        with patch.object(app.QFileDialog, 'getSaveFileName', return_value=(str(self.root/'other.txt'), 'Text (*.txt)')):
            self.window.save_revision()
        self.assertNotIn('Saturday', (self.root/'other.txt').read_text(encoding='utf-8'))
        self.window._load_revisions(self.revision['id'])
        self.assertEqual(self.window.transcript.toPlainText(), text)
        self.assertEqual(self.window.transcript.blockCount(), blocks)
        self.window.close()
        self.window=app.DistilWindow(self.root/'storage')
        self.window._load_projects(self.project['id'], self.revision['id'])
        self.window.show();self.qt.processEvents()
        self.assertEqual(self.window.transcript.toPlainText(), text)
        self.assertEqual(self.window.transcript.blockCount(), blocks)
        self.assertTrue(self.window.transcript.line_number_area.isVisible())
        self.assertEqual(self.window.input_text.toPlainText(), 'Unsent private draft')
        self.window.job={'id':'busy'}
        self.window._set_enabled()
        self.assertFalse(self.window.export_button.isEnabled())
        self.window.job=None
        self.window._set_enabled()
        self.assertTrue(self.window.export_button.isEnabled())


if __name__ == '__main__':
    unittest.main()
