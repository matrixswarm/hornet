"""Standalone sessions persist without giving coding cells host-side edits."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from hornet.coding import registry as cell_registry
from hornet.registry.proxy_resolver import ProxyResolver
from hornet.registry.registry_manager import RegistryManager

URI = 'files.text.session_edit'


class SessionEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / 'app.py'
        self.path.write_bytes(b'\xef\xbb\xbfx = 1\r\ny = 2\r\n')
        self.proxy = ProxyResolver(registry=RegistryManager(allowed_uris=[URI]))

    def call(self, action, status='SUCCESS', **kwargs):
        result = self.proxy.execute(URI, {'root': str(self.root), 'path': 'app.py', 'action': action, **kwargs})
        self.assertEqual(result['status'], status, result)
        return result['proxy_response']

    def begin(self):
        return self.call('begin', source_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())

    @staticmethod
    def credentials(result):
        return {'session': result['session'], 'revision': result['revision']}

    def test_opt_in_and_no_cell_adapter(self):
        with self.assertRaises(ValueError):
            RegistryManager().details(URI)
        with self.assertRaises(ValueError):
            cell_registry.manager([URI])
        self.assertEqual(RegistryManager(allowed_uris=[URI]).doctor()['issues'], [])

    def test_restart_multiple_edits_validation_and_commit(self):
        first = self.begin()
        one = self.call('replace', **self.credentials(first), target_text='x = 1', new_text='x =')
        self.assertEqual(self.path.read_bytes(), b'\xef\xbb\xbfx = 1\r\ny = 2\r\n')
        self.call('validate', status='ERROR', **self.credentials(one))
        # A new resolver simulates a process restart; the session is not in memory.
        self.proxy = ProxyResolver(registry=RegistryManager(allowed_uris=[URI]))
        shown = self.call('show', **self.credentials(one))
        self.assertIn('x =\r\n', shown['text'])
        two = self.call('replace', **self.credentials(one), target_text='x =\r\n', new_text='x = 3\r\n')
        self.call('validate', **self.credentials(two))
        self.call('commit', **self.credentials(two))
        self.assertEqual(self.path.read_bytes(), b'\xef\xbb\xbfx = 3\r\ny = 2\r\n')
        self.assertTrue(Path(first['sidecar']).is_file())
        self.call('commit', status='ERROR', **self.credentials(two))

    def test_external_change_after_show_or_stage_never_refreshes_baseline(self):
        first = self.begin()
        staged = self.call('replace', **self.credentials(first), target_text='x = 1', new_text='x = 9')
        sidecar = Path(first['sidecar'])
        before = sidecar.read_bytes()
        self.path.write_text('x = 50\n', encoding='utf-8')
        shown = self.call('show', **self.credentials(staged))
        self.assertFalse(shown['source_unchanged'])
        self.call('replace', status='ERROR', **self.credentials(staged), target_text='y = 2', new_text='y = 8')
        self.call('commit', status='ERROR', **self.credentials(staged))
        self.assertEqual(self.path.read_text(), 'x = 50\n')
        self.assertEqual(sidecar.read_bytes(), before)

    def test_revision_binding_and_independent_sessions(self):
        one, two = self.begin(), self.begin()
        self.assertNotEqual(one['session'], two['session'])
        edited = self.call('replace', **self.credentials(one), target_text='x = 1', new_text='x = 3')
        self.call('replace', status='ERROR', **self.credentials(one), target_text='y = 2', new_text='y = 9')
        self.call('commit', **self.credentials(edited))
        self.call('commit', status='ERROR', **self.credentials(two))
        self.assertTrue(Path(two['sidecar']).exists())

    def test_refuses_traversal_and_preserves_malformed_sidecar(self):
        result = self.proxy.execute(URI, {'root': str(self.root), 'path': '../app.py', 'action': 'begin', 'source_sha256': 'a'})
        self.assertEqual(result['status'], 'ERROR')
        first = self.begin()
        sidecar = Path(first['sidecar'])
        sidecar.write_text('{broken', encoding='utf-8')
        invalid = dict(self.credentials(first), revision=hashlib.sha256(sidecar.read_bytes()).hexdigest())
        self.call('show', status='ERROR', **invalid)
        self.assertEqual(sidecar.read_text(), '{broken')

    def test_ambiguous_edit_and_lock_do_not_change_session(self):
        first = self.begin()
        self.call('replace', status='ERROR', **self.credentials(first), target_text=' = ', new_text=': ')
        sidecar = Path(first['sidecar'])
        before = sidecar.read_bytes()
        lock = self.path.with_name(self.path.name + '.hornet.lock')
        lock.write_text('existing lock')
        self.call('replace', status='ERROR', **self.credentials(first), target_text='x = 1', new_text='x = 5')
        self.assertEqual(lock.read_text(), 'existing lock')
        self.assertEqual(sidecar.read_bytes(), before)

    def test_symlink_target_is_refused(self):
        link = self.root / 'link.py'
        try:
            link.symlink_to(self.path)
        except OSError as exc:
            self.skipTest('Creating a symbolic link is unavailable: ' + str(exc))
        result = self.proxy.execute(URI, {'root': str(self.root), 'path': 'link.py', 'action': 'begin',
                                          'source_sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()})
        self.assertEqual(result['status'], 'ERROR')
        self.assertEqual(self.path.read_bytes(), b'\xef\xbb\xbfx = 1\r\ny = 2\r\n')

    def test_session_survives_stdio_server_restart(self):
        def invoke(action, **kwargs):
            request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'call_proxy_tool', 'arguments': {'uri': URI, 'arguments': {
                    'root': str(self.root), 'path': 'app.py', 'action': action, **kwargs}}}}
            process = subprocess.run([sys.executable, '-B', '-m', 'hornet.registry', '--allow-uri', URI],
                                     input=json.dumps(request) + '\n', text=True, encoding='utf-8',
                                     capture_output=True, timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr)
            response = json.loads(process.stdout)['result']
            self.assertFalse(response['isError'], response)
            return json.loads(response['content'][0]['text'])['proxy_response']
        first = invoke('begin', source_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())
        edited = invoke('replace', **self.credentials(first), target_text='x = 1', new_text='x = 7')
        invoke('commit', **self.credentials(edited))
        self.assertEqual(self.path.read_bytes(), b'\xef\xbb\xbfx = 7\r\ny = 2\r\n')


if __name__ == '__main__':
    unittest.main()
