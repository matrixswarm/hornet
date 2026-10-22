"""An OS lock survives neither process death nor stale metadata."""
import ctypes
import errno
import json
import os
from pathlib import Path
import uuid

from hornet.project import HornetError, write_text


def process_alive(pid):
    if type(pid) is not int or pid <= 0:
        return True  # Unknown ownership is never permission to reclaim.
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, 0, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no PID.
        try:
            code = ctypes.c_ulong()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH


class WorkerLock:
    def __init__(self, path, resume_run=None):
        self.path = Path(path)
        self.resume_run = str(resume_run) if resume_run else None
        self.previous = None
        self.token = uuid.uuid4().hex
        self.guard = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.guard = open(str(self.path) + ".guard", "a+b")
        self.guard.seek(0, 2)
        if not self.guard.tell():
            self.guard.write(b"\0")
            self.guard.flush()
        self.guard.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.guard.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.guard.close()
            raise HornetError("A coding run already holds the worker lock") from None
        try:
            if self.path.exists():
                try:
                    old = json.loads(self.path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    raise HornetError("Worker lock ownership is unreadable; inspect it before recovery") from None
                if not isinstance(old, dict) or process_alive(old.get("pid")):
                    raise HornetError("A live or unknown process owns the worker lock")
                if not self.resume_run or old.get("run_dir") != self.resume_run:
                    raise HornetError("A stale lock belongs to another run; resume that run or inspect its lock")
                self.previous = old
                write_text(Path(self.resume_run) / ("lock-recovery-" + uuid.uuid4().hex + ".json"), json.dumps(old, indent=2))
            self.set_run(self.resume_run)
            return self
        except BaseException:
            self.guard.close()
            raise

    def set_run(self, run):
        write_text(self.path, json.dumps({"pid": os.getpid(), "token": self.token,
                   "run_dir": str(run) if run else None}), force=True)

    def __exit__(self, *exc):
        try:
            if self.path.exists():
                owner = json.loads(self.path.read_text(encoding="utf-8"))
                if owner.get("token") == self.token:
                    self.path.unlink()
        finally:
            self.guard.close()
