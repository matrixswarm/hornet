"""Pre-freeze corrections, strict example receipts and durable review evidence."""
import argparse
import copy
import json
import unittest
from unittest.mock import patch

from hornet.coding import autonomous, contract_review as review, contract_wire, mission
from hornet.project import HornetError
import test_autonomous as fixtures
from test_resume import call


def plan(args='[3]', schema=None):
    value = copy.deepcopy(autonomous.PLAN_EXAMPLE)
    value['cells'][0]['result'].update(path='counter.py', function='offset', args_json=args,
        schema_json=json.dumps(schema or {'type': 'integer', 'enum': [7]}))
    return value


class LiteralTests(unittest.TestCase):
    def test_calculator_plan_selects_one_of_three_required_examples(self):
        goal = 'double(-3) == -6, double(0) == 0, double(7) == 14.'
        proposal = plan('[7]', {'type': 'integer', 'enum': [14]})
        proposal['cells'][0]['result'].update(path='calculator.py', function='double')
        review.preflight(goal, 'submit_intake', {'plan': proposal})
        proposal['cells'][0]['result']['schema_json'] = '{"type":"integer","enum":[-6,0,14]}'
        with self.assertRaisesRegex(HornetError, 'exact example'):
            review.preflight(goal, 'submit_intake', {'plan': proposal})
        with self.assertRaisesRegex(HornetError, 'one Python call'):
            contract_wire.parse_call('[double(-3), double(0), double(7)]')

    def test_review_instructions_distinguish_plan_and_check_coverage(self):
        for submission in review.SUBMISSIONS:
            prompt = review.review_prompt(submission)
            if submission in {'submit_checks', 'submit_goal_checks'}:
                self.assertIn(review.CHECK_REVIEW, prompt)
                self.assertNotIn(review.PLAN_REVIEW, prompt)
            else:
                self.assertIn(review.PLAN_REVIEW, prompt)
                self.assertNotIn(review.CHECK_REVIEW, prompt)
        with self.assertRaises(HornetError):
            review.review_prompt('write_file')
        self.assertIn('"enum":[false]', contract_wire.CALL_GUIDANCE)
        self.assertIn('singleton enum', contract_wire.CALL_GUIDANCE)

    def test_changed_review_instructions_get_new_cache_identity(self):
        state = {'roles': {}, 'goal': 'offset(3)=7.', 'goal_sha256': 'goal'}
        first = review.packet(state, 'intake', 'submit_intake', {'plan': plan()}, None, {})
        legacy = dict(first)
        legacy.pop('review_instructions_sha256')
        self.assertNotEqual(review.acceptance.digest(first), review.acceptance.digest(legacy))
        with patch.object(review, 'PLAN_REVIEW', review.PLAN_REVIEW + ' Changed guidance.'):
            second = review.packet(state, 'intake', 'submit_intake', {'plan': plan()}, None, {})
        self.assertNotEqual(review.acceptance.digest(first), review.acceptance.digest(second))
        self.assertEqual(first['candidate'], second['candidate'])

    def test_review_presents_candidate_last_without_changing_evidence(self):
        packet = {'candidate': {'checks': []}, 'requirements': 'Required behavior', 'role': 'verifier'}
        before = review.acceptance.digest(packet)
        presented = review.review_task(packet)
        self.assertEqual(json.loads(presented), packet)
        self.assertEqual(list(json.loads(presented))[-1], 'candidate')
        self.assertEqual(review.acceptance.digest(packet), before)

    def test_check_review_exposes_parsed_exception_control_flow(self):
        import ast
        source = ('try:\n    validate(True)\nexcept TypeError:\n    pass\n'
                  '# independent negative case\try:\n    validate(-1)\n'
                  'except ValueError:\n    pass\n')
        packet = {'submission': 'submit_checks', 'candidate': {'checks': [{'id': 'types', 'python': source}]}}
        before = review.acceptance.digest(packet)
        normalized = json.loads(review.review_task(packet))['candidate']['checks'][0]['python']
        self.assertNotIn('# independent', normalized)
        self.assertIn('except TypeError:\n    pass\n    validate(-1)', normalized)
        self.assertEqual(ast.dump(ast.parse(source)), ast.dump(ast.parse(normalized)))
        self.assertEqual(review.acceptance.digest(packet), before)
        self.assertIn('inside an except handler', review.CHECK_REVIEW)
        self.assertIn('assertRaises', review.CHECK_AUTHOR_GUIDANCE)
        for guidance in (review.CHECK_REVIEW, review.CHECK_AUTHOR_GUIDANCE):
            self.assertIn('test discovery does not run automatically', guidance)
            self.assertIn('test class', guidance)

    def test_exact_typed_examples(self):
        found = review.examples('offset(3)=7, offset(-5)=-9. f(true) returns false. g("3") == "7".')
        self.assertEqual([e['expected'] for e in found], [7, -9, False, '7'])
        self.assertEqual(found[2]['args'], [True])
        self.assertEqual(found[3]['args'], ['3'])

    def test_expressions_and_code_are_never_evaluated(self):
        self.assertEqual(review.examples('f(n)=2*n. f(3)=7*n. f(open("x"))=2.'), [])

    def test_bad_argument_from_real_failure_is_rejected(self):
        with self.assertRaisesRegex(HornetError, 'argument type mismatch'):
            review.preflight('counter.offset(3)=7.', 'submit_plan', plan('["3"]'))

    def test_type_alone_cannot_accept_34_instead_of_7(self):
        with self.assertRaisesRegex(HornetError, 'exact example'):
            review.preflight('offset(3)=7.', 'submit_plan', plan(schema={'type': 'integer'}))
        review.preflight('offset(3)=7.', 'submit_plan', plan())

    def test_wrong_return_shape_explains_the_actual_value(self):
        with self.assertRaisesRegex(HornetError, 'call must return 7'):
            review.preflight('offset(3)=7.', 'submit_plan', plan(schema={
                'type': 'object', 'properties': {'offset': {'type': 'string'}}}))

    def test_conflicting_examples_fail_closed(self):
        with self.assertRaisesRegex(HornetError, 'Conflicting'):
            review.examples('offset(3)=7; offset(3)=34.')

    def test_qualified_modules_are_distinct(self):
        review.preflight('other.offset(3)=8.', 'submit_plan', plan())

    def test_wrong_negative_arithmetic_assertion_rejected(self):
        checks = {'checks': [{'python': 'import counter as m\nassert m.offset(-5) == -8'}]}
        with self.assertRaisesRegex(HornetError, 'contradicts'):
            review.preflight('counter.offset(-5)=-9.', 'submit_checks', checks)
        checks['checks'][0]['python'] = 'from counter import offset as f\nassert -9 == f(-5)'
        review.preflight('counter.offset(-5)=-9.', 'submit_checks', checks)

    def test_enum_preserves_boolean_type(self):
        with self.assertRaises(HornetError):
            review.preflight('offset(3)=true.', 'submit_plan', plan(schema={'type': 'integer', 'enum': [1]}))

    def test_review_cannot_approve_and_list_issues(self):
        for value in ({'decision': 'approve', 'issues': ['Wrong'], 'rationale': 'No'},
                      {'decision': 'uncertain', 'issues': [], 'rationale': 'Unknown'}):
            with self.assertRaises(HornetError):
                review.verdict(value)

    def test_nested_split_uses_same_literal_gate(self):
        proposal = {'route': 'needs_split', 'plan': plan('["3"]')}
        with self.assertRaisesRegex(HornetError, 'argument type mismatch'):
            review.preflight('offset(3)=7.', 'submit_route', proposal)

    def test_keyword_arguments_and_string_returns(self):
        proposal = plan('[]', {'type': 'string', 'enum': ['ok']})
        proposal['cells'][0]['result']['kwargs_json'] = '{"n":3}'
        review.preflight('offset(n=3)="ok".', 'submit_plan', proposal)
        proposal['cells'][0]['result']['kwargs_json'] = '{"n":"3"}'
        with self.assertRaises(HornetError):
            review.preflight('offset(n=3)="ok".', 'submit_plan', proposal)


