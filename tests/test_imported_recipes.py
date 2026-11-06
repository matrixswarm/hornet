import json
import unittest

from hornet.registry.proxy_resolver import ProxyResolver
from hornet.registry.registry_manager import RegistryManager
from hornet.registry.registry_mcp_server import RegistryMCPServer


class ImportedRecipeTests(unittest.TestCase):
    def setUp(self):
        self.registry = RegistryManager()
        self.server = RegistryMCPServer(self.registry)

    def read(self, uri):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': 'read_recipe', 'arguments': {'uri': uri}}}
        response = self.server.handle_request(json.dumps(request))['result']
        self.assertFalse(response['isError'], response)
        payload = json.loads(response['content'][0]['text'])
        return payload, json.loads(payload['content'])

    def test_matrix_recipe_preserves_nested_check_and_tool_reference(self):
        payload, recipe = self.read('matrix_multiply_checked_v1')
        self.assertEqual(payload['uri'], 'recipe.math.matrix_multiply_checked')
        nested = next(step for step in recipe['steps'] if step.get('tool') == 'read_recipe')
        self.assertEqual(nested['uri'], 'recipe.math.matrix_input_check')
        _, check = self.read(nested['uri'])
        self.assertIn('finite', check['steps'][0]['purpose'])
        self.assertIn('column', check['steps'][1]['purpose'])
        execute = next(step for step in recipe['steps'] if step.get('tool') == 'call_proxy_tool')
        self.assertEqual(execute['uri'], 'math.linear_algebra.compute')
        self.assertEqual(self.registry.details(execute['uri'])['type'], 'TOOL')
        self.assertIn('matrix_multiplication', execute['purpose'])
        self.assertIn('verify', [step.get('action') for step in recipe['steps']])

    def test_input_recipe_is_reference_only_and_handles_all_outcomes(self):
        _, recipe = self.read('input_result_handling_v1')
        instruction = recipe['steps'][0]['purpose']
        for state in ('answer', 'cancellation', 'timeout', 'error'):
            self.assertIn(state, instruction)
        self.assertIn('does not open a dialog', recipe['limits'])
        result = ProxyResolver(registry=self.registry).execute('recipe.input.result_handling', {})
        self.assertEqual(result['status'], 'ERROR')
        self.assertFalse(result['was_proxied'])

    def test_session_recipe_chain_is_opt_in_and_explicit(self):
        uris = ['recipe.edit.session_prepare', 'recipe.edit.session_prepare_with_backup',
                'recipe.edit.session_edit_verified', 'recipe.edit.session_workflow',
                'files.text.session_edit', 'files.text.read']
        with self.assertRaises(ValueError):
            self.registry.details(uris[0])
        registry = RegistryManager(allowed_uris=uris)
        self.server = RegistryMCPServer(registry)
        for alias, expected in [('atomic_prepare_v1', uris[0]),
                                ('safe_atomic_prepare_v1', uris[1]),
                                ('atomic_edit_verified_v1', uris[2]),
                                ('python_suite_blitzkrieg_v1', uris[3])]:
            payload, recipe = self.read(alias)
            self.assertEqual(payload['uri'], expected)
            for step in recipe['steps']:
                if step.get('tool') == 'read_recipe':
                    self.assertEqual(registry.details(step['uri'])['type'], 'RECIPE')
        _, verified = self.read('atomic_edit_verified_v1')
        self.assertEqual(verified['steps'][0]['uri'], uris[1])
        _, backup = self.read('safe_atomic_prepare_v1')
        self.assertEqual(backup['steps'][1]['uri'], uris[0])
        self.assertIn('does not create', backup['limits'])


if __name__ == '__main__':
    unittest.main()
