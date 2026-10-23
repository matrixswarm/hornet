"""Native model payloads are exactly encoded; no syntax repair or guessing."""
import copy
import json
import unittest

from hornet.coding import autonomous, cells, contract_wire, mission
from hornet.coding.model import json_action, validate_arguments
from hornet.coding.role_schema import DOCUMENTS, recon_schema, check_schema
from hornet.project import HornetError
import test_autonomous as fixtures


class WireTests(unittest.TestCase):
    def native(self):
        plan = copy.deepcopy(autonomous.PLAN_EXAMPLE)
        plan.update(schema_version=2, coverage=[{'requirement_id': 'r001', 'cell_ids': ['check-even']}])
        value = {'schema_version': 1, 'mode': 'execute', 'reason': 'One file', 'self_cell_id': 'check-even', 'plan': plan}
        return contract_wire.presentation(value)

    def test_check_paths_use_only_declared_source_files(self):
        for name in ('submit_checks', 'submit_goal_checks'):
            schema = check_schema(name, ['tax_math.py'])
            check = {'schema_version': 1, 'checks': [{'id': 'tax', 'description': 'Tax arithmetic',
                'paths': ['tax_math.py'], 'timeout': 5,
                'python': 'from tax_math import add_tax\nassert add_tax(350, 8) == 378'}]}
            validate_arguments(check, schema)
            check['checks'][0]['paths'] = ['src/tax_math.py']
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_arguments(check, schema)

    def test_native_arguments_and_schema_pass_generation_validation(self):
        value = self.native()
        result = value['plan']['cells'][0]['result']
        result.update(args=[3, True, None, 1.5, {'x': 'quotes " and commas, } <nl_50>'}], kwargs={'flag': False})
        schema = contract_wire.schema(DOCUMENTS['submit_intake'])
        tools = [{'function': {'name': 'submit_intake', 'parameters': schema}}]
        action = json_action(json.dumps({'action':'call', 'name':'submit_intake','arguments':value}), tools)
        received = json.loads(action['tool_calls'][0]['function']['arguments'])
        normalized = contract_wire.decode(received)
        stored = normalized['plan']['cells'][0]['result']
        self.assertEqual(json.loads(stored['args_json']), result['args'])
        self.assertEqual(json.loads(stored['kwargs_json']), {'flag': False})
        self.assertEqual(json.loads(stored['schema_json']), {'type': 'boolean'})
        cells.validate(normalized['plan'])
        self.assertEqual(contract_wire.presentation(normalized), value)

    def test_closed_schema_rejects_invented_root_keywords(self):
        value = self.native()
        value['plan']['cells'][0]['result']['schema']['required_properties'] = ['x']
        with self.assertRaises(ValueError):
            validate_arguments(value, contract_wire.schema(DOCUMENTS['submit_intake']))

    def test_schema_variants_offer_only_fields_for_the_selected_return_type(self):
        for spec in ({'type': 'integer', 'properties': {}}, {'type': 'array'},
                     {'type': 'object', 'items': {'type': 'integer'}}, {'type': 'object'}):
            value = self.native()
            value['plan']['cells'][0]['result']['schema'] = spec
            with self.assertRaises(ValueError):
                validate_arguments(value, contract_wire.schema(DOCUMENTS['submit_intake']))

    def test_literal_call_encodes_exact_positional_and_keyword_arguments(self):
        value = self.native()
        value['plan']['cells'][0]['result'] = {'path': 'parity_cell.py',
            'call': "is_even(3, [True, None], label='commas, quotes \\\" and }')", 'schema': {'type': 'boolean'}}
        validate_arguments(value, contract_wire.schema(DOCUMENTS['submit_intake'], calls=True))
        stored = contract_wire.decode(value)
        result = stored['plan']['cells'][0]['result']
        self.assertEqual(result['function'], 'is_even')
        self.assertEqual(json.loads(result['args_json']), [3, [True, None]])
        self.assertEqual(json.loads(result['kwargs_json']), {'label': 'commas, quotes " and }'})
        self.assertEqual(contract_wire.decode(contract_wire.presentation(stored, calls=True)), stored)
        self.assertEqual(contract_wire.parse_call("consume({'$cell': 'first'})"), ('consume', [{'$cell': 'first'}], {}))

    def test_call_parser_rejects_code_expressions_and_ambiguous_literals(self):
        invalid = ["os.system('whoami')", "f(open('x'))", 'f(1+2)', 'f(*[1])', 'f(**{})',
                   "f({'x':1,'x':2})", 'f(x=1,x=2)', 'f((1,2))', "f(b'bytes')", 'f({1,2})',
                   'f(1e999)', 'f([x for x in []])', 'f(unknown)', 'f(3); print(1)',
                   '<nl_50>f(3)', 'f(', 'f(' + '[' * 100 + '0' + ']' * 100 + ')']
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(HornetError):
                contract_wire.parse_call(text)
        value = self.native()
        value['plan']['cells'][0]['result']['call'] = 'is_even(3)'
        with self.assertRaisesRegex(HornetError, 'do not mix'):
            contract_wire.decode(value)

    def test_legacy_inputs_are_unchanged_and_mixed_fields_rejected(self):
        original = contract_wire.decode(self.native())
        self.assertEqual(contract_wire.decode(original), original)
        for bad in ({'args': '[]'}, {'args_json': '[]'}, {'schema': '{}'}, {'kwargs': []}):
            value = self.native()
            value['plan']['cells'][0]['result'].update(bad)
            with self.assertRaises(HornetError):
                contract_wire.decode(value)

    def test_limits_and_nonfinite_values_are_not_repaired(self):
        for args in (['a' * 501], [float('nan')], [float('inf')]):
            value = self.native()
            value['plan']['cells'][0]['result']['args'] = args
            with self.assertRaises(HornetError):
                contract_wire.decode(value)
        tools = [{'function': {'name': 'submit_intake', 'parameters': contract_wire.schema(DOCUMENTS['submit_intake'])}}]
        for text in ('{"action":"call"', '<nl_50>{"action":"finish","summary":"done"}',
                     '{"action":"finish","summary":"a","summary":"b"}', '[' * 2000 + '0' + ']' * 2000):
            with self.assertRaises(ValueError):
                json_action(text, tools)

    def test_research_mode_keeps_schema_object_and_empty_route(self):
        value = self.native()
        value['plan']['schema_version'] = 3
        value['plan']['cells'][0]['kind'] = 'code'
        schema = contract_wire.schema(recon_schema('submit_intake', [{'id':'r001'}], 1, research=True))
        validate_arguments(value, schema)
        normalized = contract_wire.decode(value)
        self.assertIn('schema', normalized['plan']['cells'][0]['result'])
        self.assertNotIn('schema_json', normalized['plan']['cells'][0]['result'])
        cells.validate(normalized['plan'])
        empty = {'schema_version':1,'route':'execute','reason':'Small','plan':{'schema_version':2,'cells':[],'coverage':[]}}
        self.assertEqual(contract_wire.decode(empty), empty)

    def test_generated_checks_reject_unbound_globals_without_rewriting_source(self):
        for source in ('from invoice import quote\n# Keep input\\norig = [100, 200]\nquote(orig, 5)',
                       'def verify():\n    assert missing == 1\nverify()'):
            value = {'checks': [{'id': 'check', 'python': source}]}
            with self.assertRaisesRegex(HornetError, 'undefined names'):
                mission.reject_unbound_check_globals(value)
            self.assertEqual(value['checks'][0]['python'], source)

    def test_generated_checks_accept_explicit_bindings_and_nested_local_scopes(self):
        source = '''import math as m
values = [0, 1]
def verify(items):
    total = sum(x for x in items)
    assert total == 1 and m.floor(total) == total
verify(values)
assert all(x >= 0 for x in values)
class Check:
    def run(self):
        assert len(values) == 2
Check().run()
'''
        mission.reject_unbound_check_globals({'checks': [{'id': 'check', 'python': source}]})


