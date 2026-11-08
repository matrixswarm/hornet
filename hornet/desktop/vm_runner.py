"""Run a Python project copy in the dedicated MatrixSwarm test VM."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

from .workspace import source_files, safe_path, write_json

VM_TOOL = os.environ.get('HORNET_VM_TOOL', str(Path.home() / '.local' / 'bin' / 'project-vm'))
VM_NAME = os.environ.get('HORNET_TEST_VM', 'MatrixSwarm-Test')
MAX_COPY_BYTES = 55_000
MAX_LOG_CHARS = 16_000

# This program receives an encoded file mapping and argv, writes an isolated test
# copy, then starts it without a shell. Logs remain on the VM as well as the host.
REMOTE_PROGRAM = '''import base64,json,os,pathlib,shutil,signal,subprocess,sys
payload=json.loads(base64.b64decode(sys.argv[1]))
root=pathlib.Path('/srv/projects/test-results/hornet-desktop-runs') / payload['run_id']
root.mkdir(parents=True,exist_ok=False)
for name,encoded in payload['files'].items():
 p=root.joinpath(*pathlib.PurePosixPath(name).parts)
 p.parent.mkdir(parents=True,exist_ok=True)
 p.write_bytes(base64.b64decode(encoded))
env=dict(os.environ)
env['PYTHONPATH']=str(root/'src')
argv=payload['argv']
virtual_display = len(argv) >= 3 and argv[1:3] == ['-m', 'unittest'] and bool(shutil.which('xvfb-run')) and not env.get('DISPLAY')
if virtual_display:
 argv=['xvfb-run', '-a'] + argv
with (root/'hornet-stdout.log').open('wb') as out, (root/'hornet-stderr.log').open('wb') as err:
 proc=subprocess.Popen(argv,cwd=root,env=env,stdout=out,stderr=err,start_new_session=True)
 try:
  code=proc.wait(timeout=60)
  timed_out=False
 except subprocess.TimeoutExpired:
  os.killpg(proc.pid,signal.SIGKILL)
  proc.wait()
  code=proc.returncode
  timed_out=True
result={'exit_code':code,'timed_out':timed_out,'vm_directory':str(root),'virtual_display':virtual_display}
for label in ('stdout','stderr'):
 p=root/('hornet-'+label+'.log')
 with p.open('rb') as f:
  f.seek(max(0,p.stat().st_size-16000))
  result[label]=f.read().decode('utf-8',errors='replace')
 result[label+'_truncated']=p.stat().st_size>16000
print('HORNET_RUN_RESULT='+json.dumps(result))
'''


def run_python(ws, project, revision, command):
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValueError('Could not read the run command: ' + str(exc)) from exc
    if not argv or argv[0] not in ('python', 'python3'):
        raise ValueError('Enter a Python command beginning with python3.')
    argv[0] = 'python3'
    if len(argv) > 30 or sum(map(len, argv)) > 4000:
        raise ValueError('The run command is too long.')
    root = ws.revision(project, revision)
    from . import worker_vm
    worker_config = worker_vm.config_path(ws)
    copy_limit = worker_vm.MAX_COPY_BYTES if worker_config else MAX_COPY_BYTES
    files, hashes, total = {}, {}, 0
    for name in source_files(root):
        path = safe_path(root, name)
        if total + path.stat().st_size > copy_limit:
            raise ValueError('VM project copy exceeds its configured size limit.')
        data = path.read_bytes()
        total += len(data)
        if total > copy_limit:
            raise ValueError('VM project copy exceeds the configured limit of %s bytes.' % copy_limit)
        if worker_config and len(files) >= worker_vm.MAX_FILES:
            raise ValueError('VM project copy exceeds 2000 files.')
        files[name] = base64.b64encode(data).decode()
        hashes[name] = hashlib.sha256(data).hexdigest()
    from .vm_project import apply_template
    files, template = apply_template(ws, project, files)
    if template and not worker_config:
        raise ValueError('Project templates require the configured pinned VM worker.')
    if len(files) > worker_vm.MAX_FILES or sum(len(base64.b64decode(value)) for value in files.values()) > copy_limit:
        raise ValueError('Prepared VM project exceeds its file-count or copy-size limit.')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid.uuid4().hex[:8]
    payload = {'run_id': run_id, 'files': files, 'argv': argv}
    if worker_config:
        outcome = worker_vm.run_copy(worker_config, files, argv)
    else:
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        remote = 'python3 -c ' + shlex.quote(REMOTE_PROGRAM) + ' ' + shlex.quote(encoded)
        try:
            result = subprocess.run([VM_TOOL, 'exec', VM_NAME, remote], capture_output=True, text=True, timeout=150)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise ValueError('Could not start the test VM runner: ' + str(exc)) from exc
        records = [line.removeprefix('HORNET_RUN_RESULT=') for line in result.stdout.splitlines()
                   if line.startswith('HORNET_RUN_RESULT=')]
        if result.returncode or not records:
            detail = (result.stderr or result.stdout).strip()[-2000:]
            raise ValueError('Test VM run did not complete. ' + detail)
        outcome = json.loads(records[-1])
    if template:
        outcome['template'] = template
    outcome.update({'id': run_id, 'project': project, 'revision': revision,
                    'command': argv, 'source_sha256': hashes,
                    'created': datetime.now(timezone.utc).isoformat()})
    directory = ws.project(project) / 'runs'
    directory.mkdir(exist_ok=True)
    write_json(directory / (run_id + '.json'), outcome)
    return outcome


def latest_run(ws, project, revision):
    directory = ws.project(project) / 'runs'
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob('*.json'), reverse=True):
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('revision') == revision:
            return data
    return None


def run_hint(data):
    """Recognize a known environment failure without claiming a code diagnosis."""
    stderr = data.get('stderr', '')
    if '_tkinter.TclError' in stderr and 'no display name' in stderr and 'DISPLAY' in stderr:
        return ('The Test VM run has no graphical display. A Tkinter window cannot open '
                'in this headless session; this alone does not establish a problem in '
                'the project code. Use a graphical VM session for interactive use or '
                'a virtual display for automated GUI tests.')
    return None


def diagnostic_for_mission(ws, project, revision):
    data = latest_run(ws, project, revision)
    if not data:
        raise ValueError('No VM run exists for this revision. Run it first.')
    root = ws.revision(project, revision)
    current = {name: hashlib.sha256(safe_path(root, name).read_bytes()).hexdigest()
               for name in source_files(root)}
    if current != data.get('source_sha256'):
        raise ValueError('Files changed after the last VM run. Run this revision again.')
    from .vm_project import apply_template
    current_files = {name: base64.b64encode(safe_path(root, name).read_bytes()).decode() for name in source_files(root)}
    _, template = apply_template(ws, project, current_files)
    if template != data.get('template'):
        raise ValueError('VM template or overlay settings changed after the last run. Run this revision again.')
    diagnostic = {key: data[key] for key in ('id', 'command', 'exit_code', 'timed_out',
                                             'stdout', 'stderr', 'stdout_truncated', 'stderr_truncated')}
    if run_hint(data):
        diagnostic['environment_hint'] = run_hint(data)
    return diagnostic
