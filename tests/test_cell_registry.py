"""Registry edits use real cell scope, checkpoints, and durable operation receipts."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hornet.coding import registry, session, context, goal_repair, agent, model
from hornet.coding.transport import SSHWorker
from hornet.coding.autonomous import ScopedWorker
from hornet.project import Project, HornetError
from test_operations import JournalWorker
from test_resume import call


class RegistryJournalWorker(JournalWorker):
    def call(self, name, arguments=None):
        if name == 'read_source':
            raw = self.worker.path(arguments['path']).read_bytes()
            return {'ok': True, 'path': arguments['path'], 'content_base64': base64.b64encode(raw).decode(),
                    'sha256': hashlib.sha256(raw).hexdigest()}
        return super().call(name, arguments)


class CellRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hornet-cell-registry-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        (root / 'controller').mkdir()
        (root / 'guest').mkdir()
        self.target = root / 'guest/calc.py'
        self.target.write_text('class Calc:\n    def twice(self, n):\n        return n\n', encoding='utf-8')
        (root / 'guest/other.py').write_text('untouched = True\n')
        self.backend = RegistryJournalWorker(root / 'guest')
        self.worker = ScopedWorker(self.backend, ['calc.py'])
        self.project = Project(root / 'controller')
        self.args = argparse.Namespace(task='Fix only Calc.twice to return n * 2, then verify', task_file=None,
            model='fixture', base_url='http://127.0.0.1:1234/v1', protocol='native', context_chars=20000,
            max_tokens=1024, timeout=20, max_turns=15, max_tools=20, max_repairs=0,
            worker_config='worker.json', run_dir='runs', tools=sorted(registry.NAMES))
        self.uri = 'languages.coding.python.method.fill'
        self.count = 0

    def run_dir(self):
        return next((self.project.root / 'runs').iterdir())

    def checkpoint(self):
        return json.loads((self.run_dir() / 'checkpoint.json').read_text(encoding='utf-8'))

    def model(self, *unused):
        self.count += 1
        if self.count == 1:
            return call('get_asset_details', {'uri': self.uri}, 'details'), {}
        if self.count == 2:
            return call('get_asset_details', {'uri': 'files.text.read'}, 'read-contract'), {}
        if self.count in (3, 6):
            return call('call_proxy_tool', {'uri': 'files.text.read', 'arguments': {'path': 'calc.py'}}, 'read-' + str(self.count)), {}
        if self.count == 4:
            return call('call_proxy_tool', {'uri': self.uri, 'arguments': {'path': 'calc.py', 'class_name': 'Calc',
                'method_name': 'twice', 'logic': 'return n * 2', 'commit': True,
                'expected_sha256': hashlib.sha256(self.target.read_bytes()).hexdigest()}}, 'edit'), {}
        return {'role': 'assistant', 'content': 'Done'}, {}

    def test_real_cell_edit_automatically_reads_back_and_retains_operation_provenance(self):
        result = session.work(self.project, self.args, self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.count, 5, 'Confirmed readback must not cost another model turn')
        self.assertEqual(self.backend.executions, ['write_file'])
        self.assertIn('return n * 2', self.target.read_text())
        self.assertEqual((self.target.parent / 'other.py').read_text(), 'untouched = True\n')
        job = session.load(self.project, self.run_dir())
        edit = next(e for e in job['history'] if e.get('call_id') == 'edit')
        self.assertEqual(edit['dispatched_tool'], 'write_file')
        self.assertEqual(edit['result']['uri'], self.uri)
        self.assertTrue((self.run_dir() / edit['result']['registry_receipt']['path']).is_file())
        ref = edit['result']['readback_receipt']
        retained = (self.run_dir() / ref['path']).read_bytes()
        self.assertEqual(hashlib.sha256(retained).hexdigest(), ref['sha256'])
        self.assertEqual(base64.b64decode(json.loads(retained)['content_base64']), self.target.read_bytes())
        self.assertEqual(edit['result']['readback_sha256'], hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertIn('return n * 2', edit['result']['readback_content'])
        self.assertEqual(registry.pending_readbacks(job), [])

    def test_readback_interruption_recovers_original_write_once(self):
        original = self.backend.call
        def interrupted(name, arguments=None):
            if name == 'read_source' and self.backend.executions:
                raise KeyboardInterrupt()
            return original(name, arguments)
        with patch.object(self.backend, 'call', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            session.work(self.project, self.args, self.worker, self.model)
        job = self.checkpoint()
        self.assertEqual(job['phase'], 'tool_in_flight')
        self.assertIn('prepared_registry', job)
        operation_id = job['in_flight_operation']['id']
        result = session.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.backend.executions, ['write_file'])
        job = session.load(self.project, self.run_dir())
        edit = next(e for e in job['history'] if e.get('call_id') == 'edit')
        self.assertEqual(edit['operation_id'], operation_id)
        self.assertTrue(edit['result']['readback'])

    def test_bad_readback_blocks_completion_and_keeps_recoverable_write(self):
        original = self.backend.call
        def bad_bytes(name, arguments=None):
            if name == 'read_source' and self.backend.executions:
                return {'ok': True, 'content_base64': base64.b64encode(b'wrong bytes').decode()}
            return original(name, arguments)
        with patch.object(self.backend, 'call', side_effect=bad_bytes):
            result = session.work(self.project, self.args, self.worker, self.model)
        self.assertFalse(result['ok'], result)
        self.assertIn('readback bytes', result['error'])
        self.assertEqual(self.checkpoint()['phase'], 'tool_in_flight')
        self.assertFalse((self.run_dir() / 'registry-readbacks').exists())
        recovered = session.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(recovered['ok'], recovered)
        self.assertEqual(self.backend.executions, ['write_file'])

    def test_completed_readback_receipt_survives_lost_controller_observation(self):
        original = registry.confirm_commit
        def interrupted(*args):
            original(*args)
            raise KeyboardInterrupt()
        with patch.object(registry, 'confirm_commit', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            session.work(self.project, self.args, self.worker, self.model)
        receipt = next((self.run_dir() / 'registry-readbacks').glob('*.json'))
        retained = receipt.read_bytes()
        self.assertIn('prepared_registry', self.checkpoint())
        result = session.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(receipt.read_bytes(), retained)
        self.assertEqual(self.backend.executions, ['write_file'])
        session.load(self.project, self.run_dir())

    def test_readback_refuses_changed_worker_snapshot(self):
        current = session.snapshot(self.worker)
        job = {'job_id': 'fixture', 'snapshot': current}
        prepared = {'operation': {'path': 'calc.py'}, 'result': {'sha256': current['files']['calc.py']}}
        for changed in (dict(current, binding={'other': True}),
                        dict(current, files=dict(current['files'], **{'calc.py': '0' * 64}))):
            with self.subTest(changed=changed), self.assertRaisesRegex(HornetError, 'changed during'):
                registry.confirm_commit(self.project.root, job, {'id': 'write'}, prepared,
                                        self.worker, current, lambda unused: changed)
        self.assertFalse((self.project.root / 'registry-readbacks').exists())

    def test_missing_or_tampered_readback_evidence_blocks_reload(self):
        self.assertTrue(session.work(self.project, self.args, self.worker, self.model)['ok'])
        original = self.checkpoint()
        entry = next(e for e in original['history'] if e.get('call_id') == 'edit')
        ref = entry['result'].pop('readback_receipt')
        checkpoint = self.run_dir() / 'checkpoint.json'
        checkpoint.write_text(json.dumps(original), encoding='utf-8')
        with self.assertRaises(HornetError):
            session.load(self.project, self.run_dir())
        entry['result']['readback_receipt'] = ref
        checkpoint.write_text(json.dumps(original), encoding='utf-8')
        path = self.run_dir() / ref['path']
        path.write_text('{}')
        with self.assertRaises(HornetError):
            session.load(self.project, self.run_dir())
        path.unlink()
        with self.assertRaises(HornetError):
            session.load(self.project, self.run_dir())

    def test_readback_preview_is_bounded_and_compaction_keeps_evidence(self):
        raw = ('long line ' * 50 + '\n') * 100
        ref = {'path': 'registry-readbacks/' + 'a' * 64 + '.json', 'sha256': 'b' * 64}
        result = registry._readback_result({'content_base64': base64.b64encode(raw.encode()).decode(),
            'sha256': hashlib.sha256(raw.encode()).hexdigest()}, ref)
        self.assertTrue(result['readback_truncated'])
        self.assertLessEqual(len(result['readback_content']), 6000)
        compact = json.loads(context.short_result({'role': 'tool', 'name': 'call_proxy_tool',
            'content': json.dumps(result)}, 1000)['content'])
        self.assertEqual(compact['readback_receipt'], ref)
        self.assertEqual(compact['readback_sha256'], result['readback_sha256'])
        self.assertNotIn('long line', json.dumps(compact))

    def test_lost_write_reply_recovers_without_duplicate_execution(self):
        def fault(stage):
            if stage == 'completed':
                raise KeyboardInterrupt()
        self.backend.fault = fault
        with self.assertRaises(KeyboardInterrupt):
            session.work(self.project, self.args, self.worker, self.model)
        self.assertEqual(self.checkpoint()['phase'], 'tool_in_flight')
        self.backend.fault = None
        result = session.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.backend.executions, ['write_file'])
        session.load(self.project, self.run_dir())

    def test_unknown_write_outcome_remains_blocked(self):
        def fault(stage):
            if stage == 'executed':
                raise KeyboardInterrupt()
        self.backend.fault = fault
        with self.assertRaises(KeyboardInterrupt):
            session.work(self.project, self.args, self.worker, self.model)
        self.backend.fault = None
        with self.assertRaisesRegex(HornetError, 'inspection is required'):
            session.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertEqual(self.backend.executions, ['write_file'])

    def test_prepared_read_recovers_with_receipt_and_resumes_again(self):
        save = session.save
        cut = [False]
        def interrupt(run, job):
            save(run, job)
            if job['phase'] == 'tool_in_flight' and not cut[0]:
                cut[0] = True
                raise KeyboardInterrupt()
        with patch.object(session, 'save', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            session.work(self.project, self.args, self.worker, self.model)
        def stop_again(*unused):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            session.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, stop_again)
        job = session.load(self.project, self.run_dir())
        self.assertTrue(any(e.get('registry_observation_recovered') == 'details' for e in job['history']))
        result = session.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        session.load(self.project, self.run_dir())

    def test_paths_and_assets_are_enforced_before_candidate_work(self):
        policy, snap = registry.binding(self.worker), session.snapshot(self.worker)
        for path in ('other.py', '../calc.py', '/tmp/calc.py', 'C:/calc.py', '.git/config'):
            with self.subTest(path=path), self.assertRaises((ValueError, HornetError)):
                registry.prepare(self.worker, 'call_proxy_tool', {'uri': 'files.text.read', 'arguments': {'path': path}},
                                 policy, snap, {'files.text.read'})
        self.assertFalse(self.worker.call('read_source', {'path': 'other.py'})['ok'])
        with self.assertRaises(ValueError):
            registry.prepare(self.worker, 'get_asset_details', {'uri': 'runtime.shell.execute'}, policy, snap, set())

    def test_read_and_preview_do_not_mutate_worker_files(self):
        policy, snap = registry.binding(self.worker), session.snapshot(self.worker)
        result, write = registry.prepare(self.worker, 'call_proxy_tool', {'uri': self.uri, 'arguments': {
            'path': 'calc.py', 'method_name': 'twice', 'logic': 'return n * 2', 'commit': False,
            'expected_sha256': snap['files']['calc.py']}}, policy, snap, {self.uri})
        self.assertEqual(result['status'], 'STAGED')
        self.assertIn('return n * 2', result['preview'])
        self.assertIsNone(write)
        self.assertEqual(session.snapshot(self.worker), snap)
        self.assertNotIn('shadow_path', json.dumps(result))

    def test_uninspected_tool_and_stale_source_are_refused(self):
        policy, snap = registry.binding(self.worker), session.snapshot(self.worker)
        args = {'uri': self.uri, 'arguments': {'path': 'calc.py', 'method_name': 'twice', 'logic': 'return n * 2', 'expected_sha256': '0'*64}}
        with self.assertRaisesRegex(ValueError, 'Inspect'):
            registry.prepare(self.worker, 'call_proxy_tool', args, policy, snap, set())
        with self.assertRaisesRegex(ValueError, 'Read the current'):
            registry.prepare(self.worker, 'call_proxy_tool', args, policy, snap, {self.uri})

    def test_cell_asset_subset_and_implementation_binding(self):
        self.worker.registry_assets = ['files.text.read']
        policy = registry.binding(self.worker)
        result, _ = registry.prepare(self.worker, 'find_asset', {'query': ''}, policy, session.snapshot(self.worker), set())
        self.assertEqual([a['uri'] for a in result['assets']], ['files.text.read'])
        changed = dict(policy, implementation_sha256='0'*64)
        with self.assertRaises(HornetError):
            registry.check_binding(changed, self.worker)

    def test_empty_discovery_does_not_offer_unrelated_editors_as_matches(self):
        self.worker.registry_assets = ['files.text.read', self.uri]
        policy, snap = registry.binding(self.worker), session.snapshot(self.worker)
        result, operation = registry.prepare(self.worker, 'find_asset', {'query': 'Calculator'}, policy, snap, set())
        self.assertEqual(result['assets'], [])
        self.assertIn('operations', result['hint'])
        self.assertNotIn('suggestions', result)
        self.assertIn('No matching capability', result['hint'])
        self.assertIsNone(operation)
        limited, _ = registry.prepare(self.worker, 'find_asset', {'query': 'Calculator', 'limit': 1}, policy, snap, set())
        self.assertEqual(limited['assets'], [])
        self.assertNotIn('suggestions', limited)
        self.assertEqual(session.snapshot(self.worker), snap)

    def test_registry_only_hides_bypass_tools_and_rejects_unoffered_calls(self):
        self.args.registry_only = True
        self.args.tools = sorted(registry.NAMES | session.REGISTRY_BYPASSES | {'read_file'})
        config = session.settings(self.args)
        self.assertFalse(set(config['tools']) & session.REGISTRY_BYPASSES)
        self.assertTrue(registry.NAMES <= set(config['tools']))
        for tool in session.REGISTRY_BYPASSES:
            with self.subTest(tool=tool), self.assertRaises(HornetError):
                session.validate_settings(dict(config, tools=config['tools'] + [tool]))
        seen = []
        def bypass(messages, tools, *unused):
            seen.extend(t['function']['name'] for t in tools)
            return call('write_file', {'path': 'calc.py', 'content': 'wrong = True', 'expected_sha256': ''}, 'bypass'), {}
        before = self.target.read_bytes()
        result = session.work(self.project, self.args, self.worker, bypass)
        self.assertFalse(result['ok'], result)
        self.assertIn('not offered', result['error'])
        self.assertNotIn('write_file', seen)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.backend.executions, [])
        session.load(self.project, self.run_dir())

    def test_registry_only_preserves_internal_journaled_write(self):
        self.args.registry_only = True
        result = session.work(self.project, self.args, self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.backend.executions, ['write_file'])
        job = session.load(self.project, self.run_dir())
        self.assertTrue(job['settings']['registry_only'])
        self.assertNotIn('write_file', job['settings']['tools'])

    def test_strict_prompt_does_not_recommend_unavailable_edits(self):
        self.assertIn('Prefer edit_file', agent.system_prompt())
        strict = agent.system_prompt(True)
        self.assertNotIn('Prefer edit_file', strict)
        self.assertNotIn('read_ref', strict)
        self.assertIn('operation\'s exact arguments', strict)
        self.assertIn('cannot create a missing function', strict)
        self.assertIn('insert/create operation', strict)
        self.assertIn('exact requested name', strict)

    def test_execution_menu_requires_inspection_and_exact_contract_arguments(self):
        job = {'job_id': 'menu', 'snapshot': session.snapshot(self.worker),
               'registry_binding': registry.binding(self.worker), 'settings': {'registry_only': True}, 'history': []}
        selected = [t for t in agent.TOOLS if t['function']['name'] in registry.NAMES | {'read_file'}]
        original = json.dumps(selected, sort_keys=True)
        offered = registry.offered_tools(selected, job)
        self.assertNotIn('call_proxy_tool', [t['function']['name'] for t in offered])
        job['history'].append({'tool': 'get_asset_details', 'result': {'ok': True, 'asset': {'uri': self.uri}}})
        self.assertNotIn('call_proxy_tool', [t['function']['name'] for t in registry.offered_tools(selected, job)])
        job['history'].append({'tool': 'call_proxy_tool', 'result': {'ok': True, 'path': 'calc.py',
                               'readback': True, 'source_sha256': job['snapshot']['files']['calc.py']}})
        offered = registry.offered_tools(selected, job)
        schema = next(t['function']['parameters'] for t in offered if t['function']['name'] == 'call_proxy_tool')
        args = {'uri': self.uri, 'arguments': {'path': 'calc.py', 'class_name': 'Calc',
                'method_name': 'twice', 'logic': 'return n * 2', 'commit': True,
                'expected_sha256': hashlib.sha256(self.target.read_bytes()).hexdigest()}}
        model.validate_arguments(args, schema)
        for invalid in (dict(args, uri='self.python.edit_file'),
                        dict(args, arguments=dict(args['arguments'], replacement='wrong')),
                        dict(args, arguments='{}')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                model.validate_arguments(invalid, schema)
        self.assertEqual(json.dumps(selected, sort_keys=True), original)
        self.assertIn('read_ref', next(t['function']['description'] for t in offered if t['function']['name'] == 'read_file'))
        by_ref = dict(args, arguments=dict(args['arguments'], read_ref='r_observed'))
        with self.assertRaises(ValueError):
            model.validate_arguments(by_ref, schema)
        del by_ref['arguments']['expected_sha256']
        with self.assertRaises(ValueError):
            model.validate_arguments(by_ref, schema)

    def test_execution_menu_is_bounded_rebuildable_and_ignores_failed_inspections(self):
        job = {'job_id': 'menu', 'snapshot': session.snapshot(self.worker),
               'registry_binding': registry.binding(self.worker), 'settings': {}, 'history': [
                   {'tool': 'call_proxy_tool', 'result': {'ok': True, 'path': 'calc.py', 'readback': True,
                    'source_sha256': hashlib.sha256(self.target.read_bytes()).hexdigest()}}]}
        def inspect(uri, ok=True):
            job['history'].append({'tool': 'get_asset_details', 'result': {'ok': ok, 'asset': {'uri': uri}}})
        def uris(value):
            offered = registry.offered_tools(registry.TOOLS, value)
            schema = next(t['function']['parameters'] for t in offered if t['function']['name'] == 'call_proxy_tool')
            return [item['properties']['uri']['enum'][0] for item in schema['anyOf']]
        inspect(self.uri)
        inspect('files.text.read')
        inspect('files.text.atomic_edit')
        inspect('recipe.atomic_commit')
        inspect('invented.tool', False)
        self.assertEqual(uris(job), ['files.text.atomic_edit', 'files.text.read'])
        inspect(self.uri)
        self.assertEqual(uris(json.loads(json.dumps(job))), [self.uri, 'files.text.atomic_edit'])
        self.assertEqual(self.backend.executions, [])

    def test_live_session_rebuilds_menu_from_observed_inspections(self):
        self.args.registry_only = True
        menus = []
        def capture(messages, offered, *rest):
            menus.append([t['function']['name'] for t in offered])
            self.assertNotIn('Prefer edit_file', messages[0]['content'])
            return self.model(messages, offered, *rest)
        result = session.work(self.project, self.args, self.worker, capture)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('call_proxy_tool', menus[0])
        self.assertNotIn('call_proxy_tool', menus[1])
        self.assertIn('call_proxy_tool', menus[2])

    def test_execution_menu_versions_follow_reads_and_writes(self):
        from hornet.coding import read_refs
        job = {'job_id': 'menu', 'snapshot': session.snapshot(self.worker),
               'registry_binding': registry.binding(self.worker), 'settings': {}, 'history': []}
        asset = registry.details(registry.manager(), self.uri)
        self.assertIsNone(registry.execution_schema(asset, job))
        digest = job['snapshot']['files']['calc.py']
        ref = read_refs.reference(job, 'read', 'calc.py', digest)
        job['history'].append({'tool': 'read_file', 'call_id': 'read', 'arguments': {'path': 'calc.py'},
                               'result': {'ok': True, 'path': 'calc.py', 'sha256': digest, 'read_ref': ref}})
        schema = registry.execution_schema(asset, job)
        args = {'path': 'calc.py', 'method_name': 'twice', 'logic': 'return n * 2', 'read_ref': ref}
        model.validate_arguments(args, schema)
        for invalid in (dict(args, read_ref='r_stale'), dict(args, path='other.py'),
                        dict(args, expected_sha256=digest)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                model.validate_arguments(invalid, schema)
        self.assertEqual(schema, registry.execution_schema(asset, json.loads(json.dumps(job))))
        job['snapshot']['files']['calc.py'] = '0' * 64
        self.assertIsNone(registry.execution_schema(asset, job))
        # A different current version is never substituted for the observed one.
        self.assertEqual(read_refs.resolve(job, args)['expected_sha256'], digest)
        job['history'].append({'tool': 'call_proxy_tool', 'result': {
            'ok': True, 'path': 'calc.py', 'readback': True, 'source_sha256': digest,
            'readback_sha256': '0' * 64}})
        current_schema = registry.execution_schema(asset, job)
        current_args = dict(args, expected_sha256='0' * 64)
        del current_args['read_ref']
        model.validate_arguments(current_args, current_schema)
        with self.assertRaises(ValueError):
            model.validate_arguments(args, current_schema)
        self.assertEqual(read_refs.resolve(job, args)['expected_sha256'], digest)

    def test_execution_menu_new_files_require_empty_version_and_exact_scope(self):
        job = {'job_id': 'new', 'snapshot': session.snapshot(self.worker),
               'registry_binding': registry.binding(self.worker), 'settings': {}, 'history': []}
        del job['snapshot']['files']['calc.py']
        asset = registry.details(registry.manager(), 'files.text.atomic_edit')
        schema = registry.execution_schema(asset, job)
        choice = schema['anyOf'][0]
        self.assertEqual(choice['properties']['path']['enum'], ['calc.py'])
        self.assertEqual(choice['properties']['expected_sha256']['enum'], [''])
        self.assertNotIn('read_ref', choice['properties'])

    def test_registry_read_reference_survives_confirmed_write_interruption(self):
        self.args.registry_only = True
        self.args.tools = sorted(registry.NAMES | {'read_file'})
        def request(*unused):
            self.count += 1
            if self.count in (1, 4):
                return call('read_file', {'path': 'calc.py'}, 'read-' + str(self.count)), {}
            if self.count == 2:
                return call('get_asset_details', {'uri': self.uri}, 'details'), {}
            if self.count == 3:
                job = self.checkpoint()
                read = next(e for e in job['history'] if e.get('tool') == 'read_file')
                return call('call_proxy_tool', {'uri': self.uri, 'arguments': {
                    'path': 'calc.py', 'method_name': 'twice', 'logic': 'return n * 2',
                    'read_ref': read['result']['read_ref'], 'commit': True}}, 'ref-edit'), {}
            return {'role': 'assistant', 'content': 'Done'}, {}
        def fault(stage):
            if stage == 'completed':
                raise KeyboardInterrupt()
        self.backend.fault = fault
        with self.assertRaises(KeyboardInterrupt):
            session.work(self.project, self.args, self.worker, request)
        self.backend.fault = None
        result = session.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, request)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.backend.executions, ['write_file'])
        job = session.load(self.project, self.run_dir())
        edit = next(e for e in job['history'] if e.get('call_id') == 'ref-edit')
        self.assertIn('read_ref', edit['arguments']['arguments'])
        self.assertNotIn('read_ref', edit['dispatched_arguments'])
        self.assertEqual(len(edit['dispatched_arguments']['expected_sha256']), 64)

    def test_registry_read_reference_rejects_wrong_file_unknown_and_stale_versions(self):
        from hornet.coding import read_refs
        policy, snap = registry.binding(self.worker), session.snapshot(self.worker)
        job = {'job_id': 'fixture', 'snapshot': snap, 'history': []}
        observed = snap['files']['calc.py']
        ref = read_refs.reference(job, 'read', 'calc.py', observed)
        job['history'].append({'tool': 'read_file', 'call_id': 'read', 'arguments': {'path': 'calc.py'},
                               'result': {'ok': True, 'path': 'calc.py', 'sha256': observed, 'read_ref': ref}})
        args = {'path': 'calc.py', 'method_name': 'twice', 'logic': 'return n * 2', 'read_ref': ref}
        for invalid in (dict(args, read_ref='r_missing'), dict(args, path='other.py'),
                        dict(args, expected_sha256=observed)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                registry.prepare(self.worker, 'call_proxy_tool', {'uri': self.uri, 'arguments': invalid},
                                 policy, snap, {self.uri}, job=job)
        self.target.write_text('class Calc:\n    def twice(self, n):\n        return n + 1\n', encoding='utf-8')
        changed = session.snapshot(self.worker)
        with self.assertRaisesRegex(ValueError, 'Read the current file again'):
            registry.prepare(self.worker, 'call_proxy_tool', {'uri': self.uri, 'arguments': args},
                             policy, changed, {self.uri}, job=job)
        self.assertEqual(self.backend.executions, [])

    def test_repeated_discovery_stops_for_bounded_recovery(self):
        self.worker.detect_stagnation = True
        def repeated(*unused):
            self.count += 1
            return call('find_asset', {'query': 'Calculator'}, str(self.count)), {}
        result = session.work(self.project, self.args, self.worker, repeated)
        self.assertEqual(result['status'], 'stagnated', result)
        self.assertEqual(self.count, 3)
        self.assertEqual(len([e for e in self.checkpoint()['history'] if e.get('tool') == 'find_asset']), 3)
        self.assertEqual(self.backend.executions, [])

    def test_goal_repair_refuses_lost_registry_requirement(self):
        with patch.object(goal_repair, 'job_path', return_value=self.project.root), \
                patch.object(session, 'load', return_value={'settings': {}}), \
                self.assertRaisesRegex(HornetError, 'registry-only'):
            goal_repair.checked_job(self.project, self.project.root, {'registry_only': True})

    def test_registry_receipt_tampering_blocks_resume(self):
        def stop(*args):
            if self.count == 2:
                raise KeyboardInterrupt()
            return self.model(*args)
        with self.assertRaises(KeyboardInterrupt):
            session.work(self.project, self.args, self.worker, stop)
        receipt = next((self.run_dir() / 'registry').glob('*.json'))
        receipt.write_text('{}')
        with self.assertRaises(HornetError):
            session.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)

    def test_identical_failures_stop_at_three(self):
        def fail(*unused):
            self.count += 1
            return call('get_asset_details', {'uri': 'invented.asset'}, str(self.count)), {}
        result = session.work(self.project, self.args, self.worker, fail)
        self.assertFalse(result['ok'])
        self.assertEqual(self.count, 3)
        self.assertEqual(len(list((self.run_dir() / 'registry').glob('*.json'))), 3)

    def test_asset_selection_survives_settings_validation(self):
        self.args.registry_assets = ['files.text.read', self.uri]
        self.assertEqual(session.settings(self.args)['registry_assets'], sorted(self.args.registry_assets))
        self.args.registry_assets = ['runtime.shell.execute']
        with self.assertRaises(HornetError):
            session.settings(self.args)

    def test_failure_identity_normalizes_nested_json_and_changes_with_source(self):
        snap = session.snapshot(self.worker)
        args = {'uri': self.uri, 'arguments': {'path': 'calc.py', 'logic': 'return 2'}}
        encoded = dict(args, arguments=json.dumps(args['arguments']))
        self.assertEqual(registry.failure_signature('call_proxy_tool', args, snap),
                         registry.failure_signature('call_proxy_tool', encoded, snap))
        changed = dict(snap, files=dict(snap['files'], **{'calc.py': '0'*64}))
        self.assertNotEqual(registry.failure_signature('call_proxy_tool', args, snap),
                            registry.failure_signature('call_proxy_tool', args, changed))

    def test_failure_identity_resolves_equivalent_reads_without_advancing_stale_ones(self):
        from hornet.coding import read_refs
        snap = session.snapshot(self.worker)
        job = {'job_id': 'fixture', 'snapshot': snap, 'history': []}
        observed = snap['files']['calc.py']
        refs = []
        for call_id, sha in (('read-a', observed), ('read-b', observed), ('old-read', '0' * 64)):
            ref = read_refs.reference(job, call_id, 'calc.py', sha)
            refs.append(ref)
            job['history'].append({'tool': 'read_file', 'call_id': call_id, 'arguments': {'path': 'calc.py'},
                                   'result': {'ok': True, 'path': 'calc.py', 'sha256': sha, 'read_ref': ref}})
        inner = {'path': 'calc.py', 'method_name': 'twice', 'logic': 'x = n\n    return x'}
        def signature(args, source=snap):
            return registry.failure_signature('call_proxy_tool', {'uri': self.uri, 'arguments': args}, source, job)
        same = signature(dict(inner, read_ref=refs[0]))
        self.assertEqual(same, signature(dict(inner, read_ref=refs[1])))
        self.assertEqual(same, signature(dict(inner, expected_sha256=observed)))
        self.assertNotEqual(same, signature(dict(inner, read_ref=refs[2])))
        self.assertNotEqual(same, signature(dict(inner, read_ref='unknown')))
        self.assertNotEqual(same, signature(dict(inner, read_ref=refs[0], expected_sha256=observed)))
        self.assertNotEqual(same, signature(dict(inner, read_ref=refs[0], logic='return n')))
        changed = dict(snap, files=dict(snap['files'], **{'calc.py': '1' * 64}))
        self.assertNotEqual(same, signature(dict(inner, read_ref=refs[0]), changed))

    def test_fresh_read_handles_cannot_reset_repeated_body_failure_budget(self):
        self.args.tools = sorted(registry.NAMES | {'read_file'})
        def request(*unused):
            self.count += 1
            if self.count == 1:
                return call('get_asset_details', {'uri': self.uri}, 'inspect'), {}
            if self.count % 2 == 0:
                return call('read_file', {'path': 'calc.py'}, 'read-' + str(self.count)), {}
            job = self.checkpoint()
            read = [e for e in job['history'] if e.get('tool') == 'read_file'][-1]
            return call('call_proxy_tool', {'uri': self.uri, 'arguments': {
                'path': 'calc.py', 'method_name': 'twice', 'read_ref': read['result']['read_ref'],
                'logic': 'x = n\n        return x', 'commit': True}}, 'bad-' + str(self.count)), {}
        before = self.target.read_bytes()
        result = session.work(self.project, self.args, self.worker, request)
        self.assertFalse(result['ok'], result)
        self.assertEqual(self.count, 7)
        job = session.load(self.project, self.run_dir())
        failed = [e for e in job['history'] if e.get('tool') == 'call_proxy_tool']
        self.assertEqual(len(failed), 3)
        self.assertTrue(all('line 2' in e['result']['error'] for e in failed))
        self.assertEqual(self.backend.executions, [])
        self.assertEqual(self.target.read_bytes(), before)

    def test_preparation_refuses_source_changed_after_snapshot(self):
        snap, policy = session.snapshot(self.worker), registry.binding(self.worker)
        self.target.write_text('other = True\n')
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            registry.prepare(self.worker, 'call_proxy_tool', {'uri': 'files.text.read',
                'arguments': {'path': 'calc.py'}}, policy, snap, {'files.text.read'})

    def test_preparation_new_file_is_hash_guarded(self):
        self.worker.allowed.add('new.py')
        uri = 'languages.coding.python.interface.create'
        result, op = registry.prepare(self.worker, 'call_proxy_tool', {'uri': uri, 'arguments': {
            'path': 'new.py', 'class_name': 'Demo', 'methods': [], 'commit': True}},
            registry.binding(self.worker), session.snapshot(self.worker), {uri})
        self.assertEqual(op['expected_sha256'], '')
        self.assertIn('class Demo', op['content'])
        self.assertTrue(result['changed'])
        self.assertFalse((self.target.parent / 'new.py').exists())

    def test_altered_outer_registry_result_or_call_cannot_resume(self):
        self.assertTrue(session.work(self.project, self.args, self.worker, self.model)['ok'])
        original = self.checkpoint()
        for field, value in [('result', {'ok': True, 'status': 'STAGED'}),
                             ('arguments', {'uri': 'invented.asset'}), ('dispatched_tool', 'edit_file')]:
            job = json.loads(json.dumps(original))
            entry = next(e for e in job['history'] if e.get('call_id') == 'edit')
            if field == 'result':
                entry[field].update(value)
            else:
                entry[field] = value
            (self.run_dir() / 'checkpoint.json').write_text(json.dumps(job), encoding='utf-8')
            with self.subTest(field=field), self.assertRaises(HornetError):
                session.load(self.project, self.run_dir())

    def test_compacted_registry_result_retains_receipt_without_partial_source(self):
        value = {'ok': True, 'uri': self.uri, 'path': 'calc.py', 'status': 'STAGED',
                 'registry_receipt': {'path': 'registry/'+'a'*64+'.json', 'sha256': 'b'*64},
                 'preview': 'PRIVATE_SOURCE\n' * 500}
        original = {'role': 'tool', 'name': 'call_proxy_tool', 'content': json.dumps(value)}
        compact = json.loads(context.short_result(original, 1000)['content'])
        self.assertEqual(compact['uri'], self.uri)
        self.assertEqual(compact['registry_receipt'], value['registry_receipt'])
        self.assertNotIn('PRIVATE_SOURCE', json.dumps(compact))


class RegistryTransportTests(unittest.TestCase):
    def worker(self, content, change=False):
        worker = object.__new__(SSHWorker)
        def rpc(name, arguments):
            self.assertEqual(name, 'run_command')
            request = json.loads(arguments['argv'][-1])
            offset = request['offset']
            digest = hashlib.sha256(content).hexdigest()
            return {'ok': True, 'output': json.dumps({'ok': True, 'bytes': len(content),
                'sha256': '0'*64 if change and offset else digest,
                'chunk': base64.b64encode(content[offset:offset+12000]).decode()})}
        worker._rpc = rpc
        return worker

    def test_bounded_chunks_preserve_exact_utf8_bytes(self):
        for content in (b'', ('hello \u2603\r\n'*4000).encode()):
            result = self.worker(content).call('read_source', {'path': 'file.py'})
            self.assertEqual(base64.b64decode(result['content_base64']), content)

    def test_chunk_source_change_and_escaping_path_refused(self):
        with self.assertRaisesRegex(HornetError, 'changed'):
            self.worker(b'x'*24001, True).call('read_source', {'path': 'file.py'})
        with self.assertRaises(HornetError):
            self.worker(b'').call('read_source', {'path': '../outside.py'})


if __name__ == '__main__':
    unittest.main()