class CallBindingTests(unittest.TestCase):
    def packet(self, sources):
        candidate = {'checks': [{'id': 'check-' + str(i), 'python': source}
                                for i, source in enumerate(sources)]}
        return review.packet({'roles': {}, 'goal': 'FlatRate returns 500; WeightRate returns 200 + grams * 3.',
                              'goal_sha256': 'goal'}, 'verifier', 'submit_checks', candidate, None, {})

    def test_identically_named_methods_keep_their_own_receiver(self):
        packet = self.packet([
            'from shipping import FlatRate\ninstance = FlatRate()\nassert instance.quote(500) == 500',
            'from shipping import WeightRate\ninstance = WeightRate()\nassert instance.quote(500) == 1700'])
        calls = review.oracle_packet(packet)['calls']
        self.assertEqual([c['call_context']['receiver']['function'] for c in calls],
                         ['shipping.FlatRate', 'shipping.WeightRate'])
        self.assertEqual([c['call_context']['method'] for c in calls], ['quote', 'quote'])
        self.assertEqual([c['args'] for c in calls], [[500], [500]])
        result = {'expectations': [{'id': 'c00', 'status': 'determined', 'value_json': '500', 'basis': 'Flat rate'},
                                   {'id': 'c01', 'status': 'determined', 'value_json': '1700', 'basis': '200 + 1500'}]}
        self.assertEqual(review.oracle_issues(packet, result), [])
        result['expectations'][0]['value_json'] = '1700'
        self.assertTrue(review.oracle_issues(packet, result))

    def test_aliases_and_prior_calls_preserve_order_without_answers(self):
        packet = self.packet(['import shipping as api\ninstance = api.FlatRate(3, mode="x")\n'
            'alias = instance\nassert alias.quote(1) == "SECRET_ANSWER"\nassert instance.quote(2) == 9999'])
        blind = review.oracle_packet(packet)
        first, second = [c['call_context'] for c in blind['calls']]
        self.assertEqual(first['receiver'], {'function': 'shipping.FlatRate', 'args': [3], 'kwargs': {'mode': 'x'}})
        self.assertEqual(first['prior_calls'], [])
        self.assertEqual(second['prior_calls'], [{'method': 'quote', 'args': [1], 'kwargs': {}}])
        self.assertNotIn('SECRET_ANSWER', json.dumps(blind))
        self.assertNotIn('9999', json.dumps(blind))
        self.assertNotIn('candidate', blind)

    def test_constructor_reassignment_and_separate_checks_never_reuse_receivers(self):
        packet = self.packet(['from shipping import FlatRate, WeightRate\ninstance = FlatRate()\n'
            'assert instance.quote(1) == 500\ninstance = WeightRate()\nassert instance.quote(1) == 203',
            'assert instance.quote(1) == 500'])
        contexts = [c['call_context'] for c in review.oracle_packet(packet)['calls']]
        self.assertEqual(contexts[0]['receiver']['function'], 'shipping.FlatRate')
        self.assertEqual(contexts[1]['receiver']['function'], 'shipping.WeightRate')
        self.assertEqual(contexts[1]['prior_calls'], [])
        self.assertEqual(contexts[2], {'resolution': 'unresolved'})

    def test_unknown_control_flow_and_mutations_discard_context(self):
        for mutation in ('instance = factory()', 'mutate(instance)', 'instance.quote = custom',
                         'if condition:\n    instance = factory()', 'del instance',
                         'other = mutate(instance)', 'instance, other = factory()'):
            with self.subTest(mutation=mutation):
                packet = self.packet(['from shipping import FlatRate\ninstance = FlatRate()\n' +
                    mutation + '\nassert instance.quote(500) == 500'])
                self.assertEqual(review.oracle_packet(packet)['calls'][0]['call_context'], {'resolution': 'unresolved'})

    def test_named_import_aliases_are_resolved_in_statement_order(self):
        packet = self.packet(['from shipping import flat as f\nassert f(1) == 500\n'
            'from shipping import weight as f\nassert f(1) == 203'])
        contexts = [c['call_context'] for c in review.oracle_packet(packet)['calls']]
        self.assertEqual([c['function'] for c in contexts], ['shipping.flat', 'shipping.weight'])

    def test_old_packets_keep_original_oracle_shape_and_new_prompt_is_bound(self):
        packet = self.packet(['from shipping import flat\nassert flat(1) == 500'])
        blind = review.oracle_packet(packet)
        self.assertEqual(blind['kind'], 'literal_calls_v2')
        self.assertEqual(blind['instructions_sha256'], review.acceptance.digest(review.BOUND_ORACLE_PROMPT))
        self.assertEqual(review.oracle_prompt(blind), review.BOUND_ORACLE_PROMPT)
        previous = copy.deepcopy(packet)
        previous.pop('call_bindings')
        previous.pop('oracle_instructions_sha256')
        old = review.oracle_packet(previous)
        self.assertNotIn('kind', old)
        self.assertNotIn('call_context', old['calls'][0])
        self.assertEqual(review.oracle_prompt(old), review.ORACLE_PROMPT)
        with patch.object(review, 'BOUND_ORACLE_PROMPT', review.BOUND_ORACLE_PROMPT + ' revised'):
            changed = self.packet(['from shipping import flat\nassert flat(1) == 500'])
        self.assertNotEqual(review.acceptance.digest(changed), review.acceptance.digest(packet))
        self.assertEqual(review.oracle_packet(packet), blind)

    def test_large_context_is_bounded_and_generated_code_is_not_executed(self):
        packet = self.packet(['from shipping import FlatRate\ninstance = FlatRate("' + 'x'*3000 + '")\n'
            'assert instance.quote(1) == 500'])
        self.assertEqual(packet['call_bindings']['c00'], {'resolution': 'unresolved'})
        packet = self.packet(['raise RuntimeError("do not run")\n'
            'from shipping import flat\nassert True; assert flat(1) == 500'])
        self.assertEqual(packet['call_bindings']['c00']['function'], 'shipping.flat')


