"""One JSON RPC per SSH invocation; installed root-owned in the coding VM."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

BLOCKED = {".git", ".venv", "venv", "__pycache__"}
MAX_FILE = 256 * 1024
MAX_REPLY = 1500 * 1024
READY_MARKER = '/opt/hornet-worker/ready'


class WorkerError(Exception):
    pass


class Worker:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise WorkerError("Workspace is not ready")

    def path(self, value="."):
        if not isinstance(value, str) or len(value) > 500:
            raise WorkerError("Path must be a short relative string")
        raw = Path(value.replace("\\", "/"))
        if raw.is_absolute() or ".." in raw.parts or any(p in BLOCKED for p in raw.parts):
            raise WorkerError("Path is outside the editable workspace")
        result = self.root / raw
        for item in (result, *result.parents):
            if item == self.root:
                break
            if item.is_symlink():
                raise WorkerError("Linked paths are not available to coding tools")
        try:
            result.resolve().relative_to(self.root)
        except ValueError:
            raise WorkerError("Path escapes workspace")
        return result

    def rel(self, value):
        return Path(value).relative_to(self.root).as_posix()

    def files(self, path="."):
        base = self.path(path)
        if base.is_file():
            yield base
            return
        for directory, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in BLOCKED and not (Path(directory) / d).is_symlink())
            for name in sorted(names):
                item = Path(directory) / name
                if not item.is_symlink():
                    yield item

    def status(self):
        checks = {'provisioning_marker': Path(READY_MARKER).is_file(),
                  'git_metadata': (self.root / '.git').is_dir() and not (self.root / '.git').is_symlink(),
                  'git_root': False, 'git_head': False}
        if checks['git_metadata']:
            try:
                base = ['git', '-C', str(self.root), 'rev-parse']
                top = subprocess.run(base + ['--show-toplevel'], capture_output=True, text=True, timeout=10)
                head = subprocess.run(base + ['--verify', 'HEAD'], capture_output=True, text=True, timeout=10)
                checks['git_root'] = top.returncode == 0 and Path(top.stdout.strip()).resolve() == self.root
                checks['git_head'] = head.returncode == 0 and bool(re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', head.stdout.strip()))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        return {"ok": True, "platform": sys.platform, "uid": os.getuid() if hasattr(os, "getuid") else None,
                "root": str(self.root), "python": sys.version.split()[0],
                "ready": all(checks.values()), 'readiness_checks': checks, 'worker_protocol': 1,
                "sandbox": "dedicated-virtualbox-vm"}

    def list_files(self, path="."):
        entries = []
        for item in self.files(path):
            entries.append({"path": self.rel(item), "bytes": item.stat().st_size})
            if len(entries) >= 200:
                return {"ok": True, "files": entries, "truncated": True}
        return {"ok": True, "files": entries, "truncated": False}

    def read_file(self, path, start_line=1, max_lines=200):
        if type(start_line) is not int or type(max_lines) is not int or start_line < 1 or not 1 <= max_lines <= 400:
            raise WorkerError("Use positive start_line and max_lines between 1 and 400")
        item = self.path(path)
        raw = item.read_bytes()
        if len(raw) > MAX_FILE:
            raise WorkerError("File exceeds the 256 KiB read limit")
        lines = raw.decode("utf-8-sig").splitlines()
        selected = lines[start_line - 1:start_line - 1 + max_lines]
        return {"ok": True, "path": path, "sha256": hashlib.sha256(raw).hexdigest(), "total_lines": len(lines),
                "content": "\n".join("{}: {}".format(i, line) for i, line in enumerate(selected, start_line)),
                "truncated": start_line - 1 + len(selected) < len(lines)}

    def search(self, query, path="."):
        if not isinstance(query, str) or not query or len(query) > 200:
            raise WorkerError("Search query must contain 1-200 characters")
        matches = []
        for item in self.files(path):
            if item.stat().st_size > MAX_FILE:
                continue
            try:
                lines = item.read_text(encoding="utf-8-sig").splitlines()
            except UnicodeError:
                continue
            for number, line in enumerate(lines, 1):
                if query in line:
                    matches.append({"path": self.rel(item), "line": number, "text": line[:300]})
                    if len(matches) >= 40:
                        return {"ok": True, "matches": matches, "truncated": True}
        return {"ok": True, "matches": matches, "truncated": False}

    def write_file(self, path, content, expected_sha256):
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE:
            raise WorkerError("Write content must be UTF-8 text below 256 KiB")
        if not isinstance(expected_sha256, str) or (expected_sha256 and not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)):
            raise WorkerError("Use the read_file SHA-256, or an empty string for a new file")
        item = self.path(path)
        if item == self.root:
            raise WorkerError("Choose a file path")
        current = hashlib.sha256(item.read_bytes()).hexdigest() if item.exists() else ""
        if current != expected_sha256:
            raise WorkerError("File changed or already exists; read it again before editing")
        item.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".hornet-edit-", dir=str(item.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            os.replace(temporary, item)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"ok": True, "path": path, "sha256": hashlib.sha256(item.read_bytes()).hexdigest(), "changed": current != hashlib.sha256(item.read_bytes()).hexdigest()}

    def run_command(self, argv, timeout=45):
        if not isinstance(argv, list) or not argv or len(argv) > 30 or any(not isinstance(v, str) or len(v) > 4000 for v in argv):
            raise WorkerError("argv must be a short array of strings")
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise WorkerError("Command timeout must be between 1 and 120 seconds")
        if argv[0] in {"python", "python3"}:
            command = [sys.executable, *argv[1:]]
        elif argv[0] == "git" and len(argv) > 1 and argv[1] in {"diff", "status", "log", "show", "ls-files"}:
            command = [shutil.which("git") or "git", *argv[1:]]
        elif argv[0] == "rg":
            command = [shutil.which("rg") or "rg", *argv[1:]]
        else:
            raise WorkerError("Allowed executables: python3, rg, and git diff/status/log/show/ls-files")
        env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "USER", "LANG", "TMPDIR", "SYSTEMROOT", "WINDIR"}}
        env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(self.root), GIT_TERMINAL_PROMPT="0")
        started = time.monotonic()
        timed_out = False
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen(command, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                     stdout=output, stderr=subprocess.STDOUT, start_new_session=(os.name == "posix"))
            try:
                code = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "posix":
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
                child.wait()
                code = 124
            size = output.tell()
            output.seek(max(0, size - 24000))
            text = output.read().decode("utf-8", "replace")
        return {"ok": code == 0, "exit_code": code, "output": text, "output_bytes": size,
                "truncated": size > 24000, "timed_out": timed_out, "seconds": round(time.monotonic() - started, 3)}

    def run_tests(self):
        return self.run_command(["python3", "-m", "unittest", "discover", "-s", "tests", "-v"], timeout=120)

    def diff(self):
        return self.run_command(["git", "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--"])

    def changes(self):
        tracked = subprocess.check_output(["git", "diff", "--name-only", "-z", "HEAD", "--"], cwd=self.root)
        new = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=self.root)
        names = sorted(set(v.decode("utf-8") for v in (tracked + new).split(b"\x00") if v))
        files = []
        total = 0
        if len(names) > 50:
            raise WorkerError("Change set exceeds 50 files; review it inside the VM")
        for name in names:
            item = self.path(name)
            if not item.exists():
                files.append({"path": name, "deleted": True})
                continue
            raw = item.read_bytes()
            total += len(raw)
            if len(raw) > MAX_FILE or total > 800000:
                raise WorkerError("Change set exceeds export size limits")
            files.append({"path": name, "deleted": False, "sha256": hashlib.sha256(raw).hexdigest(), "content": raw.decode("utf-8")})
        return {"ok": True, "files": files, "tracked_diff": self.diff()}

    def call(self, tool, arguments):
        methods = {"status": self.status, "list_files": self.list_files, "read_file": self.read_file,
                   "search": self.search, "write_file": self.write_file, "run_command": self.run_command,
                   "run_tests": self.run_tests, "diff": self.diff, "changes": self.changes}
        if tool not in methods or not isinstance(arguments, dict):
            raise WorkerError("Unknown worker operation or invalid arguments")
        return methods[tool](**arguments)


def main():
    try:
        line = sys.stdin.buffer.readline(MAX_REPLY + 1)
        if len(line) > MAX_REPLY:
            raise WorkerError("Request exceeds size limit")
        message = json.loads(line)
        if not isinstance(message, dict):
            raise WorkerError("Request must be an object")
        result = Worker("/home/hornet/workspace/hornet_cli").call(message.get("tool"), message.get("arguments", {}))
    except (WorkerError, OSError, ValueError, TypeError, UnicodeError) as exc:
        result = {"ok": False, "error": str(exc)}
    encoded = json.dumps(result, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_REPLY:
        encoded = json.dumps({"ok": False, "error": "Worker response exceeds size limit"})
    print(encoded)


if __name__ == "__main__":
    main()
