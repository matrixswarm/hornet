"""Pre-freeze corrections, strict example receipts and durable review evidence."""
import argparse
import copy
import json
import unittest

from hornet.coding import autonomous, contract_review as review, mission
from hornet.project import HornetError
import test_autonomous as fixtures
from test_resume import call


def plan(args='[3]', schema=None):
    value = copy.deepcopy(autonomous.PLAN_EXAMPLE)
    value['cells'][0]['result'].update(path='counter.py', function='offset', args_json=args,
        schema_json=json.dumps(schema or {'type': 'integer', 'enum': [7]}))
    return value


class LiteralTests(unittest.TestCase):
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
                basis='Required even parity.', value_json=json.dumps(c['args'][0] % 2 == 0))
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
        self.assertEqual(state['model_turns'], 11)
        completion = mission.read(self.run_dir() / 'goal-completion.json')
        self.assertEqual(len(completion['contract_reviews']), 3)

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
            if any(t['function']['name'] == 'submit_goal_checks' for t in tools) and 'expects true' in json.dumps(messages):
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
        self.assertIn('expects -9', review.oracle_issues(self.packet(), result)[0])

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
