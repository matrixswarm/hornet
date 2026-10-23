"""Pinned, noninteractive SSH transport to the dedicated localhost VM."""
import json
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import base64
import zlib

from hornet.project import HornetError, read_json
from .suite import SCRIPT as TEST_SCRIPT, run_suite
from . import workspaces


EDIT_SCRIPT = """import json, runpy, sys
from pathlib import Path
api = runpy.run_path('/opt/hornet-worker/worker.py')
try:
    args = json.loads(sys.argv[1])
    worker = api['Worker'](Path.cwd())
    text = worker.path(args['path']).read_text(encoding='utf-8')
    matches = text.count(args['old_text'])
    if matches != 1:
        raise api['WorkerError']('old_text matched %s places; it must match exactly once. Read the current file and include the enclosing function definition to make the block unique. Do not repeat the same ambiguous block.' % matches)
    result = worker.write_file(args['path'], text.replace(args['old_text'], args['new_text'], 1), args['expected_sha256'])
except (api['WorkerError'], OSError, ValueError, TypeError, UnicodeError) as exc:
    result = {'ok': False, 'error': str(exc)}
print(json.dumps(result, ensure_ascii=False))
"""

SNAPSHOT_SCRIPT = """import hashlib, json, os, runpy, subprocess
from pathlib import Path
api = runpy.run_path('/opt/hornet-worker/worker.py')
try:
    worker = api['Worker'](Path.cwd())
    files = {}
    for directory, dirs, names in os.walk(worker.root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in api['BLOCKED']]
        if any((Path(directory) / name).is_symlink() for name in dirs + names):
            raise ValueError('Linked workspace entries require inspection before snapshotting')
    for item in worker.files():
        if len(files) >= 200 or item.stat().st_size > 256 * 1024:
            raise ValueError('Workspace snapshot exceeds 200 files or 256 KiB per file')
        files[worker.rel(item)] = hashlib.sha256(worker.path(worker.rel(item)).read_bytes()).hexdigest()
    result = {'ok': True, 'binding': {'root': str(worker.root), 'uid': os.getuid(),
        'machine_id': Path('/etc/machine-id').read_text().strip(),
        'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=worker.root, text=True).strip()}, 'files': files}
    if len(json.dumps(result).encode()) > 20000:
        raise ValueError('Workspace snapshot exceeds 20000 bytes')
except (api['WorkerError'], OSError, ValueError, subprocess.SubprocessError) as exc:
    result = {'ok': False, 'error': str(exc)}
print(json.dumps(result))
"""

