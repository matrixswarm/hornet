"""Synthetic crash/resume tests; no model server or VM is contacted."""
import json
import tempfile
import unittest
from unittest.mock import patch

from hornet.desktop import automation as api
from hornet.desktop.cancellation import Cancellation, Cancelled, bind
from hornet.desktop.mission_lease import mission_lease
from hornet.desktop.mission_resume import resume_mission
from hornet.desktop.workspace import Workspace, write_json


class MissionResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(bind, None)
        self.ws = Workspace(self.tmp.name)
        base = self.ws.create('demo')
        api.make_initialization_draft(self.ws, 'demo', base, self.describe)
        self.base = api.accept_initialization(self.ws, 'demo')
        self.plan = self.enter_patch('hornet.desktop.automation.request_json')
        self.plan.return_value = {'existing_files': ['main.py'], 'new_files': [], 'explanation': 'Update main'}
        self.propose = self.enter_patch('hornet.desktop.registry_mission.propose_with_tools')
        self.propose.return_value = ({'summary': 'Update', 'files': {'main.py': 'print(42)\n'}}, [])
        self.memory = self.enter_patch('hornet.desktop.automation.model_describer')
        self.memory.return_value = self.describe
        self.run = self.enter_patch('hornet.desktop.vm_runner.run_python')
        self.run.return_value = {'exit_code': 0, 'timed_out': False, 'stdout': 'OK', 'stderr': ''}

    def enter_patch(self, target):
        patcher = patch(target)
        self.addCleanup(patcher.stop)
        return patcher.start()

    @staticmethod
    def describe(payload):
        return {'summaries': {name: 'Purpose of ' + name for name in payload['files']},
                'blueprint': {'purpose': 'Test project'}}

    def prepare(self, command='python3 -m unittest', **kwargs):
        return api.prepare_mission(self.ws, 'demo', self.base, 'Update', 'http://localhost', 'test-model',
                                   command, **kwargs)

    def saved(self):
        paths = list((self.ws.project('demo') / 'missions').glob('*.json'))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text(encoding='utf-8'))

    def resume(self):
        return resume_mission(self.ws, 'demo', self.saved()['id'], 'http://localhost', 'test-model')

    def assert_stopped_pointer(self, status, path, resume_status):
        record = self.saved()
        self.assertEqual(record['state'], status)
        directory = self.ws.project('demo') / 'missions' / record['id']
        authority = json.loads((directory / 'state.json').read_text())
        self.assertEqual(authority['pointer'], path)
        node = authority['nodes'][path]
        self.assertEqual(node['status'], status)
        self.assertIsNotNone(node['attempt_id'])
        for mirror in ('pointers.json', 'pointers/current.json'):
            value = json.loads((directory / mirror).read_text())
            self.assertEqual(value['pointer'], node)
            self.assertEqual(value['resume']['status'], resume_status)
        nested = json.loads((directory / 'pointers' / path / '_pointer.json').read_text())
        self.assertEqual(nested['status'], status)

    def test_failed_proposal_reuses_completed_plan(self):
        self.propose.side_effect = ValueError('offline')
        with self.assertRaisesRegex(ValueError, 'offline'):
            self.prepare()
        self.assert_stopped_pointer('failed', 'root/proposal', 'resume')
        self.propose.side_effect = None
        result = self.resume()
        self.assertEqual(result['state'], 'review')
        self.assertFalse(result['resumable'])
        self.assertEqual(self.plan.call_count, 1)
        self.assertEqual(self.propose.call_count, 2)
        self.assertEqual(api.require_ready(self.ws, 'demo')['accepted_revision'], self.base)

    def test_failed_memory_reuses_proposal_publication_and_vm_receipt(self):
        self.memory.return_value = lambda payload: (_ for _ in ()).throw(ValueError('memory offline'))
        with self.assertRaisesRegex(ValueError, 'memory offline'):
            self.prepare()
        revisions = self.ws.revisions('demo')
        self.memory.return_value = self.describe
        result = self.resume()
        self.assertEqual(self.plan.call_count, 1)
        self.assertEqual(self.propose.call_count, 1)
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(len(self.ws.revisions('demo')), len(revisions) + 1)
        self.assertTrue(result['checks_passed'])
        self.assertEqual(len(result['candidates']), 2)
        api.accept_mission(self.ws, 'demo', result)
        self.assertEqual(api.require_ready(self.ws, 'demo')['accepted_revision'], result['final_revision'])

    def test_crash_after_candidate_publication_reconciles_tag_without_duplicate(self):
        fork = self.ws.fork
        count = []
        def interrupted(*args, **kwargs):
            revision = fork(*args, **kwargs)
            count.append(revision)
            raise RuntimeError('lost publish response')
        with patch.object(self.ws, 'fork', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'lost publish response'):
                self.prepare()
        result = self.resume()
        self.assertEqual(result['candidates'][0], count[0])
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(len(self.ws.revisions('demo')), 4)
        self.assertEqual(self.propose.call_count, 1)

    def test_crash_after_memory_publication_reuses_every_completed_stage(self):
        fork = self.ws.fork
        count = []
        def interrupted(*args, **kwargs):
            revision = fork(*args, **kwargs)
            count.append(revision)
            if len(count) == 2:
                raise RuntimeError('lost memory response')
            return revision
        with patch.object(self.ws, 'fork', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'lost memory response'):
                self.prepare()
        revisions = self.ws.revisions('demo')
        result = self.resume()
        self.assertEqual(result['final_revision'], count[-1])
        self.assertEqual(self.ws.revisions('demo'), revisions)
        self.assertEqual(self.memory.call_count, 1)
        self.assertEqual(self.run.call_count, 1)

    def test_uncertain_vm_effect_blocks_without_automatic_retry(self):
        self.run.side_effect = ValueError('lost VM response')
        with self.assertRaisesRegex(ValueError, 'lost VM response'):
            self.prepare()
        revisions = self.ws.revisions('demo')
        self.run.side_effect = None
        with self.assertRaisesRegex(ValueError, 'check_candidate.*uncertain outcome'):
            self.resume()
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.ws.revisions('demo'), revisions)
        self.memory.assert_not_called()
        self.assertEqual(api.require_ready(self.ws, 'demo')['accepted_revision'], self.base)

    def test_interrupted_publication_without_proof_does_not_retry(self):
        with patch.object(self.ws, 'fork', side_effect=RuntimeError('publication interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'publication interrupted'):
                self.prepare()
        self.assert_stopped_pointer('failed', 'root/publish_candidate', 'blocked')
        with patch.object(self.ws, 'fork') as fork:
            with self.assertRaisesRegex(ValueError, 'publish_candidate.*uncertain outcome'):
                self.resume()
            fork.assert_not_called()

    def test_cancelled_model_preserves_plan_but_discards_late_proposal(self):
        token = Cancellation()
        bind(token)
        value = self.propose.return_value
        def cancelled(*args, **kwargs):
            token.cancel()
            return value
        self.propose.side_effect = cancelled
        with self.assertRaises(Cancelled):
            self.prepare()
        self.assert_stopped_pointer('cancelled', 'root/proposal', 'resume')
        self.assertTrue(self.saved()['resumable'])
        self.assertEqual(len(self.ws.revisions('demo')), 2)
        bind(None)
        self.propose.side_effect = None
        self.resume()
        self.assertEqual(self.plan.call_count, 1)
        self.assertEqual(self.propose.call_count, 2)

    def test_changed_base_or_model_cannot_reuse_checkpoint(self):
        self.propose.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        with self.assertRaisesRegex(ValueError, 'inputs.*changed'):
            resume_mission(self.ws, 'demo', self.saved()['id'], 'http://localhost', 'different-model')
        (self.ws.revision('demo', self.base) / 'main.py').write_text('print(99)\n')
        with self.assertRaisesRegex(ValueError, 'inputs.*changed'):
            self.resume()
        self.assertEqual(self.plan.call_count, 1)

    def test_changed_candidate_is_rejected_before_reusing_check(self):
        self.memory.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        candidate = self.saved()['candidates'][0]
        (self.ws.revision('demo', candidate) / 'main.py').write_text('print(99)\n')
        self.memory.side_effect = None
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.resume()
        self.assertEqual(self.run.call_count, 1)

    def test_corrupted_cached_result_is_rejected(self):
        self.propose.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        identifier = self.saved()['id']
        path = self.ws.project('demo') / 'missions' / identifier / 'results' / 'plan.json'
        value = json.loads(path.read_text())
        value['result']['existing_files'] = ['evil.py']
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, 'checkpoint result is invalid'):
            self.resume()

    def test_same_mission_cannot_run_concurrently(self):
        self.propose.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        directory = self.ws.project('demo') / 'missions' / self.saved()['id']
        with mission_lease(directory):
            with self.assertRaisesRegex(ValueError, 'still running'):
                self.resume()

    def test_repair_resume_reuses_failed_check_and_limits_repairs(self):
        self.run.return_value = {'exit_code': 1, 'timed_out': False, 'stdout': '', 'stderr': 'failed'}
        first = self.propose.return_value
        self.propose.side_effect = [first, ValueError('repair offline')]
        with self.assertRaisesRegex(ValueError, 'repair offline'):
            self.prepare()
        self.propose.side_effect = None
        self.propose.return_value = ({'summary': 'Repair', 'files': {'main.py': 'print(43)\n'}}, [])
        result = self.resume()
        self.assertEqual(self.plan.call_count, 1)
        self.assertEqual(self.propose.call_count, 3)
        self.assertEqual(self.run.call_count, 2)
        self.assertEqual(result['repairs'], 1)
        self.assertFalse(result['checks_passed'])
        with self.assertRaisesRegex(ValueError, 'did not pass'):
            api.accept_mission(self.ws, 'demo', result)

    def test_completed_review_cannot_be_resumed(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'no resumable checkpoints'):
            self.resume()

    def test_saved_result_survives_crash_before_goal_completion(self):
        from hornet.desktop.goal_store import GoalStore
        original = GoalStore.complete
        raised = []
        def fail_once(store, path, evidence):
            if not raised:
                raised.append(True)
                raise RuntimeError('lost completion')
            return original(store, path, evidence)
        with patch.object(GoalStore, 'complete', fail_once):
            with self.assertRaisesRegex(RuntimeError, 'lost completion'):
                self.prepare()
        result = self.resume()
        self.assertEqual(self.plan.call_count, 1)
        self.assertEqual(result['state'], 'review')


    def test_duplicate_publication_proofs_block_instead_of_guessing(self):
        fork = self.ws.fork
        def duplicated(*args, **kwargs):
            fork(*args, **kwargs)
            fork(*args, **kwargs)
            raise RuntimeError('ambiguous publication')
        with patch.object(self.ws, 'fork', side_effect=duplicated):
            with self.assertRaisesRegex(RuntimeError, 'ambiguous publication'):
                self.prepare()
        with self.assertRaisesRegex(ValueError, 'Multiple revisions'):
            self.resume()
        self.run.assert_not_called()

    def test_vm_result_cache_survives_failure_to_advance_pointer(self):
        from hornet.desktop.goal_store import GoalStore
        original = GoalStore.complete
        def interrupted(store, path, evidence):
            if path == 'root/check_candidate':
                raise RuntimeError('receipt saved but pointer not advanced')
            return original(store, path, evidence)
        with patch.object(GoalStore, 'complete', interrupted):
            with self.assertRaisesRegex(RuntimeError, 'receipt saved'):
                self.prepare()
        result = self.resume()
        self.assertTrue(result['checks_passed'])
        self.assertEqual(self.run.call_count, 1)

    def test_missing_completed_cache_is_not_silently_recomputed(self):
        self.propose.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        identifier = self.saved()['id']
        path = self.ws.project('demo') / 'missions' / identifier / 'results' / 'plan.json'
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'Completed mission checkpoint result is missing'):
            self.resume()
        self.assertEqual(self.plan.call_count, 1)

    def test_changed_check_config_or_mission_cannot_reuse_results(self):
        self.propose.side_effect = ValueError('offline')
        with self.assertRaises(ValueError):
            self.prepare()
        with patch('hornet.desktop.vm_runner.VM_NAME', 'Different-VM'):
            with self.assertRaisesRegex(ValueError, 'inputs.*changed'):
                self.resume()
        record = self.saved()
        with self.assertRaisesRegex(ValueError, 'inputs.*changed'):
            api.prepare_mission(self.ws, 'demo', self.base, 'Different mission', 'http://localhost',
                                'test-model', record['check_command'], resume_id=record['id'])
        self.assertEqual(self.plan.call_count, 1)

    def test_publication_contains_current_operation_graph_and_new_goal(self):
        result = self.prepare()
        graphs = [json.loads((self.ws.revision('demo', name) / '.hornet-meta' / 'goals.json').read_text())
                  for name in result['candidates']]
        self.assertEqual(len({g['goal_id'] for g in graphs}), len(graphs))
        for graph in graphs:
            self.assertEqual(graph['operation']['operation_id'], result['id'])
            saved = graph['operation_snapshot']
            self.assertEqual(saved['identity']['operation_id'], result['id'])
            self.assertEqual(saved['nodes']['root/plan']['status'], 'complete')
            self.assertEqual(saved['pointer']['path'], graph['operation']['checkpoint'])


    def test_review_record_write_failure_can_recover_cached_final_revision(self):
        from hornet.desktop import mission_resume
        original = mission_resume.write_json
        failures = []
        def interrupted(path, value):
            if value.get('state') == 'review' and not failures:
                failures.append(True)
                raise OSError('final review record interrupted')
            return original(path, value)
        with patch('hornet.desktop.mission_resume.write_json', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'final review record interrupted'):
                self.prepare()
        self.assertTrue(self.saved()['resumable'])
        revisions = self.ws.revisions('demo')
        result = self.resume()
        self.assertEqual(result['state'], 'review')
        self.assertEqual(self.ws.revisions('demo'), revisions)
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.memory.call_count, 1)



if __name__ == '__main__':
    unittest.main()
