"""Quiet, dependency-free Windows reply notification; never blocks the UI."""
import io
import json
import math
import struct
import sys
import threading
import wave
from functools import lru_cache


def load_enabled(path):
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
        value = settings.get('response_sound', True) if isinstance(settings, dict) else True
        return value if isinstance(value, bool) else True
    except (OSError, ValueError):
        return True


def save_enabled(path, enabled):
    from .workspace import write_json
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError('Desktop settings must be an object')
    settings['response_sound'] = bool(enabled)
    write_json(path, settings)


@lru_cache(maxsize=1)
def sparkle_wave():
    """A soft ascending four-note bell, fading out in under one second."""
    rate = 22050
    notes = (1046.50, 1318.51, 1567.98, 2093.00)
    samples = bytearray()
    for index in range(int(rate * 0.85)):
        t = index / rate
        value = 0.0
        for offset, frequency in enumerate(notes):
            age = t - offset * 0.085
            if 0 <= age < 0.57:
                envelope = min(age / 0.008, 1.0) * math.exp(-age * 9)
                envelope *= min((0.57 - age) / 0.05, 1.0)
                value += 0.055 * envelope * (math.sin(2 * math.pi * frequency * age)
                         + 0.18 * math.sin(2 * math.pi * frequency * 2.01 * age))
        samples.extend(struct.pack('<h', int(max(-1, min(1, value)) * 32767)))
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples)
    return output.getvalue()


class ResponseChime:
    def __init__(self):
        self._playing = threading.Lock()

    def play(self):
        # No default system beep or external player on unsupported platforms.
        if sys.platform != 'win32' or not self._playing.acquire(blocking=False):
            return
        def worker():
            try:
                import winsound
                # SND_MEMORY cannot be combined with SND_ASYNC. Play synchronously
                # on this daemon thread, never Tk's event loop.
                winsound.PlaySound(sparkle_wave(), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
            except Exception:
                pass  # Missing/muted audio devices must not affect agent work.
            finally:
                self._playing.release()
        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            self._playing.release()
