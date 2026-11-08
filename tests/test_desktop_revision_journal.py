"""Journal integration tests use synthetic workspaces, never a live model or VM."""
import json
import tempfile
import unittest
from unittest.mock import patch

from hornet.desktop import automation as api
from hornet.desktop.mission_resume import resume_mission
from hornet.desktop.revision_journal import MAX_BYTES, bounded, capture, inject, publication_entry
from hornet.desktop.workspace import Workspace, write_json
from hornet.desktop.registry_mission import propose_with_tools as real_propose


class RevisionJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(self.tmp.name)
        base = self.ws.create('demo')
        api.make_initialization_draft(self.ws, 'demo', base, self.describe)
        self.base = api.accept_initialization(self.ws, 'demo')
        self.planner = self.mock('hornet.desktop.automation.request_json')
        self.planner.return_value = {'existing_files': ['main.py'], 'new_files': [], 'explanation': 'Update main'}
        self.propose = self.mock('hornet.desktop.registry_mission.propose_with_tools')
        self.propose.return_value = ({'summary': 'Changed main, functionality not tested',
                                     'files': {'main.py': 'print(42)\n'}}, [])
        self.mock('hornet.desktop.automation.model_describer').return_value = self.describe

    def mock(self, target):
        patcher = patch(target)
        self.addCleanup(patcher.stop)
        return patcher.start()

    @staticmethod
    def describe(payload):
        return {'summaries': {name: 'Purpose of ' + name for name in payload['files']},
                'blueprint': {'purpose': 'Test'}}

    def prepare(self, base=None):
        return api.prepare_mission(self.ws, 'demo', base or self.base, 'Update main',
                                   'http://localhost', 'test-model')

    def records(self):
        return [json.loads(p.read_text(encoding='utf-8'))
                for p in (self.ws.project('demo') / 'missions').glob('*.json')]

    def test_understanding_provenance_is_not_a_missing_mission(self):
        journal = capture(self.ws, 'demo', self.base)
        self.assertEqual(journal['entries'], [])
        self.assertEqual(journal['attention'], [])

    def test_next_mission_gets_published_changes_not_completion_claim(self):
        first = self.prepare()
        self.propose.return_value = ({'summary': 'Next', 'files': {'main.py': 'print(43)\n'}}, [])
        second = self.prepare(first['final_revision'])
        entry = second['identity']['revision_journal']['entries'][0]
        self.assertEqual(entry['changed_files'], ['main.py'])
        self.assertEqual(entry['acceptance'], 'pending_human_review')
        self.assertEqual(entry['checks'], 'not_run')
        self.assertEqual(entry['syntax'], 'passed')
        self.assertIn('revision_journal', self.planner.call_args.args[3])
        self.assertIn('revision_journal', self.propose.call_args.args[1])

    def test_selected_files_are_not_published_changes(self):
        with patch('hornet.desktop.registry_mission.propose_with_tools', side_effect=ValueError('No edit')):
            with self.assertRaises(ValueError):
                self.prepare()
        record = self.records()[0]
        self.assertEqual(record['plan']['existing_files'], ['main.py'])
        # Non-session failed attempts are not silently imported into another branch.
        self.assertEqual(capture(self.ws, 'demo', self.base)['entries'], [])

    def test_branch_isolation(self):
        first = self.prepare()
        other = self.ws.fork('demo', self.base, {'main.py': 'print(99)\n'}, 'Other branch')
        self.assertEqual(capture(self.ws, 'demo', other)['entries'], [])
        self.assertEqual(capture(self.ws, 'demo', first['final_revision'])['entries'][0]['mission_id'], first['id'])

    def test_resume_never_recaptures_history(self):
        self.propose.side_effect = ValueError('offline')
        with self.assertRaisesRegex(ValueError, 'offline'):
            self.prepare()
        record = self.records()[0]
        frozen = record['identity']
        self.propose.side_effect = None
        with patch('hornet.desktop.revision_journal.capture', side_effect=AssertionError('recaptured')):
            result = resume_mission(self.ws, 'demo', record['id'], 'http://localhost', 'test-model')
        self.assertEqual(result['identity'], frozen)
        self.assertEqual(self.planner.call_count, 1)

    def test_legacy_resume_keeps_old_inputs(self):
        self.propose.side_effect = ValueError('offline')
        with patch('hornet.desktop.revision_journal.capture', return_value=None):
            with self.assertRaisesRegex(ValueError, 'offline'):
                self.prepare()
        record = self.records()[0]
        self.assertNotIn('revision_journal', record['identity'])
        self.assertNotIn('revision_journal', self.planner.call_args.args[3])
        self.propose.side_effect = None
        result = resume_mission(self.ws, 'demo', record['id'], 'http://localhost', 'test-model')
        self.assertEqual(result['state'], 'review')
        self.assertNotIn('revision_journal', self.propose.call_args.args[1])
        self.assertEqual(self.planner.call_count, 1)
        payload = {'files': {}}
        self.assertEqual(inject({}, payload), {'files': {}})

    def test_corrupt_publication_never_claims_completed(self):
        record = self.prepare()
        root = self.ws.revision('demo', record['final_revision'])
        (root / 'main.py').write_text('print(999)\n', encoding='utf-8')
        journal = capture(self.ws, 'demo', record['final_revision'])
        self.assertTrue(journal['attention'])
        self.assertFalse(any(e.get('acceptance') == 'accepted' for e in journal['entries']))
        with self.assertRaises(ValueError):
            publication_entry(self.ws, 'demo', record['final_revision'])

    def test_bounds_and_truncation_are_explicit(self):
        journal = bounded([{'goal': 'x' * 10000, 'changed_files': ['large.py']} for _ in range(50)])
        self.assertLessEqual(len(json.dumps(journal).encode('utf-8')), MAX_BYTES)
        self.assertTrue(journal['history_incomplete'])
        self.assertGreater(journal['omitted_entries'], 0)
        journal = bounded([{'goal': '\U0001f600' * 10000}], attention=['\U0001f600' * 100000] * 8)
        self.assertLessEqual(len(json.dumps(journal).encode('utf-8')), MAX_BYTES)

    def test_session_failed_attempt_is_retained_without_edits(self):
        from hornet.desktop.working_revision import create_working_revision, abandon_working_revision
        head = create_working_revision(self.ws, 'demo', self.base)
        self.propose.side_effect = ValueError('offline')
        with self.assertRaisesRegex(ValueError, 'offline'):
            self.prepare(head)
        abandon_working_revision(self.ws, 'demo', head)
        journal = capture(self.ws, 'demo', head, head)
        self.assertEqual(journal['entries'][0]['outcome'], 'failed')
        self.assertEqual(journal['entries'][0]['changed_files'], [])
        self.assertEqual(journal['entries'][0]['attention'], 'offline')
        self.assertEqual(journal['entries'][0]['closure'], 'explicitly_closed_without_replay')
        self.assertEqual(journal['entries'][0]['files_read_during_attempt'], [])

    def test_leaf_journal_reconstructs_identically_after_resume(self):
        self.planner.return_value['goals'] = [
            {'id': 'root', 'parent': None, 'goal': 'Change main twice', 'files': [],
             'success_criteria': 'Main ends at 43', 'depends_on': []},
            {'id': 'first', 'parent': 'root', 'goal': 'Set main value to 42', 'files': ['main.py'],
             'success_criteria': 'Main prints 42', 'depends_on': []},
            {'id': 'second', 'parent': 'root', 'goal': 'Set main value to 43', 'files': ['main.py'],
             'success_criteria': 'Main prints 43', 'depends_on': ['first']}]
        seen = []
        fail = [True]
        def propose(root, context, request, **kwargs):
            goal = context['active_goal_id']
            seen.append(context)
            if goal == 'second' and fail[0]:
                raise ValueError('offline second')
            value = 42 if goal == 'first' else 43
            return ({'summary': goal + ' changed main', 'files': {'main.py': 'print(%d)\n' % value},
                     'goal_links': {'main.py': [goal]}}, [])
        self.propose.side_effect = propose
        with self.assertRaisesRegex(ValueError, 'offline second'):
            self.prepare()
        record = self.records()[0]
        before = seen[-1]['revision_journal']
        self.assertEqual(before['entries'][-1]['changed_files'], ['main.py'])
        self.assertEqual(before['entries'][-1]['goal_id'], 'first')
        self.assertEqual(before['entries'][-1]['acceptance'], 'not_established')
        fail[0] = False
        result = resume_mission(self.ws, 'demo', record['id'], 'http://localhost', 'test-model')
        self.assertEqual(result['state'], 'review')
        self.assertEqual(seen[-1]['revision_journal'], before)
        self.assertEqual([c['active_goal_id'] for c in seen], ['first', 'second', 'second'])

    def test_skipped_syntax_is_not_a_pass(self):
        record = self.prepare()
        path = self.ws.project('demo') / 'missions' / (record['id'] + '.json')
        record['syntax_checks']['files'][0]['status'] = 'skipped'
        write_json(path, record)
        entry = capture(self.ws, 'demo', record['final_revision'])['entries'][0]
        self.assertEqual(entry['syntax'], 'partial_or_skipped')

    def test_briefing_requires_orientation_only_when_journal_is_present(self):
        from hornet.desktop.model_briefing import brief
        self.assertIn('First orient from revision_journal', brief('Task', {'revision_journal': bounded([])}))
        self.assertNotIn('First orient from revision_journal', brief('Task', {}))

    def test_failed_attempts_do_not_displace_a_later_publication(self):
        identifiers = ['%032x' % n for n in range(1, 7)]
        directory = self.ws.project('demo') / 'missions'
        directory.mkdir(exist_ok=True)
        for identifier in identifiers:
            write_json(directory / (identifier + '.json'), {
                'id': identifier, 'project': 'demo', 'base_revision': self.base,
                'mission': 'Earlier attempt', 'state': 'failed', 'error': 'offline', 'candidates': []})
        record = self.prepare()
        state = {'sessions': {'session': {'missions': identifiers + [record['id']]}}}
        with patch('hornet.desktop.working_revision.load_working', return_value=state):
            journal = capture(self.ws, 'demo', record['final_revision'], 'session')
        self.assertEqual(journal['entries'][-1]['mission_id'], record['id'])
        self.assertEqual(journal['entries'][-1]['changed_files'], ['main.py'])
        self.assertEqual(journal['omitted_entries'], 1)

    def test_rebinding_preserves_omitted_attention(self):
        frozen = bounded([{'acceptance': 'pending_human_review'} for _ in range(7)])
        self.assertEqual(frozen['omitted_attention_entries'], 1)
        payload = inject({'revision_journal': frozen}, {})
        self.assertEqual(payload['revision_journal']['omitted_attention_entries'], 1)
        payload = inject({'revision_journal': frozen}, {}, [{'acceptance': 'pending_human_review'}])
        self.assertEqual(payload['revision_journal']['omitted_attention_entries'], 2)

    def test_new_mission_on_legacy_base_does_not_require_a_journal_snapshot(self):
        (self.ws.revision('demo', self.base) / '.hornet-meta' / 'goals.json').unlink()
        result = self.prepare()
        self.assertEqual(result['state'], 'review')
        entry = publication_entry(self.ws, 'demo', result['candidates'][0])
        self.assertFalse(entry['file_changes_verified'])
        self.assertIn('unavailable', entry['attention'])

    def test_registry_passes_journal_to_model(self):
        from hornet.desktop.registry_mission import propose_with_tools
        root = self.ws.revision('demo', self.base)
        context = self.ws.mission_context('demo', self.base, ['main.py'], 'Update')
        context['revision_journal'] = bounded([])
        seen = []
        def request(instruction, payload):
            seen.append(payload)
            return {'summary': 'No edits', 'edits': []}
        with self.assertRaisesRegex(ValueError, 'no usable edits'):
            real_propose(root, context, request)
        self.assertEqual(seen[0]['revision_journal'], context['revision_journal'])


if __name__ == '__main__':
    unittest.main()