class ReviewWorkflowTests(unittest.TestCase):
    setUp = fixtures.AutonomousTests.setUp
    submit = fixtures.AutonomousTests.submit
    write = fixtures.AutonomousTests.write
    run_dir = fixtures.AutonomousTests.run_dir
    state = fixtures.AutonomousTests.state

    def prepare(self):
        # Absence of the internal legacy fixture override exercises the production default.
        del self.args.contract_review
        self.args.goal += ' Required example: is_even(3)=false.'
        self.plan['cells'][0]['result']['schema_json'] = '{"type":"boolean","enum":[false]}'
        self.reviews = []

    def model(self, messages, tools, *unused):
        if any(t['function']['name'] == 'submit_contract_oracle' for t in tools):
            self.assertEqual(len(tools), 1)
            packet = json.loads(messages[1]['content'])
            self.assertNotIn('candidate', packet)
            self.assertFalse(any('proposed' in c for c in packet['calls']))
            return call('submit_contract_oracle', {'expectations': [dict(id=c['id'], status='determined',
                basis='Required even parity.', value_json=json.dumps(c['args'][0] % 2 == 0),
                **({'return_type': 'boolean'} if packet.get('kind') == 'plan_returns_v1' else {}))
                for c in packet['calls']]}, 'oracle'), {}
        if any(t['function']['name'] == 'submit_contract_review' for t in tools):
            self.assertEqual(len(tools), 1)  # No shell, reads, writes or delegation.
            self.assertFalse(any(m.get('tool_calls') for m in messages))  # No author exchanges.
            self.reviews.append(json.loads(messages[1]['content']))
            return call('submit_contract_review', {'decision': 'approve', 'issues': [],
                'rationale': 'Requirements, types and example agree.'}, 'review'), {}
        return fixtures.AutonomousTests.model(self, messages, tools, *unused)

    def test_default_review_and_exact_return_receipt(self):
        self.prepare()
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertIs(result['values']['check-even'], False)
        self.assertEqual(len(self.reviews), 3)
        state = autonomous.load(self.project, self.run_dir())
        self.assertEqual(state['model_turns'], 12)
        completion = mission.read(self.run_dir() / 'goal-completion.json')
        self.assertEqual(len(completion['contract_reviews']), 3)

    def test_one_return_call_with_multiple_examples_reaches_independent_checks(self):
        self.prepare()
        self.args.goal += ' Other required examples: is_even(0)=true; is_even(-3)=false.'
        stages = []
        def model(messages, tools, *unused):
            if any(t['function']['name'] == 'submit_contract_review' for t in tools):
                packet = json.loads(messages[1]['content'])
                submission = packet['submission']
                stages.append(submission)
                self.assertIn(review.review_prompt(submission), messages[0]['content'])
                self.assertEqual(packet['review_instructions_sha256'],
                                 review.acceptance.digest(review.review_prompt(submission)))
                if submission == 'submit_intake':
                    self.assertEqual(len(packet['literal_examples']), 3)
                    self.assertEqual(len(packet['candidate']['plan']['cells']), 1)
            return self.model(messages, tools, *unused)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(stages, ['submit_intake', 'submit_goal_checks', 'submit_checks'])
        self.assertEqual(self.state()['roles']['intake']['invalid_submissions'], 0)

    def test_wrong_argument_is_returned_to_author_before_freeze(self):
        self.prepare()
        self.plan['cells'][0]['result']['args_json'] = '["3"]'
        attempts = []
        def model(messages, tools, *unused):
            names = {t['function']['name'] for t in tools}
            if 'submit_intake' in names:
                attempts.append(copy.deepcopy(messages))
                if len(attempts) == 2:
                    self.assertIn('argument type mismatch', json.dumps(messages))
                    self.plan['cells'][0]['result']['args_json'] = '[3]'
            return self.model(messages, tools, *unused)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        state = self.state()
        self.assertEqual(len(state['roles']['intake']['review_attempts']), 2)
        first = mission.read(self.run_dir() / state['roles']['intake']['review_attempts'][0]['path'])
        self.assertEqual(first['origin'], 'literal_preflight')
        self.assertEqual(first['packet']['candidate']['plan']['cells'][0]['result']['args_json'], '["3"]')
        self.assertEqual(state['plan']['cells'][0]['result']['args_json'], '[3]')
        rejected = self.run_dir() / state['roles']['intake']['review_attempts'][0]['path']
        rejected.write_text('{}')
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())

    def test_semantic_rejection_regenerates_checks_without_coding(self):
        self.prepare()
        rejected = [False]
        corrected = [False]
        def model(messages, tools, *unused):
            names = {t['function']['name'] for t in tools}
            if 'submit_contract_review' in names:
                packet = json.loads(messages[1]['content'])
                if packet['submission'] == 'submit_goal_checks' and not rejected[0]:
                    rejected[0] = True
                    self.assertEqual(self.worker_turn, 0)
                    return call('submit_contract_review', {'decision': 'revise',
                        'issues': ['Add the required negative even integer case.'],
                        'rationale': 'Coverage omits negative even behavior.'}, 'reject'), {}
            if 'submit_goal_checks' in names and rejected[0] and not corrected[0]:
                self.assertIn('negative even', json.dumps(messages))
                self.checks['checks'][0]['python'] += '\nassert is_even(-4) is True'
                corrected[0] = True
            return self.model(messages, tools, *unused)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertTrue(corrected[0])
        self.assertEqual(len(self.state()['roles']['goal-verifier']['review_attempts']), 2)

    def test_uncertain_reviewer_never_starts_coding_and_is_bounded(self):
        self.prepare()
        def model(messages, tools, *unused):
            if any(t['function']['name'] == 'submit_contract_review' for t in tools):
                return call('submit_contract_review', {'decision': 'uncertain', 'issues': ['Missing evidence.'],
                    'rationale': 'Cannot establish requested meaning.'}, 'uncertain'), {}
            return self.model(messages, tools, *unused)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertFalse(result['ok'])
        self.assertEqual(self.worker_turn, 0)
        self.assertIsNone(self.state()['plan'])
        self.assertLessEqual(self.state()['model_turns'], 12)
        self.assertTrue(list((self.run_dir() / 'contract-reviews').glob('*.json')))

    def test_resume_after_review_interruption_preserves_rejected_history(self):
        self.prepare()
        interrupted = [False]
        def model(messages, tools, *unused):
            if any(t['function']['name'] == 'submit_contract_review' for t in tools) and not interrupted[0]:
                interrupted[0] = True
                raise HornetError('Simulated model disconnection')
            if interrupted[0]:
                raise HornetError('Offline')
            return self.model(messages, tools, *unused)
        first = autonomous.start(self.project, self.args, self.worker, model)
        self.assertFalse(first['ok'])
        self.assertIsNone(self.state()['plan'])
        before = (self.run_dir() / 'attempts/1/result.json').read_bytes()
        second = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(second['ok'], second)
        self.assertEqual(before, (self.run_dir() / 'attempts/1/result.json').read_bytes())

    def test_resume_rechecks_legacy_rejection_and_preserves_its_evidence(self):
        self.prepare()
        original_packet = review.packet
        def legacy_packet(*args, **kwargs):
            packet = original_packet(*args, **kwargs)
            packet.pop('review_instructions_sha256')
            return packet
        def legacy_model(messages, tools, *unused):
            if any(t['function']['name'] == 'submit_contract_review' for t in tools):
                return call('submit_contract_review', {'decision': 'revise',
                    'issues': ['Put every example in the result call.'],
                    'rationale': 'Old reviewer confused the return call with acceptance tests.'}, 'old-review'), {}
            return self.model(messages, tools, *unused)
        with patch.object(review, 'packet', legacy_packet):
            first = autonomous.start(self.project, self.args, self.worker, legacy_model)
        self.assertFalse(first['ok'])
        self.assertIsNone(self.state()['plan'])
        old = {path: path.read_bytes() for path in (self.run_dir() / 'contract-reviews').glob('*.json')}
        self.assertTrue(old)
        second = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(second['ok'], second)
        for path, content in old.items():
            self.assertEqual(path.read_bytes(), content)
        state = autonomous.load(self.project, self.run_dir())
        approved = mission.read(self.run_dir() / state['roles']['intake']['contract_review']['path'])
        self.assertIn('review_instructions_sha256', approved['packet'])
        self.assertEqual(len(state['roles']['intake']['review_attempts']), 2)

    def test_missing_or_changed_review_prevents_resume(self):
        self.prepare()
        self.assertTrue(autonomous.start(self.project, self.args, self.worker, self.model)['ok'])
        state = self.state()
        ref = state['roles']['intake']['contract_review']
        path = self.run_dir() / ref['path']
        original = path.read_bytes()
        path.write_text('{}')
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())
        path.write_bytes(original)
        state['roles']['intake'].pop('submission')
        autonomous.stored(self.run_dir() / 'recipe-checkpoint.json', state)
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())

    def test_review_cannot_escape_tools_or_start_coding(self):
        self.prepare()
        def model(messages, tools, *unused):
            if any(t['function']['name'] == 'submit_contract_review' for t in tools):
                return call('run_python', {'code': 'raise RuntimeError("must not execute")'}, 'bad'), {}
            return self.model(messages, tools, *unused)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertFalse(result['ok'])
        self.assertEqual(self.worker_turn, 0)
        self.assertIsNone(self.state()['plan'])

    def test_review_shares_total_model_budget(self):
        self.prepare()
        self.args.max_turns = 1
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertFalse(result['ok'])
        self.assertEqual(result['model_turns'], 1)
        self.assertEqual(self.worker_turn, 0)
        self.assertIsNone(self.state()['plan'])

    def test_removing_policy_binding_cannot_downgrade_new_run(self):
        self.prepare()
        self.assertTrue(autonomous.start(self.project, self.args, self.worker, self.model)['ok'])
        state = self.state()
        state.pop('contract_policy')
        autonomous.stored(self.run_dir() / 'recipe-checkpoint.json', state)
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())

    def test_blind_expectations_reject_wrong_assertion_before_reviewer(self):
        self.prepare()
        self.checks['checks'][0]['python'] += '\nassert is_even(-4) is False'
        corrected = [False]
        def model(messages, tools, *unused):
            if any(t['function']['name'] == 'submit_goal_checks' for t in tools) and 'independent derivation returned true' in json.dumps(messages):
                self.checks['checks'][0]['python'] = self.checks['checks'][0]['python'].replace('is_even(-4) is False', 'is_even(-4) is True')
                corrected[0] = True
            if any(t['function']['name'] == 'submit_contract_review' for t in tools):
                self.assertNotIn('is_even(-4) is False', json.dumps(messages))
            return self.model(messages, tools, *unused)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertTrue(corrected[0])
        refs = self.state()['roles']['goal-verifier']['review_attempts']
        rejected = mission.read(self.run_dir() / refs[0]['path'])
        self.assertEqual(rejected['origin'], 'independent_expected_values')
        self.assertEqual(rejected['oracle']['expectations'][-1]['value_json'], 'true')


