"""Strict action parsing and LM Studio request/response boundaries."""
import json
import unittest
from unittest.mock import MagicMock, patch

from hornet.coding.agent import TOOLS
from hornet.coding.model import json_action, lmstudio_json, generation_schema, validate_arguments
from hornet.coding.role_schema import DOCUMENTS
from hornet.coding.agent import tool
from hornet.project import HornetError


class JsonActionTests(unittest.TestCase):
    def test_complete_version_variants_preserve_path_and_edit_requirements(self):
        original = next(t['function']['parameters'] for t in TOOLS if t['function']['name'] == 'edit_file')
        before = json.dumps(original, sort_keys=True)
        schema = generation_schema(original)
        self.assertEqual(len(schema['anyOf']), 2)
        for variant in schema['anyOf']:
            self.assertTrue({'path', 'old_text', 'new_text'} <= set(variant['required']))
            self.assertEqual(len({'read_ref', 'expected_sha256'} & set(variant['properties'])), 1)
        for version in ('read_ref', 'expected_sha256'):
            valid = {'path': 'a.py', 'old_text': 'old', 'new_text': 'new', version: 'ref'}
            validate_arguments(valid, schema)
            validate_arguments(valid, original)
            for required in ('path', 'old_text', 'new_text', version):
                bad = dict(valid)
                del bad[required]
                with self.subTest(version=version, required=required), self.assertRaises(ValueError):
                    validate_arguments(bad, schema)
        with self.assertRaises(ValueError):
            validate_arguments({'path': 'a.py', 'old_text': 'old', 'new_text': 'new',
                                'read_ref': 'ref', 'expected_sha256': 'sha'}, schema)
        self.assertEqual(json.dumps(original, sort_keys=True), before)

    def test_structured_check_submission_preserves_source_without_double_encoding(self):
        schema = DOCUMENTS['submit_checks']
        offered = [tool('submit_checks', 'Submit checks', schema['properties'], schema['required'])]
        document = {'schema_version': 1, 'checks': [{'id': 'edge', 'description': 'Observed behavior',
            'paths': ['a.py'], 'timeout': 5, 'python': "from a import f\nassert f('quoted') is False"}]}
        def parse(value):
            return json_action(json.dumps({'action': 'call', 'name': 'submit_checks', 'arguments': value}), offered)
        message = parse(document)
        self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']), document)
        for bad in ({'json': json.dumps(document)}, dict(document, schema_version=True),
                    dict(document, checks=document['checks'] * 5),
                    dict(document, checks=[dict(document['checks'][0], paths=[3])]),
                    dict(document, checks=[dict(document['checks'][0], extra='bad')])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse(bad)

    def test_call_preserves_plain_source_and_generates_ids(self):
        code = 'print("quotes", "\\n")\n'
        content = json.dumps({'action': 'call', 'name': 'run_python', 'arguments': {'code': code}})
        first = json_action(content, TOOLS)['tool_calls'][0]
        second = json_action(content, TOOLS)['tool_calls'][0]
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(json.loads(first['function']['arguments'])['code'], code)

    def test_finish_has_no_calls(self):
        self.assertEqual(json_action('{"action":"finish","summary":"Observed tests passed"}', TOOLS),
                         {'role': 'assistant', 'content': 'Observed tests passed'})

    def test_malformed_prose_duplicate_and_empty_finish_are_rejected(self):
        for content in ('[]', '```json\n{}\n```', '{} trailing',
                        '{"action":"finish","action":"finish","summary":"x"}',
                        '{"action":"finish","summary":""}',
                        '{"action":"finish","summary":"ok","extra":1}'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                json_action(content, TOOLS)

    def test_unoffered_unknown_missing_and_wrong_arguments_rejected(self):
        cases = [('shell', {}), ('read_file', {}), ('read_file', {'path': 'a', 'extra': 1}),
                 ('read_file', {'path': 3}), ('read_file', {'path': 'a', 'start_line': True}),
                 ('run_command', {'argv': ['python3', 7]})]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments), self.assertRaises(ValueError):
                json_action(json.dumps({'action': 'call', 'name': name, 'arguments': arguments}), TOOLS)
        with self.assertRaises(ValueError):
            json_action('{"action":"call","name":"run_tests","arguments":{}}', TOOLS[:1])


class LMStudioJsonTests(unittest.TestCase):
    def response(self, content=None, tokens=30, reasoning=0, finish='stop'):
        return {'model': 'fixture', 'choices': [{'message': {'content': content or '{"action":"finish","summary":"Done"}'}, 'finish_reason': finish}],
                'usage': {'completion_tokens': tokens, 'completion_tokens_details': {'reasoning_tokens': reasoning}}}

    def request(self, body, messages=None, max_tokens=128, offered=None):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(body).encode()
        with patch('hornet.coding.model.request.build_opener', return_value=opener):
            result = lmstudio_json(messages or [{'role': 'user', 'content': 'small ticket'}],
                                   TOOLS[:2] if offered is None else offered,
                                   'fixture', 'http://127.0.0.1:1234/v1', max_tokens, 60)
        return result, opener.open.call_args[0][0]

    def test_payload_uses_constrained_output_and_disables_reasoning(self):
        (_, stats), req = self.request(self.response())
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, 'http://127.0.0.1:1234/v1/chat/completions')
        self.assertEqual(payload['reasoning_effort'], 'none')
        self.assertTrue(payload['response_format']['json_schema']['strict'])
        self.assertNotIn('tools', payload)
        self.assertNotIn('temperature', payload)
        self.assertEqual(stats['usage']['completion_tokens_details']['reasoning_tokens'], 0)

    def test_finish_is_offered_only_to_roles_without_required_submissions(self):
        schema = DOCUMENTS['submit_advice']
        offered = [tool('submit_advice', 'Submit advice', schema['properties'], schema['required'])]
        content = json.dumps({'action': 'call', 'name': 'submit_advice', 'arguments': {'message': 'Use integer arithmetic'}})
        _, req = self.request(self.response(content), offered=offered)
        payload = json.loads(req.data)
        alternatives = payload['response_format']['json_schema']['schema']['anyOf']
        self.assertEqual([row['properties']['action']['const'] for row in alternatives], ['call'])
        self.assertIn('Complete this role by calling its offered submit tool', payload['messages'][0]['content'])
        self.assertNotIn('To finish:', payload['messages'][0]['content'])
        _, req = self.request(self.response())
        payload = json.loads(req.data)
        alternatives = payload['response_format']['json_schema']['schema']['anyOf']
        self.assertIn('finish', [row['properties']['action']['const'] for row in alternatives])
        self.assertIn('To finish:', payload['messages'][0]['content'])

    def test_history_keeps_raw_source_and_omits_opaque_reasoning(self):
        source = '1: print("quoted", "\\n")\n'
        messages = [{'role': 'system', 'content': 'Fixture system instruction'},
                    {'role': 'assistant', 'content': '', 'reasoning_content': 'secret opaque state',
                     'tool_calls': [{'function': {'name': 'read_file', 'arguments': '{"path":"a.py"}'}}]},
                    {'role': 'tool', 'name': 'read_file', 'content': source}]
        _, req = self.request(self.response(), messages)
        payload = json.loads(req.data)
        self.assertIn(source, payload['messages'][1]['content'])
        self.assertIn('Fixture system instruction', payload['messages'][0]['content'])
        self.assertNotIn('secret opaque state', payload['messages'][1]['content'])

    def test_truncation_and_thinking_are_rejected(self):
        for body in (self.response(tokens=128), self.response(finish='length'), self.response(reasoning=1)):
            with self.subTest(body=body), self.assertRaises(HornetError):
                self.request(body)

    def test_native_calls_bad_content_and_missing_usage_are_rejected(self):
        native = self.response()
        native['choices'][0]['message']['tool_calls'] = [{'function': {'name': 'run_tests'}}]
        for body in (native, self.response(content='<|tool_call>bad'), {'choices': []}):
            with self.subTest(body=body), self.assertRaises(HornetError):
                self.request(body)

    def test_bad_endpoint_and_authenticated_http_are_rejected_before_network(self):
        with patch('hornet.coding.model.request.build_opener') as opener:
            for url in ('file:///tmp/model', 'http://user:pass@localhost:1234/v1', 'http://localhost:1234/v1?x=y', 'http://localhost:1234/other'):
                with self.subTest(url=url), self.assertRaises(HornetError):
                    lmstudio_json([], TOOLS, 'fixture', url)
            with patch.dict('os.environ', {'HORNET_API_KEY': 'fixture'}), self.assertRaises(HornetError):
                lmstudio_json([], TOOLS, 'fixture', 'http://example.com/v1')
            opener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
