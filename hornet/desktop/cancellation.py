"""Cooperative cancellation with guarded commits and discardable model responses."""
import queue
import threading
from contextlib import contextmanager


class Cancelled(Exception):
    pass


_local = threading.local()


class Cancellation:
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.RLock()

    def cancel(self):
        with self.lock:
            self.event.set()

    def check(self):
        if self.event.is_set():
            raise Cancelled('Stopped by user.')


def bind(token):
    _local.token = token


def check():
    token = getattr(_local, 'token', None)
    if token:
        token.check()


@contextmanager
def commit():
    token = getattr(_local, 'token', None)
    if token:
        with token.lock:
            token.check()
            yield
    else:
        yield


def model_call(function):
    token = getattr(_local, 'token', None)
    if token is None:
        return function()
    token.check()
    result = queue.Queue(maxsize=1)
    def fetch():
        try:
            result.put((True, function()))
        except Exception as exc:
            result.put((False, exc))
    threading.Thread(target=fetch, daemon=True).start()
    while True:
        token.check()
        try:
            success, value = result.get(timeout=0.1)
        except queue.Empty:
            continue
        token.check()
        if success:
            return value
        raise value


@contextmanager
def terminal_metadata():
    """Serialize stopped-state bookkeeping only, even after cancellation.

    This does not clear the token or authorize results, publication or effects.
    """
    token = getattr(_local, 'token', None)
    if token:
        with token.lock:
            yield
    else:
        yield
