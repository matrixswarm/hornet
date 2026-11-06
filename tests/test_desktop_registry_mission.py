from tempfile import TemporaryDirectory
import unittest
from hornet.desktop.workspace import Workspace
from hornet.desktop.registry_mission import propose_with_tools


class RegistryMissionTests(unittest.TestCase):
    def test_atomic_editor_is_scoped_and_leaves_base_unchanged(self):
        with TemporaryDirectory() as tmp:
            ws=Workspace(tmp); revision=ws.create('demo'); root=ws.revision('demo',revision)
            before=(root/'main.py').read_text()
            context=ws.mission_context('demo',revision,['main.py'],'Change message')
            def model(instruction,payload):
                self.assertIn('files.text.atomic_edit',payload['tool_contracts'])
                return {'summary':'Change message','edits':[{'uri':'files.text.atomic_edit','arguments':{'path':'main.py','action':'replace','target_text':'print("Ready for your first mission")','new_text':'    print("Hello")'}}]}
            proposal,receipts=propose_with_tools(root,context,model)
            self.assertIn('print("Hello")',proposal['files']['main.py'])
            self.assertEqual((root/'main.py').read_text(),before)
            calls = [r for r in receipts if r['status'] == 'SUCCESS']
            self.assertEqual([r['uri'] for r in calls],['files.text.read','files.text.atomic_edit'])
            self.assertTrue(any(r['uri']=='files.text.atomic_edit' and r['status']=='INSPECTED' for r in receipts))

    def test_registry_reader_matches_crlf_and_utf8_bom_context(self):
        for prefix in (b'', b'\xef\xbb\xbf'):
            with self.subTest(bom=bool(prefix)), TemporaryDirectory() as tmp:
                ws=Workspace(tmp); revision=ws.create('demo'); root=ws.revision('demo',revision)
                path=root/'main.py'; original=prefix+b'VALUE = 1\r\nOTHER = 2\r\n'; path.write_bytes(original)
                context=ws.mission_context('demo',revision,['main.py'],'Change value')
                self.assertEqual(context['files']['main.py'], 'VALUE = 1\r\nOTHER = 2\r\n')
                def model(instruction,payload):
                    self.assertEqual(payload['files']['main.py'], context['files']['main.py'])
                    return {'summary':'Changed value','edits':[{'uri':'files.text.atomic_edit','arguments':{
                        'path':'main.py','action':'replace','target_text':'VALUE = 1','new_text':'VALUE = 3'}}]}
                proposal, receipts=propose_with_tools(root,context,model)
                self.assertIn('VALUE = 3',proposal['files']['main.py'])
                self.assertEqual(path.read_bytes(),original)
                self.assertTrue(any(r['uri']=='files.text.read' and r['status']=='SUCCESS' for r in receipts))

    def test_scoped_new_file_path_in_uri_uses_atomic_create(self):
        with TemporaryDirectory() as tmp:
            ws=Workspace(tmp); revision=ws.create('demo'); root=ws.revision('demo',revision)
            context=ws.mission_context('demo',revision,['main.py'],'Add file',['tests/new_test.py'])
            def model(instruction,payload):
                return {'summary':'new','edits':[{'uri':'tests/new_test.py','arguments':{'action':'create','new_text':'VALUE = 42\n'}}]}
            proposal, receipts=propose_with_tools(root,context,model)
            self.assertEqual(proposal['files']['tests/new_test.py'],'VALUE = 42\n')
            self.assertTrue(any(r['uri']=='files.text.atomic_edit' and r.get('path')=='tests/new_test.py' and r['status']=='SUCCESS' for r in receipts))
            self.assertFalse((root/'tests/new_test.py').exists())

    def test_preflight_requests_one_correction_before_editing(self):
        with TemporaryDirectory() as tmp:
            ws=Workspace(tmp); rev=ws.create('demo'); root=ws.revision('demo',rev)
            before=(root/'main.py').read_text()
            context=ws.mission_context('demo',rev,['main.py'],'Edit')
            calls=[]
            def model(instruction,payload):
                calls.append(payload)
                if len(calls)==1:
                    return {'summary':'bad','edits':[{'uri':'main.py','action':'replace','new_text':'bad'}]}
                self.assertIn('arguments object',payload['preflight_error'])
                self.assertIn('atomic_recipe',payload)
                self.assertEqual((root/'main.py').read_text(),before)
                return {'summary':'fixed','edits':[{'uri':'files.text.atomic_edit','arguments':{'path':'main.py','action':'replace','target_text':'print("Ready for your first mission")','new_text':'    print("Hello")'}}]}
            proposal,receipts=propose_with_tools(root,context,model)
            self.assertEqual(len(calls),2)
            self.assertIn('Hello',proposal['files']['main.py'])
            self.assertEqual((root/'main.py').read_text(),before)
            self.assertTrue(any(x['uri']=='hornet.plan_preflight' and x['status']=='RETRY' for x in receipts))

    def test_ambiguous_target_is_rehearsed_then_corrected(self):
        with TemporaryDirectory() as tmp:
            ws=Workspace(tmp); rev=ws.create('demo'); root=ws.revision('demo',rev)
            target=root/'main.py'; target.write_bytes(b'A = 1\nA = 1\nB = 2\n')
            context=ws.mission_context('demo',rev,['main.py'],'Edit'); calls=[]
            def model(instruction,payload):
                calls.append(payload)
                if len(calls)==1:
                    return {'summary':'bad','edits':[{'uri':'files.text.atomic_edit','arguments':{'path':'main.py','action':'replace','target_text':'A = 1','new_text':'A = 3'}}]}
                self.assertIn('found 2',payload['preflight_error'])
                return {'summary':'fixed','edits':[{'uri':'files.text.atomic_edit','arguments':{'path':'main.py','action':'replace','target_text':'A = 1\nB = 2','new_text':'A = 3\nB = 2'}}]}
            proposal, receipts=propose_with_tools(root,context,model)
            self.assertEqual(len(calls),2)
            self.assertEqual(proposal['files']['main.py'],'A = 1\nA = 3\nB = 2\n')
            self.assertEqual(target.read_text(),'A = 1\nA = 1\nB = 2\n')
            self.assertTrue(any(r['status']=='RETRY' and 'found 2' in r['message'] for r in receipts))

    def test_syntax_failure_gives_specific_guidance_and_can_be_corrected(self):
        with TemporaryDirectory() as tmp:
            ws=Workspace(tmp); rev=ws.create('demo'); root=ws.revision('demo',rev)
            target=root/'main.py'; original='def existing():\n    return True\n'; target.write_text(original)
            context=ws.mission_context('demo',rev,['main.py'],'Add a safe block'); calls=[]
            def model(instruction,payload):
                calls.append(payload)
                if len(calls)==1:
                    return {'summary':'invalid','edits':[{'uri':'files.text.atomic_edit','arguments':{
                        'path':'main.py','action':'insert','line_number':1,'new_text':'try:\n    value = 1'}}]}
                self.assertIn('resulting Python is invalid',payload['preflight_error'])
                self.assertIn('try/except/finally',payload['preflight_error'])
                self.assertNotIn('unique exact target',payload['preflight_error'])
                return {'summary':'complete','edits':[{'uri':'files.text.atomic_edit','arguments':{
                    'path':'main.py','action':'insert','line_number':1,'new_text':'try:\n    value = 1\nexcept Exception:\n    value = 0'}}]}
            proposal,receipts=propose_with_tools(root,context,model)
            self.assertEqual(len(calls),2)
            self.assertIn('except Exception:',proposal['files']['main.py'])
            self.assertEqual(target.read_text(),original)
            self.assertTrue(any(r['uri']=='hornet.plan_preflight' and r['status']=='RETRY' for r in receipts))

    def test_invalid_json_gets_one_correction(self):
        with TemporaryDirectory() as tmp:
            ws=Workspace(tmp); rev=ws.create('demo'); root=ws.revision('demo',rev)
            context=ws.mission_context('demo',rev,['main.py'],'Edit'); calls=[]
            def model(instruction,payload):
                calls.append(payload)
                if len(calls)==1:
                    raise ValueError('Model did not return a valid JSON response.')
                self.assertIn('valid JSON',payload['preflight_error'])
                return {'summary':'fixed','edits':[{'uri':'files.text.atomic_edit','arguments':{'path':'main.py','action':'replace','target_text':'print("Ready for your first mission")','new_text':'    print("Hello")'}}]}
            proposal,receipts=propose_with_tools(root,context,model)
            self.assertEqual(len(calls),2)
            self.assertIn('Hello',proposal['files']['main.py'])
            self.assertTrue(any(r['uri']=='hornet.plan_preflight' and r['status']=='RETRY' for r in receipts))

    def test_preflight_stops_after_one_failed_correction(self):
        with TemporaryDirectory() as tmp:
            ws=Workspace(tmp); rev=ws.create('demo'); root=ws.revision('demo',rev)
            context=ws.mission_context('demo',rev,['main.py'],'Edit'); before=(root/'main.py').read_text(); calls=[]
            def model(instruction,payload):
                calls.append(1)
                return {'summary':'bad','edits':[{'uri':'files.text.atomic_edit','arguments':{'path':'../bad.py','action':'create','new_text':'x'}}]}
            with self.assertRaisesRegex(ValueError,'corrected plan also failed preflight'):
                propose_with_tools(root,context,model)
            self.assertEqual(len(calls),2)
            self.assertEqual((root/'main.py').read_text(),before)

    def test_tool_cannot_write_outside_scope(self):
        with TemporaryDirectory() as tmp:
            ws=Workspace(tmp); revision=ws.create('demo'); root=ws.revision('demo',revision)
            context=ws.mission_context('demo',revision,['main.py'],'Change message')
            def model(instruction,payload):
                return {'summary':'escape','edits':[{'uri':'files.text.atomic_edit','arguments':{'path':'../escape.py','action':'create','new_text':'bad'}}]}
            with self.assertRaisesRegex(ValueError,'out-of-scope'):
                propose_with_tools(root,context,model)

    def test_line_editor_cannot_bypass_atomic_policy(self):
        with TemporaryDirectory() as tmp:
            ws=Workspace(tmp); revision=ws.create('demo'); root=ws.revision('demo',revision)
            context=ws.mission_context('demo',revision,['main.py'],'Change message')
            def model(instruction,payload):
                self.assertNotIn('files.text.lines',payload['tool_contracts'])
                return {'summary':'edit','edits':[{'uri':'files.text.lines','arguments':{'path':'main.py','action':'replace_line','line':1,'text':'bad'}}]}
            with self.assertRaisesRegex(ValueError,'unsupported registry tool'):
                propose_with_tools(root,context,model)

    def test_atomic_create_then_delete_line_and_no_overwrite(self):
        with TemporaryDirectory() as tmp:
            ws=Workspace(tmp); revision=ws.create('demo'); root=ws.revision('demo',revision)
            context=ws.mission_context('demo',revision,[],'Create file',['component/new.py'])
            def model(instruction,payload):
                return {'summary':'create','edits':[
                    {'uri':'files.text.atomic_edit','arguments':{'path':'component/new.py','action':'create','new_text':'VALUE = 1\nREMOVE = 2\n'}},
                    {'uri':'files.text.atomic_edit','arguments':{'path':'component/new.py','action':'delete','target_text':'REMOVE = 2','new_text':''}}]}
            proposal,receipts=propose_with_tools(root,context,model)
            self.assertEqual(proposal['files']['component/new.py'],'VALUE = 1\n')
            self.assertEqual(sum(r['uri']=='files.text.atomic_edit' and r['status']=='SUCCESS' for r in receipts),2)
            def overwrite(instruction,payload):
                return {'summary':'overwrite','edits':[{'uri':'files.text.atomic_edit','arguments':{'path':'main.py','action':'create','new_text':'bad'}}]}
            existing=ws.mission_context('demo',revision,['main.py'],'Try overwrite')
            with self.assertRaisesRegex(ValueError,'outside the new-file scope'):
                propose_with_tools(root,existing,overwrite)

    def test_scoped_path_in_uri_is_normalized_only_for_atomic_operation(self):
        with TemporaryDirectory() as tmp:
            ws=Workspace(tmp); revision=ws.create('demo'); root=ws.revision('demo',revision)
            context=ws.mission_context('demo',revision,['main.py'],'Change message')
            def model(instruction,payload):
                return {'summary':'Change','edits':[{'uri':'main.py','arguments':{'action':'replace','target_text':'print("Ready for your first mission")','new_text':'    print("Hello")'}}]}
            proposal,receipts=propose_with_tools(root,context,model)
            self.assertIn('print("Hello")',proposal['files']['main.py'])
            self.assertTrue(any(r['uri']=='files.text.atomic_edit' and r['status']=='SUCCESS' for r in receipts))


if __name__=='__main__': unittest.main()
