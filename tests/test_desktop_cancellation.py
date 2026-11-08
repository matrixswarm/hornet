import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from hornet.desktop.cancellation import Cancellation, Cancelled, bind, model_call
from hornet.desktop.workspace import Workspace, write_json
from hornet.desktop.automation import make_initialization_draft


class CancellationTests(unittest.TestCase):
    def tearDown(self):
        bind(None)

    def test_model_wait_stops_promptly_and_discards_late_result(self):
        token = Cancellation()
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        results = []
        def network():
            started.set()
            release.wait(3)
            done.set()
            return {'late': True}
        def worker():
            bind(token)
            try:
                results.append(model_call(network))
            except Cancelled:
                results.append('cancelled')
        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(started.wait(2))
        token.cancel()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        release.set()
        self.assertTrue(done.wait(2))
        self.assertEqual(results, ['cancelled'])

    def test_cancelled_commit_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'draft.json'
            write_json(path, {'saved': True})
            token = Cancellation()
            bind(token)
            token.cancel()
            with self.assertRaises(Cancelled):
                write_json(path, {'late': True})
            self.assertEqual(json.loads(path.read_text()), {'saved': True})
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_initialization_cancel_during_response_keeps_saved_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            ws = Workspace(directory)
            revision = ws.create('demo')
            token = Cancellation()
            bind(token)
            calls = []
            def describe(payload):
                calls.append(payload)
                if len(calls) == 2:
                    token.cancel()
                return {'summaries': {name: 'Purpose' for name in payload['files']},
                        'blueprint': {'purpose': 'Example'}}
            with self.assertRaises(Cancelled):
                make_initialization_draft(ws, 'demo', revision, describe)
            draft = json.loads((ws.project('demo') / 'initialization-draft.json').read_text())
            self.assertEqual(len(draft['summaries']), 1)
            self.assertEqual(ws.revisions('demo'), [revision])
            bind(None)
            resumed = make_initialization_draft(ws, 'demo', revision,
                lambda p: {'summaries': {name: 'Purpose' for name in p['files']}, 'blueprint': {'purpose': 'Example'}})
            self.assertEqual(resumed['unreviewed'], [])

    def test_mission_cancel_before_candidate_publish_keeps_accepted_revision(self):
        from hornet.desktop.automation import accept_initialization, prepare_mission, read_state
        with tempfile.TemporaryDirectory() as directory:
            ws = Workspace(directory)
            base = ws.create('demo')
            make_initialization_draft(ws, 'demo', base,
                lambda p: {'summaries': {n: 'Purpose' for n in p['files']}, 'blueprint': {'purpose': 'Test'}})
            base = accept_initialization(ws, 'demo')
            before = ws.revisions('demo')
            token = Cancellation()
            bind(token)
            def proposal(*args, **kwargs):
                token.cancel()
                return {'summary': 'Late', 'files': {'main.py': 'print(1)'}}, []
            with patch('hornet.desktop.automation.request_json', return_value={'existing_files': ['main.py'], 'new_files': []}), \
                 patch('hornet.desktop.registry_mission.propose_with_tools', side_effect=proposal):
                with self.assertRaises(Cancelled):
                    prepare_mission(ws, 'demo', base, 'Create main', 'http://localhost', 'model')
            self.assertEqual(ws.revisions('demo'), before)
            self.assertEqual(read_state(ws, 'demo')['accepted_revision'], base)

    def test_diagnosis_cancel_does_not_save_late_report(self):
        from hornet.desktop.automation import accept_initialization, diagnose_mission
        with tempfile.TemporaryDirectory() as directory:
            ws = Workspace(directory)
            base = ws.create('demo')
            make_initialization_draft(ws, 'demo', base,
                lambda p: {'summaries': {n: 'Purpose' for n in p['files']}, 'blueprint': {'purpose': 'Test'}})
            base = accept_initialization(ws, 'demo')
            token = Cancellation()
            bind(token)
            def response(*args, **kwargs):
                token.cancel()
                return {'diagnosis': 'Late', 'evidence': 'Late', 'recommended_next_steps': 'Late'}
            with patch('hornet.desktop.automation.request_json', side_effect=response):
                with self.assertRaises(Cancelled):
                    diagnose_mission(ws, 'demo', base, 'Review', 'http://localhost', 'model')
            records = [json.loads(p.read_text()) for p in (ws.project('demo') / 'missions').glob('*.json')]
            self.assertTrue(records)
            self.assertTrue(all('report' not in record for record in records))

    def test_cancelled_publish_does_not_create_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            ws = Workspace(directory)
            revision = ws.create('demo')
            token = Cancellation()
            bind(token)
            token.cancel()
            stage = ws.project('demo') / 'revisions' / '.test-stage'
            (stage / '.hornet-meta').mkdir(parents=True)
            with self.assertRaises(Cancelled):
                ws._publish(stage, revision, 'Late result')
            self.assertEqual(ws.revisions('demo'), [revision])


class CancellationUiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from hornet.desktop.app import App
        self.root = tk.Tk()
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)
        self.revision = self.ws.create('demo')
        self.app = App(self.root, self.ws)
        self.app.refresh('demo', self.revision)
        self.root.update()

    def tearDown(self):
        for callback in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.tmp.cleanup()

    def test_stop_discards_queued_success_and_keeps_prompt(self):
        self.app.goal.insert('1.0', 'Keep this request')
        self.app.begin_query()
        self.app.update_gate()
        self.assertEqual(self.app.stop_button.winfo_manager(), 'pack')
        self.app.stop_query()
        self.app.events.put(('mission_review', {'should': 'never be displayed'}))
        self.app.poll()
        self.assertFalse(self.app.busy)
        self.assertIsNone(self.app.pending_mission)
        self.assertIn('Keep this request', self.app.goal.get('1.0', 'end'))
        self.assertIn('Late model responses are ignored', self.app.status.get())
        self.assertFalse(self.app.stop_button.winfo_manager())

    def test_progress_and_understanding_disclosure(self):
        self.app.model.set('test-model')
        with patch.object(self.app, 'choose_project_files') as picker:
            self.app.initialize_project()
            explanation = picker.call_args.args[2]
            self.assertNotIn('blueprint', explanation.lower())
            self.assertIn('selected files and current project understanding', explanation)
            self.assertIn(self.app.endpoint.get(), explanation)
        self.app.begin_query()
        self.app.events.put(('automation_progress', 'Understanding file 2 of 5'))
        self.app.poll()
        self.app.poll()
        self.assertIn('Understanding file 2 of 5', self.app.status.get())
        self.assertIn('elapsed', self.app.status.get())


if __name__ == '__main__':
    unittest.main()
