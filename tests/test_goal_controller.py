"""Durable root review, bounded recovery and final lease-release evidence."""
import argparse
import copy
import json
import unittest
from unittest.mock import patch

from hornet import hive, hive_status
from hornet.cli import build_parser
from hornet.coding import autonomous, planning, recovery, session, closeout, mission, workspaces, questions, cell_actions
from hornet.coding.locking import WorkerLock
from hornet.project import HornetError
import test_autonomous as fixtures
from test_resume import call


class ControllerTests(unittest.TestCase):
    setUp = fixtures.AutonomousTests.setUp
    model = fixtures.AutonomousTests.model
    submit = fixtures.AutonomousTests.submit
    write = fixtures.AutonomousTests.write
    state = fixtures.AutonomousTests.state
    run_dir = fixtures.AutonomousTests.run_dir

    def decide(self, action, revision=1, feedback=''):
        return planning.command(self.project, argparse.Namespace(run=str(self.run_dir()),
            plan_action=action, revision=revision, feedback=feedback))

    def resume(self, model=None):
        return autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, model or self.model)

    def named(self):
        hive.initialize(self.project)
        self.args.model = self.args.base_url = None
        self.args.agent = 'gemma'
        claims = patch.object(hive, 'claims_root', return_value=self.root / 'claims')
        claims.start()
        self.addCleanup(claims.stop)

    def test_review_wait_is_durable_and_costs_no_worker_or_model_calls(self):
        self.args.plan_mode = 'review'
        before = self.source.read_bytes()
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertEqual(result['status'], 'waiting_for_plan_approval', result)
        self.assertFalse((self.run_dir() / 'intake.json').exists())
        self.assertEqual(self.source.read_bytes(), before)
        calls, state = list(self.worker.calls), self.state()
        for _ in range(3):
            self.assertEqual(self.resume(lambda *a: self.fail('No inference while parked'))['status'], result['status'])
        self.assertEqual(self.worker.calls, calls)
        self.assertEqual(self.state(), state)
        row, _ = hive_status.goal(self.project, self.run_dir())
        self.assertEqual(row['status'], 'waiting_for_plan_approval')
        self.decide('approve')
        self.assertTrue(self.resume()['ok'])
        self.assertTrue((self.run_dir() / 'closeout.json').exists())

    def test_exact_file_scope_rejects_path_drift_before_plan_review(self):
        self.args.scope_files = ['parity_cell.py']
        seen = []
        scoped_checks = []
        def model(messages, tools, *rest):
            if any(t['function']['name'] == 'submit_intake' for t in tools):
                response, stats = self.model(messages, tools, *rest)
                offered = next(t for t in tools if t['function']['name'] == 'submit_intake')
                schema = offered['function']['parameters']
                self.assertEqual(len(schema['anyOf']), 3)
                for variant in schema['anyOf']:
                    self.assertEqual(variant['properties']['plan']['properties']['cells']['items']
                                     ['properties']['files']['items']['enum'], ['parity_cell.py'])
                seen.append(messages)
                if len(seen) == 1:
                    function = response['tool_calls'][0]['function']
                    value = json.loads(function['arguments'])
                    value['plan']['cells'][0]['files'] = ['unexpected/parity_cell.py']
                    value['plan']['cells'][0]['result']['path'] = 'unexpected/parity_cell.py'
                    function['arguments'] = json.dumps(value)
                return response, stats
            for name in ('submit_goal_checks', 'submit_checks'):
                offered = next((t for t in tools if t['function']['name'] == name), None)
                if offered is not None:
                    self.assertEqual([t['function']['name'] for t in tools], [name])
                    allowed = offered['function']['parameters']['properties']['checks']['items']
                    self.assertEqual(allowed['properties']['paths']['items']['enum'], ['parity_cell.py'])
                    scoped_checks.append(name)
            return self.model(messages, tools, *rest)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(seen), 2)
        self.assertEqual(scoped_checks, ['submit_goal_checks', 'submit_checks'])
        self.assertIn('parity_cell.py', str(seen[1]))
        self.assertEqual(self.state()['plan']['cells'][0]['files'], ['parity_cell.py'])
        scope = self.run_dir() / 'file-scope.json'
        value = json.loads(scope.read_text())
        value['files'] = ['unexpected/parity_cell.py']
        scope.write_text(json.dumps(value))
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())

    def test_revision_keeps_first_draft_and_rejects_stale_or_conflicting_decisions(self):
        self.args.plan_mode = 'review'
        autonomous.start(self.project, self.args, self.worker, self.model)
        first = (self.run_dir() / 'planning/1/draft.json').read_bytes()
        self.decide('revise', feedback='Mention the zero case explicitly.')
        with self.assertRaises(HornetError):
            self.decide('approve')
        seen = []
        def revised(messages, tools, *rest):
            seen.append(messages)
            return self.model(messages, tools, *rest)
        result = self.resume(revised)
        self.assertEqual(result['revision'], 2)
        self.assertIn('Mention the zero', str(seen))
        self.assertEqual(first, (self.run_dir() / 'planning/1/draft.json').read_bytes())
        with self.assertRaisesRegex(HornetError, 'stale'):
            self.decide('approve', revision=1)
        self.decide('approve', revision=2)
        self.assertTrue(self.resume()['ok'])
        state = self.state()
        self.assertEqual(set(state['planning']['decisions']), {'1', '2'})

    def test_draft_and_consumed_decision_tampering_fail_closed(self):
        self.args.plan_mode = 'review'
        autonomous.start(self.project, self.args, self.worker, self.model)
        path = self.run_dir() / 'planning/1/draft.json'
        original = path.read_text()
        value = json.loads(original)
        value['candidate']['reason'] = 'tampered'
        path.write_text(json.dumps(value))
        with self.assertRaises(HornetError):
            self.resume()
        path.write_text(original)
        self.decide('approve')
        self.assertTrue(self.resume()['ok'])
        path = self.run_dir() / 'planning/1/decision.json'
        value = json.loads(path.read_text())
        value['feedback'] = 'changed after consumption'
        path.write_text(json.dumps(value))
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())

    def test_named_supervisor_approves_once_and_cannot_be_overridden_by_user(self):
        self.named()
        self.args.approver = 'qwen'
        seen = []
        def model(messages, tools, selected, *rest):
            if any(t['function']['name'] == 'submit_contract_review' for t in tools):
                seen.append(selected)
                return self.submit('submit_contract_review', {'decision': 'approve', 'issues': [],
                    'rationale': 'One bounded file and complete parity requirements.'}, 'approval')
            return self.model(messages, tools, selected, *rest)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(seen, [hive.EXAMPLE['agents']['qwen']['model']])
        self.assertEqual(planning.decision(self.run_dir(), self.state())['actor'], 'qwen')
        with self.assertRaises(HornetError):
            self.decide('approve')

    def test_supervisor_revisions_are_bounded_and_preserved(self):
        self.named()
        self.args.approver = 'qwen'
        def reject(messages, tools, *rest):
            if any(t['function']['name'] == 'submit_contract_review' for t in tools):
                return self.submit('submit_contract_review', {'decision': 'revise', 'issues': ['Clarify zero'],
                    'rationale': 'Needs clarification'}, 'approval')
            return self.model(messages, tools, *rest)
        result = autonomous.start(self.project, self.args, self.worker, reject)
        self.assertFalse(result['ok'])
        self.assertIn('revision limit', result['error'])
        self.assertEqual(len(self.state()['planning']['drafts']), 3)
        self.assertFalse((self.run_dir() / 'intake.json').exists())

    def test_repeated_reads_handoff_to_named_agent_with_unchanged_contract(self):
        self.named()
        self.args.recovery_agents = ['qwen']
        reads = []
        def model(messages, tools, selected, *rest):
            if not any(t['function']['name'].startswith('submit_') for t in tools) and selected == hive.EXAMPLE['agents']['gemma']['model']:
                reads.append(messages)
                return call('read_file', {'path': 'parity_cell.py'}, 'loop-' + str(len(reads))), {}
            return self.model(messages, tools, selected, *rest)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(reads), 3)
        record = self.state()['cells']['check-even']
        event = recovery.latest(self.run_dir(), record)
        self.assertEqual(event['from_agent'], 'gemma')
        self.assertEqual(event['to_agent'], 'qwen')
        child = session.load(self.project, self.project.path(record['run_dir']))
        self.assertEqual(child['attempts'], 2)
        self.assertEqual(child['acceptance_sha256'], event['checks_sha256'])
        failure = json.loads((self.run_dir() / event['failure']['path']).read_text())
        self.assertEqual(failure['status'], 'stagnated')
        self.assertTrue(any('handoff' in row for row in child['history']))
        self.assertTrue(all(row['agent'] == 'qwen' for row in self.state()['request_records'][-3:]))

    def test_revised_plans_keep_separate_rejected_contexts_in_one_attempt(self):
        self.named()
        self.args.approver = 'qwen'
        count = [0]
        def reject(messages, tools, *rest):
            if any(t['function']['name'] == 'submit_contract_review' for t in tools):
                return self.submit('submit_contract_review', {'decision': 'revise',
                    'issues': ['Clarify zero'], 'rationale': 'Needs clarification'}, 'review')
            if any(t['function']['name'] == 'submit_intake' for t in tools):
                count[0] += 1
                if count[0] % 3:
                    return self.submit('submit_intake', {'schema_version': 1, 'mode': 'execute',
                        'reason': 'Invalid draft ' + str(count[0]), 'self_cell_id': '',
                        'plan': {'schema_version': 2, 'cells': [], 'coverage': []}}, 'invalid')
            return self.model(messages, tools, *rest)
        result = autonomous.start(self.project, self.args, self.worker, reject)
        self.assertFalse(result['ok'])
        self.assertIn('revision limit', result['error'])
        state = autonomous.load(self.project, self.run_dir())
        refs = []
        for ref in state['planning']['drafts']:
            draft = mission.read(self.run_dir() / ref['path'])
            archived = mission.read(self.run_dir() / draft['role']['path'])
            evidence = archived['refocus_history'][0]
            self.assertEqual(mission.reference(self.run_dir(), self.run_dir() / evidence['path']), evidence)
            refs.append(evidence['path'])
        self.assertEqual(len(set(refs)), 3)
        self.assertEqual(count[0], 9)

    def test_stagnation_recovery_is_bounded_and_disableable(self):
        self.args.max_handoffs = 0
        turns = []
        def loop(messages, tools, *rest):
            if any(t['function']['name'].startswith('submit_') for t in tools):
                return self.model(messages, tools, *rest)
            turns.append(1)
            return call('read_file', {'path': 'parity_cell.py'}, 'loop-' + str(len(turns))), {}
        result = autonomous.start(self.project, self.args, self.worker, loop)
        self.assertFalse(result['ok'])
        self.assertEqual(len(turns), 3)
        self.assertFalse(self.state()['cells']['check-even'].get('recoveries'))

    def test_default_handoff_uses_fresh_context_then_stops_same_loop(self):
        turns = []
        def loop(messages, tools, *rest):
            if any(t['function']['name'].startswith('submit_') for t in tools):
                return self.model(messages, tools, *rest)
            turns.append(messages)
            return call('read_file', {'path': 'parity_cell.py'}, 'loop-' + str(len(turns))), {}
        result = autonomous.start(self.project, self.args, self.worker, loop)
        self.assertFalse(result['ok'])
        self.assertEqual(len(turns), 6)
        self.assertIn('Verified resume checkpoint', str(turns[3]))
        self.assertEqual(len(self.state()['cells']['check-even']['recoveries']), 1)

    def test_explicit_cell_resume_checks_id_and_reuses_saved_attempt(self):
        self.args.max_handoffs = 0
        def loop(messages, tools, *rest):
            if any(t['function']['name'].startswith('submit_') for t in tools):
                return self.model(messages, tools, *rest)
            return call('read_file', {'path': 'parity_cell.py'}, 'same-read'), {}
        stopped = autonomous.start(self.project, self.args, self.worker, loop)
        self.assertFalse(stopped['ok'])
        run = self.run_dir()
        previous = (run / 'attempts/1/result.json').read_bytes()
        with self.assertRaisesRegex(HornetError, 'exact unfinished cell'):
            cell_actions.resume(self.project, run, 'absent', self.worker, self.model)
        result = cell_actions.resume(self.project, run, 'check-even', self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual((run / 'attempts/1/result.json').read_bytes(), previous)
        with self.assertRaisesRegex(HornetError, 'verified cells'):
            cell_actions.resume(self.project, run, 'check-even', self.worker, self.model)

    def test_handoff_refuses_unknown_operations_or_changed_source(self):
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        record = self.state()['cells']['check-even']
        child = session.load(self.project, self.project.path(record['run_dir']))
        child['phase'] = 'tool_in_flight'
        self.assertIsNone(recovery.prepare(self.run_dir(), self.state(), self.state()['plan']['cells'][0],
            self.project.path(record['run_dir']), child, {'status': 'stagnated'}, None))
        self.assertTrue(result['ok'])

    def test_closeout_occurs_after_worker_lease_release(self):
        original = closeout.finish
        observed = []
        def finish(run, state, result):
            with WorkerLock(workspaces.lock_path(self.project, state['settings'], self.worker), run):
                observed.append(True)
            return original(run, state, result)
        with patch.object(closeout, 'finish', side_effect=finish):
            result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertTrue(result['ok'])
        self.assertEqual(observed, [True])
        autonomous.load(self.project, self.run_dir())

    def test_interrupted_closeout_resumes_without_inference_or_reexecuting_cells(self):
        with patch.object(closeout, 'finish', side_effect=OSError('simulated power loss')):
            with self.assertRaises(OSError):
                autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertEqual(self.state()['phase'], 'complete')
        self.assertFalse((self.run_dir() / 'closeout.json').exists())
        result = self.resume(lambda *a: self.fail('No inference for closeout'))
        self.assertTrue(result['ok'])
        self.assertIn('closeout', result)
        self.assertEqual(self.state()['attempts'], 1)

    def test_cli_and_invalid_policy(self):
        parser = build_parser()
        args = parser.parse_args(['plan', 'approve', 'coding/run', '--revision', '2'])
        self.assertEqual(args.revision, 2)
        scoped = parser.parse_args(['autowork', '--worker-config', 'worker.json', '--goal', 'Fix the module',
                                    '--scope-file', 'a.py', '--scope-file', 'b.py'])
        self.assertEqual(scoped.scope_files, ['a.py', 'b.py'])
        for value in ({'mode': []}, {'approver': 'absent'}, {'max_drafts': True}):
            with self.assertRaises(HornetError):
                planning.policy(value)
        for value in ({'max_handoffs': 3}, {'agents': ['absent']}, {'agents': 'gemma'}):
            with self.assertRaises(HornetError):
                recovery.policy(value)

    def test_goal_turn_budget_can_exceed_single_job_limit(self):
        self.args.max_turns = 60
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(autonomous.load(self.project, self.run_dir())['settings']['max_turns'], 60)
        with self.assertRaisesRegex(HornetError, 'max-turns 1-30'):
            session.validate_settings(self.state()['settings'])
        self.args.max_turns = 61
        with self.assertRaisesRegex(HornetError, 'max-turns 1-60'):
            autonomous.start(self.project, self.args, self.worker, self.model)

    def test_user_clarification_survives_a_plan_revision(self):
        self.args.plan_mode = 'review'
        result = autonomous.start(self.project, self.args, self.worker,
            lambda *a: (call('ask_user', {'question': 'Which file?'}, 'target-question'), {}))
        questions.respond(self.project, self.run_dir(), result['question_id'], 'Keep parity_cell.py as the target.')
        self.assertEqual(self.resume()['status'], 'waiting_for_plan_approval')
        self.decide('revise', feedback='Clarify the edge cases.')
        seen = []
        def model(messages, tools, *rest):
            seen.append(messages)
            return self.model(messages, tools, *rest)
        self.assertEqual(self.resume(model)['revision'], 2)
        self.assertIn('Keep parity_cell.py as the target.', str(seen))
        self.decide('approve', revision=2)
        self.assertTrue(self.resume()['ok'])


if __name__ == '__main__':
    unittest.main()
