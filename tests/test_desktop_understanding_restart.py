import json
import tempfile
import threading
import tkinter as tk
import unittest
from hornet.desktop.app import App
from hornet.desktop.workspace import Workspace
from hornet.desktop.automation import make_initialization_draft, accept_initialization, read_state
from hornet.desktop.cancellation import Cancellation, Cancelled, bind, model_call


class RestartUnderstandingTests(unittest.TestCase):
    def test_real_cancel_then_restart_new_and_previously_accepted(self):
        for previously_accepted in (False, True):
            with self.subTest(previously_accepted=previously_accepted), tempfile.TemporaryDirectory() as directory:
                ws = Workspace(directory)
                revision = ws.create('demo')
                if previously_accepted:
                    make_initialization_draft(ws, 'demo', revision,
                        lambda p: {'summaries': {n: 'Purpose' for n in p['files']}, 'blueprint': {'purpose': 'Test'}})
                    revision = accept_initialization(ws, 'demo')
                accepted_before = read_state(ws, 'demo').get('accepted_revision')
                token = Cancellation()
                started, release, finished = threading.Event(), threading.Event(), threading.Event()
                def response(payload):
                    started.set()
                    release.wait(4)
                    finished.set()
                    return {'summaries': {n: 'Late' for n in payload['files']}, 'blueprint': {'purpose': 'Late'}}
                def worker():
                    bind(token)
                    try:
                        make_initialization_draft(ws, 'demo', revision,
                            lambda p: model_call(lambda: response(p)), resume=False)
                    except Cancelled:
                        pass
                    finally:
                        bind(None)
                thread = threading.Thread(target=worker)
                thread.start()
                self.assertTrue(started.wait(3))
                token.cancel()
                thread.join(2)
                self.assertFalse(thread.is_alive())
                path = ws.project('demo') / 'initialization-draft.json'
                before = path.read_bytes()
                release.set()
                self.assertTrue(finished.wait(2))
                self.assertEqual(path.read_bytes(), before)
                root = tk.Tk()
                root.withdraw()
                try:
                    app = App(root, ws)
                    app.refresh('demo', revision)
                    root.update()
                    self.assertIn('incomplete', app.status.get().lower())
                    self.assertIn('0 of', app.workflow_hint.get())
                    self.assertEqual(app.init_button.cget('text'), 'Resume understanding')
                    self.assertEqual(app.init_button.winfo_manager(), 'pack')
                    self.assertFalse(app.accept_map.winfo_manager())
                    self.assertEqual(str(app.accept_map.cget('state')), 'disabled')
                    self.assertEqual(read_state(ws, 'demo').get('accepted_revision'), accepted_before)
                    if previously_accepted:
                        self.assertIn('previously accepted', app.workflow_hint.get().lower())
                    resumed = make_initialization_draft(ws, 'demo', revision,
                        lambda p: {'summaries': {n: 'Purpose' for n in p['files']}, 'blueprint': {'purpose': 'Test'}})
                    app.pending_initialization = resumed
                    app.update_gate()
                    self.assertEqual(app.accept_map.winfo_manager(), 'pack')
                    self.assertIn('Not accepted yet', app.workflow_hint.get())
                finally:
                    for callback in root.tk.splitlist(root.tk.call('after', 'info')):
                        root.after_cancel(callback)
                    root.destroy()


if __name__ == '__main__':
    unittest.main()
