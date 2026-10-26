"""Shared file transactions. Cooperating writers serialize; previews never commit.

The lock is not a sandbox and cannot stop an unrelated editor. A crash leaves
the lock for explicit operator recovery rather than guessing that it is stale.
"""
import ast
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_text(path):
    return Path(path).read_bytes().decode("utf-8-sig")


def check_content(path, text):
    if Path(path).suffix.lower() == ".py":
        compile(text, str(path), "exec")
    elif Path(path).suffix.lower() == ".json":
        json.loads(text)


def update_file(path, transform, *, commit=True, expected_sha256=None, create=False):
    target = Path(path).expanduser().resolve()
    if not target.parent.is_dir():
        raise ValueError(f"Parent directory does not exist: {target.parent}")
    lock = target.with_name(target.name + ".hornet.lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(f"File is locked: {lock}. Inspect owner/process before explicit recovery.")
    shadow = None
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"pid": os.getpid(), "target": str(target)}, stream)
        exists = target.exists()
        if not exists and not create:
            raise FileNotFoundError(str(target))
        original = target.read_bytes() if exists else b""
        before = digest(original)
        if expected_sha256 is not None and expected_sha256 != before:
            raise ValueError("Source changed since it was read; expected_sha256 does not match")
        content = transform(original.decode("utf-8-sig"))
        check_content(target, content)
        encoded = content.encode("utf-8")
        fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".atomic", dir=str(target.parent))
        shadow = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if exists:
            shutil.copymode(target, shadow)
        result = {"status": "SUCCESS" if commit else "STAGED", "path": str(target),
                  "source_sha256": before, "sha256": digest(encoded)}
        if commit:
            if target.exists() != exists or (exists and target.read_bytes() != original):
                raise ValueError("Source changed during edit; commit refused")
            os.replace(str(shadow), str(target))
        else:
            result.update(shadow_path=str(shadow), shadow_content=content)
        shadow = None
        return result
    finally:
        if shadow is not None:
            shadow.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def edit_args(args):
    return {"commit": args.get("commit", True), "expected_sha256": args.get("expected_sha256")}


def execute_guarded(function):
    def wrapped(args):
        try:
            return function(args)
        except Exception as exc:
            return {"status": "ERROR", "message": str(exc)}
    return wrapped
