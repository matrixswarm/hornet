"""Read-only snapshots, live navigation, action clocks and execution telemetry."""
import copy
from contextlib import nullcontext, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hornet import hive, hive_status, hive_terminal
from hornet.cli import main
from hornet.coding import activity, autonomous
from hornet.project import HornetError, Project
import test_autonomous as fixtures


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hornet-terminal-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = Project(self.root)
        self.run = self.root / 'coding' / 'recipes' / 'one'
        self.run.mkdir(parents=True)
        self.state = {'schema_version': 6, 'project_root': str(self.root), 'run_dir': str(self.run),
            'goal': 'Write a parity function', 'goal_sha256': 'a' * 64, 'phase': 'cell_running',
            'settings': {'model': hive.EXAMPLE['agents']['gemma']['model'], 'max_turns': 30, 'max_tools': 80,
                         'hive': {'registry': copy.deepcopy(hive.EXAMPLE), 'agent': 'gemma'}},
            'plan': {'cells': [{'id': 'parent', 'task': 'Parity', 'files': ['parity.py'], 'depends_on': []}]},
            'routes': {'parent': {'plan': {'cells': [
                {'id': 'child-a', 'task': 'First half', 'files': ['parity.py'], 'depends_on': []},
                {'id': 'child-b', 'task': 'Second half', 'files': ['parity.py'], 'depends_on': ['child-a']} ]}}},
            'active_cell': 'child-a', 'cells': {}, 'values': {}, 'model_turns': 3,
            'role_tools': 2, 'cell_usage': {}, 'attempts': 1}
        self.save()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def save(self):
        self.write(self.run / 'recipe-checkpoint.json', self.state)

    def snapshot(self):
        result = hive_status.snapshot(self.project)
        self.assertTrue(result['ok'], result['warnings'])
        return result

    def cell(self, result, ident='child-a'):
        return next(c for c in result['cells'] if c['id'] == ident)

    def inventory(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_snapshot_is_read_only_without_models_or_worker(self):
        before = self.inventory()
        with patch('hornet.coding.model.completion', side_effect=AssertionError('model called')), patch('hornet.coding.transport.SSHWorker', side_effect=AssertionError('worker called')):
            first, second = self.snapshot(), self.snapshot()
        self.assertEqual(first, second)
        self.assertEqual(before, self.inventory())
        self.assertEqual(first['active_cells'], 0)
        self.assertEqual(len(first['cells']), 4)

    def test_only_live_owner_and_current_cell_are_active(self):
        with activity.running(self.run):
            data = self.snapshot()
            self.assertEqual(data['active_cells'], 1)
            self.assertTrue(self.cell(data)['active'])
            self.assertFalse(self.cell(data, 'parent')['active'])
        self.assertEqual(self.snapshot()['active_cells'], 0)

    def test_dead_pid_is_interrupted_instead_of_active(self):
        with activity.running(self.run):
            with patch.object(hive_status, 'process_alive', return_value=False):
                data = self.snapshot()
                self.assertEqual(data['active_cells'], 0)
                self.assertEqual(self.cell(data)['status'], 'interrupted')
                view = hive_terminal.View()
                view.screen, view.selected = 'detail', (str(self.run), 'child-a')
                self.assertIn('resume-cell', '\n'.join(view.lines(data, 120, 50)))

    def test_completed_cell_shows_linked_revision_command(self):
        self.state.update(phase='complete', values={'parent': True, 'child-a': True, 'child-b': True})
        self.save()
        data = self.snapshot()
        view = hive_terminal.View()
        view.screen, view.selected = 'detail', (str(self.run), 'child-a')
        self.assertIn('--cell child-a', '\n'.join(view.lines(data, 120, 50)))

    def test_paused_runner_is_not_an_active_cell(self):
        with activity.running(self.run):
            self.write(self.run / 'progress.json', {'observed_at': activity.now(), 'stage': 'paused'})
            self.assertEqual(self.snapshot()['active_cells'], 0)

    def test_previous_failure_does_not_hide_a_resumed_attempt(self):
        self.state['last_outcome'] = {'status': 'stopped'}
        self.save()
        self.write(self.run / 'progress.json', {'observed_at': '2020-01-01T00:00:00+00:00', 'stage': 'stopped'})
        with activity.running(self.run):
            self.assertTrue(self.cell(self.snapshot())['active'])

    def test_last_action_keeps_its_timestamp_across_refreshes(self):
        with patch.object(activity, 'now', return_value='2026-01-01T00:00:00+00:00'):
            activity.record(self.run, 'model_response', 'child-a', 'qwen', 'remote-model')
        first = self.cell(self.snapshot())
        self.assertEqual(first['actual_agent'], 'qwen')
        self.assertEqual(first['actual_model'], 'remote-model')
        self.assertEqual(hive_status.elapsed(first['last_action']['observed_at'], hive_status.timestamp('2026-01-01T00:00:42+00:00')), 42)
        self.assertEqual(first['last_action'], self.cell(self.snapshot())['last_action'])
        self.assertIsNone(self.cell(self.snapshot(), 'parent')['last_action'])

    def test_child_discovery_before_parent_checkpoint_sync(self):
        child = self.run / 'cells' / 'child-a' / 'jobs' / 'job'
        self.write(child / 'checkpoint.json', {'phase': 'dispatch', 'tools_used': 3, 'turns_used': 2})
        activity.record(child, 'tool_returned', 'child-a', 'qwen', 'qwen-model', 'write_file')
        row = self.cell(self.snapshot())
        self.assertEqual(row['last_action']['tool'], 'write_file')
        self.assertEqual(row['tools_used'], 3)
        self.assertEqual(row['actual_agent'], 'qwen')
        self.assertEqual(len(self.snapshot()['goals']), 1)

    def test_live_assignment_differs_from_last_actual_agent(self):
        activity.record(self.run, 'model_response', 'child-a', 'gemma', 'gemma-model')
        self.write(self.run / 'hive-assignments.json', {'schema_version': 1, 'cells': {'child-a': 'qwen'}})
        row = self.cell(self.snapshot())
        self.assertEqual(row['assigned_agent'], 'qwen')
        self.assertEqual(row['actual_agent'], 'gemma')

    def test_checks_show_only_matching_recorded_evidence(self):
        self.state['cells']['child-a'] = {'checks': {'checks': [{'id': 'one'}, {'id': 'two'}]}, 'checks_sha256': 'digest'}
        self.save()
        self.write(self.run / 'final-checks' / 'child-a.json', {'plan_sha256': 'different', 'results': [{'passed': True, 'completed': True}]})
        self.assertIsNone(self.cell(self.snapshot())['checks']['passed'])
        self.write(self.run / 'final-checks' / 'child-a.json', {'plan_sha256': 'digest', 'results': [{'passed': True, 'completed': True}, {'passed': False, 'completed': True}]})
        self.assertEqual(self.cell(self.snapshot())['checks'], {'passed': 1, 'total': 2, 'observed': True})

    def test_inverted_tree_places_children_before_parents_and_goal(self):
        data = self.snapshot()
        lines = hive_terminal.inverted_tree(data, self.cell(data))
        self.assertIn('> child-a', lines[0])
        self.assertIn('parent', lines[-2])
        self.assertTrue(lines[-1].startswith('GOAL '))

    def test_navigation_selects_exact_goal_cell_and_scrolls(self):
        data = self.snapshot()
        view = hive_terminal.View()
        self.assertTrue(view.key('g', data))
        view.key('enter', data)
        self.assertEqual(view.goal_filter, str(self.run))
        view.key('down', data)
        view.key('enter', data)
        self.assertEqual(view.selected, (str(self.run), 'parent'))
        view.key('down', data)
        self.assertEqual(view.scroll, 1)
        self.assertLessEqual(len(view.lines(data, 40, 12)), 12)
        view.key('a', data)
        self.assertEqual(view.rows(data), [])
        view.key('b', data)
        self.assertEqual(view.screen, 'menu')
        self.assertFalse(view.key('q', data))

    def test_terminal_control_characters_are_sanitized(self):
        self.state['goal'] = '\x1b[2J MALICIOUS\x07\u009b0m'
        self.save()
        data = self.snapshot()
        view = hive_terminal.View()
        view.screen, view.selected = 'detail', (str(self.run), 'child-a')
        rendered = '\n'.join(view.lines(data, 100, 40))
        self.assertNotIn('\x1b', rendered)
        self.assertNotIn('\x07', rendered)
        self.assertNotIn('\u009b', rendered)

    def test_malformed_run_does_not_hide_healthy_runs(self):
        bad = self.root / 'coding' / 'recipes' / 'bad'
        bad.mkdir()
        (bad / 'recipe-checkpoint.json').write_text('{partial')
        data = hive_status.snapshot(self.project)
        self.assertFalse(data['ok'])
        self.assertEqual(len(data['goals']), 1)
        self.assertTrue(data['warnings'])

    def test_unknown_activity_is_not_fabricated_from_checkpoint_mtime(self):
        self.assertIsNone(self.cell(self.snapshot())['last_action'])
        self.assertEqual(hive_terminal.age(None), 'unrecorded')
        self.assertEqual(hive_status.elapsed('bad'), None)
        self.assertEqual(hive_status.elapsed('2026-01-01T00:00:00'), None)

    def test_oversized_records_are_rejected_before_json_parsing(self):
        path = self.run / 'activity.json'
        path.write_bytes(b' ' * (128 * 1024 + 1))
        self.assertIn('size limit', hive_status.snapshot(self.project)['warnings'][0])

    def test_cyclic_or_oversized_tree_is_not_rendered(self):
        self.state['routes']['child-a'] = {'plan': {'cells': [self.state['plan']['cells'][0]]}}
        self.save()
        self.assertIn('cell tree', hive_status.snapshot(self.project)['warnings'][0])

    def test_child_checkpoint_cannot_escape_cell_or_project(self):
        self.state['cells']['child-a'] = {'run_dir': str(self.root / 'elsewhere')}
        self.save()
        self.assertIn('outside its cell', hive_status.snapshot(self.project)['warnings'][0])
        with self.assertRaises(HornetError):
            hive_status.snapshot(self.project, run=str(self.root.parent))

    def test_existing_standalone_ticket_is_visible(self):
        ticket = self.root / 'coding' / 'runs' / 'two'
        self.write(ticket / 'checkpoint.json', {'run_dir': str(ticket), 'job_id': 'standalone',
            'task': 'Fix a script', 'phase': 'complete', 'settings': {'model': 'direct'},
            'snapshot': {'files': {'a.py': 'digest'}}, 'acceptance': {'checks': [{'id': 'works'}]},
            'acceptance_sha256': 'digest'})
        self.write(ticket / 'acceptance-results.json', {'plan_sha256': 'digest', 'results': [{'passed': True, 'completed': True}]})
        data = self.snapshot()
        self.assertEqual(len(data['goals']), 2)
        row = next(c for c in data['cells'] if c['id'] == 'worker')
        self.assertEqual(row['assigned_model'], 'direct')
        self.assertEqual(row['files'], ['a.py'])
        self.assertEqual(row['checks']['passed'], 1)
        self.assertEqual(next(g for g in data['goals'] if g['run_dir'] == str(ticket))['cells_verified'], 1)

    def test_completed_or_prior_cell_is_not_active_during_final_checks(self):
        self.state.update(phase='final_checks', values={'child-a': True})
        self.save()
        with activity.running(self.run):
            data = self.snapshot()
            self.assertFalse(self.cell(data)['active'])
            self.assertTrue(self.cell(data, '@goal')['active'])

    def test_bad_directory_scan_does_not_hide_other_runs(self):
        from hornet import hive_status as module
        actual = os.scandir
        private = self.root / 'coding' / 'private'
        private.mkdir()
        def scan(path):
            if Path(path) == private:
                raise PermissionError('fixture denied')
            return actual(path)
        with patch.object(module.os, 'scandir', side_effect=scan):
            data = hive_status.snapshot(self.project)
        self.assertEqual(len(data['goals']), 1)
        self.assertIn('fixture denied', data['warnings'][0])

    def test_cli_json_and_once_do_not_require_a_tty(self):
        for suffix in (['--json'], ['--once']):
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(['--root', str(self.root), 'hive', 'terminal', '--run', str(self.run)] + suffix)
            self.assertEqual(code, 0, out.getvalue())
            if suffix == ['--json']:
                self.assertEqual(len(json.loads(out.getvalue())['cells']), 4)
            else:
                self.assertIn('COMMAND CENTER', out.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--root', str(self.root), 'hive', 'terminal', '--interval', 'nan', '--json']), 2)

    def test_remote_endpoint_is_supported_by_the_registry(self):
        value = copy.deepcopy(hive.EXAMPLE)
        value['agents']['remote'] = {'base_url': 'https://inference.example.com/v1', 'model': 'remote-model', 'protocol': 'native'}
        value['default_agent'] = 'remote'
        self.assertEqual(hive.endpoint(hive.validate(value)['agents']['remote']), 'https://inference.example.com:443')

    def test_runner_is_idle_even_after_an_exception(self):
        with self.assertRaises(RuntimeError):
            with activity.running(self.run):
                raise RuntimeError('fixture interrupted')
        self.assertEqual(activity.read(self.run / 'runner.json')['status'], 'idle')

    def test_live_view_remains_responsive_during_a_slow_refresh(self):
        data = self.snapshot()
        started, release = threading.Event(), threading.Event()
        calls = []
        def snapshot(*args):
            calls.append(True)
            if len(calls) > 1:
                started.set()
                release.wait(5)
            return data
        keys = iter(['1', 'enter', 'q'])
        def key(*args):
            if len(calls) == 1:
                self.assertTrue(started.wait(2))
            return next(keys)
        args = SimpleNamespace(interval=0.25, run_dir='coding', run=None, once=False, json=False)
        output = io.StringIO()
        output.isatty = lambda: True
        try:
            with patch.object(hive_terminal, 'screen', return_value=nullcontext()), patch.object(hive_terminal, 'read_key', side_effect=key), patch.object(hive_terminal.sys.stdin, 'isatty', return_value=True), patch.object(hive_status, 'snapshot', side_effect=snapshot), redirect_stdout(output):
                before = time.monotonic()
                result = hive_terminal.run(self.project, args)
                self.assertLess(time.monotonic() - before, 3)
            self.assertTrue(result['terminal_closed'])
            self.assertIn('DETAIL', output.getvalue())
        finally:
            release.set()

    def test_actual_execution_emits_cell_actions_and_preserves_checkpoints(self):
        fixture = fixtures.AutonomousTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        def observed_model(*args):
            if fixture.worker_turn == 0:
                # Observe during execution, using the real saved cell state.
                observed = hive_status.snapshot(fixture.project, directory='recipes')
                self.assertTrue(observed['ok'], observed['warnings'])
            return fixture.model(*args)
        result = autonomous.start(fixture.project, fixture.args, fixture.worker, observed_model)
        self.assertTrue(result['ok'], result)
        data = hive_status.snapshot(fixture.project, directory='recipes')
        self.assertTrue(data['ok'], data['warnings'])
        cell = self.cell(data, 'check-even')
        self.assertEqual(cell['status'], 'verified')
        self.assertEqual(cell['checks']['passed'], cell['checks']['total'])
        self.assertIsNotNone(cell['last_action'])
        self.assertEqual(data['active_cells'], 0)
        autonomous.load(fixture.project, fixture.run_dir())
