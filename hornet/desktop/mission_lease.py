"""Exclusive process-level lease for explicit mission resume."""
from contextlib import contextmanager
import os


@contextmanager
def mission_lease(directory):
    from .goal_store import _safe_path, _regular_file
    _safe_path(directory)
    directory.mkdir(exist_ok=True)
    path = directory / 'running.lock'
    _regular_file(path)
    stream = path.open('a+b')
    locked = False
    try:
        if stream.seek(0, 2) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise ValueError('This mission is still running. Stop it or wait before resuming.') from exc
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def project_operation(function):
    """Serialize processing and maintenance without waiting or dropping work."""
    from functools import wraps
    @wraps(function)
    def serialized(ws, project, *args, **kwargs):
        with mission_lease(ws.project(project) / '.candidate-operation'):
            return function(ws, project, *args, **kwargs)
    return serialized
