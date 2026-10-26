"""Scoped dependency declarations survive handoff without accumulating history."""
import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hornet.coding import acceptance, autonomous, dependency_context, mission
from hornet.project import HornetError
import test_mission as fixtures
from test_resume import LocalWorker


class DependencyContextTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='hornet-dependency-context-')
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name)
        self.nodes = [dict(id=name, task='Implement ' + name, files=[name + '.py'], depends_on=deps,
                           result=dict(path=name + '.py', function='value', args_json='[0]',
                                       kwargs_json='{}', schema_json='{"type":"integer"}'))
                      for name, deps in [('base', []), ('validator', ['base']),
                                         ('strategy', ['validator']), ('future', ['strategy'])]]
        self.state = {'schema_version': 3, 'goal': 'Base behavior.\nReturn nonnegative weight unchanged, including zero.\nCompute the price.\nFUTURE_ONLY\nShared rule.',
                      'goal_sha256': 'goal',
                      'plan': {'cells': self.nodes, 'coverage': [
                          {'requirement_id': 'r%03d' % (i + 1), 'cell_ids': [node['id']]}
                          for i, node in enumerate(self.nodes)] + [
                          {'requirement_id': 'r005', 'cell_ids': ['validator', 'strategy']}]},
                      'cells': {node['id']: {'initial_snapshot': {},
                                            'return_manifest': {'path': node['id'] + '/return.json', 'sha256': 'receipt'},
                                            'work_packet': {'path': node['id'] + '/work.json', 'sha256': 'packet'},
                                            'attempt_history': 'DO_NOT_FORWARD_HISTORY'} for node in self.nodes},
                      'values': {'base': 3, 'validator': 0}}
        # Version-one runs preserve their already frozen behavior-only packets.
        mission.immutable(self.run / dependency_context.FILENAME, dependency_context.LEGACY_POLICY)
        self.state['dependency_context_policy'] = mission.reference(self.run, self.run / dependency_context.FILENAME)

    def packet(self):
        return mission.packet(self.state, self.nodes[2])

    def test_direct_behavior_and_call_inputs_are_visible_but_not_future_or_transitive(self):
        packet = self.packet()
        context = packet['dependency_context']
        self.assertEqual(set(context['providers']), {'validator'})
        self.assertEqual(context['requirements'], [{'id': 'r002', 'text': 'Return nonnegative weight unchanged, including zero.'}])
        provider = context['providers']['validator']
        self.assertEqual(provider['result_call']['args_json'], '[0]')
        self.assertNotIn('schema_json', provider['result_call'])
        self.assertEqual(provider['contract_sha256'], acceptance.digest(self.nodes[1]))
        self.assertEqual(provider['work_packet'], self.state['cells']['validator']['work_packet'])
        self.assertEqual(packet['dependencies']['validator']['value'], 0)
        encoded = acceptance.encoded(packet)
        for absent in ('Base behavior.', 'FUTURE_ONLY', 'DO_NOT_FORWARD_HISTORY'):
            self.assertNotIn(absent, encoded)

    def test_shared_requirements_are_verbatim_once_across_all_providers(self):
        self.nodes[2]['depends_on'].append('base')
        self.state['plan']['coverage'][1]['cell_ids'].append('base')
        packet = self.packet()
        self.assertEqual(acceptance.encoded(packet).count('Shared rule.'), 1)
        self.assertEqual(acceptance.encoded(packet).count('Return nonnegative weight unchanged'), 1)
        self.assertEqual([row['id'] for row in packet['dependency_context']['requirements']], ['r001', 'r002'])

    def test_context_cannot_change_file_permissions_or_mutate_provider_state(self):
        packet = self.packet()
        packet['permissions'] = {'read_files': ['strategy.py'], 'write_files': ['strategy.py']}
        before = copy.deepcopy(self.state)
        dependency_context.extend(self.state, packet)
        self.assertEqual(packet['permissions'], {'read_files': ['strategy.py'], 'write_files': ['strategy.py']})
        packet['dependency_context']['providers']['validator']['files'].append('unrelated.py')
        packet['dependency_context']['requirements'][0]['text'] = 'Mutated'
        packet['dependency_context']['policy']['sha256'] = 'Mutated'
        self.assertEqual(self.state, before)

    def test_no_dependencies_adds_no_context(self):
        self.assertNotIn('dependency_context', mission.packet(self.state, self.nodes[0]))

    def test_legacy_packets_remain_unchanged(self):
        del self.state['dependency_context_policy']
        (self.run / dependency_context.FILENAME).unlink()
        dependency_context.validate(self.run, self.state)
        self.assertNotIn('dependency_context', self.packet())

    def test_policy_binding_loss_missing_artifact_and_changed_content_are_rejected(self):
        old = copy.deepcopy(self.state)
        del self.state['dependency_context_policy']
        with self.assertRaisesRegex(HornetError, 'lost its policy'):
            dependency_context.validate(self.run, self.state)
        self.state = old
        path = self.run / dependency_context.FILENAME
        path.unlink()
        with self.assertRaisesRegex(HornetError, 'policy changed'):
            dependency_context.validate(self.run, self.state)
        path.write_text('{}')
        self.state['dependency_context_policy'] = mission.reference(self.run, path)
        with self.assertRaisesRegex(HornetError, 'policy changed'):
            dependency_context.validate(self.run, self.state)

    def test_incomplete_provider_is_refused(self):
        packet = self.packet()
        del self.state['values']['validator']
        with self.assertRaisesRegex(HornetError, 'completed provider'):
            dependency_context.extend(self.state, packet)

    def test_parent_input_and_direct_input_share_one_provider(self):
        packet = self.packet()
        packet['parent_inputs'] = copy.deepcopy(packet['dependencies'])
        dependency_context.extend(self.state, packet)
        self.assertEqual(list(packet['dependency_context']['providers']), ['validator'])
        packet['dependencies'] = {}
        dependency_context.extend(self.state, packet)
        self.assertEqual(list(packet['dependency_context']['providers']), ['validator'])

    def test_nested_provider_uses_its_assigned_subset(self):
        self.state['schema_version'] = 5
        child = dict(self.nodes[1], id='child-validator', depends_on=[])
        sibling = dict(child, id='child-shared', depends_on=['child-validator'])
        self.state['routes'] = {'validator': {'plan': {'cells': [child, sibling], 'coverage': [
            {'requirement_id': 'r002', 'cell_ids': ['child-validator']},
            {'requirement_id': 'r005', 'cell_ids': ['child-shared']}]}}}
        self.state['cells'][child['id']] = copy.deepcopy(self.state['cells']['validator'])
        self.state['values'][child['id']] = 0
        packet = {'dependencies': {child['id']: {'value': 0}}, 'requirements': []}
        dependency_context.extend(self.state, packet)
        self.assertEqual(packet['dependency_context']['providers'][child['id']]['requirement_ids'], ['r002'])
        self.assertEqual([r['id'] for r in packet['dependency_context']['requirements']], ['r002'])

    def test_research_provider_has_no_executable_result_call(self):
        self.nodes[1]['kind'] = 'research'
        self.nodes[1]['result'] = {'schema_json': '{"type":"string"}'}
        self.state['values']['validator'] = 'Cited findings'
        packet = self.packet()
        self.assertEqual(packet['dependency_context']['providers']['validator']['result_call'], {})
        self.assertEqual(packet['dependencies']['validator']['value'], 'Cited findings')


