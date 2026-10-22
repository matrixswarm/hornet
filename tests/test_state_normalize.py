"""Status normalization and its real goal/pointer consumers."""
import json
from pathlib import Path
import tempfile
import unittest

from hornet.operations import initialize
from hornet.project import Project
from hornet.state import node_status, normalize, status


class NormalizeTests(unittest.TestCase):
    def test_falsey_values_remain_unknown(self):
        for value in (None, False, 0, 0.0, '', [], {}, (), set()):
            with self.subTest(value=value):
                self.assertEqual(normalize(value), 'UNKNOWN')

    def test_whitespace_only_text_is_unknown(self):
        for value in (' ', '\t', '\r\n', ' \t\n ', '\u2003\u00a0'):
            with self.subTest(value=value):
                self.assertEqual(normalize(value), 'UNKNOWN')

    def test_separator_runs_collapse(self):
        for value in ('in--progress', 'in__progress', ' in \t progress ',
                      'in -__\nprogress', 'in\u2003progress'):
            with self.subTest(value=value):
                self.assertEqual(normalize(value), 'IN_PROGRESS')
        self.assertEqual(normalize('  spaced   out  '), 'SPACED_OUT')

    def test_other_text_and_type_conversions_are_preserved(self):
        for value, expected in ((True, 'TRUE'), (7, '7'), (-7, '_7'),
                                (3.5, '3.5'), ('done', 'DONE'),
                                (' failed ', 'FAILED'), ('x/y.z', 'X/Y.Z')):
            with self.subTest(value=value):
                # Hyphens are separators even in a converted numeric value.
                self.assertEqual(normalize(value), expected)

    def test_normalization_is_idempotent(self):
        for value in (None, False, 0, '', ' \t\n ', 'in--progress',
                      ' in \t progress ', '__', ' --x-- ', True, 7):
            with self.subTest(value=value):
                once = normalize(value)
                self.assertEqual(normalize(once), once)


class StatusIntegrationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='hornet-status-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        initialize('normalization-check', self.root)
        self.project = Project(self.root)

    def goal(self, name, value, filename='state.json'):
        node = self.root / 'continue/goals' / name
        node.mkdir()
        content = json.dumps({'status': value}) if filename.endswith('.json') else value
        (node / filename).write_text(content, encoding='utf-8')
        return node

    def test_actual_status_summary_counts_normalized_goals_and_pointers(self):
        self.goal('active', ' in \t progress ')
        self.goal('complete', ' done ')
        self.goal('unknown', ' \t\n ')
        directory = self.root / 'continue/pointers'
        (directory / 'worker.json').write_text(json.dumps({'status': ' locked \t ', 'current_node': 'active'}), encoding='utf-8')
        (directory / 'idle.json').write_text(json.dumps({'status': ' idle '}), encoding='utf-8')
        result = status(self.project)
        self.assertTrue(result['ok'])
        self.assertEqual(result['metrics']['total_goals'], 3)
        self.assertEqual(result['metrics']['active_goals'], 1)
        self.assertEqual(result['metrics']['completed_goals'], 1)
        self.assertEqual(result['metrics']['unknown_goals'], 1)
        self.assertEqual(result['metrics']['active_pointers'], 1)
        self.assertEqual(result['metrics']['idle_pointers'], 1)

    def test_existing_plain_and_explicit_state_records_use_normalization(self):
        self.assertEqual(node_status(self.goal('plain', 'in--progress', 'state')), 'IN_PROGRESS')
        self.assertEqual(node_status(self.goal('explicit', 'Status: in__progress', 'state')), 'IN_PROGRESS')

    def test_status_summary_remains_read_only(self):
        self.goal('active', 'in--progress')
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        status(self.project)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
