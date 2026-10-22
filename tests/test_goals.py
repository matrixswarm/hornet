"""Independent goal copies, bounded revision routing and retained evidence."""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from hornet.cli import build_parser
from hornet.coding import autonomous, goals, mission, session, workspaces
from hornet.coding.transport import SSHWorker
from hornet.project import HornetError, Project
from test_resume import LocalWorker, call


class ForkWorker(LocalWorker):
    """Exercise the real recipe lifecycle with independently copied source."""
    def __init__(self, root, base=None):
        super().__init__(root)
        self.binding['root'] = root.as_posix()
        self.base = base or root.parent / 'goals'

    def fork_workspace(self, expected):
        import uuid
        ident = 'g-' + uuid.uuid4().hex
        root = self.base / ident
        shutil.copytree(self.worker.root, root)
        result = ForkWorker(root, self.base)
        result.workspace = ident
        ForkWorker.latest = root
        return result


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hornet-goal-copies-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.host, self.source, self.base = root / 'host', root / 'source', root / 'goals'
        self.host.mkdir()
        self.source.mkdir()
        self.base.mkdir()
        self.patch = patch.object(workspaces, 'BASE', self.base.as_posix())
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.project = Project(self.host)
        (self.host / 'worker.json').write_text('{}')
        (self.source / 'number.py').write_text('def double(n):\n    return n\n')
        self.worker = ForkWorker(self.source, self.base)
        self.args = build_parser().parse_args(['autowork', '--worker-config', 'worker.json',
            '--model', 'fixture', '--run-dir', 'runs', '--goal', 'Fix number.double(n) to return n * 2.'])
        self.active = None
        self.turn = 0

    def model(self, messages, tools, *unused):
        names = {t['function']['name'] for t in tools}
        if 'submit_contract_oracle' in names:
            packet = json.JSONDecoder().raw_decode(messages[1]['content'])[0]
            return call('submit_contract_oracle', {'expectations': [dict(id=c['id'], status='determined',
                basis='Apply the required multiplier.', value_json=json.dumps(c['args'][0] *
                    (3 if c['function'].endswith('.triple') else 2))) for c in packet['calls']]}, 'oracle'), {}
        if 'submit_contract_review' in names:
            return call('submit_contract_review', {'decision': 'approve', 'issues': [],
                'rationale': 'Fixture contract matches the scoped task.'}, 'review'), {}
        contract = {'id': 'double', 'task': self.args.goal, 'files': ['number.py'], 'depends_on': [],
            'result': {'path': 'number.py', 'function': 'double', 'args_json': '[3]',
                       'kwargs_json': '{}', 'schema_json': '{"type":"integer"}'}}
        revising = self.args.command == 'revise'
        if revising:
            contract['result']['function'] = 'triple'
        if 'submit_intake' in names:
            plan = {'schema_version': 2, 'cells': [contract], 'coverage': [
                {'requirement_id': r['id'], 'cell_ids': ['double']} for r in mission.document(self.args.goal)['requirements']]}
            return call('submit_intake', {'schema_version': 1, 'mode': 'execute', 'reason': 'Small function',
                'self_cell_id': 'double', 'plan': plan}, 'plan'), {}
        checks = {'schema_version': 1, 'checks': [{'id': 'double-values', 'description': 'Doubles inputs',
            'paths': ['number.py'], 'timeout': 5, 'python': 'from number import double\nassert double(3) == 6'}]}
        if revising:
            checks['checks'][0]['python'] = 'from number import triple\nassert triple(3) == 9'
        for name in ('submit_goal_checks', 'submit_checks'):
            if name in names:
                return call(name, checks, name), {}
        self.turn += 1
        if self.turn == 1:
            return call('read_file', {'path': 'number.py'}, 'read'), {}
        if self.turn == 2:
            # Find the goal directory, never edit the source baseline.
            current = ForkWorker.latest / 'number.py'
            text = 'def double(n):\n    return n * 2\n'
            if getattr(self.args, 'command', None) == 'revise':
                text += '\ndef triple(n):\n    return n * 3\n'
                if getattr(self, 'break_parent', False):
                    text = text.replace('n * 2', 'n * 5')
            return call('write_file', {'path': 'number.py', 'content': text,
                        'expected_sha256': hashlib.sha256(current.read_bytes()).hexdigest()}, 'write'), {}
        return {'role': 'assistant', 'content': 'Ready for checks'}, {}

    def start(self):
        self.turn = 0
        return autonomous.start(self.project, self.args, self.worker, self.model)

    def test_goals_use_independent_directories_and_baseline_remains_unchanged(self):
        original = (self.source / 'number.py').read_bytes()
        first, second = self.start(), self.start()
        self.assertTrue(first['ok'], first)
        self.assertTrue(second['ok'], second)
        self.assertNotEqual(first['workspace'], second['workspace'])
        self.assertEqual(original, (self.source / 'number.py').read_bytes())
        index = goals.inspect(self.project, first['run_dir'])
        self.assertEqual(index['files']['number.py']['cells'][0]['id'], 'double')
        self.assertTrue(index['files']['number.py']['cells'][0]['verified'])
        self.assertEqual(len(goals.listing(self.project, 'runs')['goals']), 2)

    def test_fresh_resume_reconstructs_selected_workspace(self):
        def offline(*unused):
            raise HornetError('offline')
        first = autonomous.start(self.project, self.args, self.worker, offline)
        run = Path(first['run_dir'])
        saved = autonomous.load(self.project, run)
        old = (run / 'attempts/1/result.json').read_bytes()
        chosen = saved['settings']['workspace']
        def connect(config, workspace):
            self.assertEqual(workspace, chosen)
            result = ForkWorker(self.base / workspace, self.base)
            result.workspace = workspace
            return result
        self.turn = 0
        with patch.object(autonomous, 'SSHWorker', side_effect=connect):
            result = autonomous.resume(self.project, argparse.Namespace(run=str(run)), request_model=self.model)
        self.assertTrue(result['ok'], result)
        self.assertEqual(old, (run / 'attempts/1/result.json').read_bytes())

    def test_revision_keeps_parent_evidence_and_checks(self):
        first = self.start()
        self.assertTrue(first['ok'], first)
        parent = Path(first['run_dir'])
        before = {p.relative_to(parent).as_posix(): p.read_bytes() for p in parent.rglob('*') if p.is_file()}
        self.args = build_parser().parse_args(['revise', str(parent), '--file', 'number.py', '--run-dir', 'runs',
            '--goal', 'Add triple(n) returning n * 3 to number.py. Preserve double(n).'])
        source = ForkWorker(self.base / first['workspace'], self.base)
        self.turn = 0
        result = goals.revise(self.project, self.args, source, self.model)
        self.assertTrue(result['ok'], result)
        self.assertNotEqual(result['workspace'], first['workspace'])
        self.assertNotIn('triple', (source.worker.root / 'number.py').read_text())
        self.assertEqual(before, {p.relative_to(parent).as_posix(): p.read_bytes() for p in parent.rglob('*') if p.is_file()})
        state = autonomous.load(self.project, Path(result['run_dir']))
        self.assertTrue(goals.regression_evidence(Path(result['run_dir']), state)['reports'])

    def test_new_checks_cannot_hide_a_parent_regression(self):
        first = self.start()
        self.assertTrue(first['ok'], first)
        self.args = build_parser().parse_args(['revise', first['run_dir'], '--file', 'number.py',
            '--run-dir', 'runs', '--goal', 'Add triple(n) to number.py and preserve existing behavior.'])
        self.break_parent, self.turn = True, 0
        source = ForkWorker(self.base / first['workspace'], self.base)
        result = goals.revise(self.project, self.args, source, self.model)
        self.assertFalse(result['ok'], result)
        self.assertIn('Inherited revision checks failed', result['error'])
        self.assertFalse((Path(result['run_dir']) / 'goal-completion.json').exists())
        self.assertIn('n * 2', (source.worker.root / 'number.py').read_text())

    def test_changed_parent_source_is_rejected_before_fork(self):
        first = self.start()
        self.args = build_parser().parse_args(['revise', first['run_dir'], '--file', 'number.py',
            '--goal', 'Add triple(n).'])
        source = ForkWorker(self.base / first['workspace'], self.base)
        (source.worker.root / 'number.py').write_text('changed = True\n')
        before = set(self.base.iterdir())
        with self.assertRaisesRegex(HornetError, 'Revision source changed'):
            goals.revise(self.project, self.args, source, self.model)
        self.assertEqual(before, set(self.base.iterdir()))

    def test_workspace_receipt_tampering_blocks_resume(self):
        result = self.start()
        path = Path(result['run_dir']) / 'workspace.json'
        data = json.loads(path.read_text())
        data['id'] = 'g-' + '0' * 32
        path.write_text(json.dumps(data))
        with self.assertRaises(HornetError):
            autonomous.load(self.project, path.parent)

    def test_revision_routes_dependents_and_rejects_scope_expansion(self):
        result = self.start()
        run = Path(result['run_dir'])
        state = autonomous.load(self.project, run)
        second = copy.deepcopy(state['plan']['cells'][0])
        second.update(id='consumer', files=['consumer.py'], depends_on=['double'])
        state['plan']['cells'].append(second)
        state['cells']['consumer'] = copy.deepcopy(state['cells']['double'])
        route = goals.route(run, state, ['number.py'])
        self.assertEqual(route['affected_cells'], ['consumer', 'double'])
        self.assertEqual(route['allowed_files'], ['consumer.py', 'number.py'])
        plan = copy.deepcopy(state['plan'])
        plan['cells'][0]['files'] = ['escape.py']
        with self.assertRaises(HornetError):
            goals.validate_scope(route, plan)
        for target in ('../outside.py', 'absent.py'):
            with self.assertRaises(HornetError):
                goals.route(run, state, [target])


