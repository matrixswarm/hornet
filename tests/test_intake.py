"""Frozen first-turn selection, ownership, recovery and legacy compatibility."""
import argparse
import copy
import json
import unittest
from unittest.mock import patch

from hornet.coding import autonomous, intake, mission
from hornet.project import HornetError
from test_resume import call
import test_autonomous as fixtures
import test_mission as goal_fixtures


class IntakeTests(unittest.TestCase):
    setUp = fixtures.AutonomousTests.setUp
    model = fixtures.AutonomousTests.model
    submit = fixtures.AutonomousTests.submit
    write = fixtures.AutonomousTests.write
    run_dir = fixtures.AutonomousTests.run_dir
    state = fixtures.AutonomousTests.state
    run_goal = goal_fixtures.MissionTests.run_goal
    save_state = goal_fixtures.MissionTests.save_state
    chain = goal_fixtures.MissionTests.chain

    def decision(self, mode='execute'):
        plan = dict(copy.deepcopy(self.plan), schema_version=2)
        plan['coverage'] = [{'requirement_id': item['id'], 'cell_ids': [cell['id'] for cell in plan['cells']]}
                            for item in mission.document(self.args.goal)['requirements']]
        return {'schema_version': 1, 'mode': mode, 'reason': 'Small bounded responsibilities',
                'self_cell_id': '' if mode == 'delegate' else plan['cells'][0]['id'], 'plan': plan}

    def choose(self, mode, backend=None):
        def model(messages, tools, *args):
            names = {item['function']['name'] for item in tools}
            if 'submit_intake' in names:
                self.assertEqual(names, {'submit_intake', 'ask_user'})
                self.assertNotIn('write_file', self.worker.calls)
                return self.submit('submit_intake', self.decision(mode), 'selection')
            return (backend or self.model)(messages, tools, *args)
        return model

    def assert_evidence(self, result, owners):
        self.assertTrue(result['ok'], result)
        state = autonomous.load(self.project, self.run_dir())
        self.assertEqual(result['intake']['owners'], owners)
        self.assertEqual(result['intake']['execution'], 'sequential')
        self.assertEqual(mission.read(self.run_dir() / 'goal-completion.json')['intake'], result['intake'])
        self.assertEqual(mission.read(self.run_dir() / 'goal-status.json')['intake'], result['intake'])
        for ident, owner in owners.items():
            packet = mission.read(self.run_dir() / 'cells' / ident / 'work-packet.json')
            receipt = mission.read(self.run_dir() / state['cells'][ident]['return_manifest']['path'])
            self.assertEqual(packet['assignment']['owner'], owner)
            self.assertEqual(receipt['goal']['assignment'], packet['assignment'])
            self.assertEqual(packet['assignment']['intake_record'], state['intake_record'])

    def test_execute_owns_one_cell_without_an_extra_model_turn(self):
        result = self.run_goal(self.choose('execute'))
        self.assert_evidence(result, {'check-even': 'intake'})
        self.assertIs(result['values']['check-even'], False)
        self.assertEqual(result['model_turns'], 6)

    def test_prompt_example_is_a_valid_intake_document(self):
        text = intake.PROMPT.split('the shape is ', 1)[1].split('. Use actual', 1)[0]
        intake.validate(json.loads(text), mission.document('Implement parity.'), 3)

    def test_delegate_assigns_every_cell_to_a_fresh_worker(self):
        result = self.run_goal(self.choose('delegate', self.chain()))
        self.assert_evidence(result, {'check-even': 'delegate/check-even', 'payload': 'delegate/payload'})
        state = self.state()
        jobs = [record['run_dir'] for record in state['cells'].values()]
        self.assertEqual(len(set(jobs)), 2)

    def test_delegate_and_work_resumes_without_reselecting_or_repeating_self_work(self):
        backend = self.chain()
        original = autonomous.stored
        def interrupt(path, value):
            original(path, value)
            if path.name == 'progress.json' and value.get('stage') == 'cell_completed':
                raise KeyboardInterrupt()
        with patch.object(autonomous, 'stored', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.run_goal(self.choose('delegate-and-work', backend))
        paths = ['intake.json', 'recipe-plan.json', 'cells/check-even/return-manifest.json', 'attempts/1/result.json']
        before = {path: (self.run_dir() / path).read_bytes() for path in paths}
        turns = self.state()['model_turns']
        def resume(messages, tools, *args):
            self.assertNotIn('submit_intake', {item['function']['name'] for item in tools})
            return backend(messages, tools, *args)
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, resume)
        self.assert_evidence(result, {'check-even': 'intake', 'payload': 'delegate/payload'})
        self.assertGreater(result['model_turns'], turns)
        self.assertEqual(self.worker.calls.count('write_file'), 2)
        for path in paths:
            self.assertEqual((self.run_dir() / path).read_bytes(), before[path])

    def test_invalid_choices_and_coverage_are_rejected(self):
        valid = self.decision()
        cases = [dict(valid, mode='parallel'), dict(valid, schema_version=True), dict(valid, reason=''),
                 dict(valid, reason='x' * 601), dict(valid, self_cell_id='missing'), dict(valid, mode='delegate'),
                 dict(valid, mode='delegate-and-work'), dict(valid, extra=True)]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(HornetError):
                intake.validate(value, mission.document(self.args.goal), 3)
        invalid = copy.deepcopy(valid)
        invalid['plan']['coverage'] = []
        with self.assertRaisesRegex(HornetError, 'r001'):
            intake.validate(invalid, mission.document(self.args.goal), 3)

    def test_independent_cells_cannot_share_files_and_dependencies_are_transitive(self):
        plan = self.plan
        plan['cells'].append(dict(copy.deepcopy(plan['cells'][0]), id='second'))
        value = self.decision('delegate-and-work')
        goal = mission.document(self.args.goal)
        with self.assertRaisesRegex(HornetError, 'sharing files'):
            intake.validate(value, goal, 3)
        value['plan']['cells'][1]['depends_on'] = ['check-even']
        intake.validate(value, goal, 3)
        value['plan']['cells'].append(dict(copy.deepcopy(value['plan']['cells'][1]), id='third', depends_on=['second']))
        value['plan']['coverage'][0]['cell_ids'].append('third')
        intake.validate(value, goal, 3)
        with self.assertRaises(HornetError):
            intake.validate(value, goal, 2)

    def test_peer_address_collision_is_rejected(self):
        self.plan['cells'][0]['id'] = 'intake'
        with self.assertRaisesRegex(HornetError, 'collide'):
            intake.validate(self.decision(), mission.document(self.args.goal), 3)

    def test_invalid_intake_consumes_budget_without_any_edits(self):
        self.args.max_turns = 3
        bad = dict(self.decision(), mode='delegate-and-work')
        result = self.run_goal(lambda *args: self.submit('submit_intake', bad, 'invalid'))
        self.assertFalse(result['ok'])
        self.assertEqual(result['model_turns'], 3)
        self.assertNotIn('write_file', self.worker.calls)
        self.assertFalse((self.run_dir() / 'intake.json').exists())
        self.assertEqual(self.state()['roles']['intake']['invalid_submissions'], 3)

    def test_intake_cannot_request_source_tools(self):
        result = self.run_goal(lambda *args: (call('read_file', {'path': 'parity_cell.py'}, 'early-read'), {}))
        self.assertFalse(result['ok'])
        self.assertIn('unoffered', result['error'])
        self.assertNotIn('read_file', self.worker.calls)
        self.assertNotIn('write_file', self.worker.calls)

    def test_publication_crash_adopts_exact_selection_without_another_intake_call(self):
        original = mission.immutable
        def crash(path, value):
            original(path, value)
            if path.name == 'intake.json':
                raise KeyboardInterrupt()
        with patch.object(mission, 'immutable', side_effect=crash), self.assertRaises(KeyboardInterrupt):
            self.run_goal(self.choose('execute'))
        original_bytes = (self.run_dir() / 'intake.json').read_bytes()
        self.assertEqual(self.state()['phase'], 'intake')
        self.assertIsNone(self.state()['plan'])
        def resume(messages, tools, *args):
            self.assertNotIn('submit_intake', {item['function']['name'] for item in tools})
            return self.model(messages, tools, *args)
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, resume)
        self.assert_evidence(result, {'check-even': 'intake'})
        self.assertEqual((self.run_dir() / 'intake.json').read_bytes(), original_bytes)
        self.assertEqual(result['model_turns'], 6)

    def test_frozen_ownership_and_file_tampering_block_resume(self):
        self.args.max_turns = 1
        self.assertFalse(self.run_goal()['ok'])
        original = self.state()
        forged = copy.deepcopy(original)
        forged['intake']['mode'] = 'delegate'
        forged['intake']['self_cell_id'] = ''
        self.save_state(forged)
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())
        self.save_state(original)
        path = self.run_dir() / 'intake.json'
        value = mission.read(path)
        value['decision']['reason'] = 'Changed after selection'
        path.write_text(json.dumps(value))
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())

    def test_schema_three_recipe_resumes_original_planning_without_intake(self):
        self.run_goal(lambda *args: (_ for _ in ()).throw(HornetError('offline')))
        state = self.state()
        state.update(schema_version=3, phase='plan', roles={})
        del state['intake']
        del state['intake_record']
        self.save_state(state)
        def legacy(messages, tools, *args):
            self.assertNotIn('submit_intake', {item['function']['name'] for item in tools})
            return self.model(messages, tools, *args)
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, legacy)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('intake', result)
        self.assertFalse((self.run_dir() / 'intake.json').exists())


if __name__ == '__main__':
    unittest.main()
