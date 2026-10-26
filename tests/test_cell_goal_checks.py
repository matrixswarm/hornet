"""Frozen caller checks protect consumers while their dependencies are editable."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hornet.coding import acceptance, autonomous, cells, contract_review, goal_contract, mission
from hornet.project import HornetError
import test_autonomous as fixtures
from test_resume import call


def check(ident, paths, code='assert True'):
    return {'id': ident, 'description': ident, 'paths': paths, 'python': code, 'timeout': 5}


def plan(*checks):
    return {'schema_version': 1, 'checks': list(checks)}


class CellGoalCheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='hornet-early-checks-')
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name)
        self.nodes = [dict(id=name, files=[name + '.py'], depends_on=deps,
                           result=dict(path=name + '.py', function='value', args_json='[]',
                                       kwargs_json='{}', schema_json='{"type":"boolean"}'))
                      for name, deps in [('contract', []), ('strategy', ['contract']), ('service', ['strategy'])]]
        self.state = {'schema_version': 4, 'plan': {'cells': self.nodes}, 'cells': {}, 'values': {}}
        self.supplied = plan(check('contract', ['contract.py']),
                             check('strategy', ['contract.py', 'strategy.py']),
                             check('service', ['contract.py', 'strategy.py', 'service.py']))
        goal_contract.initialize(self.run, self.state, self.supplied)

    def freeze(self, cell):
        generated = plan(check('authored', cell['files']))
        frozen, marker = cells.freeze(cell, generated, {})
        record = self.state['cells'].setdefault(cell['id'], {})
        record['checks'] = goal_contract.freeze_cell_checks(self.run, self.state, cell, generated, frozen, record)
        record['marker'] = marker
        return record

    def selected(self, cell):
        _, routing = goal_contract.extend_cell_checks(self.run, self.state, cell, plan(check('authored', cell['files'])))
        return [item['goal_check_id'] for item in routing['selected']]

    def test_transitive_dependencies_route_only_ready_intersecting_checks(self):
        self.assertEqual(self.selected(self.nodes[0]), ['contract'])
        self.assertEqual(self.selected(self.nodes[1]), ['strategy'])
        self.assertEqual(self.selected(self.nodes[2]), [])
        for node in self.nodes:
            record = self.freeze(node)
            self.assertEqual([c['id'] for c in record['checks']['checks'][:2]], ['authored', 'typed-return'])
        goal_contract.validate(self.run, self.state)
        self.assertEqual(goal_contract.read(self.run, self.state), self.supplied)

    def test_later_writers_defer_shared_file_checks(self):
        self.nodes[-1]['files'].append('contract.py')
        self.assertEqual(self.selected(self.nodes[0]), [])
        self.assertEqual(self.selected(self.nodes[1]), [])

    def test_unrelated_earlier_cell_is_not_an_implicit_dependency(self):
        self.nodes[1]['depends_on'] = []
        self.assertEqual(self.selected(self.nodes[1]), [])

    def test_completed_later_cells_do_not_change_routing_on_resume(self):
        before = self.freeze(self.nodes[0])['early_goal_checks']
        self.state['values'] = {node['id']: True for node in self.nodes}
        goal_contract.validate(self.run, self.state)
        self.assertEqual(before, self.state['cells']['contract']['early_goal_checks'])

    def test_terminal_and_nested_plans_keep_final_gate(self):
        self.state['plan']['cells'] = [self.nodes[0]]
        self.assertEqual(self.selected(self.nodes[0]), [])
        self.state['plan']['cells'] = self.nodes
        self.state['schema_version'] = 5
        for node in self.nodes:
            self.assertEqual(self.selected(node), [])

    def test_nested_child_evidence_is_validated_even_when_no_checks_attach(self):
        self.state['schema_version'] = 5
        child = dict(self.nodes[0], id='nested')
        self.state['routes'] = {'contract': {'plan': {'cells': [child]}}}
        self.freeze(child)
        goal_contract.validate(self.run, self.state)
        self.state['cells']['nested']['early_goal_checks']['selected'] = [{'goal_check_id': 'invented'}]
        with self.assertRaisesRegex(HornetError, 'frozen goal contract'):
            goal_contract.validate(self.run, self.state)

    def test_cap_preserves_authored_checks_and_all_final_checks(self):
        supplied = plan(*(check('case-' + str(i), ['contract.py']) for i in range(8)))
        other = self.run / 'other'
        other.mkdir()
        state = copy.deepcopy(self.state)
        goal_contract.initialize(other, state, supplied)
        original = plan(*(check('authored-' + str(i), ['contract.py']) for i in range(5)))
        before = copy.deepcopy(original)
        combined, routing = goal_contract.extend_cell_checks(other, state, self.nodes[0], original)
        self.assertEqual(len(combined['checks']), 8)
        self.assertEqual(combined['checks'][:5], before['checks'])
        self.assertEqual(original, before)
        self.assertEqual(routing['deferred_eligible'], ['case-' + str(i) for i in range(3, 8)])
        self.assertEqual(goal_contract.read(other, state), supplied)

    def test_id_collision_is_stable_and_does_not_drop_checks(self):
        collision = 'goal-' + acceptance.digest(self.supplied['checks'][0])[:16]
        combined, routing = goal_contract.extend_cell_checks(
            self.run, self.state, self.nodes[0], plan(check(collision, ['contract.py'])))
        self.assertEqual([item['id'] for item in combined['checks']], [collision, collision + 'x'])
        self.assertEqual(routing['selected'][0]['cell_check_id'], collision + 'x')

    def test_legacy_runs_keep_original_checks_without_new_artifacts(self):
        (self.run / goal_contract.CELL_POLICY_FILE).unlink()
        del self.state['cell_gate_policy']
        record = self.freeze(self.nodes[0])
        self.assertNotIn('early_goal_checks', record)
        self.assertEqual(len(record['checks']['checks']), 2)
        self.assertFalse((self.run / 'cells/contract/generated-acceptance.json').exists())
        goal_contract.validate(self.run, self.state)

    def test_policy_loss_and_changed_policy_fail_closed(self):
        state = copy.deepcopy(self.state)
        del state['cell_gate_policy']
        with self.assertRaisesRegex(HornetError, 'policy binding'):
            goal_contract.validate(self.run, state)
        (self.run / goal_contract.CELL_POLICY_FILE).write_text('{}')
        with self.assertRaisesRegex(HornetError, 'policy changed'):
            goal_contract.validate(self.run, self.state)

    def test_original_checks_routing_and_combined_plan_are_bound(self):
        self.freeze(self.nodes[0])
        original = copy.deepcopy(self.state)
        for field in ('generated_acceptance', 'early_goal_checks'):
            self.state = copy.deepcopy(original)
            del self.state['cells']['contract'][field]
            with self.assertRaises(HornetError):
                goal_contract.validate(self.run, self.state)
        self.state = copy.deepcopy(original)
        self.state['cells']['contract']['checks']['checks'][-1]['python'] = 'assert False'
        with self.assertRaises(HornetError):
            goal_contract.validate(self.run, self.state)
        self.state = original
        (self.run / 'cells/contract/generated-acceptance.json').write_text('{}')
        with self.assertRaises(HornetError):
            goal_contract.validate(self.run, self.state)

    def test_interrupted_preparation_repeats_with_new_typed_marker(self):
        self.freeze(self.nodes[0])
        frozen = (self.run / 'cells/contract/generated-acceptance.json').read_bytes()
        old_marker = self.state['cells']['contract']['marker']
        self.state['cells'] = {}  # interruption before checkpoint save
        record = self.freeze(self.nodes[0])
        self.assertNotEqual(record['marker'], old_marker)
        self.assertEqual(frozen, (self.run / 'cells/contract/generated-acceptance.json').read_bytes())
        goal_contract.validate(self.run, self.state)


class CellGoalControllerTests(unittest.TestCase):
    setUp = fixtures.AutonomousTests.setUp
    submit = fixtures.AutonomousTests.submit
    write = fixtures.AutonomousTests.write
    run_dir = fixtures.AutonomousTests.run_dir
    state = fixtures.AutonomousTests.state

    def test_dependency_checks_do_not_expand_cell_write_permissions(self):
        (self.guest / 'adapter.py').write_text('from parity_cell import is_even\nvalue = is_even(3)\n')
        scoped = autonomous.ScopedWorker(self.worker, ['adapter.py'])
        checks = plan(check('dependency', ['parity_cell.py', 'adapter.py'],
                            'from adapter import value\nassert value is True'))
        report = acceptance.evaluate(scoped, checks, lambda result: None)
        self.assertTrue(report['ok'], report)
        before = self.source.read_bytes()
        attempt = scoped.call('write_file', {'path': 'parity_cell.py', 'content': 'changed',
                                             'expected_sha256': hashlib.sha256(before).hexdigest()})
        self.assertFalse(attempt['ok'], attempt)
        self.assertEqual(before, self.source.read_bytes())

    def prepare(self):
        self.args.goal += '\nImplement summary() in summary.py, returning is_even(3).'
        consumer = copy.deepcopy(self.plan['cells'][0])
        consumer.update(id='consumer', task='Implement summary() by calling is_even(3).',
                        files=['summary.py'], depends_on=['check-even'])
        consumer['result'].update(path='summary.py', function='summary', args_json='[]')
        self.plan['cells'].append(consumer)
        (self.guest / 'summary.py').write_text('def summary():\n    return True\n')
        self.consumer_checks = plan(check('summary', ['summary.py'],
            'from summary import summary\nassert summary() is False'))
        supplied = copy.deepcopy(self.checks)
        supplied['checks'].append(dict(self.consumer_checks['checks'][0], paths=['parity_cell.py', 'summary.py']))
        (self.root / 'supplied.json').write_text(json.dumps(supplied))
        self.args.acceptance_file = 'supplied.json'
        self.checks['checks'][0]['python'] = 'from parity_cell import is_even\nassert is_even(3) is False'
        self.owner_turns, self.consumer_turns = 0, 0
        self.failed_early = None

    def model(self, messages, tools, *unused):
        names = {item['function']['name'] for item in tools}
        if names.intersection({'submit_plan', 'submit_intake', 'submit_goal_plan'}):
            return fixtures.AutonomousTests.model(self, messages, tools)
        active = self.state().get('active_cell')
        if 'submit_checks' in names:
            return self.submit('submit_checks', self.consumer_checks if active == 'consumer' else self.checks, 'checks-' + active)
        if active == 'check-even':
            self.owner_turns += 1
            if self.owner_turns == 1:
                return self.write('    return False\n', 'weak-implementation')
            if self.owner_turns == 3:
                self.assertEqual(self.consumer_turns, 0)
                self.assertIn('goal-', json.dumps(messages))
                job = next((self.run_dir() / 'cells/check-even/jobs').iterdir())
                self.failed_early = mission.read(job / 'acceptance-results.json')
                self.assertFalse(self.failed_early['ok'])
                self.assertTrue(next(c for c in self.failed_early['results'] if c['id'] == 'parity')['passed'])
                self.assertFalse(next(c for c in self.failed_early['results'] if c['id'].startswith('goal-'))['passed'])
                return self.write('    return value % 2 == 0\n', 'repair-in-owner')
        else:
            self.consumer_turns += 1
            self.assertIsNotNone(self.failed_early)
            self.assertIn('value % 2', self.source.read_text())
            if self.consumer_turns == 1:
                path = self.guest / 'summary.py'
                return call('write_file', {'path': 'summary.py',
                    'content': 'from parity_cell import is_even\ndef summary():\n    return is_even(3)\n',
                    'expected_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}, 'consumer-write'), {}
        return {'role': 'assistant', 'content': 'Ready for fixed checks.'}, {}

    def test_upstream_failure_repairs_before_consumer_runs_and_final_gate_stays(self):
        self.prepare()
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertTrue(result['ok'], result)
        state = autonomous.load(self.project, self.run_dir())
        self.assertEqual(self.owner_turns, 4)
        self.assertEqual(self.consumer_turns, 2)
        self.assertEqual(state['cells']['check-even']['result']['repairs_used'], 1)
        self.assertEqual(len(state['cells']['check-even']['checks']['checks']), 3)
        self.assertEqual(len(state['cells']['consumer']['checks']['checks']), 2)
        report = mission.read(self.run_dir() / state['goal_verification']['path'])
        self.assertEqual([c['id'] for c in report['results']], ['parity', 'summary'])
        self.assertTrue(report['ok'])

    def test_failed_owner_never_starts_consumer(self):
        self.prepare()
        self.args.max_repairs = 0
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertFalse(result['ok'], result)
        self.assertEqual(self.consumer_turns, 0)
        self.assertNotIn('consumer', self.state()['cells'])
        self.assertFalse((self.run_dir() / 'goal-completion.json').exists())
        autonomous.load(self.project, self.run_dir())

    def test_lost_setup_checkpoint_resumes_using_retained_author_checks(self):
        self.prepare()
        original = goal_contract.freeze_cell_checks
        def interrupt(*args):
            original(*args)
            raise KeyboardInterrupt()
        with patch.object(goal_contract, 'freeze_cell_checks', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                autonomous.start(self.project, self.args, self.worker, self.model)
        path = self.run_dir() / 'cells/check-even/generated-acceptance.json'
        frozen = path.read_bytes()
        self.assertNotIn('checks', self.state()['cells']['check-even'])
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(frozen, path.read_bytes())
        autonomous.load(self.project, self.run_dir())

    def reviewed_model(self, messages, tools, *unused):
        names = {item['function']['name'] for item in tools}
        if 'submit_contract_oracle' in names:
            packet = json.loads(messages[1]['content'])
            return call('submit_contract_oracle', {'expectations': [dict(
                id=c['id'], status='determined', basis='Parity or the summary of is_even(3).',
                value_json=json.dumps(c['args'][0] % 2 == 0 if c['args'] else False),
                **({'return_type': 'boolean'} if packet.get('kind') == 'plan_returns_v1' else {}))
                for c in packet['calls']]}, 'oracle'), {}
        if 'submit_contract_review' in names:
            packet = json.loads(messages[1]['content'])
            if packet['submission'] == 'submit_checks':
                # This reviewer fixture misses weak coverage. The caller's
                # independent gate must still catch it in the owning cell.
                self.assertEqual(len(packet['candidate']['checks']), 1)
            return call('submit_contract_review', {'decision': 'approve', 'issues': [],
                'rationale': 'Fixture approval of the submitted candidate.'}, 'review'), {}
        return self.model(messages, tools, *unused)

    def reviewed_run(self):
        self.prepare()
        del self.args.contract_review
        for node in self.plan['cells']:
            node['result']['schema_json'] = '{"type":"boolean","enum":[false]}'
        result = autonomous.start(self.project, self.args, self.worker, self.reviewed_model)
        self.assertTrue(result['ok'], result)
        return autonomous.load(self.project, self.run_dir())

    def test_reviewed_author_checks_and_caller_checks_keep_separate_authority(self):
        state = self.reviewed_run()
        contract_review.validate(self.run_dir(), state)
        record = state['cells']['check-even']
        authored = mission.read(self.run_dir() / record['generated_acceptance']['path'])
        self.assertEqual(authored, state['roles']['verifier-check-even']['value'])
        self.assertEqual(len(authored['checks']), 1)
        self.assertEqual(len(record['checks']['checks']), 3)
        self.assertEqual(record['result']['repairs_used'], 1)

    def test_rebound_author_artifact_cannot_bypass_review_receipt(self):
        state = self.reviewed_run()
        record = state['cells']['check-even']
        path = self.run_dir() / record['generated_acceptance']['path']
        altered = mission.read(path)
        altered['checks'][0]['description'] = 'Changed after review'
        path.write_text(json.dumps(altered))
        record['generated_acceptance'] = mission.reference(self.run_dir(), path)
        record['checks']['checks'][0] = altered['checks'][0]
        # Composition still matches, but the independent review must reject it.
        goal_contract.validate(self.run_dir(), state)
        with self.assertRaisesRegex(HornetError, 'reviewed submission'):
            contract_review.validate(self.run_dir(), state)

    def test_review_cannot_bypass_changed_appended_caller_checks(self):
        state = self.reviewed_run()
        state['cells']['check-even']['checks']['checks'][-1]['python'] = 'assert True'
        with self.assertRaisesRegex(HornetError, 'frozen goal contract'):
            contract_review.validate(self.run_dir(), state)


if __name__ == '__main__':
    unittest.main()
