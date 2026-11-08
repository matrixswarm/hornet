import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from hornet.desktop.workspace import Workspace, write_json, read_blueprint
from hornet.desktop.automation import make_initialization_draft, accept_initialization, inventory, accept_mission, read_state
from hornet.desktop import mission_acceptance


class MissionAcceptanceRecoveryTests(unittest.TestCase):
    def test_state_commit_then_record_failure_recovers_exact_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source'; source.mkdir(); (source/'main.py').write_bytes(b'VALUE = 1\n')
            ws=Workspace(Path(directory)/'workspace'); base=ws.create('demo',source=source)
            make_initialization_draft(ws,'demo',base,lambda p:{'summaries':{n:'Value' for n in p['files']},'blueprint':{'purpose':'Test'}})
            base=accept_initialization(ws,'demo')
            candidate=ws.fork('demo',base,{'main.py':'VALUE = 2\n'},'Change value')
            root=ws.revision('demo',candidate)
            record={'id':uuid.uuid4().hex,'project':'demo','state':'review','accepted_at_start':base,
                    'final_revision':candidate,'final_source_sha256':inventory(root),'final_blueprint':read_blueprint(root)}
            path=ws.project('demo')/'missions'/(record['id']+'.json'); path.parent.mkdir(exist_ok=True); write_json(path,record)
            before=ws.revisions('demo'); original=mission_acceptance.write_json
            def fail_record(target,value):
                if Path(target)==path: raise OSError('simulated record write failure')
                return original(target,value)
            with patch.object(mission_acceptance,'write_json',side_effect=fail_record):
                with self.assertRaises(OSError): accept_mission(ws,'demo',record)
            self.assertEqual(read_state(ws,'demo')['accepted_revision'],candidate)
            self.assertEqual(record['state'],'review')
            self.assertEqual(accept_mission(ws,'demo',record),candidate)
            self.assertEqual(ws.revisions('demo'),before)
            self.assertEqual(json.loads(path.read_text())['state'],'accepted')
            self.assertEqual(accept_mission(ws,'demo',record),candidate)
            tampered=dict(record,final_source_sha256={})
            with self.assertRaises(ValueError): accept_mission(ws,'demo',tampered)
