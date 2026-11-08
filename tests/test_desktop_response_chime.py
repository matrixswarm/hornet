import io
import json
import queue
import struct
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hornet.desktop.app import App
from hornet.desktop.cancellation import Cancellation
from hornet.desktop.response_chime import ResponseChime, load_enabled, save_enabled, sparkle_wave


class ResponseChimeTests(unittest.TestCase):
    def test_setting_persists_without_overwriting_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'desktop-settings.json'
            self.assertTrue(load_enabled(path))
            path.write_text(json.dumps({'model': 'local', 'other': 42}))
            save_enabled(path, False)
            self.assertFalse(load_enabled(path))
            self.assertEqual(json.loads(path.read_text())['model'], 'local')
            self.assertEqual(json.loads(path.read_text())['other'], 42)
            save_enabled(path, True)
            self.assertTrue(load_enabled(path))
            path.write_text('broken')
            with self.assertRaises(ValueError):
                save_enabled(path, False)
            self.assertEqual(path.read_text(), 'broken')

    def test_sound_is_short_quiet_pcm(self):
        with wave.open(io.BytesIO(sparkle_wave())) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 22050))
            self.assertLess(wav.getnframes() / wav.getframerate(), 1)
            frames = wav.readframes(wav.getnframes())
            samples = struct.unpack('<' + 'h' * (len(frames) // 2), frames)
            self.assertGreater(max(samples), 100)
            self.assertLess(max(abs(v) for v in samples), 6000)
            self.assertEqual(samples[-1], 0)

    def test_audio_failure_is_silent_and_lock_released(self):
        chime = ResponseChime()
        started = threading.Event()
        def fail(*args):
            started.set()
            raise RuntimeError('No audio device')
        fake = SimpleNamespace(PlaySound=fail, SND_MEMORY=4, SND_NODEFAULT=2)
        with patch('hornet.desktop.response_chime.sys.platform', 'win32'), patch.dict('sys.modules', winsound=fake):
            chime.play()
            self.assertTrue(started.wait(2))
            self.assertTrue(chime._playing.acquire(timeout=2))
            chime._playing.release()

    def test_nonblocking_and_no_overlapping_chimes(self):
        chime = ResponseChime()
        started, release = threading.Event(), threading.Event()
        def play(*args):
            started.set()
            release.wait(2)
        fake = SimpleNamespace(PlaySound=Mock(side_effect=play), SND_MEMORY=4, SND_NODEFAULT=2)
        with patch('hornet.desktop.response_chime.sys.platform', 'win32'), patch.dict('sys.modules', winsound=fake):
            try:
                chime.play()
                self.assertTrue(started.wait(2))
                chime.play()
                self.assertEqual(fake.PlaySound.call_count, 1)
            finally:
                release.set()
                self.assertTrue(chime._playing.acquire(timeout=2))
                chime._playing.release()

    def app_for_event(self, kind, enabled=True, cancelled=False):
        app = Mock(spec=App)
        app.busy = True
        app.operation_started = None
        app.cancel_token = Cancellation()
        if cancelled:
            app.cancel_token.cancel()
        app.project = None
        app.revision = None
        app.root = Mock()
        app.status = Mock()
        app.activity_indicator = Mock()
        app.response_sound = Mock()
        app.response_sound.get.return_value = enabled
        app.response_chime = Mock()
        app.notify_response.side_effect = lambda: App.notify_response(app)
        app.events = queue.Queue()
        app.events.put((kind, {'project': 'demo', 'final_revision': 'r'}))
        return app

    def test_each_review_ready_event_chimes_once_and_respects_mute(self):
        for kind in ('plan_review', 'diagnosis_complete', 'mission_review', 'initialized_draft'):
            for enabled in (True, False):
                with self.subTest(kind=kind, enabled=enabled):
                    app = self.app_for_event(kind, enabled)
                    with patch('hornet.desktop.understanding.draft_complete', return_value=True):
                        App.poll(app)
                        App.poll(app)
                    self.assertEqual(app.response_chime.play.call_count, int(enabled))

    def test_cancelled_late_results_and_incomplete_understanding_stay_silent(self):
        for kind in ('plan_review', 'diagnosis_complete', 'mission_review', 'initialized_draft'):
            app = self.app_for_event(kind, cancelled=True)
            App.poll(app)
            app.response_chime.play.assert_not_called()
        app = self.app_for_event('initialized_draft')
        with patch('hornet.desktop.understanding.draft_complete', return_value=False):
            App.poll(app)
        app.response_chime.play.assert_not_called()

    def test_progress_and_errors_stay_silent(self):
        for kind in ('automation_error', 'automation_progress', 'models_error', 'run_error'):
            app = self.app_for_event(kind)
            app.find_models = Mock()
            app.events.get_nowait()
            app.events.put((kind, 'Example'))
            with patch('hornet.desktop.app.messagebox.showerror'):
                App.poll(app)
            app.response_chime.play.assert_not_called()

    def test_real_header_toggle_survives_app_restart(self):
        import tkinter as tk
        from hornet.desktop.workspace import Workspace
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as directory:
                app = App(root, Workspace(directory))
                self.assertTrue(app.response_sound.get())
                self.assertEqual(app.response_sound_button.cget('text'), 'Reply chime')
                app.response_sound_button.invoke()
                self.assertFalse(app.response_sound.get())
                self.assertFalse(app.load_response_sound())
                app.model.set('saved-model')
                app.save_model()
                self.assertFalse(app.load_response_sound())
                # A fresh window in the same workspace reads the persisted mute.
                second = tk.Toplevel(root)
                second.withdraw()
                restarted = App(second, Workspace(directory))
                self.assertFalse(restarted.response_sound.get())
                self.assertEqual(restarted.model.get(), 'saved-model')
        finally:
            for callback in root.tk.splitlist(root.tk.call('after', 'info')):
                root.tk.call('after', 'cancel', callback)
            root.destroy()
