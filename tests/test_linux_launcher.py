"""Check transfer failures and setup refusals without a network or VM."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('linux_launcher_fixture', ROOT / 'starter/tools/start_linux.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_transfer_manifest_rejects_empty_corrupt_and_missing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            manifest = kit / 'SHA256SUMS'
            manifest.write_text('')
            with self.assertRaisesRegex(RuntimeError, 'Empty'):
                launcher.verify_kit(kit)
            payload = b'preview fixture'
            manifest.write_text(hashlib.sha256(payload).hexdigest() + '  wheel.whl\n')
            with self.assertRaisesRegex(RuntimeError, 'Missing'):
                launcher.verify_kit(kit)
            (kit / 'wheel.whl').write_bytes(b'corrupt')
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                launcher.verify_kit(kit)
            (kit / 'wheel.whl').write_bytes(payload)
            launcher.verify_kit(kit)

    def test_manifest_rejects_parent_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            (kit / 'SHA256SUMS').write_text('0' * 64 + '  ../outside.whl\n')
            with self.assertRaisesRegex(RuntimeError, 'Invalid'):
                launcher.verify_kit(kit)

    def test_download_requires_pinned_hash_before_final_name(self):
        payload = b'fixture cloud image'
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            with patch.object(launcher.request, 'urlopen', return_value=io.BytesIO(payload)), \
                    patch.object(launcher, 'CLOUD_SHA256', hashlib.sha256(payload).hexdigest()), \
                    contextlib.redirect_stdout(io.StringIO()):
                target = launcher.download_image(kit)
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(target.with_suffix('.vmdk.part').exists())

    def test_bad_download_is_retained_without_final_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            with patch.object(launcher.request, 'urlopen', return_value=io.BytesIO(b'bad image')), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                launcher.download_image(kit)
            image = kit / 'ubuntu-image/jammy-server-cloudimg-amd64.vmdk'
            self.assertFalse(image.exists())
            self.assertEqual(image.with_suffix('.vmdk.part').read_bytes(), b'bad image')
            with patch.object(launcher.request, 'urlopen') as request, \
                    self.assertRaisesRegex(RuntimeError, 'incomplete'):
                launcher.download_image(kit)
            request.assert_not_called()

    def test_existing_image_is_not_overwritten_or_downloaded_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            image = kit / 'ubuntu-image/jammy-server-cloudimg-amd64.vmdk'
            image.parent.mkdir()
            image.write_bytes(b'keep')
            with patch.object(launcher.request, 'urlopen') as request:
                self.assertEqual(launcher.download_image(kit), image)
            self.assertEqual(image.read_bytes(), b'keep')
            request.assert_not_called()

    def test_existing_setup_stops_before_manifest_network_or_commands(self):
        for existing in ('.venv', 'preview-env', 'my-first-goal'):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temporary:
                kit = Path(temporary)
                (kit / existing).mkdir()
                with patch.object(launcher, '__file__', str(kit / 'tools/start_linux.py')), \
                        patch.object(launcher.sys, 'platform', 'linux'), \
                        patch.object(launcher.os, 'geteuid', return_value=1000, create=True), \
                        patch('sys.argv', ['start_linux.py', '--download-image']), \
                        patch.object(launcher, 'verify_kit') as verify, \
                        patch.object(launcher, 'run') as run, \
                        patch.object(launcher, 'download_image') as download, \
                        self.assertRaisesRegex(RuntimeError, 'already exists'):
                    launcher.main()
                verify.assert_not_called()
                run.assert_not_called()
                download.assert_not_called()

    def test_root_or_non_linux_host_stops_before_setup(self):
        for platform, uid in [('linux', 0), ('win32', 1000)]:
            with self.subTest(platform=platform, uid=uid), \
                    patch.object(launcher.sys, 'platform', platform), \
                    patch.object(launcher.os, 'geteuid', return_value=uid, create=True), \
                    patch('sys.argv', ['start_linux.py', '--download-image']), \
                    patch.object(launcher, 'verify_kit') as verify, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                launcher.main()
            verify.assert_not_called()

    def test_probe_waits_past_false_ready_and_malformed_responses(self):
        replies = [SimpleNamespace(returncode=0, stdout='[]'),
                   SimpleNamespace(returncode=0, stdout='{"ok":true,"ready":false}'),
                   SimpleNamespace(returncode=0, stdout='{"ok":true,"ready":true}')]
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            with patch.object(launcher.subprocess, 'run', side_effect=replies) as run, \
                    patch.object(launcher.time, 'sleep') as sleep, contextlib.redirect_stdout(io.StringIO()):
                launcher.wait_for_worker(Path('python'), kit, kit / 'project')
            self.assertEqual(run.call_count, 3)
            self.assertEqual(sleep.call_count, 2)
            rows = [json.loads(path.read_text()) for path in sorted((kit / 'setup-probes').rglob('*.json'))]
            self.assertFalse(rows[0]['ok'])
            self.assertFalse(rows[1]['ready'])
            self.assertTrue(rows[2]['ready'])
            self.assertTrue(all('worker-status' in call.args[0] for call in run.call_args_list))

    def test_probe_attempts_preserve_previous_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            with patch.object(launcher.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout='{"ok":true,"ready":true}')), contextlib.redirect_stdout(io.StringIO()):
                launcher.wait_for_worker(Path('python'), kit, kit / 'project')
                first = {p: p.read_bytes() for p in (kit / 'setup-probes').rglob('*.json')}
                launcher.wait_for_worker(Path('python'), kit, kit / 'project')
            self.assertEqual(len(list((kit / 'setup-probes').rglob('*.json'))), 2)
            self.assertTrue(all(p.read_bytes() == data for p, data in first.items()))

    def test_release_contents_verify_required_files_and_reject_corruption(self):
        names = ['tools/start_linux.py', 'tools/prepare_worker.py', 'tools/first_run.py',
                 'worker/worker.py', 'examples/demo-goal.txt', 'examples/demo-source/calculator.py',
                 'matrixswarm_hornet-' + launcher.VERSION + '-py3-none-any.whl',
                 'dependencies/pycdlib-1.14.0-py2.py3-none-any.whl', 'source/hornet_cli/.github/workflows/checks.yml']
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary).resolve()
            records = {}
            for name in names:
                target = kit / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'fixture')
                records[name] = {'sha256': hashlib.sha256(b'fixture').hexdigest(), 'bytes': 7}
            manifest = kit / 'CONTENTS.json'
            manifest.write_text(json.dumps(records))
            launcher.verify_kit(kit)
            (kit / names[0]).write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                launcher.verify_kit(kit)
            (kit / names[0]).write_bytes(b'fixture')
            records['../escape'] = records[names[0]]
            manifest.write_text(json.dumps(records))
            with self.assertRaisesRegex(RuntimeError, 'Invalid'):
                launcher.verify_kit(kit)
            manifest.write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'missing required'):
                launcher.verify_kit(kit)
            manifest.write_text('{"duplicate":{},"duplicate":{}}')
            with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
                launcher.verify_kit(kit)

    def test_probe_timeout_never_claims_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            kit = Path(temporary)
            with patch.object(launcher.time, 'monotonic', side_effect=[0, 1]), \
                    patch.object(launcher.subprocess, 'run') as run, \
                    self.assertRaisesRegex(RuntimeError, 'did not pass preflight'):
                launcher.wait_for_worker(Path('python'), kit, kit / 'project', seconds=0)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