class DependencyInterfaceTests(unittest.TestCase):
    def setUp(self):
        DependencyContextTests.setUp(self)
        (self.run / dependency_context.FILENAME).unlink()
        dependency_context.initialize(self.run, self.state)
        self.guest = self.run / 'workspace'
        self.guest.mkdir()
        for node in self.nodes:
            (self.guest / node['files'][0]).write_text('def value(n):\n    return n\n')
        (self.guest / 'validator.py').write_text(
            'raise RuntimeError("NEVER_EXECUTE")\nclass Rate:\n'
            '    def quote(self, grams):\n        return "PRIVATE_BODY"\n')
        self.worker = LocalWorker(self.guest)
        snapshot = self.worker.call('snapshot')
        snapshot.pop('ok')
        for node in self.nodes:
            self.state['cells'][node['id']]['initial_snapshot'] = copy.deepcopy(snapshot)
        self.state['cells']['future'].pop('work_packet')
        dependency_context.prepare(self.run, self.state, self.nodes[1], self.worker)

    def prepare(self):
        dependency_context.prepare(self.run, self.state, self.nodes[2], self.worker)
        return self.state['cells']['strategy']['dependency_interfaces']

    def test_source_verified_instance_signatures_without_bodies_or_transitive_source(self):
        entry = self.prepare()
        facts = entry['facts']
        self.assertEqual([row['path'] for row in facts['files']], ['validator.py'])
        declared = facts['files'][0]['declarations'][0]
        self.assertEqual(declared['name'], 'Rate.quote')
        self.assertEqual(declared['kind'], 'instance_method')
        self.assertEqual([p['name'] for p in declared['parameters']], ['self', 'grams'])
        packet = mission.packet(self.state, self.nodes[2])
        encoded = acceptance.encoded(packet)
        self.assertNotIn('PRIVATE_BODY', encoded)
        self.assertNotIn('NEVER_EXECUTE', encoded)
        self.assertIn('Instance methods normally need an instance', encoded)
        self.assertEqual(packet['contract']['files'], ['strategy.py'])
        dependency_context.validate(self.run, self.state)

    def test_repeated_prepare_uses_frozen_artifact_without_rereading(self):
        entry = copy.deepcopy(self.prepare())
        count = self.worker.calls.count('read_source')
        (self.guest / 'strategy.py').write_text('def value(n):\n    return n + 1\n')
        self.assertEqual(self.prepare(), entry)
        self.assertEqual(self.worker.calls.count('read_source'), count)

    def test_research_providers_and_consumer_owned_files_add_no_outline(self):
        for mode in ('research_provider', 'shared_file', 'research_consumer'):
            with self.subTest(mode=mode):
                node = copy.deepcopy(self.nodes[2])
                provider = copy.deepcopy(self.nodes[1])
                if mode == 'research_provider':
                    self.nodes[1]['kind'] = 'research'
                elif mode == 'shared_file':
                    node['files'] = ['validator.py']
                else:
                    node['kind'] = 'research'
                before = self.worker.calls[:]
                dependency_context.prepare(self.run, self.state, node, self.worker)
                self.assertEqual(self.worker.calls, before)
                packet = mission.packet(self.state, node)
                self.assertNotIn('interfaces', packet['dependency_context'])
                self.assertNotIn('interface_scope', packet['dependency_context'])
                self.assertIn('Return nonnegative weight unchanged', acceptance.encoded(packet))
                self.assertFalse(dependency_context.interface_path(self.run, node).exists())
                self.nodes[1].clear()
                self.nodes[1].update(provider)

    def test_mixed_inputs_capture_only_external_code_files(self):
        self.nodes[2]['depends_on'] = ['base', 'validator']
        self.nodes[0]['kind'] = 'research'
        self.nodes[1]['files'].append('strategy.py')
        entry = self.prepare()
        self.assertEqual([row['path'] for row in entry['facts']['files']], ['validator.py'])
        self.assertEqual(entry['facts']['omitted_files'], 0)
        packet = mission.packet(self.state, self.nodes[2])
        self.assertEqual(set(packet['dependency_context']['providers']), {'base', 'validator'})

    def test_malformed_api_records_fail_with_domain_errors(self):
        entry = copy.deepcopy(self.prepare())
        path = dependency_context.interface_path(self.run, self.nodes[2])
        original = path.read_bytes()
        self.state['cells']['strategy']['dependency_interfaces'] = None
        with self.assertRaisesRegex(HornetError, 'evidence changed'):
            dependency_context.validate_interfaces(self.run, self.state, self.nodes[2])
        for facts in (None, [], {'version': 1, 'files': [None], 'omitted_files': 0},
                      {'version': 1, 'files': [{}], 'omitted_files': 0}):
            with self.subTest(facts=facts):
                payload = json.loads(original)
                payload['facts'] = facts
                path.write_text(acceptance.encoded(payload))
                self.state['cells']['strategy']['dependency_interfaces'] = dict(
                    entry, reference=mission.reference(self.run, path), facts=facts)
                with self.assertRaisesRegex(HornetError, 'source scope'):
                    dependency_context.validate_interfaces(self.run, self.state, self.nodes[2])

    def test_publication_gap_adopts_same_evidence_without_new_source_read(self):
        original = mission.immutable
        def interrupted(path, payload):
            original(path, payload)
            if path.name == 'dependency-interfaces.json':
                raise KeyboardInterrupt()
        with patch.object(mission, 'immutable', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.prepare()
        path = dependency_context.interface_path(self.run, self.nodes[2])
        before, count = path.read_bytes(), self.worker.calls.count('read_source')
        self.prepare()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.worker.calls.count('read_source'), count)

    def test_publication_gap_cannot_be_adopted_for_changed_provider(self):
        self.prepare()
        del self.state['cells']['strategy']['dependency_interfaces']
        self.nodes[1]['task'] = 'Different provider contract'
        with self.assertRaisesRegex(HornetError, 'lost its binding'):
            self.prepare()

    def test_source_changes_before_or_during_capture_prevent_publication(self):
        before = (self.guest / 'validator.py').read_bytes()
        (self.guest / 'validator.py').write_text('changed = True\n')
        with self.assertRaisesRegex(HornetError, 'before dependency API capture'):
            self.prepare()
        (self.guest / 'validator.py').write_bytes(before)
        original = self.worker.call
        def changed(name, arguments=None):
            reply = original(name, arguments)
            if name == 'read_source':
                (self.guest / 'strategy.py').write_text('changed = True\n')
            return reply
        self.worker.call = changed
        with self.assertRaisesRegex(HornetError, 'during dependency API capture'):
            self.prepare()
        self.assertFalse(dependency_context.interface_path(self.run, self.nodes[2]).exists())

    def test_changed_artifact_and_rebound_wrong_source_are_refused(self):
        entry = self.prepare()
        path = dependency_context.interface_path(self.run, self.nodes[2])
        original = path.read_bytes()
        path.write_text('{}')
        with self.assertRaisesRegex(HornetError, 'evidence changed'):
            dependency_context.validate_interfaces(self.run, self.state, self.nodes[2])
        path.write_bytes(original)
        payload = mission.read(path)
        payload['facts']['files'][0]['source_sha256'] = '0' * 64
        path.write_text(acceptance.encoded(payload))
        entry.update(reference=mission.reference(self.run, path), facts=payload['facts'])
        with self.assertRaisesRegex(HornetError, 'source scope'):
            dependency_context.validate_interfaces(self.run, self.state, self.nodes[2])

    def test_missing_capture_is_not_silently_omitted(self):
        with self.assertRaisesRegex(HornetError, 'capture is required'):
            mission.packet(self.state, self.nodes[2])
        with self.assertRaisesRegex(HornetError, 'evidence is missing'):
            dependency_context.validate(self.run, self.state)

    def test_legacy_behavior_policy_never_reads_dependency_source(self):
        path = self.run / dependency_context.FILENAME
        path.unlink()
        mission.immutable(path, dependency_context.LEGACY_POLICY)
        self.state['dependency_context_policy'] = mission.reference(self.run, path)
        before = self.worker.calls[:]
        dependency_context.prepare(self.run, self.state, self.nodes[2], self.worker)
        self.assertEqual(self.worker.calls, before)
        self.assertNotIn('interfaces', mission.packet(self.state, self.nodes[2])['dependency_context'])