class OracleTests(unittest.TestCase):
    def packet(self):
        return {'requirements': 'scale(n)=2*n; offset(n)=scale(n)+1.', 'goal_sha256': 'goal',
            'source_sha256': 'source', 'source_excerpts': [],
            'candidate': {'checks': [{'id': 'negative', 'python':
                'from counter import offset as f\nassert f(-5) == -8'}]}}

    def test_expected_answer_is_hidden(self):
        packet = review.oracle_packet(self.packet())
        self.assertNotIn('-8', json.dumps(packet))
        self.assertEqual(packet['calls'], [{'id': 'c00', 'function': 'counter.offset', 'args': [-5], 'kwargs': {}}])

    def test_derived_value_is_compared_mechanically(self):
        result = {'expectations': [{'id': 'c00', 'status': 'determined', 'basis': '-5*2+1=-9', 'value_json': '-9'}]}
        issue = review.oracle_issues(self.packet(), result)[0]
        self.assertIn('asserts -8; independent derivation returned -9', issue)
        self.assertIn('Recheck this disagreement against the original requirements', issue)

    def test_missing_reordered_duplicate_or_invalid_json_fails(self):
        packet = review.oracle_packet(self.packet())
        for rows in ([], [{'id': 'c01', 'status': 'determined', 'basis': 'Math', 'value_json': '-9'}],
                     [{'id': 'c00', 'status': 'determined', 'basis': 'Math', 'value_json': 'NaN'}]):
            with self.assertRaises((HornetError, ValueError)):
                review.oracle_verdict({'expectations': rows}, packet)

    def test_uncertainty_cannot_be_approved(self):
        value = {'expectations': [{'id': 'c00', 'status': 'uncertain', 'basis': 'Missing semantics', 'value_json': 'null'}]}
        self.assertIn('Cannot establish', review.oracle_issues(self.packet(), value)[0])

    def test_new_clarification_changes_both_review_and_oracle_cache_keys(self):
        state = {'roles': {}, 'goal': 'Required arithmetic.', 'goal_sha256': 'goal', 'steering': []}
        first = review.packet(state, 'goal-verifier', 'submit_goal_checks', self.packet()['candidate'], None, {})
        state['steering'].append({'message': 'Use integer arithmetic.'})
        second = review.packet(state, 'goal-verifier', 'submit_goal_checks', self.packet()['candidate'], None, {})
        self.assertNotEqual(review.acceptance.digest(first), review.acceptance.digest(second))
        self.assertNotEqual(review.acceptance.digest(review.oracle_packet(first)),
                            review.acceptance.digest(review.oracle_packet(second)))
        self.assertNotIn('operator_context', first)
        self.assertEqual(review.oracle_packet(second)['operator_context'], [{'message': 'Use integer arithmetic.'}])
        state['steering'][0]['message'] = 'Changed later'
        self.assertEqual(second['operator_context'][0]['message'], 'Use integer arithmetic.')


if __name__ == '__main__':
    unittest.main()
