import json
import shutil
import tempfile
import unittest
from pathlib import Path
from hornet.desktop.workspace import Workspace
from hornet.desktop.revision_goals import read_revision_goals
from hornet.desktop.goal_context import publication_tag


class RevisionGoalsTests(unittest.TestCase):
    def test_each_publication_has_fresh_goal_and_parent_snapshot_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            ws = Workspace(directory)
            base = ws.create('demo')
            root = ws.revision('demo', base)
            before = (root / '.hornet-meta/goals.json').read_bytes()
            stage = root.parent / '.test-stage'
            shutil.copytree(root, stage)
            with publication_tag('run1', 'publish', 'inputhash'):
                revision = ws._publish(stage, base, 'Implement a new goal')
            value = read_revision_goals(ws.revision('demo', revision))
            prior = read_revision_goals(root)
            self.assertNotEqual(value['goal_id'], prior['goal_id'])
            self.assertEqual(value['goal'], 'Implement a new goal')
            self.assertEqual(value['parent_revision'], base)
            self.assertEqual(value['operation']['operation_id'], 'run1')
            self.assertEqual(value['current'], 'root/review')
            self.assertEqual(value['goals']['root']['status'], 'pending_review')
            self.assertEqual((root / '.hornet-meta/goals.json').read_bytes(), before)

    def test_changed_source_and_wrong_revision_snapshot_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            ws = Workspace(directory)
            revision = ws.create('demo')
            root = ws.revision('demo', revision)
            (root / 'main.py').write_text('changed', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'changed'):
                read_revision_goals(root)
            value = json.loads((root / '.hornet-meta/goals.json').read_text())
            value['revision'] = 'other'
            (root / '.hornet-meta/goals.json').write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                read_revision_goals(root)

    def test_legacy_revision_is_not_backfilled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(read_revision_goals(root)['version'], 0)
            self.assertEqual(list(root.iterdir()), [])
