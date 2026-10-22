"""Named routing, live cell reassignment and real OS claim contention."""
import argparse
import copy
import hashlib
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hornet import hive
from hornet.cli import main
from hornet.coding import autonomous
from hornet.coding import session
from hornet.coding import goals
from hornet.project import HornetError, Project
import test_autonomous as fixtures
import test_resume as session_fixtures


class HiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='hornet-hive-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = Project(self.root)
        self.patcher = patch.object(hive, 'claims_root', return_value=self.root / 'claims')
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def profile(self, name='gemma'):
        return dict(hive.EXAMPLE['agents'][name], agent=name)

    def test_init_preserves_user_configuration_and_lists_labels(self):
        self.assertTrue(hive.initialize(self.project)['created'])
        path = self.root / 'hive.json'
        value = hive.read(path)
        value['agents']['gemma']['model'] = 'my-custom-model'
        path.write_text(json.dumps(value))
        self.assertFalse(hive.initialize(self.project)['created'])
        listing = hive.listing(self.project)
        self.assertEqual(listing['agents'][0]['model'], 'my-custom-model')

    def test_invalid_or_credential_bearing_configuration_is_rejected(self):
        for mutate in (
            lambda v: v.update(default_agent='absent'),
            lambda v: v['roles'].update(worker='absent'),
            lambda v: v['roles'].update(unknown='gemma'),
            lambda v: v['agents']['gemma'].update(base_url='http://user:secret@localhost:1234/v1'),
            lambda v: v['agents']['gemma'].update(base_url='http://localhost:99999/v1'),
            lambda v: v['agents']['gemma'].update(base_url='http://localhost:0/v1'),
            lambda v: v['agents']['gemma'].update(protocol=[]),
            lambda v: v['agents']['gemma'].update(api_key='secret'),
            lambda v: v.update(schema_version=True),
        ):
            with self.subTest(mutate=mutate):
                value = copy.deepcopy(hive.EXAMPLE)
                mutate(value)
                with self.assertRaises(HornetError):
                    hive.validate(value)

    def test_duplicate_json_fields_are_rejected(self):
        path = self.root / 'hive.json'
        path.write_text('{"schema_version": 1, "schema_version": 2}')
        with self.assertRaises(HornetError):
            hive.registry(self.project)

    def test_hive_marks_project_root_for_discovery_from_a_subdirectory(self):
        hive.initialize(self.project)
        child = self.root / 'src'
        child.mkdir()
        with patch.dict(os.environ, {'HORNET_ROOT': '', 'HORNET_PATH': ''}):
            with patch.object(Path, 'cwd', return_value=child):
                self.assertEqual(Project().root, self.root)

    def test_loopback_aliases_share_one_endpoint_claim(self):
        first, second = self.profile(), self.profile('qwen')
        first.pop('resource'); second.pop('resource')
        second['base_url'] = 'http://localhost:1234/v1'
        with hive.claim(first, self.root):
            with self.assertRaises(hive.AgentBusy):
                with hive.claim(second, self.root):
                    self.fail('Overlapping inference was allowed')
        with hive.claim(second, self.root):
            pass

    def test_shared_gpu_serializes_different_endpoints(self):
        first, second = self.profile(), self.profile('qwen')
        second['base_url'] = 'http://127.0.0.1:4321/v1'
        with hive.claim(first, self.root):
            with self.assertRaises(hive.AgentBusy):
                with hive.claim(second, self.root):
                    self.fail('Shared GPU claim was bypassed')

    def test_separate_endpoints_and_resources_can_be_claimed(self):
        first, second = self.profile(), self.profile('qwen')
        first.pop('resource'); second.pop('resource')
        second['base_url'] = 'http://127.0.0.1:4321/v1'
        with hive.claim(first, self.root), hive.claim(second, self.root):
            pass

    def test_a_second_process_cannot_overlap_a_claim(self):
        code = ('import json,sys\nfrom pathlib import Path\nfrom hornet import hive\n'
                'hive.claims_root=lambda:Path(sys.argv[1])\n'
                'try:\n with hive.claim(json.loads(sys.argv[2]),Path(sys.argv[3])): print("acquired")\n'
                'except hive.AgentBusy: print("waiting_for_agent")\n')
        arguments = [sys.executable, '-c', code, str(self.root / 'claims'),
                     json.dumps(self.profile()), str(self.root)]
        with hive.claim(self.profile(), self.root):
            result = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'waiting_for_agent')
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'acquired')

    def test_claims_release_after_an_exception(self):
        with self.assertRaises(RuntimeError):
            with hive.claim(self.profile(), self.root):
                raise RuntimeError('interrupted')
        with hive.claim(self.profile(), self.root):
            pass

    def test_same_pinned_worker_is_reserved_across_goals(self):
        worker = type('PinnedWorker', (), {'connection_binding': 'a' * 64})()
        settings = {'hive': {'registry': hive.EXAMPLE, 'agent': 'gemma'}}
        first, second = self.root / 'project-one', self.root / 'project-two'
        first.mkdir(); second.mkdir()
        with hive.worker_claim(settings, worker, first):
            with self.assertRaises(HornetError):
                with hive.worker_claim(settings, worker, second):
                    self.fail('Different projects overlapped on the same pinned worker')
        with hive.worker_claim(settings, worker, second):
            pass

    def test_crashed_claim_requires_its_own_saved_run_for_recovery(self):
        original, other = self.root / 'original', self.root / 'other'
        original.mkdir(); other.mkdir()
        code = ('import json,os,sys\nfrom pathlib import Path\nfrom hornet import hive\n'
                'hive.claims_root=lambda:Path(sys.argv[1])\n'
                'with hive.claim(json.loads(sys.argv[2]),Path(sys.argv[3])): os._exit(0)\n')
        result = subprocess.run([sys.executable, '-c', code, str(self.root / 'claims'),
            json.dumps(self.profile()), str(original)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaises(hive.AgentBusy):
            with hive.claim(self.profile(), other):
                self.fail('A different goal reclaimed the crashed claim')
        with hive.claim(self.profile(), original):
            pass
        self.assertTrue(list(original.glob('lock-recovery-*.json')))

    def test_cli_initializes_and_lists_without_inference(self):
        for action in ('init', 'list'):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(['--root', str(self.root), '--json', 'hive', action])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())['ok'])

    def test_revision_can_choose_a_label_without_inheriting_conflicting_model_flags(self):
        hive.initialize(self.project)
        settings = dict(hive.EXAMPLE['agents']['gemma'], worker_config='worker.json')
        settings['hive'] = {'registry': copy.deepcopy(hive.EXAMPLE), 'agent': 'gemma'}
        state = {'settings': settings}
        args = argparse.Namespace(run='parent', files=['a.py'], agent='qwen', model=None, base_url=None)
        with patch.object(autonomous, 'load', return_value=state), patch.object(goals, 'route', return_value={}):
            with patch.object(autonomous, 'start', return_value={'ok': True}) as start:
                goals.revise(self.project, args)
        configured = {'model': args.model, 'base_url': args.base_url, 'protocol': 'native'}
        hive.configure(self.project, args, configured)
        self.assertEqual(configured['hive']['agent'], 'qwen')
        self.assertEqual(configured['model'], hive.EXAMPLE['agents']['qwen']['model'])
        start.assert_called_once()