class TransportWorkspaceTests(unittest.TestCase):
    def large_reply(self, corrupt=False, short=False):
        value = {'ok': True, 'files': [{'path': 'large.py', 'content': 'x' * 50000}]}
        raw = json.dumps(value).encode()
        ref = {'id': 'r-' + 'a' * 32, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        if corrupt:
            ref['sha256'] = '0' * 64
        requests = []
        class Wire:
            def _wire(self, tool, args):
                requests.append(args['argv'])
                if args['argv'][4] == 'remove':
                    result = {'ok': True}
                else:
                    offset = int(args['argv'][5])
                    part = raw[offset:offset + (1 if short else 12000)]
                    result = {'ok': True, 'data': base64.b64encode(part).decode()}
                return {'ok': True, 'output': json.dumps(result)}
        return Wire(), {'ok': True, 'output': json.dumps({'ok': True, 'hornet_reply': ref})}, requests, value

    def test_large_reply_is_reassembled_hashed_and_removed(self):
        worker, reply, calls, expected = self.large_reply()
        self.assertEqual(workspaces.receive(worker, reply), expected)
        self.assertEqual(calls[-1][4], 'remove')
        self.assertEqual(len(calls), 6)

    def test_corrupt_or_incomplete_large_reply_is_not_adopted(self):
        for options in ({'corrupt': True}, {'short': True}):
            worker, reply, calls, _ = self.large_reply(**options)
            with self.assertRaises(HornetError):
                workspaces.receive(worker, reply)
            self.assertFalse(any(c[4] == 'remove' for c in calls))

    def test_malformed_large_reply_reference_is_rejected(self):
        for reference in ({}, {'id': '../escape', 'bytes': 20000, 'sha256': 'a' * 64}):
            with self.assertRaises(HornetError):
                workspaces.receive(None, {'ok': True, 'output': json.dumps({'ok': True, 'hornet_reply': reference})})

    def test_workspace_ids_and_request_bounds(self):
        for value in ('../../escape', 'g-X', '', None, 'g-' + 'a' * 33):
            with self.assertRaises(HornetError):
                workspaces.identifier(value)
        with self.assertRaises(HornetError):
            workspaces.chunks('x' * 90000)
        self.assertLess(len(workspaces.DISPATCH), 4000)
        self.assertLess(len(workspaces.CLONE), 4000)

    def test_every_rpc_is_wrapped_and_data_is_not_interpolated(self):
        worker = object.__new__(SSHWorker)
        worker.workspace = 'g-' + 'a' * 32
        recorded = []
        def wire(tool, args):
            recorded.append((tool, args))
            return {'ok': True, 'output': '{"ok":true,"result":"received"}'}
        worker._wire = wire
        value = {'path': 'a.py', 'content': "x = '" + 'x' * 7000 + "'", 'expected_sha256': ''}
        self.assertTrue(worker._rpc('write_file', value)['ok'])
        tool, args = recorded[0]
        self.assertEqual(tool, 'run_command')
        self.assertEqual(args['argv'][2], workspaces.DISPATCH)
        self.assertEqual(json.loads(''.join(args['argv'][4:]))['arguments'], value)
        self.assertTrue(all(len(arg) <= 4000 for arg in args['argv']))

    def test_transport_failures_and_invalid_receipts_fail_closed(self):
        for value in ({'ok': False, 'error': 'offline'}, {'ok': True, 'output': 'garbage'},
                      {'ok': True, 'output': '{}'}):
            with self.assertRaises(HornetError):
                workspaces.unpack(value)

    def test_copied_worker_configs_share_same_lock_in_project(self):
        with tempfile.TemporaryDirectory() as root:
            worker = type('Pinned', (), {'connection_binding': 'a' * 64})()
            self.assertEqual(workspaces.lock_path(Project(root), {'worker_config': 'a.json'}, worker),
                             workspaces.lock_path(Project(root), {'worker_config': 'copy/b.json'}, worker))


if __name__ == '__main__':
    unittest.main()
