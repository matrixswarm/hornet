"""Revision copies over the existing pinned VM worker; never execute on the host."""
import base64
import hashlib
import json
import io
import zipfile
import os
import subprocess
import time
from pathlib import Path

from hornet.coding.transport import SSHWorker
from hornet.project import HornetError

MAX_COPY_BYTES = 25 * 1024 * 1024
MAX_FILES = 2000
CHUNK_BYTES = 48000

CREATE = """import json,pathlib,tempfile
root=pathlib.Path.home()/'.hornet-desktop-runs'
root.mkdir(mode=0o700,exist_ok=True)
if root.is_symlink(): raise ValueError('Linked test directory is not allowed')
p=pathlib.Path(tempfile.mkdtemp(prefix='run-',dir=root))
print(json.dumps({'directory':str(p)}))
"""
WRITE = """import base64,hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); name=sys.argv[2]; expected=sys.argv[3]
p=root.joinpath(*pathlib.PurePosixPath(name).parts)
if not p.resolve().is_relative_to(root.resolve()): raise ValueError('Path escapes test directory')
p.parent.mkdir(parents=True,exist_ok=True)
data=base64.b64decode(''.join(sys.argv[4:]),validate=True)
with p.open('xb') as f: f.write(data)
if hashlib.sha256(data).hexdigest()!=expected: raise ValueError('Transferred file hash mismatch')
print('{}')
"""
APPEND = """import base64,hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); p=root.joinpath(*pathlib.PurePosixPath(sys.argv[2]).parts)
if not p.resolve().is_relative_to(root.resolve()) or p.is_symlink(): raise ValueError('Unsafe transfer path')
if p.stat().st_size!=int(sys.argv[3]): raise ValueError('Transfer offset mismatch')
with p.open('ab') as f: f.write(base64.b64decode(''.join(sys.argv[4:]),validate=True))
print('{}')
"""
EXTRACT = """import hashlib,json,pathlib,sys,zipfile
stage=pathlib.Path(sys.argv[1]); root=stage/'project'
root.mkdir(mode=0o700,exist_ok=False)
with zipfile.ZipFile(stage/'payload.zip') as archive:
 expected=json.loads(archive.read('.hornet-copy-manifest.json'))
 for name,digest in expected.items():
  rel=pathlib.PurePosixPath(name)
  if rel.is_absolute() or '..' in rel.parts or '\\\\' in name: raise ValueError('Unsafe archive path')
  p=root.joinpath(*rel.parts)
  if not p.resolve().is_relative_to(root.resolve()): raise ValueError('Unsafe archive path')
  data=archive.read(name)
  if hashlib.sha256(data).hexdigest()!=digest: raise ValueError('Transferred file hash mismatch')
  p.parent.mkdir(parents=True,exist_ok=True)
  with p.open('xb') as f: f.write(data)
print(json.dumps({'directory':str(root)}))
"""
RUN = """import json,os,pathlib,shutil,signal,subprocess,sys
root=pathlib.Path(sys.argv[1]); argv=json.loads(sys.argv[2])
env=dict(os.environ); env['PYTHONPATH']=str(root/'src')
virtual_display=len(argv)>=3 and argv[1:3]==['-m','unittest'] and bool(shutil.which('xvfb-run')) and not env.get('DISPLAY')
if virtual_display: argv=['xvfb-run','-a']+argv
with (root/'hornet-stdout.log').open('wb') as out,(root/'hornet-stderr.log').open('wb') as err:
 p=subprocess.Popen(argv,cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=True)
 try: code=p.wait(timeout=60); timed_out=False
 except subprocess.TimeoutExpired:
  os.killpg(p.pid,signal.SIGKILL); p.wait(); code=p.returncode; timed_out=True
result={'exit_code':code,'timed_out':timed_out,'vm_directory':str(root),'virtual_display':virtual_display}
print(json.dumps(result))
"""
READ_LOG = """import base64,json,pathlib,sys
p=pathlib.Path(sys.argv[1])/('hornet-'+sys.argv[2]+'.log')
with p.open('rb') as f:
 f.seek(max(0,p.stat().st_size-16000)); data=f.read(16000)
print(json.dumps({'data':base64.b64encode(data).decode(),'truncated':p.stat().st_size>16000}))
"""


def config_path(ws):
    override = os.environ.get('HORNET_VM_WORKER_CONFIG')
    if override:
        return override
    path = ws.root / 'test-vm.json'
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    value = data.get('worker_config') if isinstance(data, dict) else None
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('test-vm.json needs an absolute worker_config path.')
    return value


