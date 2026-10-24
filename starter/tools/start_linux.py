"""Prepare a fresh Linux starter environment and VM; optionally run the starter goal.

Run from an extracted signed starter kit. Download the pinned Ubuntu image or supply one.
Existing environments, projects and VMs are retained and never adopted.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
import uuid
from urllib import request


VERSION = '0.17.0'
# Ubuntu's dated SHA256SUMS lists this exact image; also used by the Windows first-user trial.
CLOUD_URL = 'https://cloud-images.ubuntu.com/jammy/20261020/jammy-server-cloudimg-amd64.vmdk'
CLOUD_SHA256 = 'eac978cac67cc40afe3785c4d78c97c77a36b16b4db57ae893a69f62de7736b2'


def run(command, cwd):
    print('Running: ' + shlex.join(list(map(str, command))), flush=True)
    result = subprocess.run(list(map(str, command)), cwd=cwd, check=True)
    return result


def verify_kit(kit):
    contents = kit / 'CONTENTS.json'
    if contents.exists():
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise RuntimeError('Duplicate starter manifest entry')
                result[key] = value
            return result
        records = json.loads(contents.read_text(encoding='utf-8'), object_pairs_hook=unique)
        required = {'tools/start_linux.py', 'tools/prepare_worker.py', 'tools/first_run.py',
                    'worker/worker.py', 'examples/demo-goal.txt', 'examples/demo-source/calculator.py',
                    'matrixswarm_hornet-' + VERSION + '-py3-none-any.whl',
                    'dependencies/pycdlib-1.14.0-py2.py3-none-any.whl'}
        if not isinstance(records, dict) or not required <= set(records):
            raise RuntimeError('Starter manifest is missing required files')
        for name, entry in records.items():
            if (not re.fullmatch(r'[A-Za-z0-9.][A-Za-z0-9._/-]*', name)
                    or any(part in {'', '.', '..'} for part in name.split('/'))
                    or not isinstance(entry, dict) or set(entry) != {'sha256', 'bytes'}
                    or not isinstance(entry['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])
                    or type(entry['bytes']) is not int or entry['bytes'] < 0):
                raise RuntimeError('Invalid starter manifest entry')
            target = kit / name
            if (not target.is_file() or any(p.is_symlink() for p in (target, *target.parents))
                    or target.stat().st_size != entry['bytes']
                    or hashlib.sha256(target.read_bytes()).hexdigest() != entry['sha256']):
                raise RuntimeError('Starter checksum mismatch or linked file: ' + name)
        return
    # Earlier unsigned recovery previews used a transfer-only SHA256SUMS file.
    lines = (kit / 'SHA256SUMS').read_text(encoding='ascii').splitlines()
    if not lines:
        raise RuntimeError('Empty preview transfer manifest')
    for line in lines:
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._/-]*)', line)
        if not match or '..' in Path(match[2]).parts:
            raise RuntimeError('Invalid preview transfer manifest')
        target = kit / match[2]
        if not target.is_file() or target.is_symlink():
            raise RuntimeError('Missing or linked preview file: ' + match[2])
        if hashlib.sha256(target.read_bytes()).hexdigest() != match[1]:
            raise RuntimeError('Preview checksum mismatch: ' + match[2])


def download_image(kit):
    directory = kit / 'ubuntu-image'
    directory.mkdir(exist_ok=True)
    target = directory / 'jammy-server-cloudimg-amd64.vmdk'
    if target.exists():
        return target
    partial = directory / 'jammy-server-cloudimg-amd64.vmdk.part'
    if partial.exists():
        raise RuntimeError('An incomplete image download exists; preserve it and use a fresh kit directory to retry')
    print('Downloading the pinned Ubuntu 22.04 cloud image (about 678 MiB).', flush=True)
    digest, total = hashlib.sha256(), 0
    with request.urlopen(CLOUD_URL, timeout=30) as response, partial.open('xb') as stream:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 1536 * 1024 * 1024:
                raise RuntimeError('Ubuntu download exceeded the size limit; partial file retained')
            stream.write(chunk)
            digest.update(chunk)
            if total // (64 * 1024 * 1024) != (total - len(chunk)) // (64 * 1024 * 1024):
                print('Downloaded %d MiB...' % (total // (1024 * 1024)), flush=True)
    if digest.hexdigest() != CLOUD_SHA256:
        raise RuntimeError('Ubuntu download checksum mismatch; partial file retained and no VM created')
    partial.rename(target)
    return target


def wait_for_worker(python, kit, project, seconds=420):
    probes = kit / 'setup-probes' / uuid.uuid4().hex
    probes.mkdir(parents=True)
    deadline = time.monotonic() + seconds
    index = 0
    command = [str(python), '-I', '-m', 'hornet', '--root', str(project), 'worker-status',
               '--worker-config', 'worker.json', '--json']
    while time.monotonic() < deadline:
        index += 1
        result = subprocess.run(command, cwd=kit, capture_output=True, text=True, timeout=140)
        try:
            status = json.loads(result.stdout)
            if not isinstance(status, dict):
                raise ValueError()
        except ValueError:
            status = {'ok': False, 'ready': False, 'error': 'Worker status did not return a JSON object', 'exit_code': result.returncode}
        (probes / ('%03d.json' % index)).write_text(json.dumps(status, indent=2) + '\n', encoding='utf-8')
        if result.returncode == 0 and status.get('ok') is True and status.get('ready') is True:
            print('WORKER PREFLIGHT PASSED', flush=True)
            return
        print('Waiting for first boot/preflight; probe %d saved in setup-probes.' % index, flush=True)
        time.sleep(5)
    raise RuntimeError('Worker did not pass preflight. Preserve this folder and VM; inspect setup-probes before another attempt.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--image-sha256')
    parser.add_argument('--download-image', action='store_true', help='Download the pinned Ubuntu 22.04 image and verify its hash')
    parser.add_argument('--name', default='hornet-first-goal')
    parser.add_argument('--port', type=int, default=2224)
    parser.add_argument('--model', default=os.environ.get('HORNET_MODEL'))
    parser.add_argument('--base-url', default='http://127.0.0.1:1234/v1')
    parser.add_argument('--run-demo', action='store_true')
    args = parser.parse_args()
    if not sys.platform.startswith('linux') or getattr(os, 'geteuid', lambda: 0)() == 0:
        parser.error('Run this on the Linux host as your normal account, without sudo')
    if args.run_demo and not args.model:
        parser.error('--run-demo requires the exact --model ID shown by LM Studio')
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,40}', args.name) or not 1024 <= args.port <= 65535:
        parser.error('Use a lowercase VM name of 3-41 letters/digits/hyphens and a port from 1024-65535')
    if args.download_image and (args.image is not None or args.image_sha256 is not None):
        parser.error('Use --download-image or your own --image/--image-sha256 pair')
    if not args.download_image and (args.image is None or not isinstance(args.image_sha256, str)
            or not re.fullmatch(r'[0-9a-fA-F]{64}', args.image_sha256)):
        parser.error('Use the full published SHA-256 for your Ubuntu image')
    kit = Path(__file__).resolve().parent.parent
    environment, project = kit / '.venv', kit / 'my-first-goal'
    if environment.exists() or project.exists() or (kit / 'preview-env').exists():
        raise RuntimeError('Starter environment/project already exists. Keep it for inspection; extract a fresh kit to start another setup.')
    if (Path.home() / 'VirtualBox VMs' / args.name).exists():
        raise RuntimeError('VM directory already exists; choose another --name. Existing files were retained.')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    verify_kit(kit)
    spec = importlib.util.spec_from_file_location('hornet_preview_prepare', kit / 'tools/prepare_worker.py')
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    _, vbox, _ = prepare.host_tools()
    registered = prepare.run([vbox, 'list', 'vms'])
    if '"' + args.name + '"' in registered:
        raise RuntimeError('VM name already exists; choose another --name. Existing VM was retained.')
    image = download_image(kit) if args.download_image else args.image.expanduser().resolve(strict=True)
    image_hash = CLOUD_SHA256 if args.download_image else args.image_sha256.lower()
    if prepare.sha256(image) != image_hash:
        raise RuntimeError('Ubuntu image checksum mismatch; no environment or VM was created')
    run([sys.executable, '-m', 'venv', environment], kit)
    python = environment / 'bin/python'
    wheel = kit / ('matrixswarm_hornet-' + VERSION + '-py3-none-any.whl')
    run([python, '-m', 'pip', '--isolated', 'install', '--no-index', '--no-deps',
         '--find-links', kit / 'dependencies', wheel, 'pycdlib==1.14.0'], kit)
    run([python, '-I', '-m', 'hornet', '--version'], kit)
    run([python, kit / 'tools/first_run.py', '--project', project], kit)
    run([python, kit / 'tools/prepare_worker.py', '--project', project,
         '--image', image, '--image-sha256', image_hash,
         '--vm-parent', Path.home() / 'VirtualBox VMs', '--name', args.name, '--port', args.port], kit)
    wait_for_worker(python, kit, project)
    demo = [str(python), '-I', '-m', 'hornet', '--root', str(project), 'autowork',
            '--worker-config', 'worker.json', '--goal-file', 'demo-goal.txt',
            '--model', args.model or 'YOUR_EXACT_MODEL_ID', '--base-url', args.base_url,
            '--protocol', 'lmstudio-json', '--max-cells', '1', '--max-turns', '30',
            '--max-tools', '40', '--cell-turns', '10', '--cell-tools', '16',
            '--context-chars', '16000', '--max-tokens', '2048', '--timeout', '120', '--json']
    (kit / 'demo-command.txt').write_text(shlex.join(demo) + '\n', encoding='utf-8')
    print('Demo command saved in demo-command.txt. Use HORNET_API_KEY in the environment if server authentication is enabled.', flush=True)
    if args.run_demo:
        run(demo, kit)
    else:
        print(shlex.join(demo), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print('SETUP STOPPED: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
