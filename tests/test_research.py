"""Read-only branch contracts, evidence, repair and durable handoff."""
import argparse
import copy
import json
import unittest
from unittest.mock import patch

from hornet.coding import autonomous, cells, intake, mission, research, tree
from hornet.coding.role_schema import recon_schema
from hornet.project import HornetError
from test_resume import call
import test_autonomous as fixtures


class ResearchTests(unittest.TestCase):
    run_dir = fixtures.AutonomousTests.run_dir
    state = fixtures.AutonomousTests.state
    submit = fixtures.AutonomousTests.submit
    write = fixtures.AutonomousTests.write

    def setUp(self):
        fixtures.AutonomousTests.setUp(self)
        self.args.research_cells = True
        self.research_turns = 0
        self.bad_citation = False
        self.fail_code_once = False
        self.read_cell = {'id': 'inspect', 'kind': 'research', 'task': 'Inspect current parity implementation.',
            'files': ['parity_cell.py'], 'depends_on': [], 'result': {'schema': {'type': 'string'}}}
        code = dict(copy.deepcopy(self.plan['cells'][0]), kind='code', depends_on=['inspect'])
        code['result']['schema'] = json.loads(code['result'].pop('schema_json'))
        self.plan = {'schema_version': 3, 'cells': [self.read_cell, code],
            'coverage': [{'requirement_id': 'r001', 'cell_ids': ['inspect', 'check-even']}]}
        self.finding = {'value_json': '"Observed: is_even currently tests remainder 1; the required even test is 0."',
            'evidence': [{'path': 'parity_cell.py', 'start_line': 2, 'end_line': 2, 'quote': '    return value % 2 == 1'}]}

    def model(self, messages, tools, *unused):
        names = {item['function']['name'] for item in tools}
        if 'submit_intake' in names:
            return self.submit('submit_intake', {'schema_version': 1, 'mode': 'delegate', 'reason': 'Inspect then correct',
                'self_cell_id': '', 'plan': self.plan}, 'intake')
        if 'submit_finding' in names:
            self.assertTrue(names <= {'read_file', 'submit_finding'})
            self.research_turns += 1
            if self.research_turns == 1:
                return call('read_file', {'path': 'parity_cell.py'}, 'inspect-source'), {}
            value = copy.deepcopy(self.finding)
            if self.bad_citation and self.research_turns == 2:
                value['evidence'][0]['quote'] = 'invented'
            return self.submit('submit_finding', value, 'findings')
        if 'submit_route' in names:
            return self.submit('submit_route', {'schema_version': 1, 'route': 'execute',
                'reason': 'One bounded correction', 'plan': copy.deepcopy(tree.EMPTY)}, 'route')
        if 'submit_goal_checks' in names or 'submit_checks' in names:
            return fixtures.AutonomousTests.model(self, messages, tools)
        self.assertIn('Observed: is_even', messages[1]['content'])
        if self.fail_code_once and self.worker_turn == 1:
            self.worker_turn += 1
            return self.write('    return True\n', 'bad-edit')
        if self.fail_code_once and self.worker_turn == 2:
            self.worker_turn += 1
            return {'role': 'assistant', 'content': 'Ready for verification.'}, {}
        if self.fail_code_once and self.worker_turn == 3:
            self.worker_turn += 1
            return self.write('    return value % 2 == 0\n', 'repair')
        return fixtures.AutonomousTests.model(self, messages, tools)

    def run_goal(self, model=None):
        return autonomous.start(self.project, self.args, self.worker, model or self.model)

    def test_read_only_findings_feed_code_and_survive_source_changes(self):
        result = self.run_goal()
        self.assertTrue(result['ok'], result)
        state = autonomous.load(self.project, self.run_dir())
        self.assertEqual(state['schema_version'], 6)
        receipt = mission.read(self.run_dir() / 'cells/inspect/return-manifest.json')
        self.assertEqual(receipt['kind'], 'research_return')
        self.assertEqual(receipt['changed_files'], [])
        self.assertEqual(receipt['source']['before_sha256'], receipt['source']['after_sha256'])
        self.assertFalse((self.run_dir() / 'cells/inspect/jobs').exists())
        self.assertEqual(self.worker.calls.count('write_file'), 1)
        self.assertIs(result['values']['check-even'], False)
        packet = mission.read(self.run_dir() / 'cells/check-even/work-packet.json')
        self.assertEqual(packet['dependencies']['inspect']['value'], json.loads(self.finding['value_json']))
        self.assertEqual(mission.read(self.run_dir() / 'cells/inspect/work-packet.json')['permissions']['write_files'], [])

    def stop_after_research(self):
        original = autonomous.stored
        def stop(path, value):
            original(path, value)
            if path.name == 'progress.json' and value.get('stage') == 'research_completed':
                raise KeyboardInterrupt()
        with patch.object(autonomous, 'stored', side_effect=stop), self.assertRaises(KeyboardInterrupt):
            self.run_goal()

    def test_resume_reuses_research_without_model_replay(self):
        self.stop_after_research()
        before = (self.run_dir() / 'cells/inspect/return-manifest.json').read_bytes()
        old_attempt = (self.run_dir() / 'attempts/1/result.json').read_bytes()
        count = self.research_turns
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.research_turns, count)
        self.assertEqual((self.run_dir() / 'cells/inspect/return-manifest.json').read_bytes(), before)
        self.assertEqual((self.run_dir() / 'attempts/1/result.json').read_bytes(), old_attempt)

    def test_bad_citation_is_retained_then_agent_corrects_it(self):
        self.bad_citation = True
        result = self.run_goal()
        self.assertTrue(result['ok'], result)
        history = self.state()['roles']['research-inspect']['history']
        errors = [row for row in history if row.get('tool') == 'submit_finding']
        self.assertIn('exactly match', errors[0]['result']['error'])

    def test_failed_code_can_repair_without_redoing_research(self):
        self.fail_code_once = True
        result = self.run_goal()
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.research_turns, 2)
        receipt = mission.read(self.run_dir() / 'cells/check-even/return-manifest.json')
        self.assertTrue(any(not row['checks_passed'] for row in receipt['provenance']['verification_rounds']))
        self.assertTrue(receipt['provenance']['verification_rounds'][-1]['checks_passed'])

    def test_research_cannot_request_write_or_command(self):
        def wrong(messages, tools, *args):
            if 'submit_finding' in {t['function']['name'] for t in tools}:
                return call('write_file', {'path': 'parity_cell.py', 'content': 'bad'}, 'unoffered'), {}
            return self.model(messages, tools, *args)
        result = self.run_goal(wrong)
        self.assertFalse(result['ok'])
        self.assertIn('unoffered', result['error'])
        self.assertNotIn('write_file', self.worker.calls)

    def test_out_of_scope_reads_do_not_reach_worker(self):
        def wrong(messages, tools, *args):
            if 'submit_finding' in {t['function']['name'] for t in tools}:
                if 'read_file' in {t['function']['name'] for t in tools}:
                    return call('read_file', {'path': 'elsewhere.py'}, 'outside'), {}
                return self.submit('submit_finding', self.finding, 'without-evidence')
            return self.model(messages, tools, *args)
        result = self.run_goal(wrong)
        self.assertFalse(result['ok'])
        self.assertEqual(len([r for r in self.state()['roles']['research-inspect']['history'] if r.get('tool') == 'read_file']), 4)
        self.assertNotIn('read_file', self.worker.calls)

    def test_tampered_evidence_refuses_resume(self):
        self.stop_after_research()
        path = self.run_dir() / 'cells/inspect/research/finding.json'
        finding = mission.read(path)
        finding['value_json'] = '"tampered"'
        path.write_text(json.dumps(finding))
        with self.assertRaises(HornetError):
            autonomous.load(self.project, self.run_dir())

    def test_citation_requires_read_and_original_hash(self):
        observed = self.worker.call('read_file', {'path': 'parity_cell.py'})
        snapshot = self.worker.call('snapshot')
        row = {'arguments': {'path': 'parity_cell.py'}, 'result': observed}
        for seen in ([], [dict(row, result=dict(observed, sha256='0' * 64))]):
            with self.assertRaises(HornetError):
                research.validate(self.finding, self.read_cell, snapshot, seen)
        self.assertIsInstance(research.validate(self.finding, self.read_cell, snapshot, [row]), str)

    def test_research_type_is_strict_and_pure_research_is_explicitly_rejected(self):
        bad = dict(self.read_cell, result={'schema_json': '{"type":"boolean"}'})
        with self.assertRaises(HornetError):
            research.validate(self.finding, bad, {}, [])
        with self.assertRaisesRegex(HornetError, 'at least one code'):
            cells.validate(dict(self.plan, cells=[self.read_cell]))

    def test_false_and_null_are_valid_research_values(self):
        observed = self.worker.call('read_file', {'path': 'parity_cell.py'})
        snapshot = self.worker.call('snapshot')
        seen = [{'arguments': {'path': 'parity_cell.py'}, 'result': observed}]
        for kind, value in [('boolean', False), ('null', None)]:
            cell = dict(self.read_cell, result={'schema_json': json.dumps({'type': kind})})
            finding = dict(self.finding, value_json=json.dumps(value))
            self.assertIs(research.validate(finding, cell, snapshot, seen), value)

    def test_resumed_role_keeps_last_validation_error_in_focused_context(self):
        def malformed(messages, tools, *args):
            if 'submit_finding' in {t['function']['name'] for t in tools}:
                return self.submit('submit_finding', dict(self.finding, value_json='not-json'), 'bad')
            return self.model(messages, tools, *args)
        result = self.run_goal(malformed)
        self.assertFalse(result['ok'])
        self.assertIn('six attempts', result['error'])
        observed = []
        def resumed(messages, tools, *args):
            if 'submit_finding' in {t['function']['name'] for t in tools}:
                observed.append(json.dumps(messages))
            return self.model(messages, tools, *args)
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, resumed)
        self.assertTrue(result['ok'], result)
        self.assertIn('last_validation_errors', observed[0])
        self.assertIn('Finding must encode finite JSON', observed[0])

    def test_old_mode_rejects_new_schema_without_reading(self):
        self.args.research_cells = False
        result = self.run_goal()
        self.assertFalse(result['ok'])
        self.assertNotIn('read_file', self.worker.calls)
        self.assertNotIn('write_file', self.worker.calls)

    def test_schema_exposes_complete_closed_variants(self):
        schema = recon_schema('submit_intake', [{'id': 'r001'}], 3, research=True)
        plan = schema['properties']['plan']['properties']
        self.assertEqual(plan['schema_version']['const'], 3)
        variants = plan['cells']['items']['anyOf']
        self.assertEqual({v['properties']['kind']['const'] for v in variants}, {'research', 'code'})
        for variant in variants:
            self.assertEqual(set(variant['required']), set(variant['properties']))
            self.assertFalse(variant['additionalProperties'])
        from hornet.coding.model import validate_arguments
        value = {'schema_version': 1, 'mode': 'execute', 'reason': 'small', 'self_cell_id': 'inspect', 'plan': self.plan}
        validate_arguments(value, schema)
        with self.assertRaisesRegex(HornetError, 'Invalid ownership'):
            intake.validate(value, mission.document(self.args.goal), 3)
        value.update(mode='delegate', self_cell_id='')
        validate_arguments(value, schema)

    def test_cli_flag(self):
        from hornet.cli import build_parser
        args = build_parser().parse_args(['autowork', '--worker-config', 'worker.json', '--goal', 'inspect then code', '--research-cells'])
        self.assertTrue(args.research_cells)

    def test_structured_schemas_reach_semantic_validation_without_loosening_tool_arguments(self):
        from hornet.coding.model import validate_arguments
        plan = copy.deepcopy(self.plan)
        plan['cells'][0]['result']['schema'] = {'type': 'object', 'properties': {'rules': {'type': 'array', 'items': {'type': 'string'}}},
            'required': ['rules'], 'additionalProperties': False}
        cells.validate(plan)
        schema = recon_schema('submit_intake', [{'id': 'r001'}], 3, research=True)
        value = {'schema_version': 1, 'mode': 'delegate', 'reason': 'research and code', 'self_cell_id': '', 'plan': plan}
        validate_arguments(value, schema)
        value['unknown_command'] = 'never'
        with self.assertRaises(ValueError):
            validate_arguments(value, schema)

    def test_publication_gap_adopts_exact_research_receipt(self):
        from hornet.coding import returns
        original = returns.write_text
        def crash(path, text, *args, **kwargs):
            original(path, text, *args, **kwargs)
            if path.name == 'return-manifest.json' and path.parent.name == 'inspect':
                raise KeyboardInterrupt()
        with patch.object(returns, 'write_text', side_effect=crash), self.assertRaises(KeyboardInterrupt):
            self.run_goal()
        before = (self.run_dir() / 'cells/inspect/return-manifest.json').read_bytes()
        result = autonomous.resume(self.project, argparse.Namespace(run=str(self.run_dir())), self.worker, self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.research_turns, 2)
        self.assertEqual((self.run_dir() / 'cells/inspect/return-manifest.json').read_bytes(), before)

    def test_nested_research_and_code_satisfy_original_parent(self):
        nested = copy.deepcopy(self.plan)
        root = dict(copy.deepcopy(nested['cells'][1]), id='delivery', depends_on=[])
        self.plan = dict(nested, cells=[root], coverage=[{'requirement_id': 'r001', 'cell_ids': ['delivery']}])
        def nested_model(messages, tools, *args):
            if 'submit_route' in {t['function']['name'] for t in tools}:
                return self.submit('submit_route', {'schema_version': 1, 'route': 'needs_split',
                    'reason': 'Research then implementation', 'plan': nested}, 'split')
            return self.model(messages, tools, *args)
        result = self.run_goal(nested_model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(set(result['values']), {'inspect', 'check-even', 'delivery'})
        autonomous.load(self.project, self.run_dir())

    def test_research_receipt_cannot_be_rebound_to_another_goal(self):
        self.stop_after_research()
        state = self.state()
        state['goal_sha256'] = '0' * 64
        with self.assertRaises(HornetError):
            research.receipt(self.run_dir(), state, self.read_cell, 1)


if __name__ == '__main__':
    unittest.main()
