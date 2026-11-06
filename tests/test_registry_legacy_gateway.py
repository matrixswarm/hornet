import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from hornet.coding import registry as coding_registry
from hornet.registry.registry_manager import RegistryManager
from hornet.registry.registry_mcp_server import LEGACY_MATH_NAMES, RegistryMCPServer, legacy_registry_call


class LegacyGatewayTests(unittest.TestCase):
    def request(self, server, name, arguments):
        message = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': name, 'arguments': arguments}}
        response = server.handle_request(json.dumps(message))['result']
        return response, json.loads(response['content'][0]['text'])

    def test_standalone_discovery_does_not_expand_coding_cell_gateway(self):
        server = RegistryMCPServer()
        message = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
        names = {item['name'] for item in server.handle_request(json.dumps(message))['result']['tools']}
        self.assertIn('call_registry_tool', names)
        tools = server.handle_request(json.dumps(message))['result']['tools']
        spec = next(item for item in tools if item['name'] == 'call_registry_tool')['inputSchema']
        self.assertIn('capability_name', spec['properties']['arguments']['required'])
        self.assertNotIn('call_registry_tool', coding_registry.NAMES)
        self.assertNotIn('call_registry_tool', {item['function']['name'] for item in coding_registry.TOOLS})

    def test_math_dispatch_uses_capability_and_passes_schema_validation(self):
        proxy = Mock()
        proxy.execute.return_value = {'status': 'SUCCESS', 'proxy_response': {'status': 'SUCCESS', 'result': [[3.0]]}}
        original = {'capability_name': 'matrix_multiplication', 'A': [[1]], 'B': [[3]]}
        result = legacy_registry_call(proxy, 'matrix_addition', original)
        proxy.execute.assert_called_once_with('math.linear_algebra.compute',
            {'operation': 'matrix_multiplication', 'A': [[1]], 'B': [[3]]})
        self.assertEqual(original['capability_name'], 'matrix_multiplication')
        self.assertEqual(result['container'], 'hornet_proxy_v1')
        self.assertEqual(result['tool'], 'matrix_addition')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['payload'], {'status': 'success', 'result': [[3.0]]})
        self.assertEqual(result['metadata'], {})

    def test_process_matrix_renames_legacy_operation_and_rejects_conflicts(self):
        proxy = Mock()
        proxy.execute.return_value = {'status': 'SUCCESS', 'proxy_response': {'status': 'SUCCESS'}}
        legacy_registry_call(proxy, 'process_matrix', {'capability_name': 'process_matrix',
            'matrix_data': {'parsed_matrix': [[1]]}, 'operation': 'validate_rank'})
        proxy.execute.assert_called_once_with('math.linear_algebra.compute',
            {'operation': 'process_matrix', 'action': 'validate_rank', 'matrix_data': {'parsed_matrix': [[1]]}})
        proxy.reset_mock()
        conflict = legacy_registry_call(proxy, 'process_matrix', {'capability_name': 'process_matrix',
            'operation': 'validate_rank', 'action': 'construct_c'})
        self.assertEqual(conflict['status'], 'error')
        proxy.execute.assert_not_called()

    def test_missing_unknown_and_nested_capabilities_fail_closed(self):
        proxy = Mock()
        for name, args in [('not_a_tool', {'capability_name': 'matrix_rank'}),
                           ('matrix_rank', {}),
                           ('matrix_rank', {'capability_name': 'not_a_capability'}),
                           ('matrix_rank', {'capability_name': {'name': 'matrix_rank'}}),
                           ('matrix_rank', {'capability_name': 'matrix_rank', 'operation': 'matrix_inverse'})]:
            with self.subTest(name=name, args=args):
                self.assertEqual(legacy_registry_call(proxy, name, args)['status'], 'error')
        proxy.execute.assert_not_called()
        self.assertEqual(len(LEGACY_MATH_NAMES), 23)

    def test_allowlist_and_argument_schema_remain_authoritative(self):
        restricted = RegistryMCPServer(RegistryManager(allowed_uris=['files.text.read']))
        request = {'tool_name': 'matrix_rank', 'arguments': {'capability_name': 'matrix_rank', 'matrix': [[1]]}}
        result, payload = self.request(restricted, 'call_registry_tool', request)
        self.assertTrue(result['isError'])
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['payload']['status'], 'error')
        allowed = RegistryMCPServer(RegistryManager(allowed_uris=['math.linear_algebra.compute']))
        request['arguments']['extra'] = 7
        result, payload = self.request(allowed, 'call_registry_tool', request)
        self.assertTrue(result['isError'])
        self.assertEqual(payload['status'], 'error')

    def test_generic_and_legacy_reads_use_same_allowlisted_bounded_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.txt'
            path.write_text('first\nsecond\n', encoding='utf-8')
            server = RegistryMCPServer(RegistryManager(allowed_uris=['files.text.read']))
            for name, args in [('hornet', {'capability_name': 'files.text.read', 'path': str(path)}),
                               ('file_manager', {'capability_name': 'read_file', 'path': str(path), 'encoding': 'utf-8'})]:
                result, payload = self.request(server, 'call_registry_tool', {'tool_name': name, 'arguments': args})
                self.assertFalse(result['isError'], payload)
                self.assertEqual(payload['status'], 'success')
                self.assertEqual(payload['payload']['status'], 'success')
            _, payload = self.request(server, 'call_registry_tool', {'tool_name': 'file_manager',
                'arguments': {'capability_name': 'read_file', 'path': str(path), 'encoding': 'latin-1'}})
            self.assertEqual(payload['status'], 'error')
            _, payload = self.request(server, 'call_registry_tool', {'tool_name': 'file_manager',
                'arguments': {'capability_name': 'write_file', 'path': str(path), 'content': 'unsafe'}})
            self.assertEqual(payload['status'], 'error')
            self.assertEqual(path.read_text(encoding='utf-8'), 'first\nsecond\n')

    def test_legacy_terminal_uses_selected_shell_and_existing_resolver(self):
        server = RegistryMCPServer(RegistryManager(allowed_uris=['runtime.shell.execute']))
        result, payload = self.request(server, 'call_registry_tool', {'tool_name': 'terminal',
            'arguments': {'capability_name': 'execute', 'command': 'echo universal-call'}})
        self.assertFalse(result['isError'], payload)
        self.assertIn('universal-call', payload['payload']['stdout'])
        denied = RegistryMCPServer(RegistryManager(allowed_uris=['files.text.read']))
        result, _ = self.request(denied, 'call_registry_tool', {'tool_name': 'terminal',
            'arguments': {'capability_name': 'execute', 'command': 'echo denied'}})
        self.assertTrue(result['isError'])

    def test_legacy_python_executes_statements_without_a_second_implementation(self):
        server = RegistryMCPServer(RegistryManager(allowed_uris=['runtime.python.probe']))
        result, payload = self.request(server, 'call_registry_tool', {'tool_name': 'python_suite',
            'arguments': {'capability_name': 'execute', 'code': 'value = 3\nprint(value + 4)'}})
        self.assertFalse(result['isError'], payload)
        self.assertEqual(payload['payload']['stdout'].strip(), '7')
        result, _ = self.request(server, 'call_registry_tool', {'tool_name': 'python_suite',
            'arguments': {'capability_name': 'execute', 'code': 'print(1)', 'expression': '1'}})
        self.assertTrue(result['isError'])

    def test_legacy_user_input_requests_client_conversation(self):
        server = RegistryMCPServer()
        result, payload = self.request(server, 'call_registry_tool', {'tool_name': 'user_input',
            'arguments': {'capability_name': 'ask', 'question': 'Which file?'}})
        self.assertFalse(result['isError'])
        self.assertEqual(payload['payload']['status'], 'input_required')
        self.assertEqual(payload['payload']['question'], 'Which file?')
        result, _ = self.request(server, 'call_registry_tool', {'tool_name': 'user_input',
            'arguments': {'capability_name': 'ask', 'question': ''}})
        self.assertTrue(result['isError'])

    def test_generic_denies_unavailable_uris_and_web_stays_opt_in(self):
        server = RegistryMCPServer()
        for name, args in [('hornet', {'capability_name': 'research.web.search', 'query': 'test'}),
                           ('web_search', {'capability_name': 'search', 'query': 'test'}),
                           ('web_search', {'capability_name': 'fetch', 'url': 'https://example.com'}),
                           ('hornet', {'capability_name': 'mcp.shell', 'command': 'echo hi'}),
                           ('hornet', {'capability_name': 'not.a.real.uri'})]:
            with self.subTest(name=name):
                result, payload = self.request(server, 'call_registry_tool', {'tool_name': name, 'arguments': args})
                self.assertTrue(result['isError'])
                self.assertEqual(payload['status'], 'error')

    def test_generic_staged_response_is_not_reported_as_committed(self):
        proxy = Mock()
        proxy.registry.details.return_value = {'uri': 'files.text.atomic_edit'}
        proxy.execute.return_value = {'status': 'STAGED', 'proxy_response': {'status': 'STAGED'}}
        response = legacy_registry_call(proxy, 'hornet', {'capability_name': 'files.text.atomic_edit', 'commit': False})
        self.assertEqual(response['status'], 'staged')
        self.assertEqual(response['payload']['status'], 'staged')

    def test_generic_operation_field_is_passed_to_canonical_math_schema(self):
        proxy = Mock()
        proxy.registry.details.return_value = {'uri': 'math.linear_algebra.compute'}
        proxy.execute.return_value = {'status': 'SUCCESS', 'proxy_response': {'status': 'SUCCESS'}}
        legacy_registry_call(proxy, 'hornet', {'capability_name': 'math.linear_algebra.compute',
            'operation': 'matrix_rank', 'matrix': [[1]]})
        proxy.execute.assert_called_once_with('math.linear_algebra.compute',
            {'operation': 'matrix_rank', 'matrix': [[1]]})

    @unittest.skipUnless(importlib.util.find_spec('numpy'), 'NumPy math extra is not installed')
    def test_stdio_dispatch_preserves_legacy_wrapper(self):
        request = {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                   'params': {'name': 'call_registry_tool', 'arguments': {'tool_name': 'matrix_addition',
                     'arguments': {'capability_name': 'matrix_multiplication', 'A': [[1, 2]], 'B': [[3], [4]]}}}}
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        process = subprocess.run([sys.executable, '-m', 'hornet.registry', '--allow-uri', 'math.linear_algebra.compute'],
            input=json.dumps(request) + '\n', capture_output=True, text=True, encoding='utf-8',
            cwd=str(Path(__file__).resolve().parents[1]), env=env, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        response = json.loads(process.stdout)['result']
        self.assertFalse(response['isError'], response)
        payload = json.loads(response['content'][0]['text'])
        self.assertEqual(payload['container'], 'hornet_proxy_v1')
        self.assertEqual(payload['status'], 'success')
        self.assertEqual(payload['payload']['status'], 'success')
        self.assertEqual(payload['payload']['result'], [[11.0]])


if __name__ == '__main__':
    unittest.main()
