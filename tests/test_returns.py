"""Cell receipts gate dependency use and preserve evidence across interruptions."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from hornet.coding import acceptance, autonomous, returns
from hornet.project import HornetError
import test_autonomous as fixtures


class ReturnManifestTests(unittest.TestCase):
    setUp = fixtures.AutonomousTests.setUp
    submit = fixtures.AutonomousTests.submit
    model = fixtures.AutonomousTests.model
    write = fixtures.AutonomousTests.write
    run_dir = fixtures.AutonomousTests.run_dir
    state = fixtures.AutonomousTests.state

    def finish(self, model=None):
        result = autonomous.start(self.project, self.args, self.worker, model or self.model)
        self.assertTrue(result['ok'], result)
        return result

    def manifest_path(self):
        return self.run_dir() / 'cells/check-even/return-manifest.json'

    def manifest(self):
        return json.loads(self.manifest_path().read_text())

    def save_state(self, state):
        (self.run_dir() / 'recipe-checkpoint.json').write_text(json.dumps(state))

    def test_false_return_contains_contract_changes_checks_and_attempts(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = self.finish()
        manifest = self.manifest()
        self.assertIs(manifest['result']['value'], False)
        self.assertIs(manifest['ok'], True)
        self.assertEqual(manifest['result']['schema'], {'type': 'boolean'})
        self.assertEqual(manifest['result']['args'], [3])
        self.assertEqual(manifest['changed_files'], [{'path': 'parity_cell.py', 'change': 'modified',
            'before_sha256': before, 'after_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}])
        self.assertEqual(manifest['verification']['passed'], 2)
        self.assertEqual(manifest['verification']['total'], 2)
        self.assertEqual([item['id'] for item in manifest['verification']['checks']], ['parity', 'typed-return'])
        self.assertEqual(manifest['provenance']['job_attempts'][0]['outcome'], 'succeeded')
        self.assertIsNotNone(manifest['provenance']['job_attempts'][0]['conditions'])
        self.assertEqual(result['return_manifests']['check-even'], returns.artifact(self.run_dir(), self.manifest_path()))

    def test_changed_file_manifest_distinguishes_add_modify_delete(self):
        before = {'binding': {'id': 'worker'}, 'files': {'old.py': 'a' * 64, 'edit.py': 'b' * 64, 'same.py': 'c' * 64}}
        after = {'binding': {'id': 'worker'}, 'files': {'new.py': 'd' * 64, 'edit.py': 'e' * 64, 'same.py': 'c' * 64}}
        changes = returns.changed_files(before, after)
        self.assertEqual([(item['path'], item['change']) for item in changes],
                         [('edit.py', 'modified'), ('new.py', 'added'), ('old.py', 'deleted')])
        self.assertIsNone(changes[1]['before_sha256'])
        self.assertIsNone(changes[2]['after_sha256'])
        after['binding']['id'] = 'other'
        with self.assertRaisesRegex(HornetError, 'identity'):
            returns.changed_files(before, after)

    def test_failed_recipe_attempt_survives_and_is_linked_from_return(self):
        def offline(*unused):
            raise HornetError('Model unavailable under these conditions')
        first = autonomous.start(self.project, self.args, self.worker, offline)
        self.assertFalse(first['ok'])
        previous = (self.run_dir() / 'attempts/1/result.json').read_bytes()
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        attempts = self.manifest()['provenance']['prior_recipe_attempts']
        self.assertEqual(attempts[0]['outcome'], 'failed')
        self.assertEqual(attempts[0]['result']['sha256'], hashlib.sha256(previous).hexdigest())
        self.assertEqual((self.run_dir() / 'attempts/1/result.json').read_bytes(), previous)

    def test_failed_verification_round_is_retained_after_repair(self):
        def model(messages, tools, *unused):
            if any(item['function']['name'].startswith('submit_') for item in tools):
                return self.model(messages, tools)
            self.worker_turn += 1
            if self.worker_turn == 1:
                return self.write('    return int(value % 2 == 0)\n', 'wrong-type')
            if self.worker_turn == 3:
                return self.write('    return value % 2 == 0\n', 'fixed-type')
            return {'role': 'assistant', 'content': 'Verify.'}, {}
        self.finish(model)
        rounds = self.manifest()['provenance']['verification_rounds']
        self.assertEqual([entry['checks_passed'] for entry in rounds], [False, True])
        self.assertTrue((self.run_dir() / rounds[0]['artifacts']['acceptance-results.json']['path']).is_file())

    def test_resumed_child_keeps_failed_attempt_and_original_conditions(self):
        def interrupted(messages, tools, *unused):
            if not any(item['function']['name'].startswith('submit_') for item in tools) and self.worker_turn == 1:
                raise HornetError('Model disconnected after confirmed read')
            return self.model(messages, tools)
        failed = autonomous.start(self.project, self.args, self.worker, interrupted)
        self.assertFalse(failed['ok'])
        child = Path(self.state()['cells']['check-even']['run_dir'])
        original = (child / 'attempts/1/result.json').read_bytes()
        conditions = (child / 'attempts/1/conditions.json').read_bytes()
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual([item['outcome'] for item in self.manifest()['provenance']['job_attempts']], ['failed', 'succeeded'])
        self.assertEqual((child / 'attempts/1/result.json').read_bytes(), original)
        self.assertEqual((child / 'attempts/1/conditions.json').read_bytes(), conditions)

    def test_failed_cell_has_no_consumable_manifest(self):
        self.args.cell_turns = 1
        result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertFalse(result['ok'])
        self.assertNotIn('check-even', result['values'])
        self.assertFalse(self.manifest_path().exists())

    def test_manifest_tampering_is_refused_even_with_recomputed_index(self):
        self.finish()
        original = self.manifest()
        state = self.state()
        state['phase'] = 'final_checks'
        for field in ('value', 'source', 'checks', 'changed_files'):
            changed = copy.deepcopy(original)
            if field == 'value':
                changed['result']['value'] = 0
            elif field == 'source':
                changed['source']['after_sha256'] = 'f' * 64
            elif field == 'checks':
                changed['verification']['passed'] = 0
            else:
                changed['changed_files'] = []
            self.manifest_path().write_text(json.dumps(changed))
            state['cells']['check-even']['return_manifest'] = returns.artifact(self.run_dir(), self.manifest_path())
            self.save_state(state)
            with self.subTest(field=field), self.assertRaisesRegex(HornetError, 'checkpoint'):
                autonomous.load(self.project, self.run_dir())

    def test_missing_manifest_and_changed_evidence_block_resume(self):
        self.finish()
        state = self.state()
        state['phase'] = 'final_checks'
        self.save_state(state)
        original = self.manifest_path().read_bytes()
        self.manifest_path().unlink()
        with self.assertRaisesRegex(HornetError, 'checkpoint'):
            autonomous.load(self.project, self.run_dir())
        self.manifest_path().write_bytes(original)
        checks = Path(state['cells']['check-even']['run_dir']) / 'acceptance-results.json'
        changed = json.loads(checks.read_text())
        changed['results'][0]['passed'] = False
        checks.write_text(json.dumps(changed))
        with self.assertRaisesRegex(HornetError, 'checkpoint'):
            autonomous.load(self.project, self.run_dir())

    def test_state_zero_cannot_replace_verified_false(self):
        self.finish()
        state = self.state()
        state['values']['check-even'] = 0
        state['cells']['check-even']['value'] = 0
        self.save_state(state)
        with self.assertRaisesRegex(HornetError, 'checkpoint'):
            autonomous.load(self.project, self.run_dir())

    def test_publication_crash_adopts_receipt_without_reexecuting_cell(self):
        original_publish = returns.publish
        def interrupt(*args):
            original_publish(*args)
            raise KeyboardInterrupt()
        with patch.object(returns, 'publish', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            autonomous.start(self.project, self.args, self.worker, self.model)
        before = self.manifest_path().read_bytes()
        writes = self.worker.calls.count('write_file')
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker,
                                   lambda *args: self.fail('Completed cell must not request the model again'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.manifest_path().read_bytes(), before)
        self.assertEqual(self.worker.calls.count('write_file'), writes)

    def test_legacy_receipts_reconstructed_from_original_evidence(self):
        self.finish()
        state = self.state()
        state.update(schema_version=1, phase='final_checks')
        record = state['cells']['check-even']
        del record['return_manifest']
        del record['initial_snapshot']
        self.manifest_path().unlink()
        # Older child attempts did not record their own conditions file.
        (Path(record['run_dir']) / 'attempts/1/conditions.json').unlink()
        self.save_state(state)
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker,
                                   lambda *args: self.fail('Migration must use retained evidence'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.state()['schema_version'], 2)
        manifest = self.manifest()
        self.assertIs(manifest['provenance']['reconstructed_from_legacy'], True)
        self.assertIsNone(manifest['provenance']['job_attempts'][0]['conditions'])
        self.assertIs(manifest['result']['value'], False)

    def test_dependency_receipt_checked_before_next_model_request(self):
        future = copy.deepcopy(self.plan['cells'][0])
        future.update(id='later-cell', depends_on=['check-even'])
        self.plan['cells'].append(future)
        original_store = autonomous.stored
        corrupted = [False]
        def stored(path, value):
            original_store(path, value)
            if path.name == 'progress.json' and value.get('stage') == 'cell_completed' and not corrupted[0]:
                corrupted[0] = True
                self.manifest_path().write_text('{}')
        with patch.object(autonomous, 'stored', side_effect=stored):
            result = autonomous.start(self.project, self.args, self.worker, self.model)
        self.assertFalse(result['ok'])
        self.assertNotIn('verifier-later-cell', self.state()['roles'])
        self.assertEqual(self.worker.calls.count('write_file'), 1)

    def test_completed_cell_source_chain_cannot_be_rewritten(self):
        self.finish()
        state = self.state()
        state['cells']['check-even']['initial_snapshot']['files']['parity_cell.py'] = 'b' * 64
        self.save_state(state)
        with self.assertRaisesRegex(HornetError, 'checkpoint'):
            autonomous.load(self.project, self.run_dir())


if __name__ == '__main__':
    unittest.main()