def chunks(text):
    return [text[i:i+3000] for i in range(0, len(text), 3000)]


def rpc(worker, script, args=(), timeout=45):
    argv = ['python3', '-c', script, *args]
    if len(argv) > 30 or any(len(v) > 4000 for v in argv):
        raise ValueError('VM request exceeds worker transport limits.')
    result = worker.call('run_command', {'argv': argv, 'timeout': timeout})
    if not result.get('ok') or result.get('truncated') or result.get('timed_out'):
        raise ValueError('VM operation did not complete: ' + str(result.get('error') or result.get('output', ''))[-1500:])
    try:
        value = json.loads(result['output'])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError('VM returned an incomplete test result; inspect guest logs before retrying.') from exc
    if not isinstance(value, dict):
        raise ValueError('VM returned an invalid test result.')
    return value



def ready_worker(config):
    worker = SSHWorker(config)
    details = json.loads(Path(config).read_text(encoding='utf-8'))
    vbox = Path('C:/Program Files/Oracle/VirtualBox/VBoxManage.exe')
    started = False
    if os.name == 'nt' and vbox.is_file() and details.get('vm_uuid'):
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW}
        result = subprocess.run([str(vbox), 'showvminfo', details['vm_uuid'], '--machinereadable'], capture_output=True, text=True, timeout=15, **flags)
        if result.returncode:
            raise ValueError('Could not inspect the configured VirtualBox VM: ' + result.stderr[-1000:])
        if 'VMState="poweroff"' in result.stdout:
            result = subprocess.run([str(vbox), 'startvm', details['vm_uuid'], '--type', 'headless'], capture_output=True, text=True, timeout=60, **flags)
            if result.returncode:
                raise ValueError('Could not start the configured VirtualBox VM: ' + result.stderr[-1000:])
            started = True
        elif 'VMState="running"' not in result.stdout:
            raise ValueError('The configured VM is not running or powered off; resume it manually before testing.')
    deadline = time.monotonic() + (120 if started else 0)
    while True:
        try:
            status = worker.call('status')
            break
        except HornetError as exc:
            if not started or time.monotonic() >= deadline or not any(text in str(exc).lower() for text in ('connection refused', 'connection reset', 'connection timed out')):
                raise
            time.sleep(2)
    if not status.get('ok') or not status.get('ready') or status.get('platform') != 'linux' or type(status.get('uid')) is not int or status['uid'] <= 0:
        raise ValueError('The dedicated non-root Linux worker is not ready.')
    return worker


def run_copy(config, files, argv):
    try:
        worker = ready_worker(config)
        directory = rpc(worker, CREATE)['directory']
        if not isinstance(directory, str) or not directory.startswith('/home/hornet/.hornet-desktop-runs/run-'):
            raise ValueError('Unexpected VM test directory.')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            hashes = {}
            for name, encoded in files.items():
                data = base64.b64decode(encoded, validate=True)
                archive.writestr(name, data)
                hashes[name] = hashlib.sha256(data).hexdigest()
            archive.writestr('.hornet-copy-manifest.json', json.dumps(hashes))
        data = buffer.getvalue()
        first = data[:CHUNK_BYTES]
        rpc(worker, WRITE, [directory, 'payload.zip', hashlib.sha256(first).hexdigest(), *chunks(base64.b64encode(first).decode())])
        for offset in range(CHUNK_BYTES, len(data), CHUNK_BYTES):
            rpc(worker, APPEND, [directory, 'payload.zip', str(offset), *chunks(base64.b64encode(data[offset:offset+CHUNK_BYTES]).decode())])
        extracted = rpc(worker, EXTRACT, [directory])
        if extracted.get('directory') != directory + '/project':
            raise ValueError('Unexpected extracted project directory.')
        directory = extracted['directory']
        outcome = rpc(worker, RUN, [directory, json.dumps(argv)], timeout=90)
        for label in ('stdout', 'stderr'):
            log = rpc(worker, READ_LOG, [directory, label])
            outcome[label] = base64.b64decode(log['data'], validate=True).decode('utf-8', errors='replace')
            outcome[label + '_truncated'] = log['truncated']
        outcome['transport'] = 'pinned-ssh-worker'
        return outcome
    except HornetError as exc:
        raise ValueError(str(exc)) from exc
