"""Lost replies, interruption boundaries and scope checks for worker receipts."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

from hornet.coding import operations, operation_worker as journal, session
from hornet.coding.autonomous import ScopedWorker
from hornet.coding.worker import WorkerError
from hornet.project import HornetError, Project
from test_resume import LocalWorker, call


class JournalWorker(LocalWorker):
    operation_protocol = 1

    def __init__(self, root):
        super().__init__(root)
        self.directory = root.parent / 'journal'
        self.fault = None
        self.executions = []

    def operation(self, request):
        def dispatch(name, args):
            self.executions.append(name)
            return journal.dispatch(self.worker, WorkerError, name, args)
        return journal.execute(request, self.directory, lambda: session.snapshot(self), dispatch, self.fault)


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hornet-operation-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'controller').mkdir()
        (root / 'guest').mkdir()
        self.worker = JournalWorker(root / 'guest')
        self.path = root / 'guest/a.py'
        self.path.write_text('value = 1\n', encoding='utf-8')
        self.project = Project(root / 'controller')
        self.args = argparse.Namespace(task='Set value to 2, then verify', task_file=None, model='fixture',
            base_url='http://127.0.0.1:1234/v1', max_tokens=1024, timeout=60, max_turns=8, max_tools=8,
            protocol='lmstudio-json', context_chars=8000, worker_config='worker.json', run_dir='runs')

    def run_dir(self):
        return next((self.project.root / 'runs').iterdir())

    def checkpoint(self):
        return json.loads((self.run_dir() / 'checkpoint.json').read_text(encoding='utf-8'))

    def edit(self):
        return {'path': 'a.py', 'content': 'value = 2\n',
                'expected_sha256': hashlib.sha256(self.path.read_bytes()).hexdigest()}

    def start(self, stage='completed', name='write_file', arguments=None):
        arguments = arguments or self.edit()
        def fault(point):
            if point == stage:
                raise KeyboardInterrupt()
        self.worker.fault = fault
        with self.assertRaises(KeyboardInterrupt):
            session.work(self.project, self.args, self.worker, lambda *unused: (call(name, arguments, 'once'), {}))
        self.worker.fault = None
        self.assertEqual(self.checkpoint()['phase'], 'tool_in_flight')

    def finish(self, *unused):
        return {'role': 'assistant', 'content': 'Finished'}, {}

    def resume(self, worker=None):
        return session.resume(self.project, argparse.Namespace(run=str(self.run_dir())), worker or self.worker, self.finish)

    def test_completed_edit_lost_reply_recovers_without_reexecution(self):
        self.start()
        result = self.resume()
        self.assertTrue(result['ok'])
        self.assertEqual(self.worker.executions, ['write_file'])
        self.assertTrue(next(e for e in self.checkpoint()['history'] if e.get('tool') == 'write_file')['result']['ok'])
        self.assertEqual(result['tool_calls'], 1)
        self.assertEqual(result['attempt'], 2)
        self.assertEqual(len(list((self.run_dir() / 'operations').glob('*.json'))), 1)
        self.assertTrue(any('operation_recovered' in e for e in self.checkpoint()['history']))
        self.assertEqual(json.loads((self.run_dir() / 'attempts/1/result.json').read_text())['status'], 'cancelled')

    def test_fresh_worker_object_recovers_from_disk(self):
        self.start()
        fresh = JournalWorker(self.path.parent)
        self.assertTrue(self.resume(fresh)['ok'])
        self.assertEqual(fresh.executions, [])

    def test_pre_dispatch_checkpoint_can_send_same_id_once(self):
        original = session.save
        cut = [False]
        def save(run, job):
            original(run, job)
            if job['phase'] == 'tool_in_flight' and not cut[0]:
                cut[0] = True
                raise KeyboardInterrupt()
        with patch.object(session, 'save', side_effect=save):
            self.start(stage='none')
        self.assertFalse(self.worker.directory.exists())
        ident = self.checkpoint()['in_flight_operation']['id']
        self.assertTrue(self.resume()['ok'])
        self.assertEqual(self.worker.executions, ['write_file'])
        self.assertTrue((self.worker.directory / (ident + '.json')).exists())

    def test_started_marker_without_effect_is_not_guessed(self):
        self.start('started')
        self.assertEqual(self.worker.executions, [])
        with self.assertRaisesRegex(HornetError, 'inspection is required'):
            self.resume()
        self.assertEqual(self.worker.executions, [])

    def test_effect_without_completion_is_not_replayed(self):
        self.start('executed')
        self.assertEqual(self.path.read_text(), 'value = 2\n')
        with self.assertRaisesRegex(HornetError, 'inspection is required'):
            self.resume()
        self.assertEqual(self.worker.executions, ['write_file'])

    def test_command_lost_reply_does_not_duplicate_effect(self):
        self.args.tools = ['run_python']
        self.start(name='run_python', arguments={'code': "from pathlib import Path; p=Path('a.py'); p.write_text(p.read_text()+'# once\\n')"})
        self.assertTrue(self.resume()['ok'])
        self.assertEqual(self.path.read_text().count('# once'), 1)
        self.assertEqual(self.worker.executions, ['run_python'])

    def test_run_command_receipt(self):
        self.args.tools = ['run_command']
        self.start(name='run_command', arguments={'argv': ['python3', '-c', "from pathlib import Path; Path('a.py').write_text('value = 2\\n')"]})
        self.assertTrue(self.resume()['ok'])
        self.assertEqual(self.worker.executions, ['run_command'])

    def test_exact_block_edit_receipt(self):
        args = self.edit()
        args.pop('content')
        args.update(old_text='value = 1', new_text='value = 2')
        self.args.tools = ['edit_file']
        self.start(name='edit_file', arguments=args)
        self.assertTrue(self.resume()['ok'])
        self.assertEqual(self.worker.executions, ['edit_file'])

    def test_stale_file_hash_has_completed_failure_receipt(self):
        args = self.edit()
        args['expected_sha256'] = '0' * 64
        self.start(arguments=args)
        self.assertFalse(self.resume()['ok'])
        entry = next(e for e in self.checkpoint()['history'] if e.get('tool') == 'write_file')
        self.assertFalse(entry['result']['ok'])
        self.assertEqual(self.worker.executions, ['write_file'])

    def test_source_drift_after_completion_blocks_adoption(self):
        self.start()
        self.path.write_text('external change')
        with self.assertRaisesRegex(ValueError, 'changed after'):
            self.resume()
        self.assertEqual(self.worker.executions, ['write_file'])

    def test_identity_drift_blocks_before_receipt_call(self):
        self.start()
        self.worker.binding['connection'] = 'different'
        with self.assertRaisesRegex(HornetError, 'worker identity'):
            self.resume()

    def test_request_id_cannot_be_rebound(self):
        self.start()
        active = copy.deepcopy(self.checkpoint()['in_flight_operation'])
        active['arguments']['content'] = 'other'
        with self.assertRaisesRegex(ValueError, 'different request'):
            self.worker.operation(active)
        self.assertEqual(self.worker.executions, ['write_file'])

    def test_corrupt_result_digest_blocks_adoption(self):
        self.start()
        receipt_path = next(self.worker.directory.glob('*.json'))
        receipt = json.loads(receipt_path.read_text())
        receipt['result']['ok'] = False
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'Invalid operation completion'):
            self.resume()

    def test_corrupt_controller_request_blocks_resume(self):
        self.start()
        job = self.checkpoint()
        job['in_flight_operation']['arguments']['content'] = 'tampered'
        session.save(self.run_dir(), job)
        with self.assertRaisesRegex(HornetError, 'valid resumable checkpoint'):
            self.resume()

    def test_current_cell_scope_applies_to_recovery(self):
        self.start()
        with self.assertRaisesRegex(HornetError, 'current cell scope'):
            self.resume(ScopedWorker(self.worker, ['other.py']))
        self.assertTrue(self.resume(ScopedWorker(self.worker, ['a.py']))['ok'])

    def test_scoped_worker_rejects_unrestricted_commands(self):
        scoped = ScopedWorker(self.worker, ['a.py'])
        self.assertFalse(operations.supported(scoped, 'run_python', {'code': 'pass'}))
        self.assertFalse(operations.supported(scoped, 'write_file', {'path': 'other.py'}))

    def test_busy_operation_is_not_executed(self):
        self.start()
        active = self.checkpoint()['in_flight_operation']
        with patch.object(journal, 'lock', side_effect=OSError('busy')):
            result = self.worker.operation(active)
        self.assertEqual(result['status'], 'busy')
        self.assertEqual(self.worker.executions, ['write_file'])

    def test_pending_receipt_survives_second_lost_reply(self):
        self.start()
        original = self.worker.operation
        def lost(request):
            original(request)
            raise HornetError('reply lost again')
        with patch.object(self.worker, 'operation', side_effect=lost):
            with self.assertRaisesRegex(HornetError, 'reply lost'):
                self.resume()
        self.assertTrue(self.resume()['ok'])
        self.assertEqual(self.worker.executions, ['write_file'])

    def test_changed_source_before_first_dispatch_is_refused(self):
        active = {'version': 1, 'id': 'op-' + 'a' * 32, 'tool': 'write_file',
                  'arguments': self.edit(), 'before': session.snapshot(self.worker)}
        self.path.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'before the operation'):
            self.worker.operation(active)
        self.assertEqual(self.worker.executions, [])

    def test_source_change_between_receipt_and_adoption_is_refused(self):
        self.start()
        original = self.worker.operation
        def drift(request):
            result = original(request)
            self.path.write_text('late change')
            return result
        with patch.object(self.worker, 'operation', side_effect=drift):
            with self.assertRaisesRegex(HornetError, 'receipt was not adopted'):
                self.resume()

    def test_concurrent_duplicate_waits_for_the_single_execution(self):
        active = {'version': 1, 'id': 'op-' + 'a' * 32, 'tool': 'write_file',
                  'arguments': self.edit(), 'before': session.snapshot(self.worker)}
        entered, release = threading.Event(), threading.Event()
        def fault(point):
            if point == 'started':
                entered.set()
                if not release.wait(10):
                    raise ValueError('Test synchronization timeout')
        self.worker.fault = fault
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.worker.operation, active)
            try:
                self.assertTrue(entered.wait(10))
                duplicate = self.worker.operation(active)
                self.assertEqual(duplicate['status'], 'busy')
            finally:
                release.set()
            self.assertTrue(first.result(timeout=10)['ok'])
        self.worker.fault = None
        self.assertTrue(self.worker.operation(active)['recovered'])
        self.assertEqual(self.worker.executions, ['write_file'])

    def test_transport_sends_bounded_fixed_code_and_data(self):
        import base64
        import zlib
        from hornet.coding.transport import SSHWorker
        active = {'version': 1, 'id': 'op-' + 'a' * 32, 'tool': 'write_file',
                  'arguments': self.edit(), 'before': session.snapshot(self.worker)}
        worker = object.__new__(SSHWorker)
        worker.workspace = 'g-' + 'b' * 32
        def wire(name, args):
            self.assertEqual(name, 'run_command')
            argv = args['argv']
            self.assertLessEqual(len(argv), 30)
            self.assertTrue(all(len(arg) <= 4000 for arg in argv))
            n = int(argv[3])
            program = zlib.decompress(base64.b64decode(''.join(argv[4:4+n])))
            self.assertEqual(program, Path(journal.__file__).read_bytes())
            self.assertEqual(json.loads(''.join(argv[4+n:])), dict(active, workspace=worker.workspace))
            return {'ok': True, 'output': json.dumps({'ok': False, 'status': 'busy'})}
        worker._wire = wire
        self.assertEqual(worker.operation(active)['status'], 'busy')

    def test_new_operations_have_durable_receipts_on_normal_path(self):
        count = [0]
        args = self.edit()
        def model(*unused):
            count[0] += 1
            return (call('write_file', args, 'normal'), {}) if count[0] == 1 else self.finish()
        result = session.work(self.project, self.args, self.worker, model)
        self.assertTrue(result['ok'])
        self.assertEqual(self.worker.executions, ['write_file'])
        self.assertNotIn('in_flight_operation', self.checkpoint())
        self.assertEqual(len(list((self.run_dir() / 'operations').glob('*.json'))), 1)

    def test_confirmed_evidence_is_checked_when_loading(self):
        self.start()
        self.resume()
        path = next((self.run_dir() / 'operations').glob('*.json'))
        receipt = json.loads(path.read_text())
        receipt['result']['ok'] = False
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(HornetError, 'valid resumable checkpoint'):
            session.load(self.project, self.run_dir())

    def test_each_new_journal_ancestor_is_flushed(self):
        directory = Path(self.temp.name) / 'fresh-parent' / 'scope'
        with patch.object(journal, 'sync_directory') as flush:
            journal.make_directory(directory)
        self.assertEqual([entry.args[0] for entry in flush.call_args_list], [directory.parent.parent, directory.parent])

    def test_autonomous_cell_recovers_without_replanning(self):
        import test_autonomous
        from hornet.coding import autonomous
        fixture = test_autonomous.AutonomousTests('test_agents_plan_check_and_execute_false_result_without_human_file')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        worker = JournalWorker(fixture.guest)
        def crash(point):
            if point == 'completed':
                raise KeyboardInterrupt()
        worker.fault = crash
        with self.assertRaises(KeyboardInterrupt):
            autonomous.start(fixture.project, fixture.args, worker, fixture.model)
        state = fixture.state()
        original_roles = copy.deepcopy(state['roles'])
        worker.fault = None
        result = autonomous.resume(fixture.project, argparse.Namespace(run=str(fixture.run_dir())), worker, fixture.model)
        self.assertTrue(result['ok'], result)
        self.assertIs(result['values']['check-even'], False)
        self.assertEqual(worker.executions, ['write_file'])
        self.assertEqual(fixture.state()['roles'], original_roles)


if __name__ == '__main__':
    unittest.main()
