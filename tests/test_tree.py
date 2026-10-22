"""Recon grounding, bounded nested contracts, parent verification and recovery."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from hornet.coding import autonomous, mission, tree, context, session
from hornet.project import HornetError
from test_resume import call
import test_autonomous as fixtures


class TreeTests(unittest.TestCase):
    run_dir = fixtures.AutonomousTests.run_dir
    state = fixtures.AutonomousTests.state
    submit = fixtures.AutonomousTests.submit

    def setUp(self):
        fixtures.AutonomousTests.setUp(self)
        self.args.recon_tree = True
        self.source.write_text('def is_even(value):\n    return False\n\ndef even_helper(value):\n    return False\n')
        self.seen = []
        self.turns = {}
        self.route = {'schema_version': 1, 'route': 'needs_split', 'reason': 'Helper and public integration',
            'plan': {'schema_version': 2, 'cells': [
                dict(copy.deepcopy(self.plan['cells'][0]), id='helper', task='Implement even_helper returning parity.',
                     result=dict(self.plan['cells'][0]['result'], function='even_helper')),
                dict(copy.deepcopy(self.plan['cells'][0]), id='public', depends_on=['helper'],
                     task='Implement is_even by calling the completed even_helper.')],
                'coverage': [{'requirement_id': 'r001', 'cell_ids': ['helper', 'public']}]}}

    def model(self, messages, tools, *args):
        self.seen.append(copy.deepcopy(messages))
        names = {tool['function']['name'] for tool in tools}
        if 'submit_intake' in names:
            count = self.turns.get('intake', 0)
            self.turns['intake'] = count + 1
            self.assertIn('read_file', names)
            self.assertNotIn('write_file', names)
            if count == 0:
                return call('read_file', {'path': 'parity_cell.py'}, 'recon-read'), {}
            value = {'schema_version': 1, 'mode': 'execute', 'reason': 'One public contract',
                'self_cell_id': 'check-even', 'plan': dict(self.plan, schema_version=2,
                    coverage=[{'requirement_id': 'r001', 'cell_ids': ['check-even']}])}
            return self.submit('submit_intake', value, 'intake')
        if 'submit_route' in names:
            self.assertTrue(names <= {'read_file', 'submit_route', 'ask_user'})
            return self.submit('submit_route', self.route, 'route')
        if 'submit_goal_checks' in names:
            return self.submit('submit_goal_checks', self.checks, 'goal-checks')
        if 'submit_checks' in names:
            packet = json.JSONDecoder().raw_decode(messages[1]['content'].split('\n', 1)[1])[0]
            checks = copy.deepcopy(self.checks)
            if packet['contract']['id'] == 'helper':
                checks['checks'][0]['python'] = checks['checks'][0]['python'].replace('is_even', 'even_helper')
            return self.submit('submit_checks', checks, 'checks-' + packet['contract']['id'])
        packet = json.JSONDecoder().raw_decode(messages[1]['content'].split('\n', 1)[1])[0]
        ident = packet['contract']['id']
        count = self.turns.get(ident, 0)
        self.turns[ident] = count + 1
        if count == 0:
            self.assertEqual(len(messages), 2)
            return call('read_file', {'path': 'parity_cell.py'}, ident + '-read'), {}
        if count == 1:
            old = self.source.read_text()
            if ident == 'helper':
                new = old.replace('def even_helper(value):\n    return False',
                                  'def even_helper(value):\n    return value % 2 == 0')
            else:
                new = old.replace('def is_even(value):\n    return False',
                                  'def is_even(value):\n    return even_helper(value)')
            return call('write_file', {'path': 'parity_cell.py', 'content': new,
                'expected_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}, ident + '-write'), {}
        return {'role': 'assistant', 'content': 'Ready for verification.'}, {}

    def run_goal(self, model=None):
        return autonomous.start(self.project, self.args, self.worker, model or self.model)

    def test_grounded_recon_splits_and_parent_returns_actual_false(self):
        self.args.max_cells = 1
        result = self.run_goal()
        self.assertTrue(result['ok'], result)
        state = autonomous.load(self.project, self.run_dir())
        self.assertEqual(state['schema_version'], 5)
        self.assertEqual(result['values'], {'helper': False, 'public': False, 'check-even': False})
        self.assertEqual([tree.depth(state, c) for c in tree.all_cells(state)], [1, 2, 2])
        receipt = mission.read(self.run_dir() / 'cells/check-even/return-manifest.json')
        self.assertEqual(receipt['kind'], 'parent_return')
        self.assertEqual(set(receipt['children']), {'helper', 'public'})
        self.assertFalse((self.run_dir() / 'cells/check-even/jobs').exists())
        self.assertIn('recon_returns', mission.read(self.run_dir() / 'goal-completion.json'))
        for ident in ('helper', 'public'):
            job = session.load(self.project, Path(state['cells'][ident]['run_dir']))
            self.assertEqual(job['context_window'], 2)
            packet = mission.read(self.run_dir() / 'cells' / ident / 'work-packet.json')
            self.assertNotIn('parent_contract', packet)
            self.assertEqual(packet['parent_boundary']['id'], 'check-even')
            self.assertNotIn('task', packet['parent_boundary'])
        first_code = next(i for i, msg in enumerate(self.seen) if 'coding worker' in msg[0]['content'])
        self.assertGreater(first_code, 0)

    def test_review_enabled_nested_split_keeps_every_approval(self):
        self.args.contract_review = True
        seen_reviews = []
        def review_model(messages, tools, *args):
            if any(t['function']['name'] == 'submit_contract_oracle' for t in tools):
                packet = json.loads(messages[1]['content'])
                return call('submit_contract_oracle', {'expectations': [dict(id=c['id'], status='determined',
                    basis='Required even parity.', value_json=json.dumps(c['args'][0] % 2 == 0))
                    for c in packet['calls']]}, 'oracle'), {}
            if any(t['function']['name'] == 'submit_contract_review' for t in tools):
                packet = json.loads(messages[1]['content'])
                seen_reviews.append(packet['submission'])
                return call('submit_contract_review', {'decision': 'approve', 'issues': [],
                    'rationale': 'Scoped fixture requirements and types match.'}, 'review'), {}
            return self.model(messages, tools, *args)
        result = self.run_goal(review_model)
        self.assertTrue(result['ok'], result)
        state = autonomous.load(self.project, self.run_dir())
        self.assertEqual(seen_reviews.count('submit_route'), 1)
        self.assertEqual(seen_reviews.count('submit_checks'), 3)
        self.assertEqual(len(mission.read(self.run_dir() / 'goal-completion.json')['contract_reviews']), 6)
        self.assertEqual(result['values'], {'helper': False, 'public': False, 'check-even': False})

    def test_stop_after_child_resumes_without_recon_or_completed_child_replay(self):
        original = autonomous.stored
        def stop(path, value):
            original(path, value)
            if path.name == 'progress.json' and value.get('stage') == 'cell_completed' and value['cell'] == 'helper':
                raise KeyboardInterrupt()
        with patch.object(autonomous, 'stored', side_effect=stop), self.assertRaises(KeyboardInterrupt):
            self.run_goal()
        state = autonomous.load(self.project, self.run_dir())
        self.assertEqual(state['values'], {'helper': False})
        files = ['intake.json', 'recipe-plan.json', 'goal-acceptance.json', 'cells/check-even/acceptance.json',
                 'cells/check-even/recon/return.json', 'cells/helper/return-manifest.json', 'attempts/1/result.json']
        before = {name: (self.run_dir() / name).read_bytes() for name in files}
        def resume(messages, tools, *args):
            self.assertFalse({'submit_intake', 'submit_route'} & {t['function']['name'] for t in tools})
            return self.model(messages, tools, *args)
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, resume)
        self.assertTrue(result['ok'], result)
        for name, data in before.items():
            self.assertEqual((self.run_dir() / name).read_bytes(), data)
        self.assertEqual(self.worker.calls.count('write_file'), 2)

    def test_children_cannot_satisfy_a_failing_parent_contract(self):
        original = self.model
        def failing_parent(messages, tools, *args):
            reply = original(messages, tools, *args)
            if ('submit_checks' in {t['function']['name'] for t in tools}
                    and json.JSONDecoder().raw_decode(messages[1]['content'].split('\n', 1)[1])[0]['contract']['id'] == 'check-even'):
                checks = copy.deepcopy(self.checks)
                checks['checks'][0]['python'] += '\nassert is_even(7) is True'
                return self.submit('submit_checks', checks, 'wrong-parent')
            return reply
        result = self.run_goal(failing_parent)
        self.assertFalse(result['ok'])
        self.assertIn('Parent contract verification failed', result['error'])
        self.assertEqual(set(result['values']), {'helper', 'public'})
        self.assertFalse((self.run_dir() / 'cells/check-even/return-manifest.json').exists())

    def frozen_state(self):
        self.args.max_turns = 5
        self.run_goal()
        return self.state()

    def test_split_rejects_scope_growth_reused_ids_and_missing_coverage(self):
        state = self.frozen_state()
        cell = state['plan']['cells'][0]
        for change in ('scope', 'id', 'coverage', 'one'):
            bad = copy.deepcopy(self.route)
            if change == 'scope':
                bad['plan']['cells'][0]['files'].append('elsewhere.py')
            if change == 'id':
                bad['plan']['cells'][0]['id'] = cell['id']
                bad['plan']['cells'][1]['depends_on'] = [cell['id']]
                bad['plan']['coverage'][0]['cell_ids'][0] = cell['id']
            if change == 'coverage':
                bad['plan']['coverage'] = []
            if change == 'one':
                bad['plan']['cells'] = bad['plan']['cells'][:1]
                bad['plan']['coverage'][0]['cell_ids'] = ['helper']
            with self.subTest(change=change), self.assertRaises(HornetError):
                tree.validate_choice(state, cell, bad)

    def test_leaf_cannot_split_and_context_limits_cannot_change(self):
        state = self.frozen_state()
        leaf = tree.children(state, state['plan']['cells'][0])[0]
        with self.assertRaisesRegex(HornetError, 'depth'):
            tree.validate_choice(state, leaf, self.route)
        state['tree_limits']['max_depth'] = 3
        (self.run_dir() / 'recipe-checkpoint.json').write_text(json.dumps(state))
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())

    def test_shared_turn_budget_does_not_reset_at_children(self):
        state = self.frozen_state()
        self.assertEqual(state['model_turns'], 5)
        self.assertNotIn('write_file', self.worker.calls)
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertFalse(result['ok'])
        self.assertEqual(result['model_turns'], 5)

    def test_recon_publication_gap_recovers_without_reselection(self):
        original = mission.immutable
        def crash(path, value):
            original(path, value)
            if path.name == 'return.json' and path.parent.name == 'recon':
                raise KeyboardInterrupt()
        with patch.object(mission, 'immutable', side_effect=crash), self.assertRaises(KeyboardInterrupt):
            self.run_goal()
        before = (self.run_dir() / 'cells/check-even/recon/return.json').read_bytes()
        def resume(messages, tools, *args):
            self.assertNotIn('submit_route', {t['function']['name'] for t in tools})
            return self.model(messages, tools, *args)
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, resume)
        self.assertTrue(result['ok'], result)
        self.assertEqual((self.run_dir() / 'cells/check-even/recon/return.json').read_bytes(), before)

    def test_recon_out_of_scope_reads_are_rejected(self):
        def invalid(messages, tools, *args):
            if 'submit_route' in {t['function']['name'] for t in tools}:
                if 'read_file' in {t['function']['name'] for t in tools}:
                    return call('read_file', {'path': 'elsewhere.py'}, 'bad-read'), {}
                return self.submit('submit_route', self.route, 'route')
            return self.model(messages, tools, *args)
        result = self.run_goal(invalid)
        self.assertTrue(result['ok'], result)
        history = self.state()['roles']['recon-check-even']['history']
        errors = [r['result']['error'] for r in history if r.get('tool') == 'read_file']
        self.assertEqual(len(errors), 2)
        self.assertTrue(all('parent files' in error for error in errors))

    def test_unknown_parent_check_outcome_stops_resume(self):
        original = self.worker.call
        def interrupted(name, args=None):
            if (name == 'run_tests' and self.run_dir().exists()
                    and self.state()['cells'].get('check-even', {}).get('parent_in_flight')):
                raise KeyboardInterrupt()
            return original(name, args)
        self.worker.call = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.run_goal()
        with self.assertRaisesRegex(HornetError, 'parent verification was in flight'):
            autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)

    def test_parent_receipt_publication_crash_adopts_evidence_without_rechecking(self):
        from hornet.coding import returns
        original = returns.write_text
        def interrupted(path, value, **kwargs):
            original(path, value, **kwargs)
            if path == self.run_dir() / 'cells/check-even/return-manifest.json':
                raise KeyboardInterrupt()
        with patch.object(returns, 'write_text', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.run_goal()
        before = (self.run_dir() / 'cells/check-even/return-manifest.json').read_bytes()
        state = self.state()
        checks = dict(state['cells']['check-even']['parent_verification'])
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual((self.run_dir() / 'cells/check-even/return-manifest.json').read_bytes(), before)
        self.assertEqual(self.state()['cells']['check-even']['parent_verification'], checks)

    def test_tampered_recon_or_parent_evidence_blocks_loading(self):
        self.assertTrue(self.run_goal()['ok'])
        for name in ('cells/check-even/recon/return.json', 'cells/check-even/parent-verification/1/acceptance.json',
                     'cells/helper/return-manifest.json'):
            path = self.run_dir() / name
            original = path.read_bytes()
            value = json.loads(original)
            value['ok'] = False
            path.write_text(json.dumps(value))
            with self.subTest(name=name), self.assertRaises(HornetError):
                autonomous.load(self.project, self.run_dir())
            path.write_bytes(original)

    def test_recon_false_is_invalid_control_while_typed_false_remains_data(self):
        state = self.frozen_state()
        for value in (False, {'schema_version': 1, 'route': False, 'reason': 'done', 'plan': tree.EMPTY}):
            with self.assertRaises(HornetError):
                tree.validate_choice(state, state['plan']['cells'][0], value)

    def test_total_node_cap_is_shared_by_root_splits(self):
        state = self.frozen_state()
        # Existing valid tree has 3 nodes; enforce a deliberately smaller
        # controller cap to exercise total-node accounting without model calls.
        parent = state['plan']['cells'][0]
        with patch.dict(tree.LIMITS, max_nodes=2), self.assertRaisesRegex(HornetError, 'node limit'):
            tree.validate_choice(state, parent, self.route)

    def test_intake_cannot_write_source(self):
        result = self.run_goal(lambda *args: (call('write_file', {'path': 'parity_cell.py',
            'content': 'unsafe', 'expected_sha256': ''}, 'write-during-recon'), {}))
        self.assertFalse(result['ok'])
        self.assertIn('unoffered', result['error'])
        self.assertNotIn('write_file', self.worker.calls)


class BoundedContextTests(unittest.TestCase):
    def test_recon_schema_constrains_requirement_count_and_route_shape(self):
        from hornet.coding.model import validate_arguments
        from hornet.coding.role_schema import recon_schema
        required = [{'id': 'r001'}, {'id': 'r002'}]
        intake = recon_schema('submit_intake', required, 3)
        coverage = intake['properties']['plan']['properties']['coverage']
        self.assertEqual(coverage['minItems'], 2)
        self.assertEqual(coverage['maxItems'], 2)
        self.assertEqual(coverage['items']['properties']['requirement_id']['enum'], ['r001', 'r002'])
        route = recon_schema('submit_route', required, 3)
        valid = {'schema_version': 1, 'route': 'execute', 'reason': 'Small task', 'plan': copy.deepcopy(tree.EMPTY)}
        validate_arguments(valid, route)
        invalid = copy.deepcopy(valid)
        invalid['route'] = 'needs_split'
        with self.assertRaises(ValueError):
            validate_arguments(invalid, route)
        invalid = copy.deepcopy(valid)
        invalid['plan']['coverage'] = [{'requirement_id': 'r003', 'cell_ids': ['one']}]
        with self.assertRaises(ValueError):
            validate_arguments(invalid, route)

    def test_hundred_turns_do_not_carry_hundred_turns_of_context(self):
        messages = [{'role': 'system', 'content': 'Frozen rules'}, {'role': 'user', 'content': 'Frozen contract'}]
        sizes = []
        for number in range(100):
            messages.extend([{'role': 'assistant', 'content': 'action-%03d' % number},
                {'role': 'tool', 'name': 'read_file', 'content': 'source-%03d' % number}])
            active, info = context.focus_messages(messages, [], [], 16000, 2)
            sizes.append(info['history_chars'])
            self.assertLessEqual(info['retained_turns'], 2)
            self.assertEqual(active[:2], messages[:2])
        self.assertEqual(len(messages), 202)
        self.assertLess(sizes[-1] - sizes[5], 10)
        self.assertNotIn('source-000', json.dumps(active))
        self.assertIn('source-099', json.dumps(active))


if __name__ == '__main__':
    unittest.main()
