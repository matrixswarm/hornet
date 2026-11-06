import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from hornet.desktop.automation import (StructuredResponseUnsupported, accept_initialization, make_initialization_draft,
                                       model_describer, prepare_mission, read_state)
from hornet.desktop.workspace import Workspace


class DescriberPromptTests(unittest.TestCase):
    @patch('hornet.desktop.automation.request_json')
    def test_prompt_uses_exact_current_filename_as_summary_key(self, request):
        name = 'src/a "quoted" file.py'
        payload = {'files': {name: 'print(1)'}, 'file_part': {'path': name, 'number': 1, 'total': 1}}
        request.return_value = {'summaries': {name: 'Prints one.'}}
        self.assertEqual(model_describer('http://localhost:1234/v1', 'local-model')(payload), request.return_value)
        endpoint, model, instruction, sent = request.call_args.args
        self.assertEqual((endpoint, model, sent), ('http://localhost:1234/v1', 'local-model', payload))
        self.assertIn(json.dumps(name, ensure_ascii=False), instruction)
        self.assertNotIn('exact/path', instruction)
        self.assertIn('Do not invent, rename, or add summary keys', instruction)
        schema = request.call_args.kwargs['response_schema']
        self.assertEqual(schema['properties']['summaries']['required'], [name])
        self.assertFalse(schema['properties']['summaries']['additionalProperties'])

    def test_invalid_batches_do_not_send_a_request(self):
        with patch('hornet.desktop.automation.request_json') as request:
            describe = model_describer('http://localhost:1234/v1', 'local-model')
            for files in ({}, {'': ''}, {1: ''}):
                with self.subTest(files=files), self.assertRaisesRegex(ValueError, 'at least one named file'):
                    describe({'files': files})
            request.assert_not_called()

    @patch('hornet.desktop.automation.request_json')
    def test_multi_file_prompt_contains_exact_keys(self, request):
        names = ['src/one.py', 'src/two "quoted".py']
        payload = {'files': {name: 'value = 1' for name in names}}
        request.return_value = {'summaries': {name: 'Stores a value.' for name in names}}
        result = model_describer('http://localhost:1234/v1', 'local-model')(payload)
        self.assertEqual(set(result['summaries']), set(names))
        instruction = request.call_args.args[2]
        for name in names:
            self.assertIn(json.dumps(name, ensure_ascii=False), instruction)
        self.assertNotIn('exact/path', instruction)

    @patch('hornet.desktop.automation.request_json')
    def test_wrong_keys_get_one_correction_using_same_payload(self, request):
        names = ['src/one.py', 'src/two.py']
        payload = {'files': {name: 'value = 1' for name in names}}
        correct = {'summaries': {name: 'A real summary.' for name in names}}
        request.side_effect = [{'summaries': {'exact/path': 'Wrong key'}}, correct]
        result = model_describer('http://localhost:1234/v1', 'model')(payload)
        self.assertEqual(result, correct)
        self.assertEqual(request.call_count, 2)
        original, retry = request.call_args_list
        self.assertIs(original.args[3], payload)
        self.assertIs(retry.args[3], payload)
        self.assertIn('previous response had invalid summary keys or values', retry.args[2])
        self.assertEqual(original.kwargs['response_schema'], retry.kwargs['response_schema'])
        for name in names:
            self.assertIn(json.dumps(name), retry.args[2])

    @patch('hornet.desktop.automation.request_json')
    def test_invalid_structure_gets_one_correction_then_fails(self, request):
        request.return_value = {'summaries': None}
        with self.assertRaisesRegex(ValueError, 'summaries must be an object'):
            model_describer('http://localhost:1234/v1', 'model')({'files': {'one.py': 'x'}})
        self.assertEqual(request.call_count, 2)

    @patch('hornet.desktop.automation.request_json')
    def test_unsupported_schema_uses_prompt_with_local_validation(self, request):
        request.side_effect = [StructuredResponseUnsupported('unsupported'),
                               {'summaries': {'wrong.py': 'wrong'}},
                               {'summaries': {'one.py': 'Correct.'}}]
        response = model_describer('http://localhost:1234/v1', 'model')({'files': {'one.py': 'x'}})
        self.assertEqual(response['summaries']['one.py'], 'Correct.')
        self.assertEqual(request.call_count, 3)
        self.assertIn('response_schema', request.call_args_list[0].kwargs)
        self.assertNotIn('response_schema', request.call_args_list[1].kwargs)
        self.assertNotIn('response_schema', request.call_args_list[2].kwargs)

    @patch('hornet.desktop.automation.request_json')
    def test_blank_nontext_and_oversized_summaries_are_corrected(self, request):
        for invalid in ('  ', None, 'x' * 4001):
            with self.subTest(invalid=repr(invalid)[:40]):
                request.reset_mock(side_effect=True)
                request.side_effect = [{'summaries': {'one.py': invalid}},
                                       {'summaries': {'one.py': 'Correct.'}}]
                response = model_describer('http://localhost:1234/v1', 'model')({'files': {'one.py': 'x'}})
                self.assertEqual(response['summaries']['one.py'], 'Correct.')
                self.assertEqual(request.call_count, 2)

    @patch('urllib.request.build_opener')
    def test_wire_schema_corrects_invalid_response_without_relaxing_constraints(self, build_opener):
        opener = build_opener.return_value
        first = MagicMock()
        second = MagicMock()
        for response, summaries in ((first, {'wrong.py': 'Wrong.'}), (second, {'one.py': 'Correct.'})):
            response.__enter__.return_value.read.return_value = json.dumps({'choices': [
                {'finish_reason': 'stop', 'message': {'content': json.dumps({'summaries': summaries})}}]}).encode()
        opener.open.side_effect = [first, second]
        result = model_describer('http://localhost:1234/v1', 'model')({'files': {'one.py': 'x'}})
        self.assertEqual(result['summaries'], {'one.py': 'Correct.'})
        bodies = [json.loads(call.args[0].data) for call in opener.open.call_args_list]
        self.assertEqual(len(bodies), 2)
        for body in bodies:
            schema = body['response_format']['json_schema']['schema']['properties']['summaries']
            self.assertEqual(schema['required'], ['one.py'])
            self.assertFalse(schema['additionalProperties'])

    @patch('urllib.request.build_opener')
    def test_wire_fallback_only_for_explicitly_unsupported_schema(self, build_opener):
        opener = build_opener.return_value
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'choices': [
            {'finish_reason': 'stop', 'message': {'content': '{"summaries":{"one.py":"Correct."}}'}}]}).encode()
        error = HTTPError('http://localhost', 400, 'bad request', {},
                          io.BytesIO(b'{"error":"response_format is not supported"}'))
        opener.open.side_effect = [error, response]
        self.assertEqual(model_describer('http://localhost:1234/v1', 'model')({'files': {'one.py': 'x'}})['summaries']['one.py'], 'Correct.')
        bodies = [json.loads(call.args[0].data) for call in opener.open.call_args_list]
        self.assertIn('response_format', bodies[0])
        self.assertNotIn('response_format', bodies[1])

        opener.reset_mock()
        opener.open.side_effect = HTTPError('http://localhost', 400, 'bad request', {},
                                             io.BytesIO(b'{"error":"model is unavailable"}'))
        with self.assertRaisesRegex(ValueError, 'HTTP 400'):
            model_describer('http://localhost:1234/v1', 'model')({'files': {'one.py': 'x'}})
        opener.open.assert_called_once()

    def test_placeholder_key_is_rejected_and_draft_can_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            (source / 'calculator.py').write_text('value = 1\n', encoding='utf-8')
            ws = Workspace(root / 'workspace')
            base = ws.create('example', source=str(source), include_files=['calculator.py'])
            with patch('hornet.desktop.automation.request_json') as request:
                request.return_value = {'summaries': {'exact/path': 'Wrong key'}}
                describe = model_describer('http://localhost:1234/v1', 'local-model')
                with self.assertRaisesRegex(ValueError, 'unrequested'):
                    make_initialization_draft(ws, 'example', base, describe, included=['calculator.py'])
                self.assertEqual(request.call_count, 2)
                self.assertEqual(read_state(ws, 'example')['status'], 'uninitialized')
                self.assertTrue((ws.project('example') / 'initialization-draft.json').is_file())
                request.return_value = {'summaries': {'calculator.py': 'Stores one value.'}}
                draft = make_initialization_draft(ws, 'example', base, describe, included=['calculator.py'])
                self.assertEqual(draft['unreviewed'], [])
                self.assertEqual(draft['summaries']['calculator.py'], 'Stores one value.')
                self.assertEqual(request.call_count, 3)

    def test_failed_correction_preserves_previous_file_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            for name in ('a.py', 'b.py'):
                (source / name).write_text('value = 1\n', encoding='utf-8')
            ws = Workspace(root / 'workspace')
            base = ws.create('example', source=str(source), include_files=['a.py', 'b.py'])
            with patch('hornet.desktop.automation.request_json') as request:
                request.side_effect = [
                    {'summaries': {'a.py': 'First file.'}},
                    {'summaries': {'wrong.py': 'Wrong.'}},
                    {'summaries': {'wrong.py': 'Still wrong.'}},
                ]
                describe = model_describer('http://localhost:1234/v1', 'model')
                with self.assertRaisesRegex(ValueError, 'unrequested'):
                    make_initialization_draft(ws, 'example', base, describe, included=['a.py', 'b.py'])
                self.assertEqual(request.call_count, 3)
                saved = json.loads((ws.project('example') / 'initialization-draft.json').read_text(encoding='utf-8'))
                self.assertEqual(saved['summaries']['a.py'], 'First file.')
                self.assertNotIn('b.py', saved['summaries'])
                self.assertEqual(read_state(ws, 'example')['status'], 'uninitialized')
                request.reset_mock(side_effect=True)
                request.return_value = {'summaries': {'b.py': 'Second file.'}}
                result = make_initialization_draft(ws, 'example', base, describe, included=['a.py', 'b.py'])
                self.assertEqual(result['unreviewed'], [])
                request.assert_called_once()
                self.assertEqual(set(request.call_args.args[3]['files']), {'b.py'})

    def test_multi_file_mission_memory_requires_exact_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            (source / 'one.py').write_text('value = 1\n', encoding='utf-8')
            (source / 'two.py').write_text('value = 2\n', encoding='utf-8')
            (source / 'untouched.py').write_text('value = 0\n', encoding='utf-8')
            ws = Workspace(root / 'workspace')
            base = ws.create('example', source=str(source), include_files=['one.py', 'two.py', 'untouched.py'])
            describe = lambda payload: {'summaries': {name: 'Initial summary.' for name in payload['files']}}
            make_initialization_draft(ws, 'example', base, describe, included=['one.py', 'two.py', 'untouched.py'])
            ready = accept_initialization(ws, 'example')
            proposal = {'summary': 'Update both files.', 'files': {'one.py': 'value = 3\n', 'two.py': 'value = 4\n'}}

            def run_with_summaries(summaries):
                def respond(endpoint, model, instruction, payload, **kwargs):
                    if 'Choose only the files' in instruction:
                        return {'existing_files': ['one.py', 'two.py'], 'new_files': [], 'explanation': 'Update both.'}
                    self.assertEqual(set(payload['files']), {'one.py', 'two.py'})
                    self.assertEqual(set(payload['inventory']), {'one.py', 'two.py'})
                    self.assertEqual(set(payload['structural_shadows']), {'one.py', 'two.py'})
                    self.assertIn('"one.py"', instruction)
                    self.assertIn('"two.py"', instruction)
                    return {'summaries': summaries}
                with patch('hornet.desktop.automation.request_json', side_effect=respond), \
                     patch('hornet.desktop.registry_mission.propose_with_tools', return_value=(proposal, [])):
                    return prepare_mission(ws, 'example', ready, 'Update both.', 'http://localhost:1234/v1', 'model')

            for summaries in ({'one.py': 'Only one.'},
                              {'one.py': 'One.', 'two.py': 'Two.', 'other.py': 'Extra.'}):
                with self.subTest(summaries=summaries), self.assertRaisesRegex(ValueError, 'descriptions invalid'):
                    run_with_summaries(summaries)
                self.assertEqual(read_state(ws, 'example')['accepted_revision'], ready)

            result = run_with_summaries({'one.py': 'First value updated.', 'two.py': 'Second value updated.'})
            self.assertEqual(result['state'], 'review')
            self.assertEqual(set(result['changed_files']), {'one.py', 'two.py'})
            self.assertEqual(read_state(ws, 'example')['accepted_revision'], ready)
            final = ws.revision('example', result['final_revision'])
            shadows = json.loads((final / '.hornet-meta' / 'shadows.json').read_text(encoding='utf-8'))
            self.assertEqual(shadows['one.py']['summary'], 'First value updated.')
            self.assertEqual(shadows['two.py']['summary'], 'Second value updated.')
