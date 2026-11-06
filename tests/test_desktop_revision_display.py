from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from hornet.desktop.automation import make_initialization_draft, accept_initialization, read_state
from hornet.desktop.revision_display import ensure_display, rename_revision, revision_parent
from hornet.desktop.workspace import Workspace


class RevisionDisplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ws=Workspace(Path(self.tmp.name)/'workspace')
        self.base=self.ws.create('project')
        make_initialization_draft(self.ws,'project',self.base,lambda p: {'summaries':{n:'Test' for n in p['files']}})
        self.accepted=accept_initialization(self.ws,'project')
        self.child=self.ws.fork('project',self.accepted)

    def test_colors_are_unique_and_stable_with_correct_parent_links(self):
        before=ensure_display(self.ws,'project')
        self.assertEqual(len({before[r]['color'] for r in (self.base,self.accepted,self.child)}),3)
        self.assertEqual(revision_parent(self.ws,'project',self.child),self.accepted)
        self.assertEqual(revision_parent(self.ws,'project',self.accepted),self.base)
        self.assertIsNone(revision_parent(self.ws,'project',self.base))
        rename_revision(self.ws,'project',self.child,'My anchor')
        new=self.ws.fork('project',self.accepted)
        after=ensure_display(Workspace(self.ws.root),'project')
        for rev in (self.base,self.accepted,self.child):
            self.assertEqual(before[rev]['color'],after[rev]['color'])
        self.assertEqual(after[self.child]['name'],'My anchor')
        self.assertNotIn(after[new]['color'],{before[r]['color'] for r in (self.base,self.accepted,self.child)})
        self.assertEqual(read_state(self.ws,'project')['accepted_revision'],self.accepted)

    def test_names_do_not_modify_revision_and_accepted_marker_is_reserved(self):
        root=self.ws.revision('project',self.accepted)
        revision_metadata=(root/'.hornet-meta/revision.json').read_bytes()
        blueprint=(root/'.blueprint').read_bytes()
        rename_revision(self.ws,'project',self.accepted,'Working baseline')
        self.assertEqual((root/'.hornet-meta/revision.json').read_bytes(),revision_metadata)
        self.assertEqual((root/'.blueprint').read_bytes(),blueprint)
        self.assertEqual(read_state(self.ws,'project')['accepted_revision'],self.accepted)
        with self.assertRaisesRegex(ValueError,'reserved'):
            rename_revision(self.ws,'project',self.child,'[AcCePtEd] fake')
        with self.assertRaisesRegex(ValueError,'single-line'):
            rename_revision(self.ws,'project',self.child,'two\nlines')
        rename_revision(self.ws,'project',self.accepted,'')
        self.assertEqual(ensure_display(self.ws,'project')[self.accepted]['name'],'')

    def test_parent_color_survives_moving_parent_to_trash(self):
        before=ensure_display(self.ws,'project')
        self.ws.trash('project',self.base)
        after=ensure_display(self.ws,'project')
        self.assertEqual(before[self.base]['color'],after[self.base]['color'])
        self.assertEqual(revision_parent(self.ws,'project',self.accepted),self.base)


if __name__=='__main__':unittest.main()
