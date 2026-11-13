import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hornet.distil import app
from hornet.distil.qt import QApplication, Qt, QMessageBox
try:
    from PyQt6.QtTest import QTest
except ImportError:
    from PyQt5.QtTest import QTest


class FakeClient:
    requests = []
    answer = 'answer'
    failure = None
    release = None

    def __init__(self, *args, **kwargs):
        pass

    def complete(self, messages, event):
        type(self).requests.append(messages)
        if type(self).release:
            type(self).release.wait(5)
        if type(self).failure:
            raise type(self).failure
        return type(self).answer

    def cancel(self):
        pass  # Deliberately returns a late successful result; the UI must reject it.


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        FakeClient.requests = []
        FakeClient.answer = 'answer'
        FakeClient.failure = None
        FakeClient.release = None
        self.window = app.DistilWindow(Path(self.tmp.name), client_factory=FakeClient,
                                       model_loader=lambda *args, **kwargs: [])
        self.window.model.setCurrentText('test-model')
        self.project = self.window.store.create_project('Test project')
        self.revision = self.window.store.create_revision(self.project['id'], 'Rev 1')
        self.window._load_projects(self.project['id'], self.revision['id'])
        self.window.show()
        self.qt.processEvents()

    def tearDown(self):
        if FakeClient.release:
            FakeClient.release.set()
        self.window.close()
        self.qt.processEvents()
        self.tmp.cleanup()

    def wait(self):
        deadline = time.monotonic() + 5
        while self.window.job and time.monotonic() < deadline:
            self.qt.processEvents()
            time.sleep(.01)
        self.qt.processEvents()
        self.assertIsNone(self.window.job)

    def send(self, text, answer='answer'):
        FakeClient.answer = answer
        self.window.input_text.setPlainText(text)
        self.window.handle_send()
        self.wait()

    def test_model_discovery_preserves_manual_selection_and_ignores_stale_results(self):
        self.window.model.setCurrentText('my-manual-model')
        self.window._model_request = 10
        host = self.window.host.text().strip()
        self.window.model_results.put((9, host, ['stale-model'], None))
        self.window.model_results.put((10, host, ['model-a', 'model-b'], None))
        self.window._poll_models()
        self.assertEqual(self.window.model.currentText(), 'my-manual-model')
        self.assertEqual([self.window.model.itemText(i) for i in range(self.window.model.count())],
                         ['model-a', 'model-b'])

    def test_output_limit_and_timeout_reach_client_and_invalid_values_are_rejected(self):
        calls = []
        self.window.client_factory = lambda *args, **kwargs: calls.append(kwargs)
        self.window.max_output_tokens.setText('8192')
        self.window.request_timeout.setText('1800')
        self.window._client()
        self.assertEqual(calls[0]['max_tokens'], 8192)
        self.assertEqual(calls[0]['timeout'], 1800)
        self.window.max_output_tokens.setText('0')
        with self.assertRaises(ValueError):
            self.window._client()
        self.window.max_output_tokens.clear()
        self.window.request_timeout.setText('nope')
        with self.assertRaises(ValueError):
            self.window._client()

    def test_model_discovery_does_not_choose_an_unrequested_model(self):
        self.window.model.setCurrentText('')
        self.window._model_request = 12
        self.window.model_results.put((12, self.window.host.text().strip(), ['first', 'second'], None))
        self.window._poll_models()
        self.assertEqual(self.window.model.currentText(), '')
        self.assertEqual(self.window.model.count(), 2)

    def test_changing_server_clears_old_discovered_choices(self):
        self.window._model_host = self.window.host.text().strip()
        self.window.model.clear()
        self.window.model.addItems(['old-auto', 'old-other'])
        self.window.model.setCurrentText('')
        self.window.host.setText('http://127.0.0.1:54321')
        self.window.refresh_models()
        self.assertEqual(self.window.model.count(), 0)
        self.assertEqual(self.window.model.currentText(), '')

    def test_slow_discovery_does_not_block_or_replace_a_manual_choice(self):
        gate, entered = threading.Event(), threading.Event()
        def load(*args, **kwargs):
            entered.set()
            if not gate.wait(3):
                raise TimeoutError('test timed out')
            return ['available']
        self.window.model_loader = load
        self.window.model.setCurrentText('')
        started = time.monotonic()
        self.window.refresh_models()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(entered.wait(1))
        self.window.model.setCurrentText('manual-choice')
        gate.set()
        deadline = time.monotonic() + 3
        while self.window.model_status.text() == 'Loading models…' and time.monotonic() < deadline:
            self.qt.processEvents()
            time.sleep(.01)
        self.assertEqual(self.window.model_status.text(), '1 model')
        self.assertEqual(self.window.model.currentText(), 'manual-choice')

    def test_enter_and_shift_enter_separate_send_from_distill(self):
        QTest.keyClicks(self.window.input_text, 'hello')
        QTest.keyClick(self.window.input_text, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClicks(self.window.input_text, 'there')
        self.assertEqual(self.window.input_text.toPlainText(), 'hello\nthere')
        QTest.keyClick(self.window.input_text, Qt.Key.Key_Return)
        self.wait()
        self.assertEqual([m['content'] for m in self.window.state['messages']], ['hello\nthere', 'answer'])
        self.assertIsNone(self.window.state['distilled'])
        self.assertEqual(len(FakeClient.requests), 1)

    def test_distill_replaces_only_after_save_and_later_turns_follow(self):
        self.send('First decision', 'Agreed')
        FakeClient.answer = '  Distilled decision\n'
        FakeClient.release = threading.Event()
        self.window.detail.setCurrentText('Detailed')
        self.window.handle_distill()
        self.assertFalse(self.window.input_text.isEnabled())
        self.assertFalse(self.window.revisions.isEnabled())
        self.assertIn('First decision', self.window.transcript.toPlainText())
        self.assertIsNone(self.window.state['distilled'])
        self.window.handle_distill()  # repeated click guarded
        FakeClient.release.set()
        self.wait()
        self.assertEqual(len(FakeClient.requests), 2)
        self.assertEqual(len(self.window.state['messages']), 1)
        self.assertIn('Detail level: Detailed', FakeClient.requests[-1][-1]['content'])
        self.assertEqual([m['content'] for m in FakeClient.requests[-1][:-1]], ['First decision', 'Agreed'])
        self.assertEqual(self.window.store.distilled_path(self.revision['id']).read_text(), '  Distilled decision\n')
        self.assertIn('END DISTILL', self.window.transcript.toPlainText())
        self.assertNotIn('First decision', self.window.transcript.toPlainText())
        FakeClient.release = None
        self.send('Next step', 'Second answer')
        self.assertEqual([m['content'] for m in self.window.state['messages']], ['  Distilled decision\n', 'Next step', 'Second answer'])
        self.assertEqual(FakeClient.requests[-1][0]['content'], '  Distilled decision\n')
        FakeClient.answer = 'Second distill'
        self.window.handle_distill()
        self.wait()
        self.assertEqual(self.window.state['messages'][0]['content'], 'Second distill')
        self.assertEqual(len(list(Path(self.tmp.name).rglob('distilled.md'))), 1)

    def test_stop_failure_save_failure_and_late_response_preserve_chat(self):
        self.send('Important original')
        FakeClient.answer = 'seed'
        self.window.handle_distill()
        self.wait()
        self.send('New message')
        before = self.window.state['messages']
        original = self.window.store.distilled_path(self.revision['id']).read_bytes()
        FakeClient.failure = RuntimeError('model failed')
        self.window.handle_distill()
        self.wait()
        self.assertEqual(self.window.state['messages'], before)
        FakeClient.failure = None
        with patch.object(self.window.store, 'replace_with_distilled', side_effect=OSError('disk full')):
            self.window.handle_distill()
            self.wait()
        self.assertEqual(self.window.state['messages'], before)
        FakeClient.release = threading.Event()
        self.window.handle_distill()
        self.window.stop()
        self.assertTrue(self.window.input_text.isEnabled())
        FakeClient.release.set()
        for _ in range(20):
            self.qt.processEvents()
            time.sleep(.01)
        self.assertEqual(self.window.state['messages'], before)
        self.assertEqual(self.window.store.distilled_path(self.revision['id']).read_bytes(), original)

    def test_revision_selection_restart_and_drafts_are_isolated(self):
        self.send('First revision')
        self.window.input_text.setPlainText('  unfinished draft\n')
        second = self.window.store.create_revision(self.project['id'], 'Rev 2')
        self.window._load_revisions(second['id'])
        self.assertEqual(self.window.state['messages'], [])
        self.assertEqual(self.window.input_text.toPlainText(), '')
        self.send('Second revision')
        self.window._load_revisions(self.revision['id'])
        self.assertEqual(self.window.input_text.toPlainText(), '  unfinished draft\n')
        self.assertEqual(self.window.state['messages'][0]['content'], 'First revision')
        self.window.close()
        self.window = app.DistilWindow(Path(self.tmp.name), client_factory=FakeClient)
        self.window._load_projects(self.project['id'], self.revision['id'])
        self.assertEqual(self.window.input_text.toPlainText(), '  unfinished draft\n')
        self.assertEqual(self.window.state['messages'][0]['content'], 'First revision')

    def test_import_seed_preserves_original_and_draft_and_empty_import_fails(self):
        source = Path(self.tmp.name) / 'source.txt'
        source.write_text('  Imported seed\n')
        self.window.input_text.setPlainText('pending draft')
        with patch.object(app.QFileDialog, 'getOpenFileName', return_value=(str(source), '')):
            self.window.import_text()
        self.assertEqual(self.window.state['messages'][0]['content'], '  Imported seed\n')
        self.assertEqual(self.window.input_text.toPlainText(), 'pending draft')
        self.assertEqual(self.window.store.get_revision(self.revision['id'])['draft'], 'pending draft')
        self.assertEqual(source.read_text(), '  Imported seed\n')
        before = self.window.state['messages']
        source.write_text('  ')
        with patch.object(app.QFileDialog, 'getOpenFileName', return_value=(str(source), '')), patch.object(app.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            self.window.import_text()
        self.assertEqual(self.window.state['messages'], before)
        self.window.handle_distill()
        self.assertIsNone(self.window.job)
        self.assertIn('Send your draft', self.window.status_label.text())

    def test_manual_revision_crud_and_independent_clone(self):
        self.send('Original context')
        FakeClient.answer = 'Saved distilled context'
        self.window.handle_distill()
        self.wait()
        self.window.input_text.setPlainText('Draft to clone')
        with patch.object(app.QInputDialog, 'getText', return_value=('Branch', True)):
            self.window.clone_revision()
        clone_id = self.window.current_revision_id
        self.assertNotEqual(clone_id, self.revision['id'])
        self.assertEqual(self.window.input_text.toPlainText(), 'Draft to clone')
        self.assertEqual(self.window.state['messages'][0]['content'], 'Saved distilled context')
        self.send('Clone only')
        original = self.window.store.get_revision(self.revision['id'])
        self.assertEqual(len(original['messages']), 1)
        with patch.object(app.QInputDialog, 'getText', return_value=('Renamed branch', True)):
            self.window.rename_revision()
        self.assertEqual(self.window.state['name'], 'Renamed branch')
        with patch.object(app.QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
            self.window.delete_revision()
        self.assertEqual(self.window.current_revision_id, clone_id)
        with patch.object(app.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            self.window.delete_revision()
        self.assertNotEqual(self.window.current_revision_id, clone_id)
        label = 'Renamed branch (' + clone_id[:8] + ')'
        with patch.object(app.QInputDialog, 'getItem', return_value=(label, True)):
            self.window.restore_revision()
        self.assertEqual(self.window.current_revision_id, clone_id)
        self.assertEqual(self.window.state['messages'][-2]['content'], 'Clone only')

    def test_clicking_selected_revision_reloads_current_saved_chat(self):
        self.send('First turn')
        self.window.store.add_message(self.revision['id'], 'assistant', 'Saved later')
        self.window.revisions.itemClicked.emit(self.window.revisions.currentItem())
        self.assertEqual(self.window.state['messages'][-1]['content'], 'Saved later')
        self.assertIn('Saved later', self.window.transcript.toPlainText())

    def test_worker_start_failure_restores_controls(self):
        self.window.input_text.setPlainText('Saved before request')
        with patch.object(app.threading.Thread, 'start', side_effect=RuntimeError('cannot start thread')):
            self.window.handle_send()
            self.assertIsNone(self.window.job)
            self.assertTrue(self.window.input_text.isEnabled())
            self.assertEqual(self.window.state['messages'][0]['content'], 'Saved before request')
            self.window.handle_distill()
            self.assertIsNone(self.window.job)
            self.assertTrue(self.window.input_text.isEnabled())
            self.assertIsNone(self.window.state['distilled'])

    def test_unsaved_draft_blocks_navigation_and_close_until_saved(self):
        other = self.window.store.create_revision(self.project['id'], 'Other')
        self.window._load_revisions(self.revision['id'])
        with patch.object(self.window.store, 'save_draft', side_effect=OSError('disk full')):
            self.window.input_text.setPlainText('Never lose this draft')
            self.window.revisions.setCurrentRow(1)
            self.assertEqual(self.window.current_revision_id, self.revision['id'])
            self.assertEqual(self.window.revisions.currentRow(), 0)
            self.assertFalse(self.window.close())
            self.assertEqual(self.window.input_text.toPlainText(), 'Never lose this draft')
        self.window.revisions.setCurrentRow(1)
        self.assertEqual(self.window.current_revision_id, other['id'])
        self.window.revisions.setCurrentRow(0)
        self.assertEqual(self.window.input_text.toPlainText(), 'Never lose this draft')

    def test_close_while_distilling_never_commits_late_result(self):
        self.send('Keep me')
        FakeClient.release = threading.Event()
        self.window.handle_distill()
        self.assertTrue(self.window.close())
        FakeClient.release.set()
        time.sleep(.05)
        from hornet.distil.store import DistilStore
        reopened = DistilStore(Path(self.tmp.name))
        try:
            state = reopened.get_revision(self.revision['id'])
            self.assertIsNone(state['distilled'])
            self.assertEqual(state['messages'][0]['content'], 'Keep me')
        finally:
            reopened.close()
        # Avoid double-close in tearDown.
        self.window = app.DistilWindow(Path(self.tmp.name), client_factory=FakeClient)


if __name__ == '__main__':
    unittest.main()
