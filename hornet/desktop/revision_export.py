"""Complete, non-overwriting revision exports with an explicit destination plan."""
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


class DestinationChanged(ValueError):
    """The displayed destination is no longer available; ask again."""


def _plain(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Export does not follow symbolic links or junctions: ' + str(part))
    return path


def _parent(ws, parent):
    parent = _plain(Path(parent).expanduser()).resolve(strict=True)
    if not parent.is_dir() or parent == ws.root or ws.root in parent.parents:
        raise ValueError('Choose an existing folder outside the Hornet workspace.')
    return parent


def plan_export(ws, project, revision, parent):
    _plain(ws.revision(project, revision))
    parent = _parent(ws, parent)
    base = project + '-' + revision
    destination = parent / base
    number = 2
    while os.path.lexists(destination):
        destination = parent / (base + '-' + str(number))
        number += 1
    return destination


def _manifest(root):
    """Include hidden files, empty directories, and metadata; never follow links."""
    result = {}
    _plain(root)
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = _plain(Path(parent) / name)
            info = path.stat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                result[relative] = ('directory',)
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                result[relative] = ('file', info.st_size, digest.hexdigest())
            else:
                raise ValueError('Cannot export special file: ' + relative)
    return result


def _publish(stage, destination):
    """Atomic directory rename that cannot replace even an empty directory."""
    if os.name == 'nt':
        os.rename(stage, destination)  # Windows rename is always no-replace.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        outcome = rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1)
    elif sys.platform == 'darwin' and hasattr(libc, 'renamex_np'):
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        outcome = rename(os.fsencode(stage), os.fsencode(destination), 4)
    else:
        raise OSError('This platform cannot safely publish a non-overwriting folder export.')
    if outcome:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def export_revision(ws, project, revision, parent, destination=None):
    source = _plain(ws.revision(project, revision)).resolve(strict=True)
    parent = _parent(ws, parent)
    planned = plan_export(ws, project, revision, parent)
    if destination is not None and Path(destination) != planned:
        raise DestinationChanged('The destination changed. Review the new numbered folder before exporting.')
    destination = planned
    parent_identity = parent.stat()
    before = _manifest(source)
    stage = Path(tempfile.mkdtemp(prefix='.hornet-export-', dir=parent))
    try:
        shutil.copytree(source, stage, dirs_exist_ok=True, symlinks=True)
        if _manifest(stage) != before or _manifest(source) != before:
            raise ValueError('The revision changed while copying. No export was published; try again when idle.')
        _parent(ws, parent)
        current = parent.stat()
        if (current.st_dev, current.st_ino) != (parent_identity.st_dev, parent_identity.st_ino):
            raise ValueError('The destination folder changed during export.')
        try:
            _publish(stage, destination)
        except OSError as exc:
            if exc.errno in (errno.EEXIST, errno.ENOTEMPTY) or os.path.lexists(destination):
                raise DestinationChanged('That folder was created during export. Review the new destination.') from exc
            raise
        return destination
    finally:
        # Only our unpredictable temporary staging folder is cleaned up.
        if stage.exists():
            shutil.rmtree(stage)
