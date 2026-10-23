"""Exercise both host paths without changing a VM or running external tools."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_worker_fixture', ROOT / 'starter/tools/prepare_worker.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def test_virtualbox_only_mutates_disposable_image_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            image, vm = base / 'download.vmdk', base / 'vm'
            image.write_bytes(b'verified cloud image')
            vm.mkdir()
            expected = hashlib.sha256(image.read_bytes()).hexdigest()
            def run(args, timeout=120):
                if 'clonemedium' in args:
                    self.assertEqual(Path(args[3]), vm / 'source-image.vmdk')
                    Path(args[3]).write_bytes(b'VirtualBox assigned UUID')
                    Path(args[4]).write_bytes(b'cloned disk')
                return ''
            with patch.object(setup, 'run', side_effect=run) as command:
                disk = setup.clone_image('VBoxManage', image, vm, expected)
            self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), expected)
            self.assertEqual(disk.read_bytes(), b'cloned disk')
            self.assertFalse((vm / 'source-image.vmdk').exists())
            self.assertEqual(command.call_args_list[-1].args[0][1:3], ['closemedium', 'disk'])

    def test_failed_clone_preserves_staging_and_original_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            image, vm = base / 'download.vmdk', base / 'vm'
            image.write_bytes(b'keep original'); vm.mkdir()
            expected = hashlib.sha256(image.read_bytes()).hexdigest()
            with patch.object(setup, 'run', side_effect=RuntimeError('clone failed')), \
                    self.assertRaisesRegex(RuntimeError, 'clone failed'):
                setup.clone_image('VBoxManage', image, vm, expected)
            self.assertEqual(image.read_bytes(), b'keep original')
            self.assertEqual((vm / 'source-image.vmdk').read_bytes(), b'keep original')
            with patch.object(setup, 'run') as command, self.assertRaises(FileExistsError):
                setup.clone_image('VBoxManage', image, vm, expected)
            command.assert_not_called()

    def test_linux_tools_come_from_path(self):
        with patch.object(setup.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name), patch.object(Path, 'is_file', return_value=True):
            key, vbox, windows = setup.host_tools(platform='linux')
        self.assertEqual(key.name, 'ssh-keygen')
        self.assertEqual(vbox.name, 'VBoxManage')
        self.assertFalse(windows)

    def test_windows_keeps_existing_tools_and_acl(self):
        with patch.object(Path, 'is_file', return_value=True):
            key, _, windows = setup.host_tools(platform='win32')
        self.assertEqual(key.name, 'ssh-keygen.exe')
        self.assertTrue(windows)
        with patch.object(setup, 'run', side_effect=['DOMAIN\\fixture\n', '']) as run:
            setup.restrict_private(Path('fixture-private'), True)
        self.assertEqual(run.call_args_list[1].args[0][0], 'icacls.exe')
        self.assertIn('DOMAIN\\fixture:(OI)(CI)F', run.call_args_list[1].args[0])

    def test_unsupported_host_is_explicit(self):
        with self.assertRaisesRegex(ValueError, 'Windows and Linux'):
            setup.host_tools(platform='darwin')

    def test_linux_seed_keys_layout_and_existing_setup_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            project = base / 'project'
            project.mkdir()
            (project / '.hornet.json').write_text('{}')
            image = base / 'ubuntu.vmdk'
            image.write_bytes(b'fixture image')
            key, vbox = base / 'ssh-keygen', base / 'VBoxManage'
            key.touch(); vbox.touch()
            entries, calls = {}, []
            class ISO:
                def new(self, **kwargs): pass
                def add_fp(self, fp, length, **kwargs): entries[kwargs['rr_name']] = fp.read()
                def write(self, path): Path(path).write_bytes(b'fixture seed ISO')
                def close(self): pass
            def run(arguments, timeout=120):
                args = list(map(str, arguments)); calls.append(args)
                if args[0] == str(key):
                    dest = Path(args[args.index('-f') + 1])
                    dest.write_text('fixture private key')
                    dest.with_suffix('.pub').write_text('ssh-ed25519 fixture-key fixture')
                if 'createvm' in args:
                    (base / 'vms' / 'hornet-linux-test').mkdir(parents=True)
                if 'showvminfo' in args:
                    return 'UUID="11111111-2222-3333-4444-555555555555"\n'
                return ''
            argv = ['prepare_worker.py', '--project', str(project), '--image', str(image),
                    '--image-sha256', hashlib.sha256(image.read_bytes()).hexdigest(),
                    '--vm-parent', str(base / 'vms'), '--name', 'hornet-linux-test']
            with patch.object(setup, 'host_tools', return_value=(key, vbox, False)), \
                    patch.object(setup, 'run', side_effect=run), \
                    patch.dict('sys.modules', {'pycdlib': SimpleNamespace(PyCdlib=ISO)}), \
                    patch('sys.argv', argv), contextlib.redirect_stdout(io.StringIO()):
                setup.main()
                before = list(calls)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    setup.main()
                self.assertEqual(calls, before)
            cloud = json.loads(entries['user-data'].decode().split('\n', 1)[1])
            account = cloud['users'][0]
            self.assertEqual(account['name'], 'hornet')
            self.assertIn('restrict,command="/usr/bin/python3 -I /opt/hornet-worker/worker.py"', account['ssh_authorized_keys'][0])
            self.assertFalse(cloud['ssh_pwauth'])
            config = json.loads((project / 'worker.json').read_text())
            self.assertEqual(config['user'], 'hornet')
            self.assertEqual(config['host'], '127.0.0.1')
            self.assertNotIn('model', config)
            self.assertNotIn('api_key', config)
            self.assertTrue((project / 'private/known_hosts').read_text().startswith('[127.0.0.1]:2224 ssh-ed25519'))
            self.assertFalse(any(part in {'poweroff', 'reset', 'unregistervm'} for call in calls for part in call))
            worker_file = next(row for row in cloud['write_files'] if row['path'].endswith('/worker.py'))
            self.assertEqual(worker_file['owner'], 'root:root')
            import base64
            self.assertEqual(base64.b64decode(worker_file['content']), (ROOT / 'hornet/coding/worker.py').read_bytes())


if __name__ == '__main__':
    unittest.main()
