"""Automatic wakeups are opt-in, bounded and checked at the execution lease."""
import argparse
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from hornet.cli import build_parser
from hornet.coding import autonomous, control, mission, planning, questions, supervisor
from hornet.coding.locking import WorkerLock
from hornet.project import HornetError
import test_autonomous as fixtures
from test_resume import call


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        fixtures.AutonomousTests.setUp(self)
        self.worker.connection_binding = 'a' * 64
        self.worker.binding['connection'] = self.worker.connection_binding
    run_dir = fixtures.AutonomousTests.run_dir
    state = fixtures.AutonomousTests.state
    model = fixtures.AutonomousTests.model
    write = fixtures.AutonomousTests.write
    submit = fixtures.AutonomousTests.submit

    def prepare(self, model=None):
        self.args.max_handoffs = 0
        autonomous.start(self.project, self.args, self.worker, model or self.offline)

    def offline(self, *unused):
        raise HornetError('Model endpoint unavailable')

    def options(self, *flags):
        return build_parser().parse_args(['hive', 'supervise', '--run', str(self.run_dir()), *flags])

    def save(self, state):
        (self.run_dir() / 'recipe-checkpoint.json').write_text(json.dumps(state), encoding='utf-8')

    def resume(self, project, args, **kwargs):
        return autonomous.resume(project, args, self.worker, self.model, **kwargs)

    def tick(self, *flags, resume=None, now=1000):
        return supervisor.tick(self.project, self.options(*flags), resume=resume or self.resume, clock=lambda: now)

    def row(self):
        return self.tick()['goals'][0]

    def test_default_scan_is_read_only_and_does_not_infer(self):
        self.prepare()
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        model = Mock(side_effect=AssertionError('No dispatch'))
        value = self.tick(resume=model)
        self.assertFalse(value['goals'][0]['eligible'])
        self.assertEqual(value['goals'][0]['reason'], 'not_enrolled')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        model.assert_not_called()

    def test_enrolled_lost_model_response_completes_with_retained_attempt(self):
        self.prepare()
        original = (self.run_dir() / 'attempts/1/result.json').read_bytes()
        self.assertTrue(self.tick('--enable')['goals'][0]['eligible'])
        value = self.tick('--dispatch')
        self.assertEqual(value['activation']['status'], 'completed', value)
        self.assertEqual(original, (self.run_dir() / 'attempts/1/result.json').read_bytes())
        self.assertEqual(self.state()['settings']['max_turns'], 30)
        self.assertFalse(self.tick('--dispatch', now=2000)['goals'][0]['eligible'])
        self.assertTrue((self.run_dir() / 'supervisor/attempts/1/outcome.json').is_file())

    def test_disable_and_reenable_preserve_limit_and_history(self):
        self.prepare()
        self.tick('--enable', '--max-activations', '1')
        self.tick('--dispatch', resume=self.failed_resume)
        self.tick('--disable')
        self.assertEqual(self.row()['reason'], 'disabled')
        with self.assertRaisesRegex(HornetError, 'frozen'):
            self.tick('--enable', '--max-activations', '2')
        self.tick('--enable', '--max-activations', '1')
        self.assertEqual(self.row()['reason'], 'activation_limit')

    def failed_resume(self, project, args, **kwargs):
        return autonomous.resume(project, args, self.worker, self.offline, **kwargs)

    def test_offline_model_has_cooldown_and_lifetime_limit(self):
        self.prepare()
        self.tick('--enable', '--max-activations', '2')
        self.tick('--dispatch', resume=self.failed_resume)
        self.assertEqual(self.row()['reason'], 'cooldown')
        self.tick('--dispatch', resume=self.failed_resume, now=1200)
        self.assertEqual(self.row()['reason'], 'activation_limit')
        self.assertEqual(self.state()['model_turns'], 3)

    def test_unchanged_refused_activation_never_repeats(self):
        self.prepare()
        self.tick('--enable')
        reject = Mock(side_effect=HornetError('SSH refused'))
        self.tick('--dispatch', resume=reject)
        self.assertEqual(self.row()['reason'], 'unchanged_since_activation')
        self.tick('--dispatch', resume=reject, now=100000)
        self.assertEqual(reject.call_count, 1)

    def test_active_process_is_never_killed_or_reclaimed(self):
        self.prepare()
        self.tick('--enable')
        path = self.run_dir() / 'runner.json'
        value = json.loads(path.read_text())
        value.update(status='running', pid=os.getpid())
        path.write_text(json.dumps(value))
        self.assertEqual(self.row()['reason'], 'active_process')
        value['pid'] = None
        path.write_text(json.dumps(value))
        self.assertEqual(self.row()['reason'], 'active_process')

    def test_dead_runner_at_lost_model_boundary_can_resume(self):
        self.prepare()
        self.tick('--enable')
        path = self.run_dir() / 'runner.json'
        value = json.loads(path.read_text())
        value.update(status='running', pid=99999999)
        path.write_text(json.dumps(value))
        with patch.object(supervisor, 'process_alive', return_value=False):
            self.assertTrue(self.row()['eligible'])

    def test_pause_continue_and_sticky_stop(self):
        self.prepare()
        self.tick('--enable')
        state = self.state()
        state['paused'] = True
        self.save(state)
        self.assertEqual(self.row()['reason'], 'paused')
        control.send(self.project, str(self.run_dir()), 'continue')
        self.assertTrue(self.row()['eligible'])
        control.send(self.project, str(self.run_dir()), 'stop')
        self.assertEqual(self.row()['reason'], 'stopped_by_operator')

    def test_cancelled_operator_run_never_automatically_restarts(self):
        self.prepare()
        self.tick('--enable')
        state = self.state()
        state['last_outcome']['status'] = 'cancelled'
        self.save(state)
        self.assertEqual(self.row()['reason'], 'operator_resolution_required')

    def test_legacy_input_waits_for_explicit_resume(self):
        self.prepare()
        self.tick('--enable')
        (self.run_dir() / 'commander_input.txt').write_text('continue')
        self.assertEqual(self.row()['reason'], 'pending_legacy_control')

    def test_unknown_final_verification_is_not_replayed(self):
        self.prepare()
        self.tick('--enable')
        state = self.state()
        state['final_checks_in_flight'] = True
        self.save(state)
        self.assertEqual(self.row()['reason'], 'unknown_operation_outcome')

    def test_exhausted_goal_budget_is_not_reset(self):
        self.prepare()
        self.tick('--enable')
        state = self.state()
        state['settings']['max_turns'] = state['model_turns']
        self.save(state)
        self.assertEqual(self.row()['reason'], 'budget_exhausted')

    def test_arbitrary_failure_is_not_retried(self):
        self.prepare()
        self.tick('--enable')
        state = self.state()
        state['request_records'][-1]['status'] = 'response_received'
        self.save(state)
        self.assertEqual(self.row()['reason'], 'inspection_required')

    def test_unanswered_question_waits_until_exact_answer(self):
        def ask(messages, tools, *unused):
            return call('ask_user', {'question': 'Which parity?'}, 'q1'), {}
        self.prepare(ask)
        self.tick('--enable')
        self.assertEqual(self.row()['reason'], 'waiting_for_user')
        ref = questions.pending(self.project, self.run_dir())[0]
        questions.respond(self.project, str(self.run_dir()), ref['record']['id'], 'Even is True.')
        self.assertEqual(self.row()['reason'], 'answer_ready')
        self.assertEqual(self.tick('--dispatch')['activation']['status'], 'completed')

    def test_cancelled_question_does_not_dispatch(self):
        self.prepare(lambda *unused: (call('ask_user', {'question': 'Which parity?'}, 'q1'), {}))
        self.tick('--enable')
        ref = questions.pending(self.project, self.run_dir())[0]
        questions.respond(self.project, str(self.run_dir()), ref['record']['id'], 'Stop here', cancel=True)
        self.assertEqual(self.row()['reason'], 'question_cancelled')

    def test_scan_does_not_recreate_missing_consumed_question(self):
        self.prepare(lambda *unused: (call('ask_user', {'question': 'Which parity?'}, 'q1'), {}))
        self.tick('--enable')
        ref = questions.pending(self.project, self.run_dir())[0]
        questions.respond(self.project, str(self.run_dir()), ref['record']['id'], 'Even is True.')
        self.tick('--dispatch', resume=self.failed_resume)
        path = questions.path_for(ref['record'], 'question.json')
        path.unlink()
        self.assertEqual(self.row()['reason'], 'invalid_evidence')
        self.assertFalse(path.exists())

    def test_root_plan_waits_for_bound_approval(self):
        self.args.plan_mode = 'review'
        self.prepare(self.model)
        self.tick('--enable')
        self.assertEqual(self.row()['reason'], 'waiting_for_plan_approval')
        planning.record(self.run_dir(), self.state(), 'approve')
        self.assertEqual(self.row()['reason'], 'plan_decision_ready')
        self.assertEqual(self.tick('--dispatch')['activation']['status'], 'completed')

    def test_guard_catches_pause_arriving_after_scan(self):
        self.prepare()
        self.tick('--enable')
        def race(project, args, **kwargs):
            control.send(project, str(self.run_dir()), 'pause')
            return self.resume(project, args, **kwargs)
        value = self.tick('--dispatch', resume=race)
        self.assertEqual(value['activation']['status'], 'activation_refused')
        self.assertIn('boundary changed', value['activation']['error'])
        self.assertEqual(self.state()['model_turns'], 1)

    def test_manual_source_changes_are_still_refused(self):
        self.prepare()
        self.tick('--enable')
        self.source.write_text('def is_even(n):\n    return None\n')
        value = self.tick('--dispatch')
        self.assertEqual(value['activation']['status'], 'activation_refused')
        self.assertIn('source changed', value['activation']['error'])
        self.assertEqual(self.state()['model_turns'], 1)

    def test_incomplete_activation_receipt_requires_acknowledgment(self):
        self.prepare()
        self.tick('--enable')
        crash = Mock(side_effect=SystemExit('simulated process death'))
        with self.assertRaises(SystemExit):
            self.tick('--dispatch', resume=crash)
        self.assertEqual(self.row()['reason'], 'supervisor_outcome_unknown')
        self.tick('--acknowledge', '1')
        self.assertEqual(self.row()['reason'], 'unchanged_since_activation')
        self.assertEqual(self.row()['activations'], 1)

    def test_real_os_lock_prevents_concurrent_supervisor_activation(self):
        self.prepare()
        self.tick('--enable')
        directory = self.root / 'coding/supervisor'
        with WorkerLock(directory / 'dispatch.lock', directory):
            value = self.tick('--dispatch')
        self.assertEqual(value['status'], 'supervisor_busy')
        self.assertEqual(self.row()['activations'], 0)

    def test_active_worker_lease_does_not_spend_activation(self):
        self.prepare()
        self.tick('--enable')
        with WorkerLock(self.root / ('coding/worker-locks/' + 'a' * 64 + '.lock'), self.run_dir()):
            value = self.tick('--dispatch')
        self.assertEqual(value['goals'][0]['reason'], 'active_worker_lease')
        self.assertEqual(self.row()['activations'], 0)
        self.assertTrue(self.row()['eligible'])

    def test_interrupted_enrollment_can_be_explicitly_finished(self):
        self.prepare()
        self.tick('--enable')
        (self.run_dir() / 'supervisor/enabled.json').unlink()
        self.assertEqual(self.row()['reason'], 'invalid_evidence')
        self.assertTrue(self.tick('--enable')['goals'][0]['enabled'])

    def test_policy_tampering_fails_closed(self):
        self.prepare()
        self.tick('--enable')
        path = self.run_dir() / 'supervisor/policy.json'
        value = json.loads(path.read_text())
        value['max_activations'] = 10
        path.write_text(json.dumps(value))
        self.assertEqual(self.row()['reason'], 'invalid_evidence')

    def test_cell_lost_model_reply_resumes_its_existing_job(self):
        def model(messages, tools, *rest):
            if self.worker_turn == 1 and 'write_file' in {item['function']['name'] for item in tools}:
                raise HornetError('Lost worker model reply')
            return self.model(messages, tools, *rest)
        self.prepare(model)
        self.assertEqual(self.state()['phase'], 'cell_running')
        self.tick('--enable')
        before = list((self.run_dir() / 'cells/check-even/jobs').iterdir())
        self.assertEqual(self.tick('--dispatch')['activation']['status'], 'completed')
        self.assertEqual(before, list((self.run_dir() / 'cells/check-even/jobs').iterdir()))

    def test_unknown_child_operation_blocks_even_with_continue(self):
        self.prepare()
        self.tick('--enable')
        control.send(self.project, str(self.run_dir()), 'continue')
        for phase in ('tool_in_flight', 'verification_in_flight'):
            with self.subTest(phase=phase), patch.object(supervisor, 'child_state', return_value={'phase': phase}):
                self.assertEqual(self.row()['reason'], 'unknown_operation_outcome')

    def test_policy_can_be_disabled_when_goal_checkpoint_is_corrupt(self):
        self.prepare()
        self.tick('--enable')
        (self.run_dir() / 'recipe-checkpoint.json').write_text('{broken')
        self.tick('--disable')
        self.assertFalse(supervisor.policy(self.project, self.run_dir())[1])

    def test_outcome_tampering_does_not_authorize_dispatch(self):
        self.prepare()
        self.tick('--enable')
        self.tick('--dispatch', resume=Mock(side_effect=HornetError('Unavailable')))
        path = self.run_dir() / 'supervisor/attempts/1/outcome.json'
        value = json.loads(path.read_text())
        value['decision_sha256'] = 'wrong'
        path.write_text(json.dumps(value))
        self.assertEqual(self.row()['reason'], 'invalid_evidence')

    def test_watch_does_not_retry_after_ctrl_c_during_activation(self):
        self.prepare()
        self.tick('--enable')
        with self.assertRaises(KeyboardInterrupt):
            self.tick('--dispatch', resume=Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(self.row()['reason'], 'unchanged_since_activation')
        self.assertEqual(json.loads((self.run_dir() / 'supervisor/attempts/1/outcome.json').read_text())['status'],
                         'interrupted_by_operator')

    def test_terminal_reports_enrollment_counts(self):
        from hornet import hive_status
        self.prepare()
        self.tick('--enable')
        goal, _ = hive_status.goal(self.project, self.run_dir())
        self.assertEqual(goal['supervisor'], {'enabled': True, 'activations': 0, 'limit': 3, 'unresolved': False})

    def test_watch_prints_only_changed_observations(self):
        self.prepare()
        options = self.options('--watch')
        output = io.StringIO()
        with patch.object(supervisor.time, 'sleep', side_effect=[None, KeyboardInterrupt]), redirect_stdout(output):
            value = supervisor.command(self.project, options)
        self.assertEqual(value['status'], 'supervisor_stopped')
        self.assertEqual(json.loads(output.getvalue())['status'], 'inspected')

    def test_invalid_cli_ranges_and_broad_enable_are_rejected(self):
        self.prepare()
        for flags in (['--interval', '0'], ['--cooldown', '2'], ['--max-activations', '11']):
            with self.subTest(flags=flags), self.assertRaises(HornetError):
                supervisor.command(self.project, self.options(*flags))
        args = build_parser().parse_args(['hive', 'supervise', '--enable'])
        with self.assertRaisesRegex(HornetError, 'exact --run'):
            supervisor.command(self.project, args)


if __name__ == '__main__':
    unittest.main()
