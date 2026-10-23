"""Create a fresh Windows or Linux VirtualBox demo worker; never adopt an existing VM."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import uuid
import zipfile


def run(arguments, timeout=120):
    result = subprocess.run(list(map(str, arguments)), capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def host_tools(vbox=None, platform=None):
    platform = sys.platform if platform is None else platform
    if platform == 'win32':
        sshkey = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/OpenSSH/ssh-keygen.exe'
        virtualbox = Path(vbox) if vbox else Path('C:/Program Files/Oracle/VirtualBox/VBoxManage.exe')
        windows = True
    elif platform.startswith('linux'):
        sshkey = Path(shutil.which('ssh-keygen') or '/nonexistent/ssh-keygen')
        virtualbox = Path(vbox) if vbox else Path(shutil.which('VBoxManage') or '/nonexistent/VBoxManage')
        windows = False
    else:
        raise ValueError('This provisioning helper supports Windows and Linux hosts')
    if not sshkey.is_file() or not virtualbox.is_file():
        raise ValueError('Install the OpenSSH client and VirtualBox on this host; --vbox can select VBoxManage')
    return sshkey, virtualbox, windows


def restrict_private(directory, windows):
    if windows:
        account = run(['whoami']).strip()
        run(['icacls.exe', directory, '/inheritance:r', '/grant:r', account + ':(OI)(CI)F'])
    else:
        directory.chmod(0o700)


def clone_image(vbox, image, vm_dir, expected_hash):
    # VirtualBox may assign a UUID to its input VMDK. Never register or mutate
    # the user's verified download; only a newly created private staging copy.
    staged = vm_dir / 'source-image.vmdk'
    with image.open('rb') as source, staged.open('xb') as destination:
        shutil.copyfileobj(source, destination, length=1024 * 1024)
    if sha256(staged) != expected_hash:
        raise RuntimeError('Image changed during copy; staged image retained for inspection')
    disk = vm_dir / 'worker.vdi'
    run([vbox, 'clonemedium', 'disk', staged, disk, '--format', 'VDI'], timeout=300)
    run([vbox, 'closemedium', 'disk', staged])
    staged.unlink()  # Only our new, successfully unregistered staging file.
    return disk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--image', required=True, type=Path)
    parser.add_argument('--image-sha256', required=True)
    parser.add_argument('--vm-parent', required=True, type=Path)
    parser.add_argument('--name', default='hornet-first-goal')
    parser.add_argument('--port', type=int, default=2224)
    parser.add_argument('--memory', type=int, default=2048)
    parser.add_argument('--vbox', type=Path, help='VBoxManage path; detected automatically on Windows/Linux')
    args = parser.parse_args()
    try:
        sshkey, args.vbox, windows = host_tools(args.vbox)
    except ValueError as exc:
        parser.error(str(exc))
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,40}', args.name):
        parser.error('Use a short lowercase VM name containing letters, digits or hyphens')
    if not 1024 <= args.port <= 65535 or not 1024 <= args.memory <= 8192:
        parser.error('Use a port from 1024-65535 and memory from 1024-8192 MiB')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', args.image_sha256):
        parser.error('Supply the full SHA-256 from the Ubuntu image checksum list')
    try:
        import pycdlib
    except ImportError:
        parser.error('Install the setup dependency: python -m pip install pycdlib==1.14.0')
    kit = Path(__file__).resolve().parent.parent
    project = args.project.resolve()
    image = args.image.resolve(strict=True)
    vm_parent = args.vm_parent.resolve()
    private = project / 'private'
    config_path = project / 'worker.json'
    if not (project / '.hornet.json').is_file():
        parser.error('Run first_run.py to create the project first')
    if config_path.exists() or private.exists() or (vm_parent / args.name).exists():
        parser.error('Worker files or VM directory already exist; preserve them and use a new project/name')
    if ('"' + args.name + '"') in run([args.vbox, 'list', 'vms']):
        parser.error('VM name is already registered; choose a new name')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    if sha256(image) != args.image_sha256.lower():
        parser.error('Ubuntu image checksum mismatch; no VM was created')
    worker_source = kit / 'worker/worker.py'
    source = kit / 'examples/demo-source'
    assert worker_source.is_file() and (source / 'calculator.py').is_file()
    private.mkdir(mode=0o700)
    restrict_private(private, windows)
    identity = private / 'worker_ed25519'
    hostkey = private / 'host_ed25519'
    for key in (identity, hostkey):
        run([sshkey, '-q', '-t', 'ed25519', '-N', '', '-f', key])
    public = identity.with_suffix('.pub').read_text().strip()
    hostpublic = hostkey.with_suffix('.pub').read_text().strip()
    known_hosts = private / 'known_hosts'
    known_hosts.write_text('[127.0.0.1]:{} {}\n'.format(args.port, ' '.join(hostpublic.split()[:2])), encoding='utf-8')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*.py')):
            if path.is_symlink():
                raise RuntimeError('Linked demo files are not accepted')
            archive.writestr(path.relative_to(source).as_posix(), path.read_bytes())
    bootstrap = '''import os,pwd,subprocess,zipfile
from pathlib import Path
workspace=Path('/home/hornet/workspace/hornet_cli')
workspace.mkdir(parents=True,exist_ok=False)
with zipfile.ZipFile('/opt/hornet-worker/source.zip') as archive:
    for entry in archive.infolist():
        relative=Path(entry.filename)
        if relative.is_absolute() or '..' in relative.parts: raise RuntimeError('Invalid archive path')
        target=workspace/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(archive.read(entry))
(workspace/'.gitignore').write_text('__pycache__/\\n*.pyc\\n')
user=pwd.getpwnam('hornet')
os.chown('/home/hornet/workspace',user.pw_uid,user.pw_gid)
for directory,dirs,files in os.walk(workspace):
    os.chown(directory,user.pw_uid,user.pw_gid)
    for name in files: os.chown(Path(directory)/name,user.pw_uid,user.pw_gid)
def git(*args): subprocess.run(['runuser','-u','hornet','--','git','-c','core.hooksPath=/dev/null','-C',str(workspace),*args],check=True)
git('init');git('add','--all')
git('-c','user.name=Hornet demo','-c','user.email=hornet@localhost','-c','commit.gpgsign=false','commit','-m','First-goal baseline')
Path('/opt/hornet-worker/ready').write_text('ready\\n')
subprocess.run(['systemctl','enable','--now','ssh'],check=True)
'''

    def file(path, content, mode='0600'):
        raw = content.encode('utf-8') if isinstance(content, str) else content
        return {'path': path, 'encoding': 'b64', 'content': base64.b64encode(raw).decode('ascii'),
                'permissions': mode, 'owner': 'root:root'}

    cloud = {
        'hostname': args.name, 'manage_etc_hosts': True,
        'users': [{'name': 'hornet', 'shell': '/bin/bash', 'lock_passwd': True,
                   'ssh_authorized_keys': ['restrict,command="/usr/bin/python3 -I /opt/hornet-worker/worker.py" ' + public]}],
        'disable_root': True, 'ssh_pwauth': False,
        'ssh_keys': {'ed25519_private': hostkey.read_text(), 'ed25519_public': hostpublic},
        'package_update': False, 'package_upgrade': False,
        'write_files': [file('/opt/hornet-worker/worker.py', worker_source.read_bytes(), '0755'),
                        file('/opt/hornet-worker/source.zip', buffer.getvalue()),
                        file('/opt/hornet-worker/bootstrap.py', bootstrap, '0700')],
        'runcmd': [['/usr/bin/python3', '/opt/hornet-worker/bootstrap.py']],
    }
    data = ('#cloud-config\n' + json.dumps(cloud)).encode('utf-8')
    metadata = json.dumps({'instance-id': 'hornet-' + uuid.uuid4().hex, 'local-hostname': args.name}).encode()
    seed = private / 'seed.iso'
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge='1.09', vol_ident='CIDATA')
    iso.add_fp(io.BytesIO(data), len(data), iso_path='/USERDATA.;1', rr_name='user-data', joliet_path='/user-data')
    iso.add_fp(io.BytesIO(metadata), len(metadata), iso_path='/METADATA.;1', rr_name='meta-data', joliet_path='/meta-data')
    iso.write(str(seed))
    iso.close()
    if not windows:
        for private_file in private.iterdir():
            if private_file.is_file():
                private_file.chmod(0o600)
    vm_parent.mkdir(parents=True, exist_ok=True)
    run([args.vbox, 'createvm', '--name', args.name, '--ostype', 'Ubuntu_64', '--basefolder', vm_parent, '--register'])
    vm_dir = vm_parent / args.name
    disk = clone_image(args.vbox, image, vm_dir, args.image_sha256.lower())
    run([args.vbox, 'modifymedium', 'disk', disk, '--resize', '16384'])
    run([args.vbox, 'modifyvm', args.name, '--memory', args.memory, '--cpus', '1', '--ioapic', 'on', '--vram', '16',
         '--boot1', 'disk', '--boot2', 'dvd', '--boot3', 'none', '--boot4', 'none', '--nic1', 'nat',
         '--nictype1', '82540EM', '--cableconnected1', 'on', '--audio-enabled', 'off', '--clipboard-mode', 'disabled',
         '--draganddrop', 'disabled', '--natpf1', 'hornet-worker-ssh,tcp,127.0.0.1,{},,22'.format(args.port)])
    run([args.vbox, 'storagectl', args.name, '--name', 'SATA', '--add', 'sata', '--controller', 'IntelAhci'])
    for port, kind, medium in [(0, 'hdd', disk), (1, 'dvddrive', seed)]:
        run([args.vbox, 'storageattach', args.name, '--storagectl', 'SATA', '--port', port, '--device', '0', '--type', kind, '--medium', medium])
    info = run([args.vbox, 'showvminfo', args.name, '--machinereadable'])
    vm_uuid = re.search(r'^UUID="([a-fA-F0-9-]+)"$', info, re.MULTILINE).group(1)
    config = {'host': '127.0.0.1', 'port': args.port, 'user': 'hornet', 'vm_name': args.name, 'vm_uuid': vm_uuid,
              'identity_file': str(identity), 'known_hosts_file': str(known_hosts)}
    config_path.write_text(json.dumps(config, indent=2), encoding='utf-8')
    print(run([args.vbox, 'startvm', vm_uuid, '--type', 'headless']), flush=True)
    print('Worker configuration: ' + str(config_path))
    print('Wait for first boot, then run hornet worker-status as shown in WORKER-SETUP.md.')
    print('Keep private/ and this VM for resume. Never distribute private/ or the seed ISO.')


if __name__ == '__main__':
    main()
