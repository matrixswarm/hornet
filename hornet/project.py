"""Project discovery, confined paths, and non-destructive file operations."""
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from contextlib import contextmanager


class HornetError(Exception):
    """An actionable user, filesystem, or configuration error."""


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (ValueError, OSError, UnicodeError) as exc:
        raise HornetError("Cannot read JSON {}: {}".format(path, exc)) from exc


def is_link(path):
    """Include Windows junctions/reparse points, also on Python 3.9."""
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


class Project:
    def __init__(self, root=None, require=False):
        selected = root or os.environ.get("HORNET_ROOT") or os.environ.get("HORNET_PATH")
        if selected:
            self.root = Path(selected).expanduser().resolve()
        else:
            cwd = Path.cwd().resolve()
            self.root = next((p for p in (cwd, *cwd.parents) if self.recognized(p)), cwd)
        if not self.root.is_dir():
            raise HornetError("Project directory does not exist: {}".format(self.root))
        if require and not self.recognized(self.root):
            raise HornetError("No Hornet project at {}; run hornet init or select --root.".format(self.root))

    @staticmethod
    def recognized(path):
        return (path / ".hornet.json").is_file() or (path / "continue/goals").is_dir()

    def path(self, value, write=False):
        raw = Path(value).expanduser()
        raw = raw if raw.is_absolute() else self.root / raw
        resolved = raw.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise HornetError("Path must stay inside project {}: {}".format(self.root, value))
        if write:
            if is_link(raw):
                raise HornetError("Refusing to modify linked path: {}".format(raw))
            for part in raw.parents:
                if part == self.root:
                    break
                if is_link(part):
                    raise HornetError("Refusing to modify through linked directory: {}".format(part))
        return resolved

    def display(self, path):
        return Path(path).relative_to(self.root).as_posix()


def sharing_retry(action, *arguments):
    """Retry transient Windows publication failures; permissions stay unchanged."""
    for attempt in range(9):
        try:
            return action(*arguments)
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in {5, 32, 33} or attempt == 8:
                raise
            time.sleep(min(0.01 * 3 ** attempt, 0.5))


def write_text(path, text, force=False):
    """Publish a complete UTF-8 file without overwriting unless requested."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".hornet-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            sharing_retry(os.replace, temporary, path)
        else:
            try:
                sharing_retry(os.link, temporary, path)
            except FileExistsError:
                raise HornetError("Output already exists: {}; choose another path or --force.".format(path))
    finally:
        if os.path.exists(temporary):
            sharing_retry(os.unlink, temporary)


@contextmanager
def operation_lock(project):
    lock = project.path(".hornet-operation.lock", write=True)
    try:
        fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise HornetError("Another archive operation holds {}. Review a stale lock before removing it.".format(lock))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid()}))
        yield
    finally:
        lock.unlink()