class HiveGoalTests(unittest.TestCase):
    submit = fixtures.AutonomousTests.submit
    model = fixtures.AutonomousTests.model
    write = fixtures.AutonomousTests.write
    run_dir = fixtures.AutonomousTests.run_dir
    state = fixtures.AutonomousTests.state

    def setUp(self):
        fixtures.AutonomousTests.setUp(self)
        self.value = copy.deepcopy(hive.EXAMPLE)
        self.value['agents']['gemma']['model'] = 'fixture-gemma'
        self.value['agents']['qwen']['model'] = 'fixture-qwen'
        self.value['cells']['check-even'] = 'qwen'
        (self.root / 'hive.json').write_text(json.dumps(self.value))
        self.args.model = self.args.base_url = None
        self.args.agent = 'gemma'
        self.patcher = patch.object(hive, 'claims_root', return_value=self.root / 'claims')
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_cell_label_routes_actual_requests_and_preserves_verifier(self):
        seen = []
        def model(messages, offered, selected, *rest):
            seen.append((hive.request_role(offered), selected))
            return self.model(messages, offered, selected, *rest)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertIn(('worker', 'fixture-qwen'), seen)
        self.assertTrue(all(selected == 'fixture-gemma' for role, selected in seen if role != 'worker'))
        records = self.state()['request_records']
        self.assertIn('qwen', {row['agent'] for row in records})
        with self.assertRaisesRegex(HornetError, 'Completed'):
            hive.assign(self.project, str(self.run_dir()), 'check-even', 'gemma')

    def test_on_demand_assignment_takes_effect_on_next_worker_request(self):
        self.value['cells'] = {}
        (self.root / 'hive.json').write_text(json.dumps(self.value))
        seen = []
        original_plan = []
        def model(messages, offered, selected, *rest):
            if hive.request_role(offered) == 'worker':
                seen.append(selected)
                if len(seen) == 1:
                    original_plan.append((self.run_dir() / 'recipe-plan.json').read_bytes())
                    hive.assign(self.project, str(self.run_dir()), 'check-even', 'qwen')
            return self.model(messages, offered, selected, *rest)
        result = autonomous.start(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(seen[0], 'fixture-gemma')
        self.assertTrue(all(name == 'fixture-qwen' for name in seen[1:]))
        self.assertEqual((self.run_dir() / 'recipe-plan.json').read_bytes(), original_plan[0])
        self.assertEqual(len(list((self.run_dir() / 'hive-assignment-history').glob('*.json'))), 1)

    def test_busy_agent_preserves_goal_and_budget_for_resume(self):
        profile = dict(self.value['agents']['gemma'], agent='gemma')
        with hive.claim(profile, self.root):
            result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertEqual(result['status'], 'waiting_for_agent', result)
        self.assertEqual(result['model_turns'], 0)
        self.assertEqual(self.state()['roles']['intake']['attempt_turns'], 0)
        # Changing the project registry cannot redirect an existing saved goal.
        changed = copy.deepcopy(self.value)
        changed['agents']['gemma']['model'] = 'changed-after-start'
        (self.root / 'hive.json').write_text(json.dumps(changed))
        seen = []
        def model(messages, offered, selected, *rest):
            seen.append(selected)
            return self.model(messages, offered, selected, *rest)
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('changed-after-start', seen)
        self.assertTrue((self.run_dir() / 'attempts/1/result.json').exists())

    def test_unknown_label_stops_before_worker_or_model_access(self):
        self.args.agent = 'absent'
        with self.assertRaisesRegex(HornetError, 'Unknown hive agent'):
            autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertEqual(self.worker.calls, [])


class HiveSessionTests(unittest.TestCase):
    run_dir = session_fixtures.ResumeTests.run_dir
    checkpoint = session_fixtures.ResumeTests.checkpoint

    def setUp(self):
        session_fixtures.ResumeTests.setUp(self)
        hive.initialize(self.project)
        self.args.model = self.args.base_url = None
        self.args.agent = 'gemma'
        self.patcher = patch.object(hive, 'claims_root', return_value=self.controller / 'claims')
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_standalone_busy_ticket_resumes_with_another_named_agent(self):
        seen = []
        def finish(messages, offered, model, *rest):
            seen.append(model)
            if self.path.read_text() == 'value = 1\n':
                return session_fixtures.call('write_file', {'path': 'a.py', 'content': 'value = 2\n',
                    'expected_sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}, 'hive-edit'), {}
            return {'role': 'assistant', 'content': 'Ready for verification.'}, {}
        profile = dict(hive.EXAMPLE['agents']['gemma'], agent='gemma')
        with hive.claim(profile, self.controller):
            result = session.work(self.project, self.args, self.worker, finish)
        self.assertEqual(result['status'], 'waiting_for_agent')
        self.assertEqual(self.checkpoint()['turns_used'], 0)
        self.assertEqual(seen, [])
        result = session.resume(self.project, argparse.Namespace(run=str(self.run_dir()), agent='qwen'),
                                self.worker, finish)
        self.assertTrue(result['ok'], result)
        self.assertEqual(seen, [hive.EXAMPLE['agents']['qwen']['model']] * 2)
        self.assertEqual(self.path.read_text(), 'value = 2\n')
        requests = [row for row in self.checkpoint()['history'] if 'stats' in row]
        self.assertTrue(requests)
        self.assertTrue(all(row['stats']['agent'] == 'qwen' for row in requests))

    def test_saved_hive_refuses_unlabelled_connection_override(self):
        def stop(*unused):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            session.work(self.project, self.args, self.worker, stop)
        with self.assertRaisesRegex(HornetError, 'Resume this hive job with --agent'):
            session.resume(self.project, argparse.Namespace(run=str(self.run_dir()), model='untracked'),
                           self.worker, lambda *unused: self.fail('No inference permitted'))

    def test_legacy_ticket_claims_worker_before_switching_to_a_hive_agent(self):
        self.args.agent = None
        self.args.model = 'legacy-model'
        self.args.base_url = 'http://localhost:1234/v1'
        self.worker.connection_binding = 'b' * 64
        with self.assertRaises(KeyboardInterrupt):
            session.work(self.project, self.args, self.worker,
                lambda *unused: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertNotIn('hive', self.checkpoint()['settings'])
        with hive.worker_claim({'hive': True}, self.worker, self.controller):
            with self.assertRaises(HornetError):
                session.resume(self.project, argparse.Namespace(run=str(self.run_dir()), agent='gemma'),
                    self.worker, lambda *unused: self.fail('A different goal owns this worker'))
