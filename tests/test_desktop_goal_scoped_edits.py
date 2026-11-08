import tempfile
import unittest
from hornet.desktop.workspace import Workspace
from hornet.desktop.registry_mission import propose_with_tools


class GoalScopedEditsTests(unittest.TestCase):
    def context(self, ws, revision):
        context = ws.mission_context('demo', revision, ['main.py'], 'Change value in two small steps')
        context['goal_decomposition'] = [
            {'id':'root','parent':None,'goal':'Change value','files':['main.py'],'success_criteria':'Value is 3','depends_on':[]},
            {'id':'first','parent':'root','goal':'Prepare value','files':['main.py'],'success_criteria':'Value is 2','depends_on':[]},
            {'id':'second','parent':'root','goal':'Finalize value','files':['main.py'],'success_criteria':'Value is 3','depends_on':['first']}]
        return context

    def test_leaf_scopes_dependency_order_and_receipt_links(self):
        with tempfile.TemporaryDirectory() as directory:
            ws=Workspace(directory); revision=ws.create('demo'); root=ws.revision('demo',revision)
            (root/'main.py').write_text('VALUE = 1\n',encoding='utf-8')
            context=self.context(ws,revision)
            def model(instruction,payload):
                self.assertEqual(payload['goal_decomposition'],context['goal_decomposition'])
                self.assertIn('goal_id',instruction)
                return {'summary':'Two scoped leaves','edits':[
                    {'goal_id':'first','uri':'files.text.atomic_edit','arguments':{'path':'main.py','action':'replace','target_text':'VALUE = 1','new_text':'VALUE = 2'}},
                    {'goal_id':'second','uri':'files.text.atomic_edit','arguments':{'path':'main.py','action':'replace','target_text':'VALUE = 2','new_text':'VALUE = 3'}}]}
            proposal,receipts=propose_with_tools(root,context,model)
            self.assertEqual(proposal['goal_links'],{'main.py':['first','second']})
            self.assertEqual(proposal['files']['main.py'].replace('\r\n','\n'),'VALUE = 3\n')
            self.assertEqual((root/'main.py').read_text(),'VALUE = 1\n')
            self.assertEqual([r['goal_id'] for r in receipts if r['uri']=='files.text.atomic_edit' and r['status']=='SUCCESS'],['first','second'])

    def test_only_active_leaf_can_edit_even_when_other_leaf_scope_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            ws=Workspace(directory); revision=ws.create('demo'); root=ws.revision('demo',revision)
            (root/'main.py').write_bytes(b'VALUE = 1\n')
            context=self.context(ws,revision)
            context['active_goal_id']='first'
            context['prior_goal_edits']=['first']
            def model(*args):
                return {'summary':'Wrong leaf','edits':[{'goal_id':'second','uri':'files.text.atomic_edit','arguments':{
                    'path':'main.py','action':'replace','target_text':'VALUE = 1','new_text':'VALUE = 3'}}]}
            with self.assertRaises(ValueError): propose_with_tools(root,context,model)
            self.assertEqual((root/'main.py').read_bytes(),b'VALUE = 1\n')

    def test_unapproved_leaf_or_missing_prerequisite_never_edits_base(self):
        for leaf in ('root','outside','second'):
            with self.subTest(leaf=leaf), tempfile.TemporaryDirectory() as directory:
                ws=Workspace(directory); revision=ws.create('demo'); root=ws.revision('demo',revision)
                (root/'main.py').write_text('VALUE = 1\n',encoding='utf-8')
                context=self.context(ws,revision)
                def model(*args):
                    return {'summary':'Invalid attribution','edits':[{'goal_id':leaf,'uri':'files.text.atomic_edit','arguments':{
                        'path':'main.py','action':'replace','target_text':'VALUE = 1','new_text':'VALUE = 3'}}]}
                with self.assertRaises(ValueError):
                    propose_with_tools(root,context,model)
                self.assertEqual((root/'main.py').read_text(),'VALUE = 1\n')

    def test_correction_preserves_active_goal_examples_and_required_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            ws=Workspace(directory);revision=ws.create('demo');root=ws.revision('demo',revision)
            (root/'main.py').write_bytes(b'VALUE = 1\n')
            context=self.context(ws,revision);context['active_goal_id']='first'
            calls=[]
            def model(instruction,payload):
                calls.append(payload)
                for example in payload['atomic_recipe']['examples']:
                    self.assertEqual(example['goal_id'],'first')
                self.assertIn('assigns every edit to active goal "first"',instruction)
                edit={'goal_id':'first','uri':'files.text.atomic_edit','arguments':{
                    'path':'main.py','action':'replace','new_text':'VALUE = 2'}}
                if len(calls)==2:
                    self.assertIn('target_text',payload['preflight_error'])
                    self.assertIn('Hornet supplies its ID',instruction)
                    edit['arguments']['target_text']='VALUE = 1'
                    del edit['goal_id']
                return {'summary':'Change value','edits':[edit]}
            proposal,receipts=propose_with_tools(root,context,model)
            self.assertEqual(len(calls),2)
            self.assertEqual(proposal['goal_links'],{'main.py':['first']})
            self.assertEqual((root/'main.py').read_bytes(),b'VALUE = 1\n')
