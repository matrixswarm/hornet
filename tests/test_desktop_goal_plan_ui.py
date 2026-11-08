import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from hornet.desktop.app import App
from hornet.desktop.workspace import Workspace
from hornet.desktop.working_revision import create_working_revision
from hornet.desktop.automation import make_initialization_draft, accept_initialization


class GoalPlanUiTests(unittest.TestCase):
    def test_plan_review_approval_execution_and_final_acceptance(self):
        self._exercise_plan_review()

    def test_adopted_legacy_plan_resumes_and_approves_from_working_copy(self):
        self._exercise_plan_review(legacy=True)

    def _exercise_plan_review(self, legacy=False):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source'; source.mkdir(); (source/'main.py').write_bytes(b'VALUE = 1\n')
            ws=Workspace(Path(directory)/'workspace'); base=ws.create('demo',source=source)
            make_initialization_draft(ws,'demo',base,lambda p:{'summaries':{n:'Value module' for n in p['files']},'blueprint':{'purpose':'Test'}})
            base=accept_initialization(ws,'demo')
            original_base = base
            if not legacy:
                base=create_working_revision(ws,'demo',base)
            root=tk.Tk(); root.withdraw()
            try:
                app=App(root,ws); app.refresh('demo',base); root.update()
                app.endpoint.set('http://localhost'); app.model.set('model')
                app.goal.insert('1.0','Set VALUE to 2')
                plan={'existing_files':['main.py'],'new_files':[],'explanation':'One small action','goals':[
                    {'id':'value','parent':None,'goal':'Set VALUE to 2','files':['main.py'],
                     'success_criteria':'main.py contains VALUE = 2','depends_on':[]}]}
                if legacy:
                    from hornet.desktop.automation import prepare_mission
                    with patch('hornet.desktop.automation.request_json', return_value=plan):
                        prepare_mission(ws, 'demo', original_base, 'Set VALUE to 2',
                                        app.endpoint.get(), app.model.get(),
                                        require_plan_confirmation=True, require_goal_decomposition=True)
                    app.copy_revision()
                    root.update()
                    base = app.revision
                    self.assertNotEqual(base, original_base)
                    self.assertEqual(app.selected_mission_base(), original_base)
                before=ws.revisions('demo')
                with patch('hornet.desktop.automation.request_json',return_value=plan), \
                     patch.object(app,'launch_query',side_effect=lambda worker:worker()), \
                     patch('hornet.desktop.app.messagebox.askyesno', return_value=True):
                    if legacy:
                        app.resume_saved_mission()
                    else:
                        app.run_mission()
                    for _ in range(50): app.poll()
                self.assertEqual(ws.revisions('demo'),before)
                self.assertEqual(app.pending_mission['state'],'plan_review')
                self.assertEqual(app.approve_plan_button.winfo_manager(),'pack')
                self.assertFalse(app.auto_button.winfo_manager())
                self.assertIn('No source edits',app.views['Proposal'].get('1.0','end'))
                if legacy:
                    app.counter_saved_plan()
                    dialogs = [child for child in root.winfo_children() if isinstance(child, tk.Toplevel)]
                    self.assertEqual(len(dialogs), 1)
                    self.assertEqual(dialogs[0].title(), 'Counter agent plan')
                    dialogs[0].destroy()
                def model(endpoint,name,instruction,payload,response_schema=None):
                    if 'tool_contracts' in payload:
                        return {'summary':'Set value','edits':[{'goal_id':'value','uri':'files.text.atomic_edit',
                            'arguments':{'path':'main.py','action':'replace','target_text':'VALUE = 1','new_text':'VALUE = 2'}}]}
                    return {'summaries':{name:'Updated value' for name in payload['files']},'blueprint':{'purpose':'Test'}}
                with patch('hornet.desktop.automation.request_json',side_effect=model), \
                     patch.object(app,'launch_query',side_effect=lambda worker:worker()):
                    app.approve_plan_button.invoke()
                    # Consume phase events before the terminal result.
                    for _ in range(50): app.poll()
                    root.update()
                self.assertEqual(app.pending_mission['state'],'review')
                self.assertNotEqual(app.revision,base)
                self.assertEqual(app.pending_mission['goal_evidence'][0]['status'],'pending_human_review')
                self.assertEqual((ws.revision('demo',base)/'main.py').read_bytes(),b'VALUE = 1\n')
                self.assertEqual(str(app.accept_mission_button.cget('state')),'normal')
                app.accept_automated_mission(); root.update()
                self.assertIsNone(app.pending_mission)
            finally:
                for callback in root.tk.splitlist(root.tk.call('after','info')): root.after_cancel(callback)
                root.destroy()

    def test_failed_continuation_retires_approval_and_review_clears_stale_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source'; source.mkdir(); (source/'main.py').write_bytes(b'VALUE = 1\n')
            ws=Workspace(Path(directory)/'workspace'); base=ws.create('demo',source=source)
            make_initialization_draft(ws,'demo',base,lambda p:{'summaries':{n:'Value module' for n in p['files']},'blueprint':{'purpose':'Test'}})
            base=accept_initialization(ws,'demo')
            base=create_working_revision(ws,'demo',base)
            root=tk.Tk(); root.withdraw()
            try:
                app=App(root,ws); app.refresh('demo',base); root.update()
                app.endpoint.set('http://localhost'); app.model.set('model')
                app.goal.insert('1.0','Set VALUE to 2')
                plan={'existing_files':['main.py'],'new_files':[],'explanation':'One small action','goals':[
                    {'id':'value','parent':None,'goal':'Set VALUE to 2','files':['main.py'],
                     'success_criteria':'main.py contains VALUE = 2','depends_on':[]}]}
                before=ws.revisions('demo')
                with patch('hornet.desktop.automation.request_json',return_value=plan), \
                     patch.object(app,'launch_query',side_effect=lambda worker:worker()):
                    app.run_mission()
                    for _ in range(50): app.poll()
                self.assertEqual(ws.revisions('demo'),before)
                self.assertEqual(app.pending_mission['state'],'plan_review')
                self.assertEqual(app.approve_plan_button.winfo_manager(),'pack')
                self.assertFalse(app.auto_button.winfo_manager())
                self.assertIn('No source edits',app.views['Proposal'].get('1.0','end'))
                stale = app.pending_mission
                with patch('hornet.desktop.automation.request_json', side_effect=OSError('connection closed')), \
                     patch.object(app, 'launch_query', side_effect=lambda worker: worker()), \
                     patch('hornet.desktop.app.messagebox.showerror'):
                    app.approve_saved_plan()
                    for _ in range(50): app.poll()
                    root.update()
                import json
                path = ws.project('demo') / 'missions' / (stale['id'] + '.json')
                saved = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(saved['state'], 'failed')
                self.assertTrue(saved['plan_approval'])
                self.assertIsNone(app.pending_mission)
                self.assertFalse(app.approve_plan_button.winfo_manager())
                # Reproduce an old UI session, then reload the failed disk record.
                app.pending_mission = stale
                app.update_gate()
                app.review_pending(); root.update()
                self.assertIsNone(app.pending_mission)
                self.assertFalse(app.approve_plan_button.winfo_manager())
                self.assertIn('Resume interrupted mission', app.status.get())
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), saved)
                self.assertEqual(ws.revisions('demo'), before)
            finally:
                for callback in root.tk.splitlist(root.tk.call('after','info')): root.after_cancel(callback)
                root.destroy()
