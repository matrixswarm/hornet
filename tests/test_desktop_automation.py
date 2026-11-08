import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hornet.desktop.workspace import Workspace
from hornet.desktop.automation import (read_state, require_ready, make_initialization_draft,
                                       accept_initialization, prepare_mission, accept_mission, diagnose_mission, request_json)


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(self.tmp.name)
        self.base = self.ws.create('demo')

    @staticmethod
    def describe(payload):
        return {'summaries': {name: 'Purpose of '+name for name in payload['files']},
                'blueprint': {'purpose': 'A test application', 'architecture': 'Small application'}}

    def initialize(self):
        draft = make_initialization_draft(self.ws, 'demo', self.base, self.describe)
        self.assertEqual(draft['unreviewed'], [])
        return accept_initialization(self.ws, 'demo')

    @patch('hornet.desktop.automation.model_describer')
    @patch('hornet.desktop.automation.request_json')
    def test_mission_preserves_utf8_bom_through_registry_and_final_revision(self, request, describer):
        original = b'\xef\xbb\xbfVALUE = 1\r\nOTHER = 2\r\n'
        (self.ws.revision('demo', self.base) / 'main.py').write_bytes(original)
        ready = self.initialize()
        request.side_effect = [
            {'existing_files': ['main.py'], 'new_files': [], 'explanation': 'Change value'},
            {'summary': 'Changed value', 'edits': [{'uri': 'files.text.atomic_edit', 'arguments': {
                'path': 'main.py', 'action': 'replace', 'target_text': 'VALUE = 1', 'new_text': 'VALUE = 3'}}]},
        ]
        describer.return_value = self.describe
        record = prepare_mission(self.ws, 'demo', ready, 'Change value', 'http://localhost:1234/v1', 'model')
        accepted = accept_mission(self.ws, 'demo', record)
        self.assertEqual((self.ws.revision('demo', accepted) / 'main.py').read_bytes(),
                         b'\xef\xbb\xbfVALUE = 3\r\nOTHER = 2\r\n')
        self.assertEqual((self.ws.revision('demo', ready) / 'main.py').read_bytes(), original)

    def test_initialize_draft_requires_explicit_accept_and_binds_hashes(self):
        with self.assertRaisesRegex(ValueError, 'Initialize'):
            require_ready(self.ws, 'demo')
        draft = make_initialization_draft(self.ws, 'demo', self.base, self.describe)
        self.assertEqual(read_state(self.ws,'demo')['status'], 'uninitialized')
        self.assertIn('main.py', draft['summaries'])
        ready = accept_initialization(self.ws, 'demo')
        self.assertEqual(require_ready(self.ws,'demo')['accepted_revision'], ready)
        root = self.ws.revision('demo', ready)
        shadow = json.loads((root/'.hornet-meta/shadows.json').read_text())['main.py']
        self.assertEqual(shadow['summary'], 'Purpose of main.py')
        self.assertEqual(shadow['review'], 'agent-drafted; source hash recorded')
        self.assertTrue((root/'.hornet-meta/shadows/main.py.shadow').exists())
        self.assertEqual(json.loads((self.ws.revision('demo',self.base)/'.blueprint').read_text())['status'], 'starter structure; purpose needs human review')

    def test_initialization_refuses_changed_sources(self):
        make_initialization_draft(self.ws, 'demo', self.base, self.describe)
        (self.ws.revision('demo',self.base)/'main.py').write_text('print(42)\n')
        with self.assertRaisesRegex(ValueError, 'changed after initialization'):
            accept_initialization(self.ws, 'demo')
        self.assertEqual(read_state(self.ws,'demo')['status'], 'uninitialized')

    def test_copy_and_mission_require_initialized_revision(self):
        original_revisions = self.ws.revisions('demo')
        make_initialization_draft(self.ws, 'demo', self.base, self.describe)
        with self.assertRaisesRegex(ValueError, 'accept'):
            self.ws.fork('demo', self.base)
        with self.assertRaisesRegex(ValueError, 'accept'):
            prepare_mission(self.ws, 'demo', self.base, 'Change', 'http://localhost:1234/v1', 'model')
        self.assertEqual(self.ws.revisions('demo'), original_revisions)
        ready = accept_initialization(self.ws, 'demo')
        with self.assertRaisesRegex(ValueError, 'revision was not initialized'):
            self.ws.fork('demo', self.base)
        with self.assertRaisesRegex(ValueError, 'revision was not initialized'):
            prepare_mission(self.ws, 'demo', self.base, 'Change', 'http://localhost:1234/v1', 'model')
        self.assertEqual(len(self.ws.revisions('demo')), 2)
        cloned = self.ws.fork('demo', ready)
        self.assertEqual(require_ready(self.ws, 'demo', cloned)['accepted_revision'], ready)
        (self.ws.revision('demo', cloned) / '.hornet-meta' / 'initialization.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'revision was not initialized'):
            self.ws.fork('demo', cloned)

    def test_buttons_follow_selected_revision_initialization(self):
        from types import SimpleNamespace
        from hornet.desktop.app import App
        class Button:
            def configure(self, **kwargs):
                self.state = kwargs['state']
        app = App.__new__(App)
        app.ws, app.project, app.revision, app.busy = self.ws, 'demo', self.base, False
        app.pending_initialization = app.pending_mission = None
        for name in ('diagnose_button', 'auto_button', 'run_button', 'copy_button',
                     'blueprint_button', 'trash_button', 'delete_project_button', 'init_button', 'accept_map',
                     'edit_map', 'accept_mission_button'):
            setattr(app, name, Button())
        app.update_gate()
        self.assertEqual(app.copy_button.state, 'disabled')
        self.assertEqual(app.auto_button.state, 'disabled')
        self.assertEqual(app.init_button.state, 'normal')
        ready = self.initialize()
        app.update_gate()
        self.assertEqual(app.copy_button.state, 'disabled')
        self.assertEqual(app.auto_button.state, 'disabled')
        self.assertEqual(app.init_button.state, 'disabled')
        app.revision = ready
        app.update_gate()
        self.assertEqual(app.copy_button.state, 'normal')
        self.assertEqual(app.auto_button.state, 'disabled')
        from hornet.desktop.working_revision import create_working_revision
        app.revision = create_working_revision(self.ws, 'demo', ready)
        app.update_gate()
        self.assertEqual(app.auto_button.state, 'normal')

    @patch('hornet.desktop.app.messagebox.askyesno')
    def test_delete_confirmation_and_busy_protect_project(self, confirm):
        from unittest.mock import MagicMock
        from hornet.desktop.app import App
        app = App.__new__(App)
        app.root = MagicMock()
        app.ws, app.project, app.revision, app.busy = self.ws, 'demo', self.base, False
        app.pending = app.pending_initialization = app.pending_mission = None
        app.refresh, app.update_gate, app.show = MagicMock(), MagicMock(), MagicMock()
        app.files, app.status = MagicMock(), MagicMock()
        app.views = {'Source': None}
        app.guard = lambda action: action()
        confirm.return_value = False
        app.delete_project()
        self.assertIn('demo', self.ws.list_projects())
        app.busy = True
        confirm.return_value = True
        app.delete_project()
        self.assertEqual(confirm.call_count, 1)
        self.assertIn('demo', self.ws.list_projects())
        app.busy = False
        app.delete_project()
        self.assertNotIn('demo', self.ws.list_projects())
        self.assertIsNone(app.project)
        self.assertEqual(len(list((self.ws.root / 'project-trash').iterdir())), 1)

    def test_review_pending_prefers_selected_candidate_and_skips_trashed_candidate(self):
        from unittest.mock import MagicMock
        from hornet.desktop.app import App
        from hornet.desktop.workspace import write_json
        ready = self.initialize()
        storage = self.ws.fork('demo', ready)
        offline = self.ws.fork('demo', ready)
        directory = self.ws.project('demo')/'missions'; directory.mkdir()
        first = {'id':'a'*32,'state':'review','project':'demo','base_revision':ready,'final_revision':storage}
        second = {'id':'b'*32,'state':'review','project':'demo','base_revision':ready,'final_revision':offline}
        write_json(directory/('a'*32+'.json'),first); write_json(directory/('b'*32+'.json'),second)
        app=App.__new__(App); app.ws=self.ws; app.project='demo'; app.revision=storage; app.busy=False
        app.guard=lambda action: action()
        app.show_mission=MagicMock(); app.update_gate=MagicMock(); app.show_diagnosis=MagicMock()
        app.review_pending()
        self.assertEqual(app.pending_mission['id'],'a'*32)
        self.assertEqual(app.show_mission.call_args.args[0]['id'],'a'*32)
        with self.assertRaisesRegex(ValueError, 'required'):
            self.ws.trash('demo',offline)
        # Simulate an older externally moved revision. New UI Trash correctly
        # refuses to remove saved-review dependencies.
        trash = self.ws.project('demo')/'trash'; trash.mkdir(exist_ok=True)
        self.ws.revision('demo',offline).rename(trash/offline)
        app.revision=ready
        app.review_pending()
        self.assertEqual(app.pending_mission['id'],'a'*32)

    @patch('hornet.desktop.app.messagebox.askyesno')
    def test_context_trash_targets_clicked_revision_and_protects_accepted(self, confirm):
        from unittest.mock import MagicMock
        from hornet.desktop.app import App
        ready=self.initialize(); candidate=self.ws.fork('demo',ready)
        app=App.__new__(App); app.ws=self.ws; app.root=MagicMock(); app.busy=False
        app.project='demo'; app.revision=ready; app.pending_mission=None
        app.guard=lambda action: action(); app.refresh=MagicMock(); app.update_gate=MagicMock(); app.status=MagicMock()
        confirm.return_value=False
        app.trash_revision('demo',candidate)
        self.assertIn(candidate,self.ws.revisions('demo'))
        confirm.return_value=True
        app.trash_revision('demo',candidate)
        self.assertNotIn(candidate,self.ws.revisions('demo'))
        self.assertTrue((self.ws.project('demo')/'trash'/candidate).is_dir())
        self.assertIn(ready,self.ws.revisions('demo'))
        with self.assertRaisesRegex(ValueError,'accepted revision'):
            app.trash_revision('demo',ready)

    @patch('urllib.request.build_opener')
    def test_model_json_fences_and_invalid_response(self, build_opener):
        from unittest.mock import MagicMock
        response = MagicMock()
        build_opener.return_value.open.return_value.__enter__.return_value = response
        def give(content):
            response.read.return_value = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}).encode()
            return request_json('http://localhost:1234/v1', 'model', 'instruction', {})
        for content in ('```json\n{"ok": true}```', '```json\n{"ok": true}\n```', '{"ok": true}'):
            self.assertEqual(give(content), {'ok': True})
        with self.assertRaisesRegex(ValueError, 'valid JSON'):
            give('```json\n{"ok": }```')
        with self.assertRaisesRegex(ValueError, 'valid JSON'):
            give('prefix ```json\n{"ok": true}```')
        with self.assertRaisesRegex(ValueError, 'repeats the JSON key'):
            give('{"edits":[{"uri":"first","uri":"second"}]}')

    @patch('urllib.request.build_opener')
    def test_structured_edit_request_and_local_validation(self, build_opener):
        from unittest.mock import MagicMock
        from hornet.desktop.automation import EDIT_PLAN_SCHEMA
        response = MagicMock()
        opener = build_opener.return_value
        opener.open.return_value.__enter__.return_value = response
        value = {'summary': 'ok', 'edits': [{'uri':'files.text.atomic_edit', 'arguments': {'path':'main.py', 'action':'replace', 'target_text':'old', 'new_text':'new'}}]}
        response.read.return_value = json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}).encode()
        self.assertEqual(request_json('http://localhost:1234/v1','model','instruction',{},response_schema=EDIT_PLAN_SCHEMA),value)
        body = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(body['response_format']['type'], 'json_schema')
        self.assertEqual(body['response_format']['json_schema']['schema'], EDIT_PLAN_SCHEMA)
        value['edits'][0]['uri'] = 'wrong.tool'
        response.read.return_value = json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}).encode()
        with self.assertRaisesRegex(ValueError,'invalid structured edit plan'):
            request_json('http://localhost:1234/v1','model','instruction',{},response_schema=EDIT_PLAN_SCHEMA)

    @patch('hornet.desktop.automation.request_json')
    def test_diagnosis_is_read_only_and_persists_report(self, request):
        ready = self.initialize()
        revisions = self.ws.revisions('demo')
        request.return_value = {'diagnosis': 'Missing display', 'evidence': 'Tk error', 'recommended_next_steps': 'Use a display'}
        result = diagnose_mission(self.ws, 'demo', ready, 'Diagnose', 'http://localhost:1234/v1', 'model', ['main.py'], {'stderr': 'Tk error'})
        self.assertEqual(result['state'], 'diagnosis_complete')
        self.assertEqual(self.ws.revisions('demo'), revisions)
        self.assertEqual(require_ready(self.ws, 'demo')['accepted_revision'], ready)
        self.assertEqual(result['tool_receipts'][0]['uri'], 'files.text.read')
        payload = request.call_args.args[3]
        self.assertEqual(payload['run_diagnostic']['stderr'], 'Tk error')
        self.assertIn('main.py', payload['files'])
        self.assertEqual(json.loads((self.ws.project('demo') / 'missions' / (result['id'] + '.json')).read_text())['report']['diagnosis'], 'Missing display')

    @patch('hornet.desktop.automation.request_json')
    def test_failed_diagnosis_preserves_revision(self, request):
        ready = self.initialize()
        revisions = self.ws.revisions('demo')
        request.side_effect = ValueError('Model reached its output limit')
        with self.assertRaisesRegex(ValueError, 'output limit'):
            diagnose_mission(self.ws, 'demo', ready, 'Diagnose', 'http://localhost:1234/v1', 'model')
        self.assertEqual(self.ws.revisions('demo'), revisions)
        records = list((self.ws.project('demo') / 'missions').glob('*.json'))
        self.assertEqual(json.loads(records[0].read_text())['state'], 'failed')

    @patch('hornet.desktop.vm_runner.run_python')
    @patch('hornet.desktop.registry_mission.propose_with_tools')
    @patch('hornet.desktop.automation.model_describer')
    @patch('hornet.desktop.automation.request_json')
    def test_candidate_is_not_accepted_until_review(self, plan, describe, proposal, run):
        ready = self.initialize()
        plan.return_value = {'existing_files':['main.py'],'new_files':[],'explanation':'Update'}
        proposal.return_value = ({'summary':'Update','files':{'main.py':'print(42)\n'}}, [{'uri':'files.text.atomic_edit'}])
        describe.return_value = self.describe
        run.return_value = {'exit_code':0,'timed_out':False,'stdout':'OK','stderr':''}
        result = prepare_mission(self.ws,'demo',ready,'Update','http://localhost:1234/v1','model','python3 -m unittest')
        self.assertEqual(result['state'],'review')
        self.assertEqual(require_ready(self.ws,'demo')['accepted_revision'],ready)
        candidate = self.ws.revision('demo',result['final_revision'])
        self.assertEqual((candidate/'main.py').read_text(),'print(42)\n')
        self.assertEqual(json.loads((candidate/'.hornet-meta/shadows.json').read_text())['main.py']['summary'],'Purpose of main.py')
        accept_mission(self.ws,'demo',result)
        self.assertEqual(require_ready(self.ws,'demo')['accepted_revision'],result['final_revision'])

    @patch('hornet.desktop.vm_runner.run_python')
    @patch('hornet.desktop.registry_mission.propose_with_tools')
    @patch('hornet.desktop.automation.model_describer')
    @patch('hornet.desktop.automation.request_json')
    def test_failed_check_gets_one_repair_and_never_auto_accepts(self, plan, describe, proposal, run):
        ready=self.initialize()
        plan.return_value={'existing_files':['main.py'],'new_files':[]}
        proposal.side_effect=[({'summary':'First','files':{'main.py':'print(1)\n'}}, []), ({'summary':'Repair','files':{'main.py':'print(2)\n'}}, [])]
        describe.return_value=self.describe
        run.return_value={'exit_code':1,'timed_out':False,'stdout':'','stderr':'failure'}
        result=prepare_mission(self.ws,'demo',ready,'Update','http://localhost:1234/v1','model','python3 -m unittest')
        self.assertEqual(result['repairs'],1)
        self.assertEqual(run.call_count,2)
        with self.assertRaisesRegex(ValueError,'did not pass'):
            accept_mission(self.ws,'demo',result)
        self.assertEqual(require_ready(self.ws,'demo')['accepted_revision'],ready)

    @patch('hornet.desktop.automation.request_json')
    def test_uninitialized_project_cannot_start_mission(self, plan):
        with self.assertRaisesRegex(ValueError,'Initialize'):
            prepare_mission(self.ws,'demo',self.base,'Change','http://localhost:1234/v1','model')
        plan.assert_not_called()

    @patch('hornet.desktop.vm_runner.run_python')
    @patch('hornet.desktop.automation.model_describer')
    @patch('hornet.desktop.automation.request_json')
    def test_end_to_end_uses_real_scoped_atomic_editor(self, model, describe, run):
        ready = self.initialize()
        def respond(endpoint, name, instruction, payload, **kwargs):
            if 'Choose only the files' in instruction:
                return {'existing_files': ['main.py'], 'new_files': [], 'explanation': 'Change greeting'}
            return {'summary': 'Changed greeting', 'edits': [{'uri':'files.text.atomic_edit', 'arguments': {
                'path':'main.py', 'action':'replace', 'target_text':'print("Ready for your first mission")',
                'new_text':'    print("Hello")'}}]}
        model.side_effect = respond
        describe.return_value = self.describe
        run.return_value = {'exit_code':0,'timed_out':False,'stdout':'OK','stderr':''}
        result = prepare_mission(self.ws,'demo',ready,'Change greeting','http://localhost:1234/v1','model','python3 -m unittest')
        self.assertTrue(any(r['uri']=='files.text.atomic_edit' and r['status']=='SUCCESS' for r in result['tool_receipts']))
        self.assertIn('print("Hello")',(self.ws.revision('demo',result['final_revision'])/'main.py').read_text())
        self.assertEqual(require_ready(self.ws,'demo')['accepted_revision'],ready)
        accept_mission(self.ws,'demo',result)

    def test_rejects_initialization_when_model_omits_sources_and_reports_them(self):
        draft = make_initialization_draft(self.ws,'demo',self.base,lambda payload: {'summaries': {}})
        self.assertIn('main.py',draft['unreviewed'])

    @patch('hornet.desktop.vm_runner.run_python')
    @patch('hornet.desktop.registry_mission.propose_with_tools')
    @patch('hornet.desktop.automation.model_describer')
    @patch('hornet.desktop.automation.request_json')
    def test_zero_tests_does_not_count_as_success(self, plan, describe, proposal, run):
        ready=self.initialize()
        plan.return_value={'existing_files':['main.py'],'new_files':[]}
        proposal.side_effect=[({'summary':'First','files':{'main.py':'print(1)\n'}}, []),
                              ({'summary':'Repair','files':{'main.py':'print(2)\n'}}, [])]
        describe.return_value=self.describe
        run.return_value={'exit_code':0,'timed_out':False,'stdout':'','stderr':'Ran 0 tests in 0.000s\nOK'}
        result=prepare_mission(self.ws,'demo',ready,'Update','http://localhost:1234/v1','model','python3 -m unittest discover -s tests')
        self.assertFalse(result['checks_passed'])
        with self.assertRaisesRegex(ValueError,'did not pass'):
            accept_mission(self.ws,'demo',result)


if __name__=='__main__':
    unittest.main()