class SSHWorker:
    operation_protocol = 1

    def preflight(self):
        from .preflight import check
        return check(self)

    def __init__(self, config_path, workspace=None):
        self.workspace = workspaces.identifier(workspace) if workspace is not None else None
        config = read_json(config_path)
        if isinstance(config, dict) and any(key in config for key in ('model', 'api_key', 'tools', 'max_turns', 'max_tools', 'context_chars')):
            raise HornetError('worker.json contains model or agent settings. It holds only the pinned SSH connection; '
                              'select models with hive.json/--agent or --model, and supply credentials through HORNET_API_KEY')
        if not isinstance(config, dict) or config.get("host") != "127.0.0.1":
            raise HornetError("Coding worker must use the dedicated localhost VM connection")
        self.port = config.get("port")
        self.user = config.get("user")
        if type(self.port) is not int or not 1024 <= self.port <= 65535 or not isinstance(self.user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", self.user):
            raise HornetError("Invalid worker account or localhost port")
        self.identity = Path(config.get("identity_file", "")).expanduser().resolve()
        self.known_hosts = Path(config.get("known_hosts_file", "")).expanduser().resolve()
        if not self.identity.is_file() or not self.known_hosts.is_file():
            raise HornetError("Worker identity and pinned known-hosts files must exist")
        public_binding = {key: config.get(key) for key in ("host", "port", "user", "vm_uuid")}
        public_binding["known_hosts_sha256"] = hashlib.sha256(self.known_hosts.read_bytes()).hexdigest()
        self.connection_binding = hashlib.sha256(json.dumps(public_binding, sort_keys=True).encode()).hexdigest()
        windows_ssh = Path("C:/Windows/System32/OpenSSH/ssh.exe")
        self.ssh = str(windows_ssh) if windows_ssh.is_file() else shutil.which("ssh")
        if not self.ssh:
            raise HornetError("OpenSSH client is unavailable")

    def call(self, tool, arguments=None):
        if tool == "run_tests":
            return run_suite(self._rpc)
        if tool == "snapshot":
            reply = self._rpc("run_command", {"argv": ["python3", "-c", SNAPSHOT_SCRIPT]})
            if not reply.get("ok"):
                return reply
            try:
                result = json.loads(reply["output"])
            except (ValueError, KeyError, TypeError):
                raise HornetError("Worker returned an invalid workspace snapshot") from None
            if not isinstance(result, dict):
                raise HornetError("Worker returned an invalid workspace snapshot")
            result.setdefault("binding", {})["connection"] = self.connection_binding
            return result
        if tool == "read_file" and isinstance(arguments, dict):
            return self._rpc(tool, dict(arguments, max_lines=arguments.get("max_lines", 80)))
        if tool == "edit_file":
            return self._edit_file(arguments)
        if tool == "run_python":
            if (not isinstance(arguments, dict) or set(arguments) != {"code"}
                    or not isinstance(arguments["code"], str)
                    or not 1 <= len(arguments["code"]) <= 4000):
                return {"ok": False, "error": "run_python requires 1-4000 characters of Python code"}
            return self._rpc("run_command", {"argv": ["python3", "-c", arguments["code"]]})
        return self._rpc(tool, arguments)

    def _edit_file(self, arguments):
        fields = {"path", "old_text", "new_text", "expected_sha256"}
        if (not isinstance(arguments, dict) or set(arguments) != fields
                or any(not isinstance(arguments[k], str) for k in fields)
                or not arguments["old_text"]):
            return {"ok": False, "error": "edit_file requires path, nonempty old_text, new_text and the current SHA-256"}
        if not re.fullmatch(r"[0-9a-f]{64}", arguments["expected_sha256"]):
            return {"ok": False, "error": "Invalid expected_sha256: got {} characters; exactly 64 lowercase hex characters are required, including leading zeros. No edit ran. Call read_file now and copy its current sha256 string exactly before editing.".format(len(arguments["expected_sha256"]))}
        payload = json.dumps(arguments, ensure_ascii=False)
        if arguments["old_text"] == arguments["new_text"]:
            return {"ok": False, "unchanged": True, "error": "The old and new blocks are identical. No edit is needed here; inspect and finish the remaining ticket work."}
        if len(payload.encode("utf-8")) > 3500:
            return {"ok": False, "error": "Use a smaller source block; edit_file arguments exceed 3500 bytes"}
        # Only data enters argv. The fixed program and all file access run in
        # the guest, using the root-owned worker's path/hash/atomic-write rules.
        reply = self._rpc("run_command", {"argv": ["python3", "-c", EDIT_SCRIPT, payload]})
        if not reply.get("ok"):
            return reply
        try:
            result = json.loads(reply["output"])
        except (ValueError, KeyError, TypeError):
            raise HornetError("Worker returned invalid edit_file data") from None
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            raise HornetError("Worker edit_file response is missing its status")
        return result

    def fork_workspace(self, expected):
        return workspaces.fork(self, expected)

    def operation(self, request):
        from . import operation_worker
        operation_worker.validate(request)
        source = Path(operation_worker.__file__).read_bytes()
        data = json.dumps(dict(request, workspace=self.workspace), ensure_ascii=False)
        program = base64.b64encode(zlib.compress(source)).decode('ascii')
        parts = workspaces.chunks(program)
        arguments = [*parts, *workspaces.chunks(data)]
        if len(arguments) > 25:
            raise HornetError('Operation request exceeds the bounded transport size; use a smaller edit')
        loader = "import sys,json,base64,zlib\nn=int(sys.argv[1]);scope={}\nexec(zlib.decompress(base64.b64decode(''.join(sys.argv[2:2+n]))),scope)\nscope['main'](json.loads(''.join(sys.argv[2+n:])))"
        reply = self._wire('run_command', {'argv': ['python3', '-c', loader, str(len(parts)), *arguments], 'timeout': 120})
        return workspaces.receive(self, reply)

    def _rpc(self, tool, arguments=None):
        if self.workspace is None:
            return self._wire(tool, arguments)
        workspaces.identifier(self.workspace)
        payload = json.dumps({'tool': tool, 'arguments': arguments or {}}, ensure_ascii=False)
        reply = self._wire('run_command', {'argv': ['python3', '-c', workspaces.DISPATCH,
            self.workspace, *workspaces.chunks(payload)], 'timeout': 120})
        return workspaces.receive(self, reply)

    def _wire(self, tool, arguments=None):
        command = [self.ssh, "-T", "-i", str(self.identity), "-p", str(self.port),
                   "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                   "-o", "UserKnownHostsFile=" + str(self.known_hosts), "-o", "ConnectTimeout=5",
                   "-o", "PasswordAuthentication=no", "-o", "ClearAllForwardings=yes",
                   "-o", "LogLevel=ERROR", self.user + "@127.0.0.1", "hornet-worker"]
        data = json.dumps({"tool": tool, "arguments": arguments or {}}, ensure_ascii=False)
        try:
            result = subprocess.run(command, input=data + "\n", capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=140)
        except subprocess.TimeoutExpired:
            raise HornetError("Worker connection exceeded its 140-second deadline") from None
        if result.returncode:
            raise HornetError("Worker connection failed (exit {}): {}".format(result.returncode, result.stderr.strip()[:800] or "no SSH diagnostic; worker may still be booting"))
        try:
            response = json.loads(result.stdout)
        except ValueError:
            raise HornetError("Worker returned an invalid JSON response") from None
        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            raise HornetError("Worker response is missing an operation status")
        return response