class NativeGoalTests(unittest.TestCase):
    setUp = fixtures.AutonomousTests.setUp
    model = fixtures.AutonomousTests.model
    submit = fixtures.AutonomousTests.submit
    write = fixtures.AutonomousTests.write
    state = fixtures.AutonomousTests.state
    run_dir = fixtures.AutonomousTests.run_dir

    def test_native_intake_completes_with_exact_saved_return_and_closeout(self):
        seen = []
        def model(messages, tools, *rest):
            response, stats = self.model(messages, tools, *rest)
            if any(t['function']['name'] == 'submit_intake' for t in tools):
                func = response['tool_calls'][0]['function']
                value = contract_wire.presentation(json.loads(func['arguments']), calls=True)
                func['arguments'] = json.dumps(value)
                offered = next(t for t in tools if t['function']['name'] == 'submit_intake')
                validate_arguments(value, offered['function']['parameters'])
                seen.append(value)
            return response, stats
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertIs(result['values']['check-even'], False)
        self.assertIn('closeout', result)
        self.assertEqual(self.state()['intake'], contract_wire.decode(seen[0]))
        self.assertEqual(mission.read(self.run_dir() / 'contract-format.json'), contract_wire.POLICY)

    def test_unbound_check_is_returned_to_author_before_freezing(self):
        attempts = []
        def model(messages, tools, *rest):
            if any(t['function']['name'] == 'submit_goal_checks' for t in tools):
                attempts.append(messages)
                if len(attempts) == 1:
                    bad = copy.deepcopy(self.checks)
                    bad['checks'][0]['python'] += '\n# New input\\ninput_value = 2\nassert is_even(input_value)'
                    return self.submit('submit_goal_checks', bad, 'bad-check')
                self.assertIn('undefined names: input_value', str(messages))
            return self.model(messages, tools, *rest)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.state()['goal_checks'], self.checks)


if __name__ == '__main__':
    unittest.main()
