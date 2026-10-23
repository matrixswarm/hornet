"""Verify the fixed VM deployment before a coding turn or operation."""
import hashlib
import json
from pathlib import Path


BASELINE = '/home/hornet/workspace/hornet_cli'
GOALS = '/home/hornet/workspace/goals/'
SCRIPT = '''import hashlib,json,os,subprocess
from pathlib import Path
worker=Path('/opt/hornet-worker/worker.py')
text=worker.read_text(encoding='utf-8-sig')
head=subprocess.run(['git','rev-parse','--verify','HEAD'],capture_output=True,text=True,timeout=10)
top=subprocess.run(['git','rev-parse','--show-toplevel'],capture_output=True,text=True,timeout=10)
print(json.dumps({'root':str(Path.cwd().resolve()),'git_head_valid':head.returncode==0,
 'git_root':str(Path(top.stdout.strip()).resolve()) if top.returncode==0 else None,
 'worker_owner_uid':worker.stat().st_uid,'worker_writable':os.access(worker,os.W_OK),
 'worker_linked':any(p.is_symlink() for p in (worker,*worker.parents)),
 'worker_sha256':hashlib.sha256(('\\n'.join(text.splitlines())+'\\n').encode()).hexdigest()}))'''


def source_hash():
    text = Path(__file__).with_name('worker.py').read_text(encoding='utf-8-sig')
    return hashlib.sha256(('\n'.join(text.splitlines()) + '\n').encode()).hexdigest()


def check(worker):
    status = worker.call('status')
    expected = GOALS + worker.workspace if worker.workspace else BASELINE
    checks = {'status': status.get('ok') is True,
              'account': worker.user == 'hornet',
              'linux': status.get('platform') == 'linux',
              'non_root': type(status.get('uid')) is int and status['uid'] > 0,
              'workspace_root': status.get('root') == expected,
              'worker_ready': status.get('ready') is True}
    result = dict(status, ok=False, ready=False, expected_root=expected, preflight_checks=checks)
    if all(checks.values()):
        reply = worker.call('run_command', {'argv': ['python3', '-c', SCRIPT], 'timeout': 30})
        try:
            if reply.get('ok') is not True:
                raise ValueError()
            facts = json.loads(reply['output'])
            checks.update(workspace_root=facts['root'] == expected,
                          git_root=facts['git_root'] == expected,
                          git_head=facts['git_head_valid'] is True,
                          worker_root_owned=type(facts['worker_owner_uid']) is int and facts['worker_owner_uid'] == 0,
                          worker_protected=facts['worker_writable'] is False and facts['worker_linked'] is False,
                          worker_code=facts['worker_sha256'] == source_hash())
        except (ValueError, KeyError, TypeError, OSError):
            checks['deployment_probe'] = False
    failed = [key for key, passed in checks.items() if not passed]
    if failed:
        result['error'] = ('Worker preflight failed: ' + ', '.join(failed) + '. Expected the provisioned hornet account at ' +
                           expected + ' with matching root-owned worker code and a valid Git HEAD. '
                           'Use a fresh worker from the matching starter; preserve existing runs and VM for diagnosis.')
    else:
        result.update(ok=True, ready=True)
    return result
