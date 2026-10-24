"""Reject contradictory selected-call contracts before any implementation starts."""
import argparse
import copy
import json
import unittest
from unittest.mock import patch

from hornet.coding import acceptance, autonomous, contract_review as review, mission
from hornet.coding.role_schema import oracle_schema
from hornet.coding.model import validate_arguments
from hornet.project import HornetError
import test_contract_review as fixtures
from test_resume import call


def proposal(schema):
    return {'submission': 'submit_intake', 'requirements':
        'double(n) returns twice an integer n, as an int. Also implement clamp(value, lower, upper).',
        'goal_sha256': 'goal', 'source_sha256': 'source', 'source_excerpts': [],
        'candidate': {'plan': {'cells': [{'id': 'calculator', 'task': 'Do not leak this candidate task',
            'result': {'path': 'calculator.py', 'function': 'double', 'args_json': '[5]',
                       'kwargs_json': '{}', 'schema_json': json.dumps(schema)}}]}}}


def answer(value=10, kind='integer', status='determined'):
    return {'expectations': [{'id': 'r00', 'status': status, 'return_type': kind,
        'value_json': json.dumps(value), 'basis': 'Twice the supplied integer 5 is 10.'}]}


class ReturnOracleTests(unittest.TestCase):
    def test_real_failure_rejected_without_literal_example(self):
        packet = proposal({'type': 'object', 'properties': {'double': {'type': 'integer'},
            'clamp': {'type': 'integer'}}, 'required': ['double', 'clamp'], 'additionalProperties': False})
        self.assertEqual(review.examples(packet['requirements']), [])
        issue = review.oracle_issues(packet, answer())[0]
        self.assertIn('derived integer value 10', issue)
        self.assertIn('single returned value', issue)

    def test_blind_packet_omits_schema_candidate_tasks_and_verdict(self):
        packet = proposal({'type': 'object', 'properties': {'secret_candidate_field': {'type': 'string'}}})
        packet['verdict'] = 'approve'
        blind = review.oracle_packet(packet)
        self.assertEqual(blind['calls'], [{'id': 'r00', 'function': 'calculator.double', 'args': [5], 'kwargs': {}}])
        text = json.dumps(blind)
        for forbidden in ('secret_candidate_field', 'Do not leak', 'verdict', 'candidate', 'schema'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(blind['kind'], 'plan_returns_v1')

    def test_schema_correction_reuses_blind_answer_but_not_approval(self):
        bad, good = proposal({'type': 'object'}), proposal({'type': 'integer'})
        self.assertEqual(review.oracle_packet(bad), review.oracle_packet(good))
        self.assertNotEqual(acceptance.digest(bad), acceptance.digest(good))
        self.assertTrue(review.oracle_issues(bad, answer()))
        self.assertEqual(review.oracle_issues(good, answer()), [])

    def test_wrong_enum_and_boolean_integer_confusion_rejected(self):
        for spec, derived in [({'type': 'integer', 'enum': [11]}, answer()),
                              ({'type': 'integer'}, answer(True, 'boolean')),
                              ({'type': 'boolean'}, answer(1))]:
            self.assertTrue(review.oracle_issues(proposal(spec), derived))

    def test_nested_return_schema_checked_against_actual_derived_value(self):
        spec = {'type': 'object', 'properties': {'values': {'type': 'array', 'items': {'type': 'integer'}}},
                'required': ['values'], 'additionalProperties': False}
        self.assertEqual(review.oracle_issues(proposal(spec), answer({'values': [1, 2]}, 'object')), [])
        self.assertTrue(review.oracle_issues(proposal(spec), answer({'values': ['1']}, 'object')))

    def test_type_only_supports_unknown_identifiers_without_guessing(self):
        derived = answer(None, 'string', 'type_only')
        self.assertEqual(review.oracle_issues(proposal({'type': 'string'}), derived), [])
        self.assertTrue(review.oracle_issues(proposal({'type': 'object'}), derived))
        self.assertEqual(review.oracle_issues(proposal({'type': 'number'}), answer(None, 'integer', 'type_only')), [])
        self.assertTrue(review.oracle_issues(proposal({'type': 'integer'}), answer(None, 'number', 'type_only')))

    def test_unresolved_dependency_remains_symbolic(self):
        packet = proposal({'type': 'integer'})
        packet['candidate']['plan']['cells'][0]['result']['args_json'] = '[{"$cell":"prior"}]'
        self.assertEqual(review.oracle_packet(packet)['calls'][0]['args'], [{'$cell': 'prior'}])
        self.assertEqual(review.oracle_issues(packet, answer(None, 'integer', 'type_only')), [])

    def test_research_cells_and_execute_route_have_no_new_call(self):
        packet = proposal({'type': 'integer'})
        packet['candidate']['plan']['cells'].insert(0, {'id': 'research', 'kind': 'research',
                                                      'result': {'schema': {'type': 'string'}}})
        self.assertEqual(len(review.oracle_packet(packet)['calls']), 1)
        packet['submission'] = 'submit_route'
        packet['candidate'] = {'route': 'execute', 'plan': {'cells': []}}
        self.assertFalse(review.requires_oracle(review.POLICY, packet))

    def test_every_planning_stage_uses_same_gate_with_native_schema(self):
        for stage in review.PLAN_SUBMISSIONS:
            packet = proposal({'type': 'object'})
            packet['submission'] = stage
            cell = packet['candidate']['plan']['cells'][0]
            cell['result']['schema'] = json.loads(cell['result'].pop('schema_json'))
            if stage in {'submit_plan', 'submit_goal_plan'}:
                packet['candidate'] = packet['candidate']['plan']
            self.assertTrue(review.requires_oracle(review.POLICY, packet))
            self.assertTrue(review.oracle_issues(packet, answer()))

    def test_uncertainty_and_malformed_derivations_fail_closed(self):
        packet = proposal({'type': 'integer'})
        self.assertIn('cannot establish', review.oracle_issues(packet, answer(None, None, 'uncertain'))[0])
        for result in (answer(10, 'object'), answer(10, 'integer', 'type_only'),
                       answer(None, 'integer', 'uncertain'), answer(None, None, 'type_only'),
                       {'expectations': []}):
            with self.assertRaises((HornetError, ValueError)):
                review.oracle_verdict(result, review.oracle_packet(packet))
        result = answer()
        result['expectations'][0]['value_json'] = 'NaN'
        with self.assertRaises(ValueError):
            review.oracle_verdict(result, review.oracle_packet(packet))

    def test_versioned_instructions_change_blind_cache_identity(self):
        packet = proposal({'type': 'integer'})
        before = review.oracle_packet(packet)
        with patch.object(review, 'RESULT_ORACLE_PROMPT', review.RESULT_ORACLE_PROMPT + ' Revised.'):
            self.assertNotEqual(before, review.oracle_packet(packet))

    def test_old_policies_keep_original_authority(self):
        packet = proposal({'type': 'integer'})
        for policy in (review.LEGACY_POLICY, review.ASSERTION_POLICY):
            self.assertFalse(review.requires_oracle(policy, packet))
            review.validate_oracle(policy, packet, {'verdict': {'decision': 'approve'}})
        with self.assertRaises(HornetError):
            review.validate_oracle(review.POLICY, packet, {'verdict': {'decision': 'approve'}})

    def test_response_schema_requires_type_only_for_plan_oracle(self):
        blind = review.oracle_packet(proposal({'type': 'integer'}))
        spec = oracle_schema(blind)['properties']['expectations']['items']
        self.assertIn('return_type', spec['required'])
        self.assertIn('type_only', spec['properties']['status']['enum'])
        for value in (answer(), answer(None, 'integer', 'type_only'), answer(None, None, 'uncertain')):
            validate_arguments(value, oracle_schema(blind))
        blind.pop('kind')
        self.assertNotIn('return_type', oracle_schema(blind)['properties']['expectations']['items']['properties'])


class ReturnWorkflowTests(unittest.TestCase):
    setUp = fixtures.ReviewWorkflowTests.setUp
    prepare = fixtures.ReviewWorkflowTests.prepare
    model = fixtures.ReviewWorkflowTests.model
    submit = fixtures.ReviewWorkflowTests.submit
    write = fixtures.ReviewWorkflowTests.write
    run_dir = fixtures.ReviewWorkflowTests.run_dir
    state = fixtures.ReviewWorkflowTests.state

    def setup_contract(self):
        self.prepare()
        self.args.goal = self.args.goal.split(' Required example:')[0]
        self.plan['cells'][0]['result']['schema_json'] = '{"type":"object"}'

    def test_automatic_feedback_corrects_bad_plan_before_any_coding(self):
        self.setup_contract()
        corrected = []
        def model(messages, tools, *rest):
            names = {t['function']['name'] for t in tools}
            if 'submit_intake' in names and 'derived boolean value false' in json.dumps(messages):
                self.assertEqual(self.worker_turn, 0)
                self.plan['cells'][0]['result']['schema_json'] = '{"type":"boolean"}'
                corrected.append(True)
            if 'submit_contract_review' in names:
                packet = json.loads(messages[1]['content'])
                if packet['submission'] == 'submit_intake':
                    self.assertNotIn('"type": "object"', json.dumps(packet['candidate']))
            return self.model(messages, tools, *rest)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertTrue(corrected)
        state = autonomous.load(self.project, self.run_dir())
        refs = state['roles']['intake']['review_attempts']
        self.assertEqual(len(refs), 2)
        rejected = mission.read(self.run_dir() / refs[0]['path'])
        self.assertEqual(rejected['origin'], 'independent_expected_values')
        self.assertEqual(rejected['oracle']['expectations'][0]['return_type'], 'boolean')
        self.assertEqual(len([record for key, record in state['roles'].items()
            if key.startswith('contract-oracle-') and 'return_type' in record['value']['expectations'][0]]), 1)

    def test_repeated_mistake_stays_bounded_without_source_changes(self):
        self.setup_contract()
        before = self.source.read_bytes()
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertFalse(result['ok'])
        self.assertEqual(self.worker_turn, 0)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertIsNone(self.state()['plan'])
        self.assertLessEqual(result['model_turns'], 8)
        self.assertFalse((self.run_dir() / 'goal-completion.json').exists())

    def test_lost_oracle_connection_resumes_with_rejected_evidence_intact(self):
        self.setup_contract()
        def offline(messages, tools, *rest):
            if any(t['function']['name'] == 'submit_contract_oracle' for t in tools):
                raise HornetError('Model disconnected')
            return self.model(messages, tools, *rest)
        self.assertFalse(autonomous.start(self.project, self.args, self.worker, offline)['ok'])
        failed = (self.run_dir() / 'attempts/1/result.json').read_bytes()
        self.plan['cells'][0]['result']['schema_json'] = '{"type":"boolean"}'
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(failed, (self.run_dir() / 'attempts/1/result.json').read_bytes())

    def test_uncertain_oracle_cannot_be_overridden_by_approving_reviewer(self):
        self.setup_contract()
        def model(messages, tools, *rest):
            if any(t['function']['name'] == 'submit_contract_oracle' for t in tools):
                packet = json.loads(messages[1]['content'])
                return call('submit_contract_oracle', {'expectations': [dict(id=c['id'], status='uncertain',
                    basis='Missing required argument semantics.', value_json='null', return_type=None)
                    for c in packet['calls']]}, 'unknown'), {}
            return self.model(messages, tools, *rest)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertFalse(result['ok'])
        self.assertEqual(self.worker_turn, 0)
        self.assertEqual(self.reviews, [])

    def test_missing_or_altered_oracle_refuses_load_even_with_rebound_receipt(self):
        self.setup_contract()
        self.plan['cells'][0]['result']['schema_json'] = '{"type":"boolean"}'
        self.assertTrue(autonomous.start(self.project, self.args, self.worker, self.model)['ok'])
        original = self.state()
        ref = original['roles']['intake']['contract_review']
        path = self.run_dir() / ref['path']
        receipt = mission.read(path)
        for mutation in ('missing', 'contradictory'):
            changed = copy.deepcopy(receipt)
            if mutation == 'missing':
                changed.pop('oracle')
            else:
                changed['oracle']['expectations'][0].update(return_type='object', value_json='{}')
            # Simulate coherent hash rebinding, not merely a broken digest.
            autonomous.stored(path, changed)
            rebound = mission.reference(self.run_dir(), path)
            state = copy.deepcopy(original)
            role = state['roles']['intake']
            role['contract_review'] = rebound
            role['review_attempts'] = [rebound if item == ref else item for item in role['review_attempts']]
            with self.assertRaisesRegex(HornetError, 'independent return'):
                review.validate(self.run_dir(), state)


if __name__ == '__main__':
    unittest.main()
