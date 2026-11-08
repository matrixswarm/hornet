import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hornet.desktop.workspace import Workspace
from hornet.desktop.automation import make_initialization_draft, accept_initialization, read_state
from hornet.desktop import automation


class UnderstandingGoalRecoveryTests(unittest.TestCase):
    def setup_workspace(self, directory):
        source=Path(directory)/'source'; source.mkdir(); (source/'main.py').write_text('VALUE = 1\n',encoding='utf-8')
        ws=Workspace(Path(directory)/'workspace'); revision=ws.create('demo',source=source)
        make_initialization_draft(ws,'demo',revision,lambda p:{'summaries':{n:'One value' for n in p['files']},'blueprint':{'purpose':'Test'}})
        return ws,revision

    def test_acceptance_after_publication_before_state_write_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            ws,base=self.setup_workspace(directory)
            original=automation.write_json
            def fail_state(path,value):
                if Path(path).name=='automation.json':
                    raise OSError('simulated state write failure')
                return original(path,value)
            with patch.object(automation,'write_json',side_effect=fail_state):
                with self.assertRaises(OSError): accept_initialization(ws,'demo')
            before=ws.revisions('demo')
            accepted=accept_initialization(ws,'demo')
            self.assertEqual(ws.revisions('demo'),before)
            self.assertEqual(read_state(ws,'demo')['accepted_revision'],accepted)

    def test_acceptance_after_state_write_before_draft_cleanup_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            ws,base=self.setup_workspace(directory)
            draft=ws.project('demo')/'initialization-draft.json'
            original=Path.unlink
            def fail_cleanup(path,*args,**kwargs):
                if path==draft: raise OSError('simulated cleanup failure')
                return original(path,*args,**kwargs)
            with patch.object(Path,'unlink',fail_cleanup):
                with self.assertRaises(OSError): accept_initialization(ws,'demo')
            before=ws.revisions('demo')
            accepted=accept_initialization(ws,'demo')
            self.assertEqual(ws.revisions('demo'),before)
            self.assertFalse(draft.exists())
            self.assertEqual(read_state(ws,'demo')['accepted_revision'],accepted)

    def test_explicit_restart_gets_new_goal_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            ws,base=self.setup_workspace(directory)
            path=ws.project('demo')/'initialization-draft.json'
            previous=json.loads(path.read_text())
            next_=make_initialization_draft(ws,'demo',base,
                lambda p:{'summaries':{n:'Different valid explanation' for n in p['files']},'blueprint':{'purpose':'Test'}},resume=False)
            self.assertNotEqual(previous['progress_run_id'],next_['progress_run_id'])
            self.assertEqual(len(list((ws.project('demo')/'working_memory'/'understanding').iterdir())),2)