class DependencyContextControllerTests(unittest.TestCase):
    setUp = fixtures.MissionTests.setUp
    model = fixtures.MissionTests.model
    submit = fixtures.MissionTests.submit
    write = fixtures.MissionTests.write
    run_dir = fixtures.MissionTests.run_dir
    state = fixtures.MissionTests.state
    chain = fixtures.MissionTests.chain
    run_goal = fixtures.MissionTests.run_goal
    save_state = fixtures.MissionTests.save_state

    def separated_chain(self, observe=None):
        model = self.chain()
        def separated(messages, tools, *unused):
            names = {item['function']['name'] for item in tools}
            if observe:
                observe(messages, names)
            value, stats = model(messages, tools, *unused)
            if 'submit_intake' in names:
                args = json.loads(value['tool_calls'][0]['function']['arguments'])
                args['plan']['coverage'] = [{'requirement_id': 'r001', 'cell_ids': ['check-even']},
                                            {'requirement_id': 'r002', 'cell_ids': ['payload']}]
                value['tool_calls'][0]['function']['arguments'] = json.dumps(args)
            return value, stats
        return separated

    def test_both_checker_and_worker_receive_upstream_behavior_without_extra_requests(self):
        seen = []
        def observe(messages, names):
            text = messages[1]['content']
            if '"id":"payload"' in text and ('submit_checks' in names or not any(n.startswith('submit_') for n in names)):
                self.assertIn('Return True for even integers, False for odd.', text)
                self.assertIn('dependency_context', text)
                self.assertIn('do not expand file permissions', text)
                self.assertIn('is_even', text)
                self.assertIn('positional_or_keyword', text)
                seen.append('checker' if 'submit_checks' in names else 'worker')
        result = self.run_goal(self.separated_chain(observe))
        self.assertTrue(result['ok'], result)
        self.assertEqual(set(seen), {'checker', 'worker'})
        self.assertEqual(result['model_turns'], 9)
        packet = mission.read(self.run_dir() / 'cells/payload/work-packet.json')
        self.assertEqual([r['id'] for r in packet['requirements']], ['r002'])
        self.assertEqual([r['id'] for r in packet['dependency_context']['requirements']], ['r001'])
        self.assertEqual(packet['dependency_context']['interfaces']['facts']['files'][0]['path'], 'parity_cell.py')
        completion = mission.read(self.run_dir() / 'goal-completion.json')
        self.assertEqual(completion['dependency_context_policy'], self.state()['dependency_context_policy'])
        autonomous.load(self.project, self.run_dir())

    def test_cold_resume_preserves_packet_and_provider_receipt(self):
        model = self.separated_chain()
        original = mission.prepare_packet
        def interrupted(run, state, cell):
            original(run, state, cell)
            if cell['id'] == 'payload':
                raise KeyboardInterrupt()
        with patch.object(mission, 'prepare_packet', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.run_goal(model)
        run = self.run_dir()
        packet_path = run / 'cells/payload/work-packet.json'
        packet = packet_path.read_bytes()
        receipt_path = run / 'cells/check-even/return-manifest.json'
        receipt = receipt_path.read_bytes()
        result = autonomous.resume(self.project, argparse.Namespace(run=str(run)), self.worker, model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(packet_path.read_bytes(), packet)
        self.assertEqual(receipt_path.read_bytes(), receipt)
        self.assertEqual(self.worker.calls.count('write_file'), 2)

    def test_rebinding_modified_dependency_packet_does_not_hide_tampering(self):
        self.assertTrue(self.run_goal(self.separated_chain())['ok'])
        run = self.run_dir()
        path = run / 'cells/payload/work-packet.json'
        packet = mission.read(path)
        packet['dependency_context']['requirements'][0]['text'] = 'Return True for odd integers.'
        path.write_text(json.dumps(packet))
        state = self.state()
        state['cells']['payload']['work_packet'] = mission.reference(run, path)
        self.save_state(state)
        with self.assertRaisesRegex(HornetError, 'work packet changed'):
            mission.validate(run, state)
        with self.assertRaises(HornetError):
            autonomous.load(self.project, run)

    def test_legacy_goal_can_complete_and_load_without_new_policy(self):
        with patch.object(dependency_context, 'initialize', return_value=None):
            result = self.run_goal(self.separated_chain())
        self.assertTrue(result['ok'], result)
        self.assertNotIn('dependency_context', mission.read(self.run_dir() / 'cells/payload/work-packet.json'))
        self.assertNotIn('dependency_context_policy', mission.read(self.run_dir() / 'goal-completion.json'))
        autonomous.load(self.project, self.run_dir())


if __name__ == '__main__':
    unittest.main()
