import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from hornet.desktop.workspace import Workspace
from hornet.desktop import revision_export as ex
from hornet.desktop.revision_drag import confirm_export


class RevisionExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = Workspace(self.root / 'workspace')
        self.rev = self.ws.create('demo')
        self.source = self.ws.revision('demo', self.rev)
        self.parent = self.root / 'exports'
        self.parent.mkdir()

    def test_complete_copy_and_numbering(self):
        (self.source / '.hidden').write_bytes(b'\x00\x01')
        (self.source / 'empty').mkdir()
        before = ex._manifest(self.source)
        first = self.ws.export_revision('demo', self.rev, self.parent)
        second = self.ws.export_revision('demo', self.rev, self.parent)
        self.assertEqual(second.name, first.name + '-2')
        self.assertEqual(ex._manifest(first), before)
        self.assertEqual(ex._manifest(second), before)
        self.assertEqual(ex._manifest(self.source), before)
        self.assertFalse(list(self.parent.glob('.hornet-export-*')))

    def test_existing_file_uses_suffix(self):
        target = ex.plan_export(self.ws, 'demo', self.rev, self.parent)
        target.write_text('keep')
        copy = self.ws.export_revision('demo', self.rev, self.parent)
        self.assertEqual(copy.name, target.name + '-2')
        self.assertEqual(target.read_text(), 'keep')

    def test_confirmation_destination_race_requires_reconfirmation(self):
        target = ex.plan_export(self.ws, 'demo', self.rev, self.parent)
        target.mkdir()
        with self.assertRaises(ex.DestinationChanged):
            self.ws.export_revision('demo', self.rev, self.parent, target)
        entries = list(self.parent.iterdir())
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].samefile(target))

    def test_publish_race_does_not_replace_empty_directory(self):
        target = ex.plan_export(self.ws, 'demo', self.rev, self.parent)
        publish = ex._publish
        def raced(stage, destination):
            destination.mkdir()
            publish(stage, destination)
        with patch.object(ex, '_publish', side_effect=raced), self.assertRaises(ex.DestinationChanged):
            self.ws.export_revision('demo', self.rev, self.parent, target)
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        entries = list(self.parent.iterdir())
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].samefile(target))

    def test_source_changes_are_not_published(self):
        copytree = ex.shutil.copytree
        def changed(*args, **kwargs):
            result = copytree(*args, **kwargs)
            (self.source / 'new.txt').write_text('changed')
            return result
        with patch.object(ex.shutil, 'copytree', side_effect=changed), self.assertRaisesRegex(ValueError, 'changed'):
            self.ws.export_revision('demo', self.rev, self.parent)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_copy_failure_cleans_only_stage(self):
        keep = self.parent / 'keep.txt'
        keep.write_text('keep')
        with patch.object(ex.shutil, 'copytree', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.ws.export_revision('demo', self.rev, self.parent)
        self.assertEqual(list(self.parent.iterdir()), [keep])

    def test_workspace_and_missing_destinations_rejected(self):
        for parent in (self.ws.root, self.source, self.parent / 'missing'):
            with self.assertRaises((ValueError, OSError)):
                self.ws.export_revision('demo', self.rev, parent)

    def test_source_symlink_rejected(self):
        link = self.source / 'linked'
        try:
            link.symlink_to(self.parent, target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        with self.assertRaisesRegex(ValueError, 'links or junctions'):
            self.ws.export_revision('demo', self.rev, self.parent)
        self.assertEqual(list(self.parent.iterdir()), [])


class ExportConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(busy=False, root=Mock(), ws=Mock(), status=Mock())
        self.target = Path('destination') / 'demo-revision'

    def test_cancel_never_exports(self):
        with patch('hornet.desktop.revision_drag.plan_export', return_value=self.target), patch('hornet.desktop.revision_drag.messagebox.askyesno', return_value=False) as ask:
            confirm_export(self.app, 'demo', 'revision', parent='destination')
        self.app.ws.export_revision.assert_not_called()
        self.assertIn(str(self.target), ask.call_args.args[1])
        self.assertEqual(ask.call_args.kwargs['default'], 'no')
        self.assertFalse(self.app._export_active)

    def test_race_shows_new_exact_destination_before_copy(self):
        second = Path(str(self.target) + '-2')
        self.app.ws.export_revision.side_effect = [ex.DestinationChanged(), second]
        with patch('hornet.desktop.revision_drag.plan_export', side_effect=[self.target, second]), patch('hornet.desktop.revision_drag.messagebox.askyesno', return_value=True) as ask, patch('hornet.desktop.revision_drag.messagebox.showinfo'):
            confirm_export(self.app, 'demo', 'revision', parent='destination')
        self.assertEqual(ask.call_count, 2)
        self.assertIn(str(second), ask.call_args.args[1])
        self.assertEqual(self.app.ws.export_revision.call_args.kwargs['destination'], second)

    def test_busy_does_not_open_picker(self):
        self.app.busy = True
        with patch('hornet.desktop.revision_drag.filedialog.askdirectory') as choose:
            confirm_export(self.app, 'demo', 'revision')
        choose.assert_not_called()

    def test_picker_cancel_never_exports(self):
        with patch('hornet.desktop.revision_drag.filedialog.askdirectory', return_value='') as choose, patch('hornet.desktop.revision_drag.messagebox.askyesno') as ask:
            confirm_export(self.app, 'demo', 'revision')
        choose.assert_called_once()
        ask.assert_not_called()
        self.app.ws.export_revision.assert_not_called()

    def test_picker_selection_requires_confirmation(self):
        with patch('hornet.desktop.revision_drag.filedialog.askdirectory', return_value='chosen'), patch('hornet.desktop.revision_drag.plan_export', return_value=self.target) as plan, patch('hornet.desktop.revision_drag.messagebox.askyesno', return_value=False) as ask:
            confirm_export(self.app, 'demo', 'revision')
        plan.assert_called_once_with(self.app.ws, 'demo', 'revision', 'chosen')
        ask.assert_called_once()
        self.app.ws.export_revision.assert_not_called()
