"""Agent-created contracts, typed execution, failure retention and operator inbox."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from hornet.cli import build_parser
from hornet.coding import autonomous, cells, value_schema, mission
from hornet.coding.control import Channel, PeerInbox, send, StopRequested
from hornet.project import HornetError, Project
import test_acceptance as fixtures
from test_resume import call


class CellTests(unittest.TestCase):
    def test_strict_primitive_and_nested_types(self):
        for kind, good, bad in [('boolean', False, 0), ('integer', 3, True), ('number', 1.5, False),
                                ('string', '', 0), ('null', None, False)]:
            self.assertIs(value_schema.validate_value(good, {'type': kind}), good)
            with self.assertRaises(HornetError):
                value_schema.validate_value(bad, {'type': kind})
        schema = {'type': 'object', 'properties': {'flags': {'type': 'array', 'items': {'type': 'boolean'}}},
                  'required': ['flags'], 'additionalProperties': False}
        self.assertEqual(value_schema.validate_value({'flags': [False, True]}, schema), {'flags': [False, True]})
        for value in ({}, {'flags': [0]}, {'flags': [], 'extra': 1}):
            with self.assertRaises(HornetError):
                value_schema.validate_value(value, schema)

    def test_bad_schemas_and_nonfinite_values(self):
        for text in ('{}', '{"type":"boolean","type":"integer"}', '{"type":"integer","minimum":0}',
                     '{"type":"array"}', '{"type":"object","required":["missing"]}'):
            with self.assertRaises(HornetError):
                value_schema.parse(text)
        for value in (float('nan'), float('inf'), 'x' * 5000):
            with self.assertRaises(HornetError):
                value_schema.validate_value(value, {'type': 'number' if not isinstance(value, str) else 'string'})
        with self.assertRaises(HornetError):
            value_schema.validate_value(True, {'type': 'boolean', 'enum': [1]})

    def test_invalid_paths_and_dependency_order(self):
        for path in ('../x.py', '/tmp/x.py', 'a//b.py', '.git/a.py', 'C:/file.py', 'a/./x.py'):
            with self.assertRaises(HornetError):
                cells.path(path)
        plan = copy.deepcopy(autonomous.PLAN_EXAMPLE)
        plan['cells'][0]['depends_on'] = ['future']
        with self.assertRaises(HornetError):
            cells.validate(plan)
        plan['cells'][0]['depends_on'] = []
        plan['cells'][0]['result']['args_json'] = '[{"$cell":"future"}]'
        with self.assertRaises(HornetError):
            cells.validate(plan)

    def test_wrong_call_argument_shape_explains_the_required_correction(self):
        for field, value, explanation in [('args_json', '3', 'JSON array'), ('kwargs_json', '[]', 'JSON object')]:
            plan = copy.deepcopy(autonomous.PLAN_EXAMPLE)
            plan['cells'][0]['result'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(HornetError, explanation):
                cells.validate(plan)


class AutonomousTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hornet-autonomous-')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.root, self.guest = base / 'host', base / 'guest'
        self.root.mkdir()
        self.guest.mkdir()
        (self.root / 'worker.json').write_text('{}')
        self.source = self.guest / 'numbers.py'
        self.source.write_text('def is_even(value):\n    return value % 2 == 1\n')
        self.worker = fixtures.CheckWorker(self.guest)
        self.project = Project(self.root)
        self.plan = copy.deepcopy(autonomous.PLAN_EXAMPLE)
        self.plan['cells'][0]['task'] = 'Correct is_even; return True for even integers and False for odd integers.'
        self.checks = {'schema_version': 1, 'checks': [{'id': 'parity', 'description': 'Correct positive, zero and negative parity',
            'paths': ['numbers.py'], 'timeout': 5,
            'python': 'from numbers import is_even\nassert is_even(2) == True\nassert is_even(3) == False\nassert is_even(0) == True\nassert is_even(-3) == False'}]}
        # Avoid shadowing the standard-library numbers module in isolated child processes.
        self.source = self.source.rename(self.guest / 'parity_cell.py')
        self.plan['cells'][0]['files'] = ['parity_cell.py']
        self.plan['cells'][0]['result']['path'] = 'parity_cell.py'
        self.checks['checks'][0]['paths'] = ['parity_cell.py']
        self.checks['checks'][0]['python'] = self.checks['checks'][0]['python'].replace('from numbers', 'from parity_cell')
        self.args = argparse.Namespace(goal='Fix is_even in parity_cell.py. Return True for even integers, False for odd. Evaluate is_even(3).',
            goal_file=None, worker_config='worker.json', model='fixture', base_url='http://localhost:1234/v1',
            protocol='lmstudio-json', max_tokens=2048, timeout=30, max_turns=30, max_tools=40,
            max_repairs=2, context_chars=16000, max_cells=3, cell_turns=10, cell_tools=20, run_dir='recipes')
        self.worker_turn = 0
        # These fixtures exercise the legacy role protocol and exact request budgets.
        # New pre-freeze review behavior has its own enabled-by-default tests.
        self.args.contract_review = False

    def submit(self, name, value, ident):
        return call(name, value, ident), {}

    def model(self, messages, tools, *unused):
        names = {item['function']['name'] for item in tools}
        if 'submit_plan' in names:
            return self.submit('submit_plan', self.plan, 'plan')
        if 'submit_goal_plan' in names or 'submit_intake' in names:
            plan = copy.deepcopy(self.plan)
            plan['schema_version'] = 2
            plan['coverage'] = [{'requirement_id': item['id'],
                'cell_ids': [plan['cells'][-1]['id']] if 'FUTURE_ONLY_REQUIREMENT' in item['text'] else
                            [cell['id'] for cell in plan['cells']]}
                for item in mission.document(self.args.goal)['requirements']]
            if 'submit_intake' in names:
                return self.submit('submit_intake', {'schema_version': 1,
                    'mode': 'execute' if len(plan['cells']) == 1 else 'delegate-and-work',
                    'reason': 'Bounded scoped work', 'self_cell_id': plan['cells'][0]['id'], 'plan': plan}, 'intake')
            return self.submit('submit_goal_plan', plan, 'plan')
        if 'submit_goal_checks' in names:
            return self.submit('submit_goal_checks', self.checks, 'goal-checks')
        if 'submit_checks' in names:
            self.assertIn('Do not test later cells', messages[0]['content'])
            self.assertFalse(any(item.get('tool_calls') for item in messages) and len(messages) == 2)
            return self.submit('submit_checks', self.checks, 'checks')
        self.worker_turn += 1
        if self.worker_turn == 1:
            return call('read_file', {'path': 'parity_cell.py'}, 'read'), {}
        if self.worker_turn == 2:
            return self.write('    return value % 2 == 0\n', 'edit')
        return {'role': 'assistant', 'content': 'Implemented and ready for controller verification.'}, {}

    def write(self, expression, ident):
        return call('write_file', {'path': 'parity_cell.py', 'content': 'def is_even(value):\n' + expression,
            'expected_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}, ident), {}

    def run_dir(self):
        return next((self.root / 'recipes').iterdir())

    def state(self):
        return json.loads((self.run_dir() / 'recipe-checkpoint.json').read_text())

    def test_agents_plan_check_and_execute_false_result_without_human_file(self):
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertIs(result['values']['check-even'], False)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['model_turns'], 6)
        run = self.run_dir()
        self.assertTrue((run / 'recipe-plan.json').exists())
        self.assertTrue((run / 'cells/check-even/acceptance.json').exists())
        self.assertTrue((run / 'final-checks/check-even.json').exists())
        self.assertEqual(json.loads((run / 'attempts/1/result.json').read_text())['historical_outcome'], 'succeeded')
        with self.assertRaisesRegex(HornetError, 'already complete'):
            autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker, self.model)

    def test_downstream_roles_receive_current_contract_instead_of_future_goal(self):
        self.args.goal += '\nFUTURE_ONLY_REQUIREMENT belongs to a later cell.'
        future = copy.deepcopy(self.plan['cells'][0])
        future.update(id='later-cell', task='FUTURE_ONLY_REQUIREMENT belongs here.', depends_on=['check-even'])
        self.plan['cells'].append(future)
        self.args.max_turns = 2
        def model(messages, tools, *unused):
            names = {item['function']['name'] for item in tools}
            if names.intersection({'submit_intake', 'submit_goal_plan', 'submit_goal_checks'}):
                self.assertIn('FUTURE_ONLY_REQUIREMENT', json.dumps(messages))
            else:
                self.assertNotIn('FUTURE_ONLY_REQUIREMENT', json.dumps(messages))
                self.assertIn('is_even', json.dumps(messages))
            return self.model(messages, tools)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertFalse(result['ok'], result)
        self.assertIn('budget', result['error'])

    def test_single_cell_retains_verbatim_original_requirements(self):
        self.args.goal += '\nORIGINAL_REQUIRED_UNKNOWN must remain visible to this single cell.'
        def model(messages, tools, *unused):
            self.assertIn('ORIGINAL_REQUIRED_UNKNOWN', json.dumps(messages))
            return self.model(messages, tools)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)

    def test_typed_check_rejects_int_that_behaves_like_boolean_and_repairs(self):
        def model(messages, tools, *unused):
            if any(item['function']['name'].startswith('submit_') for item in tools):
                return self.model(messages, tools)
            self.worker_turn += 1
            if self.worker_turn == 1:
                return self.write('    return int(value % 2 == 0)\n', 'wrong-type')
            if self.worker_turn == 3:
                self.assertIn('typed-return', json.dumps(messages))
                return self.write('    return value % 2 == 0\n', 'correct-type')
            return {'role': 'assistant', 'content': 'Verify actual return type.'}, {}
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        child = self.state()['cells']['check-even']['result']
        self.assertEqual(child['repairs_used'], 1)
        self.assertIs(result['values']['check-even'], False)

    def test_invalid_plan_is_marked_and_retained_before_valid_plan(self):
        first = [True]
        def model(messages, tools, *unused):
            if first[0]:
                first[0] = False
                return self.submit('submit_intake', {'schema_version': 1, 'mode': 'execute', 'reason': 'Bad plan',
                    'self_cell_id': '', 'plan': {'schema_version': 2, 'cells': [], 'coverage': []}}, 'bad-plan')
            return self.model(messages, tools)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        history = self.state()['roles']['intake']['history']
        failures = [item for item in history if item.get('result', {}).get('ok') is False]
        self.assertEqual(len(failures), 1)
        self.assertIn('bad-plan', json.dumps(history))

    def test_failed_attempt_remains_available_after_later_conditions_succeed(self):
        def offline(*unused):
            raise HornetError('Model service temporarily offline')
        failed = autonomous.start(self.project, self.args, self.worker, offline)
        self.assertFalse(failed['ok'])
        run = self.run_dir()
        old = (run / 'attempts/1/result.json').read_bytes()
        result = autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual((run / 'attempts/1/result.json').read_bytes(), old)
        self.assertEqual(json.loads(old)['historical_outcome'], 'failed')
        self.assertTrue((run / 'attempts/1/conditions.json').exists())
        self.assertEqual(json.loads((run / 'attempts/2/result.json').read_text())['historical_outcome'], 'succeeded')

    def test_existing_attempt_directory_is_retained_after_stale_counter(self):
        def offline(*unused):
            raise HornetError('Offline')
        autonomous.start(self.project, self.args, self.worker, offline)
        run = self.run_dir()
        original = (run / 'attempts/1/conditions.json').read_bytes()
        state = self.state()
        state['attempts'] = 0
        (run / 'recipe-checkpoint.json').write_text(json.dumps(state))
        result = autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual((run / 'attempts/1/conditions.json').read_bytes(), original)
        self.assertEqual(self.state()['attempts'], 2)

    def test_corrupt_shared_counters_are_rejected_before_resume(self):
        def offline(*unused):
            raise HornetError('Offline')
        autonomous.start(self.project, self.args, self.worker, offline)
        run = self.run_dir()
        state = self.state()
        state['cell_usage'] = {'../outside': 1}
        (run / 'recipe-checkpoint.json').write_text(json.dumps(state))
        with self.assertRaisesRegex(HornetError, 'checkpoint'):
            autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker, self.model)

    def test_shared_model_budget_is_not_reset_for_worker(self):
        self.args.max_turns = 2
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertFalse(result['ok'])
        self.assertEqual(result['model_turns'], 2)
        self.assertNotIn('write_file', self.worker.calls)
        self.assertIn('budget', result['error'])

    def test_frozen_plan_tampering_refuses_resume(self):
        def interrupted(messages, tools, *unused):
            if any(item['function']['name'] == 'submit_intake' for item in tools):
                return self.model(messages, tools)
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            autonomous.start(self.project, self.args, self.worker, interrupted)
        (self.run_dir() / 'recipe-plan.json').write_text('{}')
        with self.assertRaisesRegex(HornetError, 'frozen contract'):
            autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)

    def test_scoped_worker_refuses_edits_elsewhere(self):
        scoped = autonomous.ScopedWorker(self.worker, ['allowed.py'])
        result = scoped.call('write_file', {'path': 'outside.py', 'content': 'x', 'expected_sha256': ''})
        self.assertFalse(result['ok'])
        self.assertFalse((self.guest / 'outside.py').exists())

    def test_constant_assertions_are_not_accepted_as_generated_checks(self):
        self.checks['checks'][0]['python'] = 'assert True'
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertFalse(result['ok'])
        self.assertIn('six attempts', result['error'])
        self.assertNotIn('write_file', self.worker.calls)

    def test_assert_false_is_a_valid_failure_branch(self):
        self.checks['checks'][0]['python'] += '\nif is_even(2) is not True:\n    assert False, "Expected an actual True value"'
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertTrue(result['ok'], result)

    def test_exception_checks_can_fail_with_explicit_assertion_error(self):
        self.checks['checks'][0]['python'] = ('from parity_cell import is_even\n'
            'if is_even(3) is not False:\n    raise AssertionError("Expected False")')
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertTrue(result['ok'], result)

    def test_role_inbox_is_inside_the_serialized_context_budget(self):
        original = self.model
        injected = [False]
        def model(messages, tools, *unused):
            from hornet.coding.context import history_chars
            self.assertLessEqual(history_chars(messages, tools), self.args.context_chars)
            if not injected[0] and any(item['function']['name'] == 'submit_goal_checks' for item in tools):
                injected[0] = True
                for index in range(8):
                    send(self.project, str(self.run_dir()), 'message', 'Keep the cell focused. ' + 'x' * 1800)
                return call('read_file', {'path': 'parity_cell.py'}, 'read-context'), {}
            return original(messages, tools)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(self.state()['steering']), 8)

    def test_pause_inbox_receives_information_then_continues(self):
        state = {'steering': [], 'control_seen': []}
        run = self.root / 'control-test'
        run.mkdir()
        (run / 'recipe-checkpoint.json').write_text('{}')
        stages, sleeps, saves = [], [], []
        send(self.project, str(run), 'message', 'Use exact equality and preserve False.')
        send(self.project, str(run), 'pause')
        def sleep(seconds):
            sleeps.append(seconds)
            send(self.project, str(run), 'continue')
        channel = Channel(run, state, lambda: saves.append(True), lambda stage, **unused: stages.append(stage), sleep=sleep)
        job = {'history': []}
        channel.boundary(job)
        channel.boundary(job)
        self.assertEqual(stages, ['paused'])
        self.assertEqual(len(sleeps), 1)
        self.assertEqual(len(state['steering']), 1)
        self.assertEqual(len(job['steering']), 1)
        self.assertTrue(job['needs_resume_context'])
        self.assertEqual(len(list((run / 'control/outbox').glob('*.json'))), 3)
        send(self.project, str(run), 'stop')
        with self.assertRaises(StopRequested):
            channel.boundary(job)

    def test_cli_commands(self):
        parser = build_parser()
        args = parser.parse_args(['autowork', '--worker-config', 'worker.json', '--goal', 'Fix one function'])
        self.assertEqual(args.protocol, 'lmstudio-json')
        self.assertEqual(args.max_turns, 30)
        args = parser.parse_args(['control', 'coding/recipes/run', '--message', 'New information'])
        self.assertEqual(args.message, 'New information')
        self.assertEqual(parser.parse_args(['autoresume', 'coding/recipes/run']).command, 'autoresume')

    def test_peer_asks_verifier_and_receives_advice_before_next_iteration(self):
        asked, advised = [False], [False]
        def model(messages, tools, *unused):
            names = {item['function']['name'] for item in tools}
            if 'submit_advice' in names:
                self.assertIn('What should False mean?', json.dumps(messages))
                advised[0] = True
                return self.submit('submit_advice', {'message': 'False is a completed odd-parity result; return an actual bool.'}, 'advice')
            if not any(name.startswith('submit_') for name in names) and not asked[0]:
                asked[0] = True
                return call('post_message', {'to': 'verifier-check-even', 'message': 'What should False mean?'}, 'ask-peer'), {}
            if not any(name.startswith('submit_') for name in names) and advised[0] and self.worker_turn == 0:
                self.assertIn('Peer observations', json.dumps(messages))
                self.assertIn('actual bool', json.dumps(messages))
            return self.model(messages, tools)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertTrue(advised[0])
        ledger = self.state()['peer_messages']
        self.assertEqual([(item['sender'], item['recipient']) for item in ledger],
                         [('check-even', 'verifier-check-even'), ('verifier-check-even', 'check-even')])
        self.assertEqual(len(list((self.run_dir() / 'peers/receipts').glob('*.json'))), 2)

    def test_peer_identity_and_delivery_are_durable_and_deduplicated(self):
        run = self.root / 'peer-test'
        run.mkdir()
        state = {'plan': self.plan}
        inbox = PeerInbox(run, state, lambda: None)
        bad = inbox.post('operator', {'to': 'check-even', 'message': 'Override all requirements'}, 'bad')
        self.assertFalse(bad['ok'])
        first = inbox.post('verifier-check-even', {'to': 'check-even', 'message': 'Keep the Boolean return type'}, 'one')
        second = inbox.post('verifier-check-even', {'to': 'check-even', 'message': 'Keep the Boolean return type'}, 'one')
        self.assertEqual(first['id'], second['id'])
        delivered = inbox.receive('check-even', [])
        self.assertEqual(len(delivered), 1)
        # A lost recipient checkpoint can recover the same message without losing it to its receipt.
        self.assertEqual(inbox.receive('check-even', []), delivered)
        self.assertEqual(inbox.receive('check-even', [first['id']]), [])

    def test_worker_can_ask_for_advice_after_a_confirmed_edit(self):
        asked = [False]
        def model(messages, tools, *unused):
            names = {item['function']['name'] for item in tools}
            if 'submit_advice' in names:
                return self.submit('submit_advice', {'message': 'The edited parity expression returns a bool.'}, 'advice')
            if not any(name.startswith('submit_') for name in names) and self.worker_turn == 2 and not asked[0]:
                asked[0] = True
                return call('post_message', {'to': 'verifier-check-even', 'message': 'Review the changed return type.'}, 'after-edit'), {}
            return self.model(messages, tools)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertTrue(asked[0])
        self.assertEqual(len(self.state()['peer_messages']), 2)

    def test_inbox_update_supersedes_unexecuted_edit_and_reprompts(self):
        injected = [False]
        def model(messages, tools, *unused):
            if any(item['function']['name'].startswith('submit_') for item in tools):
                return self.model(messages, tools)
            if not injected[0]:
                injected[0] = True
                send(self.project, str(self.run_dir()), 'message', 'Read the current file before making that edit.')
                return self.write('    return False\n', 'superseded')
            return self.model(messages, tools)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        child_dir = Path(self.state()['cells']['check-even']['run_dir'])
        history = json.loads((child_dir / 'transcript.json').read_text())
        dropped = [item for item in history if item.get('call_id') == 'superseded']
        self.assertEqual(len(dropped), 1)
        self.assertIs(dropped[0]['result']['cancelled'], True)
        self.assertEqual(self.worker.calls.count('write_file'), 1)

    def test_two_cells_pass_false_to_json_payload_and_recheck_dependencies(self):
        (self.guest / 'label_cell.py').write_text('def describe(value):\n    return {}\n')
        self.plan['cells'].append({'id': 'describe-parity', 'task': 'Return a JSON object with even set to the supplied Boolean.',
            'files': ['label_cell.py'], 'depends_on': ['check-even'], 'result': {'path': 'label_cell.py', 'function': 'describe',
            'args_json': '[{"$cell":"check-even"}]', 'kwargs_json': '{}',
            'schema_json': '{"type":"object","properties":{"even":{"type":"boolean"}},"required":["even"],"additionalProperties":false}'}})
        second_turn = [0]
        def model(messages, tools, *unused):
            names = {item['function']['name'] for item in tools}
            text = json.dumps(messages)
            if 'submit_checks' in names and 'describe-parity' in text:
                return self.submit('submit_checks', {'schema_version': 1, 'checks': [{'id': 'description',
                    'description': 'Preserve the input Boolean in the object', 'paths': ['label_cell.py'], 'timeout': 5,
                    'python': 'from label_cell import describe\nassert describe(False) == {"even": False}\nassert describe(True) == {"even": True}'}]}, 'second-checks')
            if not any(name.startswith('submit_') for name in names) and 'label_cell.py' in text:
                second_turn[0] += 1
                if second_turn[0] == 1:
                    source = self.guest / 'label_cell.py'
                    return call('write_file', {'path': 'label_cell.py', 'content': 'def describe(value):\n    return {"even": value}\n',
                        'expected_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}, 'second-edit'), {}
                return {'role': 'assistant', 'content': 'Second cell is ready.'}, {}
            return self.model(messages, tools)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], {'check-even': False, 'describe-parity': {'even': False}})
        self.assertEqual(len(list((self.run_dir() / 'final-checks').glob('*.json'))), 2)
        first = result['return_manifests']['check-even']
        second = json.loads((self.run_dir() / result['return_manifests']['describe-parity']['path']).read_text())
        self.assertEqual(second['dependencies'], {'check-even': first})
        self.assertIs(second['result']['args'][0], False)
        self.assertIs(second['result']['value']['even'], False)

    def test_legacy_prompt_file_is_archived_acknowledged_and_deduplicated(self):
        run = self.root / 'legacy-inbox'
        run.mkdir()
        state = {'steering': [], 'control_seen': []}
        (run / 'commander_input.txt').write_text('New input: preserve the negative cases.', encoding='utf-8')
        channel = Channel(run, state, lambda: None, lambda *args, **kwargs: None)
        job = {'history': []}
        channel.boundary(job)
        channel.boundary(job)
        self.assertEqual(len(job['steering']), 1)
        self.assertTrue((run / 'commander_input.txt').exists())
        self.assertEqual(len(list((run / 'control/received').glob('*.txt'))), 1)
        self.assertEqual(json.loads((run / 'commander_response.txt').read_text())['status'], 'received_at_boundary')

    def test_uncertain_child_edit_is_retained_and_not_replayed(self):
        original = self.worker.call
        def interrupted(name, arguments=None):
            result = original(name, arguments)
            if name == 'write_file':
                raise KeyboardInterrupt()
            return result
        self.worker.call = interrupted
        with self.assertRaises(KeyboardInterrupt):
            autonomous.start(self.project, self.args, self.worker, self.model)
        self.worker.call = original
        before = self.source.read_bytes()
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertFalse(result['ok'])
        self.assertIn('outcome is unknown', result['error'])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.worker.calls.count('write_file'), 1)
        self.assertTrue((self.run_dir() / 'attempts/1/result.json').exists())

    def test_malformed_planning_tool_does_not_execute(self):
        result = autonomous.start(self.project, self.args, self.worker,
            lambda *args: ({'role': 'assistant', 'tool_calls': ['invalid']}, {}))
        self.assertFalse(result['ok'])
        self.assertIn('Malformed planning tool', result['error'])
        self.assertNotIn('write_file', self.worker.calls)

    def test_verifier_rejects_checks_depending_on_worker_test_modules(self):
        rejected = [False]
        def model(messages, tools, *unused):
            if 'submit_checks' in {item['function']['name'] for item in tools} and not rejected[0]:
                rejected[0] = True
                checks = copy.deepcopy(self.checks)
                checks['checks'][0]['python'] = 'from tests.test_future import test_behavior\nassert test_behavior()'
                return self.submit('submit_checks', checks, 'dependent-checks')
            return self.model(messages, tools)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertTrue(rejected[0])
        history = self.state()['roles']['verifier-check-even']['history']
        self.assertTrue(any('not import test modules' in str(item) for item in history))

    def test_later_cell_cannot_regress_an_earlier_cell(self):
        self.plan['cells'].append({'id': 'describe-parity', 'task': 'Add describe(value), returning its Boolean in an even object field.',
            'files': ['parity_cell.py'], 'depends_on': ['check-even'], 'result': {'path': 'parity_cell.py', 'function': 'describe',
            'args_json': '[{"$cell":"check-even"}]', 'kwargs_json': '{}',
            'schema_json': '{"type":"object","properties":{"even":{"type":"boolean"}},"required":["even"]}'}})
        edited = [False]
        def model(messages, tools, *unused):
            names = {item['function']['name'] for item in tools}
            text = json.dumps(messages)
            if 'submit_checks' in names and 'describe-parity' in text:
                return self.submit('submit_checks', {'schema_version': 1, 'checks': [{'id': 'description',
                    'description': 'Keep the supplied Boolean', 'paths': ['parity_cell.py'], 'timeout': 5,
                    'python': 'from parity_cell import describe\nassert describe(False) == {"even": False}\nassert describe(True) == {"even": True}'}]}, 'describe-check')
            if not any(name.startswith('submit_') for name in names) and self.state().get('active_cell') == 'describe-parity':
                if not edited[0]:
                    edited[0] = True
                    return self.write('    return False\n\ndef describe(value):\n    return {"even": value}\n', 'regression')
                return {'role': 'assistant', 'content': 'Describe is implemented.'}, {}
            return self.model(messages, tools)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertFalse(result['ok'])
        self.assertIn('Final cell checks', result['error'])
        self.assertFalse(self.state()['final_checks_in_flight'])


if __name__ == '__main__':
    unittest.main()
